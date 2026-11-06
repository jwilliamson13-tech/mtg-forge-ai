"""The set of card names the parser knows, read from the sweep output `data/cards.jsonl`."""

from __future__ import annotations

import json
from pathlib import Path

from deckpool.convert import SPLIT_SEP, NameSet

DEFAULT_CARDSFOLDER = Path(__file__).resolve().parents[1].parent / "forge" / "forge-gui" / "res" / "cardsfolder"


def _is_split(script: Path) -> bool:
    return script.is_file() and "AlternateMode:Split" in script.read_text(encoding="utf-8", errors="replace")


def load_name_set(cards_jsonl: Path, cardsfolder: Path | None = DEFAULT_CARDSFOLDER) -> NameSet:
    """Every face name. With the Forge `cardsfolder` at hand, also which multi-face cards are split cards."""
    names, splits = [], []
    with open(cards_jsonl, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            faces = [face["name"] for face in row["faces"]]
            names.extend(faces)
            if len(faces) > 1 and cardsfolder and Path(cardsfolder).is_dir() and _is_split(Path(cardsfolder) / row["file"].removeprefix("cardsfolder/")):
                splits.append(SPLIT_SEP.join(faces))
    return NameSet(names, splits if cardsfolder and Path(cardsfolder).is_dir() else None)
