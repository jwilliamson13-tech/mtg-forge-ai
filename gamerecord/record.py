"""Read one game file and check it against the contract in `docs/game-record.md`.

The shapes below are that document in code. A field the document doesn't name is
drift, so it fails the same way a missing field does. Plain dicts come back, because
a long game holds hundreds of thousands of cards and the converter wants dicts anyway.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

SCHEMA_VERSION = 2
# Version 1 files still load. Their snapshots lack the version 2 scoreboard fields, and the loader fills them in
# as `V1_DEFAULTS`: version 1 recorded no such state, so these say "not recorded" as 0 and false.
SCHEMA_VERSIONS = (1, 2)

END_REASONS = ("win", "draw_clock", "draw_turn_cap", "draw_rules", "exception")
SIM_MODES = ("heuristic", "hybrid", "full")
STACK_KINDS = ("spell", "ability")
# `CardStateName` in Forge, minus FaceDown, which `face_down` says instead.
FACES = ("Original", "Flipped", "Backside", "Meld", "LeftSplit", "RightSplit", "Secondary", "PreparedSpell",
         "EmptyRoom", "SpecializeW", "SpecializeU", "SpecializeB", "SpecializeR", "SpecializeG")

# Metadata two runs of the same games can differ on: the run folder's name, and what was set up or watched around the play.
NOT_PLAY = ("batch_id", "honest_play", "tripwire")

# Java's `int` and `long`. Parquet columns use the same widths.
INT_MAX = 2**31 - 1
LONG_MAX = 2**63 - 1

Check = Callable[[object, str], None]


class GameRecordError(Exception):
    """The file is not a usable game record."""


class ExceptionGameError(GameRecordError):
    """A well-formed file that records an exception game. The hook should never write one."""


def _integer(low: int = -INT_MAX - 1, high: int = INT_MAX) -> Check:
    def check(value: object, where: str) -> None:
        if type(value) is not int:  # a bool is an int to isinstance
            raise GameRecordError(f"{where}: expected an integer, got {value!r}")
        if not low <= value <= high:
            raise GameRecordError(f"{where}: {value} is outside {low} to {high}")
    return check


def _boolean(value: object, where: str) -> None:
    if type(value) is not bool:
        raise GameRecordError(f"{where}: expected true or false, got {value!r}")


def _text(value: object, where: str) -> None:
    if type(value) is not str or not value:
        raise GameRecordError(f"{where}: expected non-empty text, got {value!r}")


def _matching(pattern: str) -> Check:
    compiled = re.compile(pattern)

    def check(value: object, where: str) -> None:
        if type(value) is not str or not compiled.fullmatch(value):
            raise GameRecordError(f"{where}: expected text matching {pattern}, got {value!r}")
    return check


def _one_of(*allowed: object) -> Check:
    def check(value: object, where: str) -> None:
        if type(value) not in (int, str) or value not in allowed:
            raise GameRecordError(f"{where}: expected one of {allowed}, got {value!r}")
    return check


def _nothing(value: object, where: str) -> None:
    if value is not None:
        raise GameRecordError(f"{where}: expected null, got {value!r}")


def _nullable(inner: Check) -> Check:
    def check(value: object, where: str) -> None:
        if value is not None:
            inner(value, where)
    return check


def _list_of(inner: Check) -> Check:
    def check(value: object, where: str) -> None:
        if type(value) is not list:
            raise GameRecordError(f"{where}: expected a list, got {value!r}")
        for index, item in enumerate(value):
            inner(item, f"{where}[{index}]")
    return check


def _counts(value: object, where: str) -> None:
    if type(value) is not dict:
        raise GameRecordError(f"{where}: expected an object, got {value!r}")
    for key, count in value.items():
        _text(key, where)
        _integer(1)(count, f"{where}.{key}")


@dataclass(frozen=True)
class Shape:
    """A JSON object with exactly these fields."""

    fields: dict[str, Check]

    def __call__(self, value: object, where: str) -> None:
        if type(value) is not dict:
            raise GameRecordError(f"{where}: expected an object, got {value!r}")
        missing = self.fields.keys() - value.keys()
        if missing:
            raise GameRecordError(f"{where}: missing {', '.join(sorted(missing))}")
        unknown = value.keys() - self.fields.keys()
        if unknown:
            raise GameRecordError(f"{where}: unknown field {', '.join(sorted(unknown))}")
        for name, check in self.fields.items():
            check(value[name], f"{where}.{name}")


SEAT_NUMBER = _one_of(1, 2)
COUNT = _integer(0)

SEAT = Shape({
    "seat": SEAT_NUMBER,
    "deck": _text,
    "ai_profile": _text,
    "sim_mode": _one_of(*SIM_MODES),
    "search_budget": COUNT,
})
TRIPWIRE = Shape({
    "enabled": _boolean,
    "hits": COUNT,
    "signatures": _list_of(_text),
})
METADATA = Shape({
    "schema_version": _integer(),
    "batch_id": _matching(r"[A-Za-z0-9_-]+"),
    "seed": _integer(0, LONG_MAX),
    "game_number": _integer(1),
    "seats": _list_of(SEAT),
    "winner": _nullable(SEAT_NUMBER),
    "final_turn": _integer(1),
    "end_reason": _one_of(*END_REASONS),
    "honest_play": _boolean,
    "tripwire": TRIPWIRE,
})
SCOREBOARD_V1 = Shape({
    "me_life": _integer(),
    "opp_life": _integer(),
    "me_poison": COUNT,
    "opp_poison": COUNT,
    "me_library": COUNT,
    "opp_library": COUNT,
    "opp_hand": COUNT,
    "turn": _integer(1),
    "me_active": _boolean,
    "me_on_play": _boolean,
})
SPEED = _integer(0, 4)
SCOREBOARD = Shape({
    **SCOREBOARD_V1.fields,
    "me_initiative": _boolean,
    "opp_initiative": _boolean,
    "me_monarch": _boolean,
    "opp_monarch": _boolean,
    "me_speed": SPEED,
    "opp_speed": SPEED,
})
V1_DEFAULTS = {"me_initiative": False, "opp_initiative": False, "me_monarch": False, "opp_monarch": False,
               "me_speed": 0, "opp_speed": 0}
CARD = Shape({
    "id": _integer(),
    "name": _nullable(_text),
    "face": _nullable(_one_of(*FACES)),
    "power": _nullable(_integer()),
    "toughness": _nullable(_integer()),
    "tapped": _boolean,
    "counters": _counts,
    "summoning_sick": _boolean,
    "attached_to": _nullable(_integer()),
    "attached_to_seat": _nullable(SEAT_NUMBER),
    "controller": SEAT_NUMBER,
    "face_down": _boolean,
    "damage": COUNT,
    "attacking_seat": _nullable(SEAT_NUMBER),
    "attacking_card": _nullable(_integer()),
    "blocking": _list_of(_integer()),
    "blocked_by": _list_of(_integer()),
})
# A logged card has no id, or the log would tie it to the face-down card it became.
LOGGED_CARD = Shape({**CARD.fields, "id": _nothing})
TARGET = Shape({
    "card": _nullable(_integer()),
    "seat": _nullable(SEAT_NUMBER),
})
STACK_ITEM = Shape({
    "id": _integer(),
    "name": _nullable(_text),
    "face": _nullable(_one_of(*FACES)),
    "face_down": _boolean,
    "controller": SEAT_NUMBER,
    "kind": _one_of(*STACK_KINDS),
    "targets": _list_of(TARGET),
})
ZONES = Shape({**{zone: _list_of(CARD) for zone in (
    "battlefield", "me_hand", "me_graveyard", "opp_graveyard", "me_exile", "opp_exile")}, "opp_revealed": _list_of(LOGGED_CARD)})
SNAPSHOT = Shape({
    "side": SEAT_NUMBER,
    "scoreboard": SCOREBOARD,
    "zones": ZONES,
    "stack": _list_of(STACK_ITEM),
})
SNAPSHOT_V1 = Shape({**SNAPSHOT.fields, "scoreboard": SCOREBOARD_V1})
SNAPSHOT_BY_VERSION = {1: SNAPSHOT_V1, 2: SNAPSHOT}


@dataclass(frozen=True)
class GameRecord:
    metadata: dict
    snapshots: list[dict]

    @property
    def game_id(self) -> str:
        """Unique across batches. A seed belongs to a worker, so it needs the game number too."""
        return f"{self.metadata['batch_id']}-{self.metadata['seed']}-{self.metadata['game_number']}"

    @property
    def schema_version(self) -> int:
        """The version the file was written in. A version 1 file's snapshots have `V1_DEFAULTS` filled in."""
        return self.metadata["schema_version"]

    @property
    def honest_play(self) -> bool:
        """True when the AI kept to what a person in its seat could know. Without it, the play used peeks."""
        return self.metadata["honest_play"]


