"""The deck manifest: every deck's id, file, source, archetype and split, plus the tracked matchups.

The file is `decks/manifest.json`, version 1. Spec 5 reads the same format.
Every list of an archetype shares one split, and `add_deck` refuses to break that.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

VERSION = 1
SPLITS = ("train", "held_out")
TRACKED_SLOTS = 4
HELD_OUT_ARCHETYPES = 10
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = DEFAULT_ROOT / "decks" / "manifest.json"


class ManifestError(ValueError):
    pass


@dataclass
class Deck:
    id: str
    file: str
    source: str
    archetype: str
    split: str

    def to_dict(self) -> dict:
        return {"id": self.id, "file": self.file, "source": self.source,
                "archetype": self.archetype, "split": self.split}


@dataclass
class Manifest:
    decks: list[Deck] = field(default_factory=list)
    tracked_matchups: list = field(default_factory=lambda: [None] * TRACKED_SLOTS)

    def get(self, deck_id: str) -> Deck:
        for deck in self.decks:
            if deck.id == deck_id:
                return deck
        raise ManifestError(f"no deck with id {deck_id!r} in the manifest")

    def split_of(self, archetype: str) -> str | None:
        return next((d.split for d in self.decks if d.archetype == archetype), None)

    def add_deck(self, deck: Deck) -> None:
        if deck.split not in SPLITS:
            raise ManifestError(f"split must be one of {SPLITS}, not {deck.split!r}")
        if any(d.id == deck.id for d in self.decks):
            raise ManifestError(f"deck id {deck.id!r} is already in the manifest")
        existing = self.split_of(deck.archetype)
        if existing is not None and existing != deck.split:
            raise ManifestError(f"archetype {deck.archetype!r} is already {existing}; "
                                f"all its lists share one split, so {deck.id!r} can't be {deck.split}")
        self.decks.append(deck)

    def set_tracked(self, slot: int, pair) -> None:
        """Fill one slot, or empty it with None. Both ids must be held-out decks."""
        if not 0 <= slot < TRACKED_SLOTS:
            raise ManifestError(f"slot must be 0 to {TRACKED_SLOTS - 1}")
        if pair is not None:
            pair = list(pair)
            if len(pair) != 2:
                raise ManifestError("a tracked matchup is a pair of deck ids")
            for deck_id in pair:
                if self.get(deck_id).split != "held_out":
                    raise ManifestError(f"tracked matchups use held-out decks; {deck_id!r} is not")
        self.tracked_matchups[slot] = pair

    def validate(self) -> None:
        """Raise ManifestError if a rule is broken. Runs on load and save, so a hand edit can't slip through."""
        ids, splits = set(), {}
        for d in self.decks:
            if d.split not in SPLITS:
                raise ManifestError(f"{d.id}: bad split {d.split!r}")
            if d.id in ids:
                raise ManifestError(f"duplicate deck id {d.id!r}")
            ids.add(d.id)
            if splits.setdefault(d.archetype, d.split) != d.split:
                raise ManifestError(f"archetype {d.archetype!r} has lists in both splits")
        if len(self.tracked_matchups) != TRACKED_SLOTS:
            raise ManifestError(f"tracked_matchups needs exactly {TRACKED_SLOTS} slots")
        held = {d.id for d in self.decks if d.split == "held_out"}
        for pair in self.tracked_matchups:
            if pair is not None and (len(pair) != 2 or not set(pair) <= held):
                raise ManifestError(f"tracked matchup {pair!r} must be two held-out deck ids")

    def to_dict(self) -> dict:
        return {"version": VERSION, "decks": [d.to_dict() for d in self.decks],
                "tracked_matchups": self.tracked_matchups}


def load(path: Path = DEFAULT_PATH) -> Manifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != VERSION:
        raise ManifestError(f"unsupported manifest version {data.get('version')!r}")
    manifest = Manifest([Deck(**d) for d in data["decks"]], data["tracked_matchups"])
    manifest.validate()
    return manifest


def save(manifest: Manifest, path: Path = DEFAULT_PATH) -> None:
    manifest.validate()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8", newline="\n")


def choose_held_out(archetypes, seed: int, count: int = HELD_OUT_ARCHETYPES) -> list[str]:
    """Pick `count` whole archetypes uniformly at random, repeatably: the same seed and names give the same pick."""
    ordered = sorted(set(archetypes))
    if len(ordered) <= count:
        raise ManifestError(f"{len(ordered)} archetypes can't hold out {count} and leave any to train")
    return sorted(random.Random(seed).sample(ordered, count))


def deck_path(manifest: Manifest, deck_id: str, root: Path = DEFAULT_ROOT) -> Path:
    deck = manifest.get(deck_id)
    path = Path(root) / deck.file
    if not path.is_file():
        raise ManifestError(f"deck {deck_id!r} lists {deck.file}, which doesn't exist")
    return path


def build_from_pull(record: dict, seed: int, left_out=()) -> Manifest:
    """The manifest for a pulled pool: ten archetypes chosen by `seed` are held out, every other one trains.

    `left_out` names deck ids that can't play in Forge. They stay on disk but out of the manifest.
    """
    skipped = set(left_out)
    decks = [d for d in record["decks"] if d["id"] not in skipped]
    held = set(choose_held_out({d["archetype"] for d in decks}, seed))
    manifest = Manifest()
    for d in decks:
        manifest.add_deck(Deck(d["id"], f"decks/forge/{d['id']}.dck", d["source"], d["archetype"],
                               "held_out" if d["archetype"] in held else "train"))
    return manifest
