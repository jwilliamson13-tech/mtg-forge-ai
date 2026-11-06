"""The set of card names the parser knows, read from the sweep output `data/cards.jsonl`."""

from __future__ import annotations

import json
from pathlib import Path

from deckpool.convert import NameSet


def load_name_set(cards_jsonl: Path) -> NameSet:
    names = []
    with open(cards_jsonl, encoding="utf-8") as f:
        for line in f:
            names.extend(face["name"] for face in json.loads(line)["faces"])
    return NameSet(names)