def _check_tripwire(summary: dict, where: str) -> None:
    hits, signatures = summary["hits"], summary["signatures"]
    if not summary["enabled"] and (hits or signatures):
        raise GameRecordError(f"{where}: a tripwire that was off can't report hits")
    if len(set(signatures)) != len(signatures):
        raise GameRecordError(f"{where}.signatures: signatures must be unique")
    if hits < len(signatures) or (hits and not signatures):
        raise GameRecordError(f"{where}: {hits} hits can't give {len(signatures)} unique signatures")


def _check_metadata(metadata: object) -> None:
    where = "line 1"
    if type(metadata) is not dict:
        raise GameRecordError(f"{where}: expected the metadata object, got {metadata!r}")
    version = metadata.get("schema_version")
    if type(version) is not int or version not in SCHEMA_VERSIONS:
        raise GameRecordError(f"{where}.schema_version: expected one of {SCHEMA_VERSIONS}, got {version!r}")
    METADATA(metadata, where)
    reason, winner = metadata["end_reason"], metadata["winner"]
    if reason == "exception":
        raise ExceptionGameError(f"{where}.end_reason: an exception game has no usable result")
    if (reason == "win") != (winner is not None):
        raise GameRecordError(f"{where}.winner: end reason {reason} can't have winner {winner!r}")
    if [seat["seat"] for seat in metadata["seats"]] != [1, 2]:
        raise GameRecordError(f"{where}.seats: expected seat 1, then seat 2")
    _check_tripwire(metadata["tripwire"], f"{where}.tripwire")


