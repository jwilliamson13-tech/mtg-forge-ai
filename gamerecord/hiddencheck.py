"""Try to name a batch's hidden cards from the rest of the batch.

A hidden card is one row with a null name: a face-down permanent or spell the side may
not look at. The ids in a game record must not undo that, so this runs the two attacks
that worked when records held Forge's own card ids:

- other games: an id that names a card in another game of the folder. Forge numbers
  cards in deck order, so its ids meant the same card in every game of a batch. It counts
  only when that name is also a card of the hidden card's own game, since only then can it
  be the hidden card. Record ids are 31-bit hashes, so in a pool of millions of ids one can
  turn up in another game by chance, naming a card from other decks. Those are counted apart
  as chance collisions and don't fail the check.
- other side: an id that the other side's rows of the same file put a name to.

Both must come to zero. A third count is not an id attack and can't reach zero: the
other side's row from the same moment holds the card under its real name, and if only
one face-down card there has the same public state, that names it. One file holds both
sides by design, so `docs/game-record.md` gives a training row to one side at a time.

It reads the raw lines and not `load_game`, so that it can run on a file the loader
refuses, such as one from before a schema change.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

GAME_FILES = "*.jsonl.gz"
PUBLIC_STATE = ("controller", "tapped", "power", "toughness", "counters", "damage")


@dataclass(frozen=True)
class HiddenCheck:
    games: int
    hidden: int  # hidden objects, one per game, side and id
    by_other_games: int
    by_other_side: int
    by_public_state: int
    by_chance: int = 0  # ids that turn up in another game under a name this game never shows

    @property
    def ids_named_nothing(self) -> bool:
        return not self.by_other_games and not self.by_other_side


def _lines(path: Path) -> Iterator[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            yield json.loads(line)


def _rows(snapshot: dict) -> Iterator[dict]:
    """Every card row and stack item in a snapshot."""
    for cards in snapshot["zones"].values():
        yield from cards
    yield from snapshot["stack"]


def _public_state(card: dict) -> tuple:
    return tuple(json.dumps(card[name], sort_keys=True) for name in PUBLIC_STATE)


def _named_by_state(card: dict, other: dict | None) -> bool:
    """True if the other side's line has exactly one name for a face-down card that looks like this one."""
    if other is None:
        return False
    names = {row["name"] for row in other["zones"]["battlefield"]
             if row["face_down"] and row["name"] is not None and _public_state(row) == _public_state(card)}
    return len(names) == 1


def _neighbors(snapshots: list[dict], index: int) -> Iterator[dict]:
    """The other side's lines right before and after this one, on the same turn.

    The hook writes both sides of a moment together, and skips a line that hasn't changed.
    """
    snapshot = snapshots[index]
    for step in (-1, 1):
        position = index + step
        while 0 <= position < len(snapshots):
            other = snapshots[position]
            if other["side"] != snapshot["side"]:
                if other["scoreboard"]["turn"] == snapshot["scoreboard"]["turn"]:
                    yield other
                break
            position += step


def check_games(paths: Iterable[Path]) -> HiddenCheck:
    """Run the attacks over these game files, taken as one batch."""
    games_naming = defaultdict(lambda: defaultdict(set))  # id -> game -> the names it has there
    game_names = defaultdict(set)  # game -> every name either side sees in it
    hidden = set()  # (game, side, id)
    named_by_side = set()
    named_by_state = set()
    games = 0
    for path in paths:
        lines = _lines(path)
        metadata = next(lines)
        game = (metadata["seed"], metadata["game_number"])
        games += 1
        snapshots = list(lines)
        names_by_side = defaultdict(set)  # side -> the ids it has a name for
        for snapshot in snapshots:
            for row in _rows(snapshot):
                if row["name"] is not None and row["id"] is not None:
                    games_naming[row["id"]][game].add(row["name"])
                    game_names[game].add(row["name"])
                    names_by_side[snapshot["side"]].add(row["id"])
        for index, snapshot in enumerate(snapshots):
            side = snapshot["side"]
            for row in _rows(snapshot):
                if row["name"] is not None:
                    continue
                key = (game, side, row["id"])
                hidden.add(key)
                if row["id"] in names_by_side[3 - side]:
                    named_by_side.add(key)
            for card in snapshot["zones"]["battlefield"]:
                if card["name"] is None and any(_named_by_state(card, other) for other in _neighbors(snapshots, index)):
                    named_by_state.add((game, side, card["id"]))
    named_by_games, by_chance = set(), set()
    for key in hidden:
        elsewhere = {name for other, names in games_naming[key[2]].items() if other != key[0] for name in names}
        if elsewhere & game_names[key[0]]:
            named_by_games.add(key)
        elif elsewhere:
            by_chance.add(key)
    return HiddenCheck(games, len(hidden), len(named_by_games), len(named_by_side), len(named_by_state),
                       len(by_chance))


def check_folder(folder: Path) -> HiddenCheck:
    return check_games(sorted(folder.rglob(GAME_FILES)))


def format_check(check: HiddenCheck) -> str:
    return (f"games {check.games}\n"
            f"hidden cards {check.hidden}\n"
            f"named by ids from other games {check.by_other_games}\n"
            f"named by ids from the other side {check.by_other_side}\n"
            f"matched by public state to the other side's row {check.by_public_state}\n"
            f"ids shared with other games by chance {check.by_chance}\n")
