"""The parsed card as plain data.

Every class here holds only strings, ints, lists, dicts and other model
objects, so `to_dict()` output is JSON-able and `from_dict()` rebuilds an
equal object. docs/card-structure.md documents the same shape for readers
outside Python.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

VAR = "<var>"
REF = "<ref>"


@dataclass
class KeyedNumber:
    key: str
    value: int


@dataclass
class Ability:
    kind: str
    tokens: list[str] = field(default_factory=list)
    numbers: list[KeyedNumber] = field(default_factory=list)
    subabilities: list[Ability] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Ability:
        return cls(
            kind=d["kind"],
            tokens=list(d["tokens"]),
            numbers=[KeyedNumber(**n) for n in d["numbers"]],
            subabilities=[cls.from_dict(s) for s in d["subabilities"]],
        )


@dataclass
class ManaCost:
    generic: int = 0
    pips: dict[str, int] = field(default_factory=dict)
    special: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> ManaCost:
        return cls(generic=d["generic"], pips=dict(d["pips"]), special=list(d["special"]))


@dataclass
class Face:
    name: str = ""
    mana_cost: ManaCost = field(default_factory=ManaCost)
    types: list[str] = field(default_factory=list)
    power: int | str | None = None
    toughness: int | str | None = None
    abilities: list[Ability] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Face:
        return cls(
            name=d["name"],
            mana_cost=ManaCost.from_dict(d["mana_cost"]),
            types=list(d["types"]),
            power=d["power"],
            toughness=d["toughness"],
            abilities=[Ability.from_dict(a) for a in d["abilities"]],
            warnings=list(d["warnings"]),
        )


@dataclass
class Card:
    faces: list[Face] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Card:
        return cls(faces=[Face.from_dict(f) for f in d["faces"]])

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> Card:
        return cls.from_dict(json.loads(s))
