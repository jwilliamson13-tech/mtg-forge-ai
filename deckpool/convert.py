"""Plain-text decklists to Forge deck files, with every name checked.

A list is lines of `count name`, optionally with a set after the name, in the
form MTGGoldfish exports. The sideboard follows a blank line or a `Sideboard`
header. Names are matched against a set of known names, ignoring case, accents
and punctuation. A name that doesn't match fails the whole conversion, and the
error lists every such name.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

SPLIT_SEP = " // "
_LINE = re.compile(r"^(?:SB:\s*)?(\d+)x?\s+(.+?)\s*$", re.IGNORECASE)
_SET_TAIL = re.compile(r"\s+\(([A-Za-z0-9]{2,6})\)(?:\s+\S+)?$")
_SIDE_HEADERS = {"sideboard", "sideboard:", "sb", "sb:"}
_SKIP_HEADERS = {"deck", "deck:", "maindeck", "main", "mainboard", "companion", "companion:"}


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
    text = re.sub("['‘’`]", "",text.casefold()).replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


class NameSet:
    """Known card names, looked up by normalized key. The value is the name as Forge spells it."""

    def __init__(self, names):
        self._by_key: dict[str, str] = {}
        for name in names:
            self._by_key.setdefault(normalize(name), name)

    def __len__(self) -> int:
        return len(self._by_key)

    def resolve(self, name: str) -> str | None:
        """The known spelling of `name`, or None. A `A // B` name resolves when both halves do."""
        hit = self._by_key.get(normalize(name))
        if hit is not None:
            return hit
        if SPLIT_SEP in name:
            halves = [self._by_key.get(normalize(h)) for h in name.split(SPLIT_SEP)]
            if all(halves):
                return SPLIT_SEP.join(halves)
        return None


@dataclass
class Decklist:
    main: list[tuple[int, str]] = field(default_factory=list)
    side: list[tuple[int, str]] = field(default_factory=list)


def parse_decklist(text: str) -> Decklist:
    """Read count and name lines. Raises DeckError on a line it can't read, or an empty main deck."""
    deck = Decklist()
    in_side = False
    seen_main = False
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line:
            if seen_main:
                in_side = True
            continue
        low = line.lower()
        if low in _SIDE_HEADERS:
            in_side = True
            continue
        if low in _SKIP_HEADERS:
            continue
        m = _LINE.match(line)
        if not m:
            raise DeckError(f"can't read line: {raw!r}")
        count, name = int(m.group(1)), _SET_TAIL.sub("", m.group(2))
        side = in_side or line.upper().startswith("SB:")
        (deck.side if side else deck.main).append((count, name))
        seen_main = seen_main or not side
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
