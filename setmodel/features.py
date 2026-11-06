"""Parsed cards as encoder input: find a card by name and face, then lay it out under the caps.

`CardTable` answers "what does this name and face do". `encode` turns the answer into
vocabulary ids under the caps, and `densify` into the arrays of the tensor contract.
Training and the export bundle both go through these, so Forge's table can't differ
from what the model trained on.

Names alone can't always say. Two rules cover it, and `docs/training.md` gives the counts:

- A name on several scripts keeps only what they all share. `Elemental Token` is 35
  scripts with different stats and abilities, and the record doesn't say which one is
  on the table. Picking one would hand the model abilities the card may not have, and
  the live power and toughness still come from the game.
- A name printed on a real card and also on a token script is the real card.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cardscript.model import Card, Face
from cardscript.vocab import Vocabulary, face_rows

SPLIT_NAME = " // "
TOKEN_SCRIPTS = "tokenscripts/"
COPIED_FACE = "copied face not resolved: "

# Where a face sits on its card. A parsed card doesn't keep Forge's state names, because
# `AlternateMode:` is dropped, so the many back states share a slot.
FRONT = "front"
BACK = "back"
EMPTY_ROOM = "empty_room"

Row = tuple[tuple[str, ...], tuple[tuple[str, int], ...]]  # one ability's tokens and keyed numbers


def face_slot(face: str) -> str:
    """The slot a record's `face` value looks up. The tensor contract repeats this rule for Forge."""
    if face == "Original":
        return FRONT
    return EMPTY_ROOM if face == "EmptyRoom" else BACK


@dataclass(frozen=True)
class Caps:
    """Fixed inner sizes of a card. The dump's faces go over them 0.03% and 0.004% of the time."""

    abilities: int = 16  # A: rows per card, the identity row included
    tokens: int = 24  # T: tokens per row


@dataclass(frozen=True)
class Script:
    file: str
    slot: str
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Resolved:
    rows: tuple[Row, ...]
    ambiguous: bool  # several scripts disagreed, so these rows are only what they share


def _rows(face: Face) -> tuple[Row, ...]:
    return tuple((tuple(tokens), tuple((n.key, n.value) for n in numbers)) for tokens, numbers in face_rows(face))


def _common(lists: list[tuple]) -> tuple:
    """The items of the first list that are in every list, counting repeats."""
    left = Counter(lists[0])
    for other in lists[1:]:
        left &= Counter(other)
    shared = []
    for item in lists[0]:
        if left[item]:
            left[item] -= 1
            shared.append(item)
    return tuple(shared)


def _shared_rows(scripts: list[Script]) -> tuple[Row, ...]:
    identity = (_common([s.rows[0][0] for s in scripts]), _common([s.rows[0][1] for s in scripts]))
    return (identity, *_common([s.rows[1:] for s in scripts]))


def _merge(scripts: list[Script]) -> Resolved:
    printed = [s for s in scripts if not s.file.startswith(TOKEN_SCRIPTS)] or scripts
    if len({s.rows for s in printed}) == 1:
        return Resolved(printed[0].rows, False)
    return Resolved(_shared_rows(printed), True)


def _face_name(face: Face) -> str:
    """A face's name, or the one it copies from another script."""
    if face.name:
        return face.name
    return next((w.removeprefix(COPIED_FACE) for w in face.warnings if w.startswith(COPIED_FACE)), "")