def _check_identity(item: dict, where: str) -> None:
    """A card and a stack item hide their name the same way."""
    if (item["name"] is None) != (item["face"] is None):
        raise GameRecordError(f"{where}.name: name and face are both there or both null")
    if item["name"] is None and not item["face_down"]:
        raise GameRecordError(f"{where}.face_down: only a face-down card can hide its name")


def _check_on_battlefield(ids: Iterable[int | None], field: str, where: str, battlefield_ids: set[int]) -> None:
    for card_id in ids:
        if card_id is not None and card_id not in battlefield_ids:
            raise GameRecordError(f"{where}.{field}: no card {card_id} on the battlefield")


def _check_card(card: dict, where: str, battlefield_ids: set[int]) -> None:
    _check_identity(card, where)
    if (card["power"] is None) != (card["toughness"] is None):
        raise GameRecordError(f"{where}.power: power and toughness are both there or both null")
    host = card["attached_to"]
    if host is not None and card["attached_to_seat"] is not None:
        raise GameRecordError(f"{where}.attached_to: a card can't attach to a card and a seat")
    _check_on_battlefield([host], "attached_to", where, battlefield_ids)
    if card["attacking_seat"] is not None and card["attacking_card"] is not None:
        raise GameRecordError(f"{where}.attacking_card: a card can't be attacking a seat and a card")
    _check_on_battlefield([card["attacking_card"]], "attacking_card", where, battlefield_ids)
    _check_on_battlefield(card["blocking"], "blocking", where, battlefield_ids)
    _check_on_battlefield(card["blocked_by"], "blocked_by", where, battlefield_ids)


