"""Readable text rendering of a parsed card, for `show`."""

from __future__ import annotations

from cardscript.model import OWN_LINE_STATS, PT, Ability, Card, Face, ManaCost


def format_card(card: Card) -> str:
    lines: list[str] = []
    for i, face in enumerate(card.faces):
        if i:
            lines.append("")
        lines.extend(_face(face))
    return "\n".join(lines) + "\n"


def _face(face: Face) -> list[str]:
    lines = [face.name, f"  cost: {_cost(face.mana_cost)}"]
    lines.append(f"  types: {' '.join(face.types)}")
    if face.colors:
        lines.append(f"  colors: {' '.join(face.colors)}")
    if any(getattr(face, stat) is not None for stat in PT):
        lines.append(f"  pt: {face.power}/{face.toughness}")
    for stat in OWN_LINE_STATS:
        if getattr(face, stat) is not None:
            lines.append(f"  {stat}: {getattr(face, stat)}")
    for n, ability in enumerate(face.abilities, 1):
        lines.extend(_ability(ability, f"ability {n}", "  "))
    for warning in face.warnings:
        lines.append(f"  warning: {warning}")
    return lines


def _cost(cost: ManaCost) -> str:
    parts = [f"generic={cost.generic}"]
    parts += [f"{pip}={count}" for pip, count in cost.pips.items()]
    parts += [f"special={s}" for s in cost.special]
    return " ".join(parts)


def _ability(ability: Ability, label: str, indent: str) -> list[str]:
    lines = [f"{indent}{label}: {ability.kind}"]
    if ability.tokens:
        lines.append(f"{indent}  tokens: {' '.join(ability.tokens)}")
    if ability.numbers:
        nums = " ".join(f"{n.key}={n.value}" for n in ability.numbers)
        lines.append(f"{indent}  numbers: {nums}")
    for n, sub in enumerate(ability.subabilities, 1):
        lines.extend(_ability(sub, f"sub {n}", indent + "  "))
    return lines