class CardTable:
    """The parsed card dump, found by name and face."""

    def __init__(self, lines: Iterable[dict]):
        self._scripts: dict[str, list[Script]] = {}
        self._whole_names: set[str] = set()
        self._resolved: dict[tuple[str, str], Resolved | None] = {}
        for line in lines:
            faces = Card.from_dict(line).faces
            if len(faces) > 1:
                self._whole_names.add(SPLIT_NAME.join(_face_name(face) for face in faces))
            for index, face in enumerate(faces):
                if face.name:
                    script = Script(line.get("file", ""), BACK if index else FRONT, _rows(face))
                    self._scripts.setdefault(face.name, []).append(script)

    @classmethod
    def load(cls, path: str | Path) -> CardTable:
        with open(path, encoding="utf-8") as lines:
            return cls(json.loads(line) for line in lines)

    def script_count(self, name: str) -> int:
        return len(self._scripts.get(name, ()))

    def resolve(self, name: str, face: str) -> Resolved | None:
        """What this name and face do, or None if the dump doesn't have them."""
        key = (name, face_slot(face))
        if key not in self._resolved:
            self._resolved[key] = self._lookup(*key)
        return self._resolved[key]

    def _lookup(self, name: str, slot: str) -> Resolved | None:
        scripts = self._scripts.get(name)
        if scripts:
            return _merge([s for s in scripts if s.slot == slot] or scripts)
        # Forge calls a whole split card `Fire // Ice`, and the parser has `Fire` and `Ice`.
        parts = name.split(SPLIT_NAME)
        if len(parts) < 2 or any(part not in self._scripts for part in parts):
            return None
        halves = [_merge(self._scripts[part]) for part in parts]
        rows = [h.rows[:1] if slot == EMPTY_ROOM else h.rows for h in halves]  # an empty Room has no door's abilities
        return Resolved(tuple(row for half in rows for row in half), any(h.ambiguous for h in halves))

    def entries(self) -> Iterator[tuple[str, str, Resolved]]:
        """(name, slot, resolved) for everything Forge can ask for, in name order."""
        keys = {(name, script.slot) for name, scripts in self._scripts.items() for script in scripts}
        keys |= {(name, slot) for name in self._whole_names for slot in (FRONT, BACK, EMPTY_ROOM)}
        for name, slot in sorted(keys):
            resolved = self._lookup(name, slot)
            if resolved is not None:
                yield name, slot, resolved


@dataclass(frozen=True)
class Encoded:
    """One card under the caps, as vocabulary ids. A row's lists hold only what is there, so they imply the masks."""

    tokens: tuple[tuple[int, ...], ...]
    numbers: tuple[tuple[tuple[int, float], ...], ...]
    rows_before_cap: int = 0
    rows_over_token_cap: int = 0
    rows_with_merged_keys: int = 0


EMPTY_CARD = Encoded((), ())  # a hidden card: no name, so nothing printed. Its live state still goes in.


def _capped_rows(rows: tuple[Row, ...], caps: Caps) -> tuple[Row, ...]:
    """Drop the oldest abilities past the cap. Row 0 says what the card is, so it stays."""
    if len(rows) <= caps.abilities:
        return rows
    return (rows[0], *rows[len(rows) - caps.abilities + 1:])


def encode(rows: tuple[Row, ...], vocab: Vocabulary, caps: Caps) -> Encoded:
    """Vocabulary ids under the caps. Two numbers under one key in a row add up, because `numbers` holds one value per key."""
    tokens, numbers = [], []
    over_tokens = merged = 0
    for row_tokens, row_numbers in _capped_rows(rows, caps):
        over_tokens += len(row_tokens) > caps.tokens
        tokens.append(tuple(vocab.index(token) for token in row_tokens[-caps.tokens:]))  # the oldest tokens go
        by_key: dict[int, float] = {}
        for key, value in row_numbers:
            index = vocab.key_index(key)
            by_key[index] = by_key.get(index, 0.0) + value
        merged += len(by_key) < len(row_numbers)
        numbers.append(tuple(by_key.items()))
    return Encoded(tuple(tokens), tuple(numbers), len(rows), over_tokens, merged)


def densify(cards: list[Encoded], caps: Caps, key_count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The contract's `token_ids`, `token_mask`, `numbers` and `number_mask` for these cards."""
    shape = (len(cards), caps.abilities)
    token_ids = np.zeros((*shape, caps.tokens), dtype=np.int64)
    token_mask = np.zeros((*shape, caps.tokens), dtype=np.float32)
    numbers = np.zeros((*shape, key_count), dtype=np.float32)
    number_mask = np.zeros((*shape, key_count), dtype=np.float32)
    for n, card in enumerate(cards):
        for a, row in enumerate(card.tokens):
            token_ids[n, a, :len(row)] = row
            token_mask[n, a, :len(row)] = 1
        for a, row in enumerate(card.numbers):
            for key, value in row:
                numbers[n, a, key] = value
                number_mask[n, a, key] = 1
    return token_ids, token_mask, numbers, number_mask


def key_scales(numbers: np.ndarray, number_mask: np.ndarray) -> np.ndarray:
    """One constant per numeric key: a robust spread of its values, so a stray huge value can't set it.

    It is the median absolute deviation, or the median size where most values agree, or 1.
    """
    scales = np.ones(numbers.shape[-1], dtype=np.float32)
    for key in range(numbers.shape[-1]):
        values = numbers[..., key][number_mask[..., key] > 0]
        if values.size:
            spread = np.median(np.abs(values - np.median(values)))
            scales[key] = spread or np.median(np.abs(values)) or 1.0
    return scales
