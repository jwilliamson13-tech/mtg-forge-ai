"""Plain-text decklists to Forge deck files, with every name checked.

A list is lines of `count name`, with an optional set after the name or in
brackets before it (`4 [ZEN] Arid Mesa`, the MTGTop8 .dec export). `//` lines are comments,
apart from `// Sideboard` and the like, which work as headers.

The sideboard follows a `Sideboard` header, or its lines carry an `SB:` prefix. With either marker,
blank lines mean nothing. Without one, a single blank line splits main from sideboard, and more than
one fails the parse, because the blocks could be card types. An Arena `Companion` block goes to the
sideboard.

Names are matched against a set of known names, ignoring case, accents and punctuation. A name that
doesn't match fails the whole conversion, and the error lists every such name.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

SPLIT_SEP = " // "
MIN_MAIN_CARDS = 60
_LINE = re.compile(r"^(?:SB:\s*)?(\d+)x?\s+(?:\[[^\]]*\]\s*)?(.+?)\s*$", re.IGNORECASE)
_SET_TAIL = re.compile(r"\s+\(([A-Za-z0-9]{2,6})\)(?:\s+\S+)?$")
_SIDE_HEADERS = {"sideboard", "sideboard:", "sb", "sb:"}
_MAIN_HEADERS = {"deck", "deck:", "maindeck", "main", "mainboard"}
_COMPANION_HEADERS = {"companion", "companion:"}


class DeckError(ValueError):
    pass


class UnresolvedNames(DeckError):
    def __init__(self, names: list[str]):
        self.names = names
        super().__init__("unresolved card names: " + "; ".join(names))


def normalize(name: str) -> str:
    """The comparison key: lowercase, no accents, no punctuation, one space between words."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub("['‘’`]", "", text.casefold()).replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


class NameSet:
    """Known card names, looked up by normalized key. The value is the name as Forge spells it.

    `split_cards` lists the `A // B` names of Forge's split cards. Forge reads `Fire // Ice` as one
    card but doesn't read `Delver of Secrets // Insectile Aberration`, which it wants as the front
    face alone, so a two-name entry resolves to the joined name only for a known split card. With
    `split_cards` left as None the layouts are unknown, and any two known halves are joined.
    """

    def __init__(self, names, split_cards=None):
        self._by_key: dict[str, str] = {}
        for name in names:
            self._by_key.setdefault(normalize(name), name)
        self._splits = None if split_cards is None else {normalize(n) for n in split_cards}

    def __len__(self) -> int:
        return len(self._by_key)

    def resolve(self, name: str) -> str | None:
        """The known spelling of `name`, or None. An `A // B` name needs both halves to be known."""
        if SPLIT_SEP not in name and " / " in name:  # MTGTop8 writes `Wear / Tear`
            name = name.replace(" / ", SPLIT_SEP)
        if SPLIT_SEP not in name:
            return self._by_key.get(normalize(name))
        halves = [self._by_key.get(normalize(h)) for h in name.split(SPLIT_SEP)]
        if not all(halves):
            return None
        joined = SPLIT_SEP.join(halves)
        return joined if self._splits is None or normalize(joined) in self._splits else halves[0]


@dataclass
class Decklist:
    main: list[tuple[int, str]] = field(default_factory=list)
    side: list[tuple[int, str]] = field(default_factory=list)

    @property
    def main_cards(self) -> int:
        return sum(count for count, _ in self.main)


def parse_decklist(text: str) -> Decklist:
    """Read count and name lines. Raises DeckError on an unreadable line, an unclear sideboard or an empty main deck."""
    deck = Decklist()
    blocks: list[list[tuple[int, str]]] = [[]]  # main-deck lines, broken at each blank line
    companions: list[tuple[int, str]] = []
    section, marked_side = "main", False
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        header = line.removeprefix("//").strip().lower()
        if not line:
            if section == "companion":
                section = "main"
            elif blocks[-1]:
                blocks.append([])
        elif header in _SIDE_HEADERS:
            section, marked_side = "side", True
        elif header in _MAIN_HEADERS:
            section = "main"
        elif header in _COMPANION_HEADERS:
            section = "companion"
        elif not line.startswith("//"):
            m = _LINE.match(line)
            if not m:
                raise DeckError(f"can't read line: {raw!r}")
            entry = (int(m.group(1)), _SET_TAIL.sub("", m.group(2)))
            if line.upper().startswith("SB:"):
                marked_side = True
                deck.side.append(entry)
            else:
                {"main": blocks[-1], "side": deck.side, "companion": companions}[section].append(entry)
    blocks = [block for block in blocks if block]
    if marked_side:
        deck.main = [entry for block in blocks for entry in block]
    elif len(blocks) > 2:
        raise DeckError(f"{len(blocks)} blocks of cards and no sideboard marker, so the main deck is unclear; "
                        "put a `Sideboard` line before the sideboard")
    elif blocks:
        deck.main = blocks[0]
        deck.side = blocks[1] if len(blocks) == 2 else []
    deck.side += [c for c in companions if c[1] not in {name for _, name in deck.side}]  # Arena lists it twice
    if not deck.main:
        raise DeckError("no main deck lines")
    return deck


def _merged(entries: list[tuple[int, str]], names: NameSet, unknown: list[str]) -> list[tuple[int, str]]:
    counts: dict[str, int] = {}
    for count, name in entries:
        resolved = names.resolve(name)
        if resolved is None:
            if name not in unknown:
                unknown.append(name)
            continue
        counts[resolved] = counts.get(resolved, 0) + count
    return [(c, n) for n, c in counts.items()]


def to_forge(deck: Decklist, names: NameSet, deck_name: str) -> str:
    """Forge deck-file text. Raises UnresolvedNames, listing every unknown name, if any fail."""
    unknown: list[str] = []
    main = _merged(deck.main, names, unknown)
    side = _merged(deck.side, names, unknown)
    if unknown:
        raise UnresolvedNames(unknown)
    lines = ["[metadata]", f"Name={deck_name}", "[Main]", *(f"{c} {n}" for c, n in main)]
    if side:
        lines += ["[Sideboard]", *(f"{c} {n}" for c, n in side)]
    return "\n".join(lines) + "\n"


def convert(text: str, names: NameSet, deck_name: str) -> str:
    return to_forge(parse_decklist(text), names, deck_name)
