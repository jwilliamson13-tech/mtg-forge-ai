"""How close two main decks are, and which archetypes that ties together.

Overlap is the cards two mains have in common, counting copies, divided by the larger main. Two lists
of different archetypes at or above the threshold are near-copies, and near-copies link their archetypes.
Links chain: if A links to B and B links to C, then A, B and C are one group and share one split.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations

from deckpool.convert import normalize

NEAR_COPY_THRESHOLD = 0.70


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


def overlap(a: Counter, b: Counter) -> float:
    return sum((a & b).values()) / max(sum(a.values()), sum(b.values()))


def near_copies(archetype_of: dict[str, str], mains: dict[str, Counter],
                threshold: float = NEAR_COPY_THRESHOLD) -> list[tuple[float, str, str]]:
    """Every pair of lists from different archetypes at or above `threshold`, closest first."""
    pairs = []
    for a, b in combinations(sorted(archetype_of), 2):
        if normalize(archetype_of[a]) != normalize(archetype_of[b]):
            score = overlap(mains[a], mains[b])
            if score >= threshold:
                pairs.append((score, a, b))
    return sorted(pairs, key=lambda pair: (-pair[0], pair[1], pair[2]))


def link_groups(archetype_of: dict[str, str], mains: dict[str, Counter]) -> list[list[str]]:
    """Every archetype in exactly one group, with an unlinked archetype alone in its own. The order is fixed."""
    spelled = {normalize(name): name for name in archetype_of.values()}
    parent = {key: key for key in spelled}

    def find(key: str) -> str:
        while parent[key] != key:
            key = parent[key]
        return key

    for _, a, b in near_copies(archetype_of, mains):
        parent[find(normalize(archetype_of[a]))] = find(normalize(archetype_of[b]))
    groups = defaultdict(list)
    for key in sorted(spelled):
        groups[find(key)].append(spelled[key])
    return sorted(groups.values())
