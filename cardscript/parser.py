"""Turn one Forge card script into a `Card`.

A script is a list of `Key:Value` lines. Ability lines (`A:` and, later,
`T:`, `S:`, `R:`) hold `|`-separated segments of the form `Key$ Value`.
"""

from __future__ import annotations

import re

from cardscript.model import VAR, Ability, Card, Face, KeyedNumber, ManaCost


class ParseError(ValueError):
    """A line that cannot be parsed. Carries the line and a short cause."""

    def __init__(self, line: str, cause: str):
        super().__init__(f"{cause}: {line}")
        self.line = line
        self.cause = cause


# First segment's key -> ability kind.
ABILITY_KINDS = {
    "SP": "spell",
    "AB": "activated",
}

# Free-text keys inside an ability line. Dropped so English never becomes a token.
DROPPED_SEGMENT_KEYS = frozenset(
    {
        "SpellDescription",
        "TriggerDescription",
        "Description",
        "TgtPrompt",
        "StackDescription",
    }
)

# Whole lines dropped without a warning.
DROPPED_LINE_KEYS = frozenset({"Oracle"})

COLORS = frozenset("WUBRG")

_INT = re.compile(r"[+-]?\d+")

# Cost grammar: space-separated atoms. An atom is an integer (generic mana), a
# mana symbol, a tap/untap flag, or a part like `ExileFromGrave<2/Card>`.
# A `<...>` part may hold spaces in its description field, hence the regex.
_COST_ATOM = re.compile(r"[^\s<]*<[^>]*>|\S+")
_COST_PART = re.compile(r"(\w+)<([^>]*)>")
_MANA_SYMBOL = re.compile(r"[WUBRGCSPX0-9/]+|XMin\d+")
COST_FLAGS = frozenset({"T", "Q"})


def parse_card(text: str) -> Card:
    """Parse a Forge card script's text into a plain-data `Card`."""
    face = Face()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        if not sep:
            face.warnings.append(f"unrecognized line: {line}")
            continue
        value = value.strip()
        if key == "Name":
            face.name = value
        elif key == "ManaCost":
            face.mana_cost = _mana_cost(value)
        elif key == "Types":
            face.types = value.split()
        elif key == "PT":
            face.power, face.toughness = _power_toughness(value)
        elif key == "A":
            face.abilities.append(_ability(line, value))
        elif key in DROPPED_LINE_KEYS:
            continue
        else:
            face.warnings.append(f"unknown key: {key}")
    return Card(faces=[face])


def _mana_cost(value: str) -> ManaCost:
    cost = ManaCost()
    if value == "no cost":
        return cost
    for symbol in value.split():
        if _INT.fullmatch(symbol):
            cost.generic += int(symbol)
        elif symbol in COLORS:
            cost.pips[symbol] = cost.pips.get(symbol, 0) + 1
        else:
            cost.special.append(symbol)
    return cost


def _power_toughness(value: str) -> tuple[int | str, int | str]:
    power, _, toughness = value.partition("/")
    return _printed_stat(power), _printed_stat(toughness)


def _printed_stat(value: str) -> int | str:
    """An integer, or `<var>` for anything computed at runtime (`*`, `1+*`)."""
    value = value.strip()
    return int(value) if _INT.fullmatch(value) else VAR


def _ability(line: str, value: str) -> Ability:
    segments = [_segment(line, s) for s in value.split("|")]
    api, effect = segments[0]
    if api not in ABILITY_KINDS:
        raise ParseError(line, f"unknown ability API {api!r}")
    ability = Ability(kind=ABILITY_KINDS[api], tokens=[f"Effect={effect}"])
    for key, val in segments[1:]:
        _add_segment(ability, key, val)
    return ability


def _add_segment(ability: Ability, key: str, value: str) -> None:
    """One `Key$ Value` segment: dropped, parsed as a cost, or a plain value."""
    if key in DROPPED_SEGMENT_KEYS:
        return
    if key.endswith("Cost"):
        _add_cost(ability, key, value)
    else:
        _add_value(ability, key, value)


def _add_value(ability: Ability, key: str, value: str) -> None:
    """The one place a value is classified: an integer, or a categorical token."""
    if _INT.fullmatch(value):
        ability.numbers.append(KeyedNumber(key, int(value)))
    else:
        ability.tokens.append(f"{key}={value}")


def _add_cost(ability: Ability, key: str, value: str) -> None:
    """A cost: `R T ExileFromGrave<2/Card>` under key `Cost`.

    Integers are generic mana, a number under `key`. Mana symbols and T/Q are
    `key=symbol` tokens. A part `Name<amount/type/description>` is a `key=Name`
    token, its amount a number under `key.Name`, its type a `key.Name=type`
    token; free-text fields are dropped. Anything else is a plain value.
    """
    for atom in _COST_ATOM.findall(value):
        part = _COST_PART.fullmatch(atom)
        if part:
            _add_cost_part(ability, key, part[1], part[2].split("/"))
        elif _INT.fullmatch(atom):
            ability.numbers.append(KeyedNumber(key, int(atom)))
        elif atom in COST_FLAGS or _MANA_SYMBOL.fullmatch(atom):
            ability.tokens.append(f"{key}={atom}")
        else:
            _add_value(ability, key, atom)


def _add_cost_part(ability: Ability, key: str, name: str, fields: list[str]) -> None:
    sub = f"{key}.{name}"
    ability.tokens.append(f"{key}={name}")
    amount, *rest = fields
    if amount:
        _add_value(ability, sub, amount)
    for position, field_ in enumerate(rest, 2):
        if not field_ or _is_free_text(field_):
            continue
        if _INT.fullmatch(field_):
            ability.numbers.append(KeyedNumber(f"{sub}.{position}", int(field_)))
        else:
            ability.tokens.append(f"{sub}={field_}")


def _is_free_text(value: str) -> bool:
    """English description fields: `another creature`, `card`, `monocolored`."""
    return " " in value or value[:1].islower()


def _segment(line: str, segment: str) -> tuple[str, str]:
    key, sep, value = segment.partition("$")
    key, value = key.strip(), value.strip()
    if not sep or not key:
        raise ParseError(line, "segment without Key$")
    return key, value