def _check_stack_item(item: dict, where: str) -> None:
    _check_identity(item, where)
    for index, target in enumerate(item["targets"]):
        if (target["card"] is None) == (target["seat"] is None):
            raise GameRecordError(f"{where}.targets[{index}]: a target is a card or a seat")


def _check_snapshot(snapshot: object, where: str, final_turn: int, version: int = SCHEMA_VERSION) -> None:
    SNAPSHOT_BY_VERSION[version](snapshot, where)
    board = snapshot["scoreboard"]
    turn = board["turn"]
    if turn > final_turn:
        raise GameRecordError(f"{where}.scoreboard.turn: {turn} is past final_turn {final_turn}")
    for marker in ("initiative", "monarch"):
        if board.get(f"me_{marker}") and board.get(f"opp_{marker}"):
            raise GameRecordError(f"{where}.scoreboard: only one player can have the {marker}")
    zones = snapshot["zones"]
    battlefield_ids = {card["id"] for card in zones["battlefield"]}
    for zone, cards in zones.items():
        for index, card in enumerate(cards):
            _check_card(card, f"{where}.zones.{zone}[{index}]", battlefield_ids)
    for index, item in enumerate(snapshot["stack"]):
        _check_stack_item(item, f"{where}.stack[{index}]")


def _read(lines: Iterable[str]) -> GameRecord:
    lines = iter(lines)
    first = next(lines, None)
    if first is None:
        raise GameRecordError("the file is empty")
    metadata = json.loads(first)
    _check_metadata(metadata)
    snapshots = []
    version = metadata["schema_version"]
    for number, line in enumerate(lines, start=2):
        snapshot = json.loads(line)
        _check_snapshot(snapshot, f"line {number}", metadata["final_turn"], version)
        if version == 1:
            snapshot["scoreboard"].update(V1_DEFAULTS)
        snapshots.append(snapshot)
    if not snapshots:
        raise GameRecordError("the file has no snapshots")
    return GameRecord(metadata, snapshots)


def read_metadata(path: Path) -> dict:
    """Return a file's metadata line as written, unchecked, or raise `GameRecordError`.

    This is for looking over a whole batch, where reading every snapshot of every file would be slow.
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as lines:
            metadata = json.loads(next(lines))
    except (OSError, EOFError, zlib.error, ValueError, StopIteration) as e:
        raise GameRecordError(f"can't read the file: {e!r}") from e
    if type(metadata) is not dict:
        raise GameRecordError(f"line 1: expected the metadata object, got {metadata!r}")
    return metadata


def game_digest(path: Path) -> tuple[int, str]:
    """Return a file's game number and a hash of the game in it, leaving out `NOT_PLAY`.

    Every snapshot goes in, so two files with one hash hold the same game, card by card. The
    file is hashed as written and not validated, because a replay check shouldn't need a clean bill first.
    """
    digest = hashlib.sha256()
    try:
        with gzip.open(path, "rb") as lines:
            metadata = json.loads(next(lines))
            number = metadata["game_number"]
            played = {name: value for name, value in metadata.items() if name not in NOT_PLAY}
            digest.update(json.dumps(played, sort_keys=True).encode("utf-8"))
            for line in lines:
                digest.update(line)
    except (OSError, EOFError, zlib.error, ValueError, KeyError, TypeError, StopIteration) as e:
        raise GameRecordError(f"can't read the file: {e!r}") from e
    return number, digest.hexdigest()


def load_game(path: Path) -> GameRecord:
    """Return the validated game in `path`, or raise `GameRecordError`.

    A worker killed mid-write leaves a truncated gzip, which raises `EOFError`, so
    reading failures of every kind become `GameRecordError` here. One bad file then
    costs one game. `ValueError` covers bad JSON and bad UTF-8.
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as lines:
            return _read(lines)
    except (OSError, EOFError, zlib.error, ValueError) as e:
        raise GameRecordError(f"can't read the file: {e}") from e
