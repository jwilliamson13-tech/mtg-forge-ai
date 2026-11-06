"""How close two main decks are, read from their Forge files."""

from __future__ import annotations

from collections import Counter


def main_cards(forge_text: str) -> Counter:
    """The `[Main]` section of a Forge deck file, as copies per card name."""
    cards: Counter = Counter()
    in_main = False
    for line in forge_text.splitlines():
        if line.startswith("["):
            in_main = line == "[Main]"
        elif in_main and line.strip():
            count, name = line.split(" ", 1)
            cards[name] += int(count)
    return cards
