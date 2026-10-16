"""Vocabulary: which categorical tokens and numeric keys get an embedding.

`build_vocabulary` counts tokens over parsed cards, keeps the ones on at
least `min_count` cards, and returns a `Vocabulary` with `<unk>`, `<var>`
and `<ref>` at indices 0, 1, 2 whatever the corpus. Numeric keys get their
own table with `<unk>` at index 0. docs/vocabulary.md describes the token
names and the lookup rules.

`face_tokens` and `face_numbers` are the one place that turns a parsed face
into what the encoder looks up, so the builder and the encoder can't drift.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cardscript.model import REF, VAR, Ability, Card, Face, KeyedNumber

UNK = "<unk>"
RESERVED = (UNK, VAR, REF)
FORMAT = 1


def ability_tokens(ability: Ability) -> list[str]:
    """`kind=<kind>` then the ability's own tokens. Sub-abilities not included."""
    return [f"kind={ability.kind}", *ability.tokens]


def _walk(abilities: list[Ability]):
    for ability in abilities:
        yield ability
        yield from _walk(ability.subabilities)


def face_tokens(face: Face) -> list[str]:
    """Every categorical token on a face: identity fields, then each ability and sub-ability."""
    tokens = [f"type={t}" for t in face.types]
    tokens += [f"color={c}" for c in face.colors]
    tokens += [f"pip={p}" for p in face.mana_cost.special]
    for field, value in (("power", face.power), ("toughness", face.toughness)):
        if isinstance(value, str):
            tokens.append(f"{field}={value}")
    for ability in _walk(face.abilities):
        tokens += ability_tokens(ability)
    return tokens


def face_numbers(face: Face) -> list[KeyedNumber]:
    """Every keyed number on a face: mana cost, printed P/T, then each ability and sub-ability."""
    numbers = [KeyedNumber("generic", face.mana_cost.generic)]
    numbers += [KeyedNumber(f"pip.{p}", n) for p, n in face.mana_cost.pips.items()]
    for field, value in (("power", face.power), ("toughness", face.toughness)):
        if isinstance(value, int):
            numbers.append(KeyedNumber(field, value))
    for ability in _walk(face.abilities):
        numbers += ability.numbers
    return numbers


@dataclass
class Vocabulary:
    """Two ordered tables. A token's or key's index is its position."""

    tokens: list[str]
    numeric_keys: list[str]
    min_count: int = 1

    def __post_init__(self):
        if tuple(self.tokens[: len(RESERVED)]) != RESERVED:
            raise ValueError(f"tokens must start with {list(RESERVED)}")
        if self.numeric_keys[:1] != [UNK]:
            raise ValueError(f"numeric_keys must start with {UNK!r}")
        self._token_index = {t: i for i, t in enumerate(self.tokens)}
        self._key_index = {k: i for i, k in enumerate(self.numeric_keys)}

    def index(self, token: str) -> int:
        """The token's index. Unknown `Key=<var>` gives `<var>`, `Key=<ref>` gives `<ref>`, else `<unk>`."""
        found = self._token_index.get(token)
        if found is not None:
            return found
        if token.endswith("=" + VAR):
            return self._token_index[VAR]
        if token.endswith("=" + REF):
            return self._token_index[REF]
        return self._token_index[UNK]

    def key_index(self, key: str) -> int:
        """The numeric key's index, or 0 (`<unk>`) for a key below the cutoff."""
        return self._key_index.get(key, 0)

    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "min_count": self.min_count,
            "tokens": list(self.tokens),
            "numeric_keys": list(self.numeric_keys),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Vocabulary:
        return cls(tokens=list(d["tokens"]), numeric_keys=list(d["numeric_keys"]), min_count=d["min_count"])

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Vocabulary:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _ordered(counts: Counter, min_count: int, reserved: tuple[str, ...]) -> list[str]:
    kept = [t for t, n in counts.items() if n >= min_count and t not in reserved]
    kept.sort(key=lambda t: (-counts[t], t))
    return [*reserved, *kept]


def build_vocabulary(cards: Iterable[Card | dict], min_count: int = 1) -> Vocabulary:
    """Count tokens and numeric keys over `cards` and keep those on at least `min_count` cards.

    `cards` holds parsed `Card`s or their `to_dict()` form (one JSONL line,
    loaded). A token or key counts once per card however often the card has it.
    """
    if min_count < 1:
        raise ValueError("min_count must be at least 1")
    token_counts: Counter = Counter()
    key_counts: Counter = Counter()
    for card in cards:
        if isinstance(card, dict):
            card = Card.from_dict(card)
        tokens: set[str] = set()
        keys: set[str] = set()
        for face in card.faces:
            tokens.update(face_tokens(face))
            keys.update(n.key for n in face_numbers(face))
        token_counts.update(tokens)
        key_counts.update(keys)
    return Vocabulary(
        tokens=_ordered(token_counts, min_count, RESERVED),
        numeric_keys=_ordered(key_counts, min_count, (UNK,)),
        min_count=min_count,
    )
