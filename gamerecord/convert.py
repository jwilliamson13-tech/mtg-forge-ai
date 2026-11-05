"""Turn a folder of game files into two Parquet tables, partitioned by batch.

Only wins are kept: a draw has no label to train on. `docs/game-record.md` lists the
columns. Rows go out one game at a time, because a night of games would not fit in
memory as Python objects.

The schemas are written out, not inferred. A batch with no attachment in it would
otherwise get a `null`-typed `attached_to`, and its file would not read alongside
the others.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from gamerecord.record import SCOREBOARD, ZONES, ExceptionGameError, GameRecord, GameRecordError, load_game

GAME_FILES = "*.jsonl.gz"
PART_FILE = "part-0.parquet"
# A leading dot keeps a leftover out of any pyarrow dataset read.
TEMP_FILE = f".{PART_FILE}.tmp"

SEAT_COLUMNS = [(f"seat{seat}_{name}", kind) for seat in (1, 2) for name, kind in (
    ("deck", pa.string()), ("ai_profile", pa.string()), ("sim_mode", pa.string()), ("search_budget", pa.int32()))]
SCOREBOARD_BOOLEANS = ("me_active", "me_on_play")

SNAPSHOT_SCHEMA = pa.schema([
    ("game_id", pa.string()),
    ("snapshot_index", pa.int32()),
    ("seed", pa.int64()),
    ("game_number", pa.int32()),
    ("final_turn", pa.int32()),
    *SEAT_COLUMNS,
    ("side", pa.int8()),
    ("won", pa.bool_()),
    *((name, pa.bool_() if name in SCOREBOARD_BOOLEANS else pa.int32()) for name in SCOREBOARD.fields),
])
CARD_SCHEMA = pa.schema([
    ("game_id", pa.string()),
    ("snapshot_index", pa.int32()),
    ("zone", pa.string()),
    ("id", pa.int32()),
    ("name", pa.string()),
    ("face", pa.string()),
    ("power", pa.int32()),
    ("toughness", pa.int32()),
    ("tapped", pa.bool_()),
    ("counters", pa.map_(pa.string(), pa.int32())),
    ("summoning_sick", pa.bool_()),
    ("attached_to", pa.int32()),
    ("attached_to_seat", pa.int8()),
    ("controller", pa.int8()),
    ("face_down", pa.bool_()),
])


@dataclass
class ConversionReport:
    kept: Counter = field(default_factory=Counter)  # by end reason
    dropped: Counter = field(default_factory=Counter)  # by end reason
    rejected: list[tuple[Path, str]] = field(default_factory=list)  # file and why
    snapshot_rows: int = 0
    card_rows: int = 0


class _PartitionedTable:
    """One Parquet table with one file per batch.

    A batch's file takes its final name only on a clean exit, so converting a folder
    again replaces that batch instead of adding to it, and a crash leaves the last
    good file alone.
    """

    def __init__(self, root: Path, schema: pa.Schema):
        self.root = root
        self.schema = schema
        self.writers: dict[str, pq.ParquetWriter] = {}

    def folder(self, batch_id: str) -> Path:
        return self.root / f"batch_id={batch_id}"

    def write(self, batch_id: str, rows: list[dict]) -> None:
        if batch_id not in self.writers:
            self.folder(batch_id).mkdir(parents=True, exist_ok=True)
            self.writers[batch_id] = pq.ParquetWriter(self.folder(batch_id) / TEMP_FILE, self.schema)
        self.writers[batch_id].write_table(pa.Table.from_pylist(rows, schema=self.schema))

    def __enter__(self) -> _PartitionedTable:
        return self

    def __exit__(self, failure: type | None, *_: object) -> None:
        for batch_id, writer in self.writers.items():
            writer.close()
            temp = self.folder(batch_id) / TEMP_FILE
            if failure:
                temp.unlink()
            else:
                os.replace(temp, self.folder(batch_id) / PART_FILE)


def snapshot_rows(game: GameRecord) -> list[dict]:
    metadata = game.metadata
    shared = {"game_id": game.game_id, "seed": metadata["seed"], "game_number": metadata["game_number"],
              "final_turn": metadata["final_turn"]}
    for seat in metadata["seats"]:
        for name in ("deck", "ai_profile", "sim_mode", "search_budget"):
            shared[f"seat{seat['seat']}_{name}"] = seat[name]
    return [{**shared, "snapshot_index": index, "side": snapshot["side"], "won": snapshot["side"] == metadata["winner"],
             **snapshot["scoreboard"]} for index, snapshot in enumerate(game.snapshots)]


def card_rows(game: GameRecord) -> list[dict]:
    rows = []
    for index, snapshot in enumerate(game.snapshots):
        for zone in ZONES.fields:
            for card in snapshot["zones"][zone]:
                rows.append({**card, "game_id": game.game_id, "snapshot_index": index, "zone": zone,
                             "counters": list(card["counters"].items())})
    return rows


def game_files(folder: Path) -> list[Path]:
    return sorted(folder.rglob(GAME_FILES))


def convert_folder(folder: Path, out: Path) -> ConversionReport:
    """Write `out/snapshots` and `out/cards` from every game file under `folder`.

    An exception game counts as dropped, by its end reason. Any other file the loader
    refuses is listed in `rejected`, and the rest of the folder still converts.
    """
    report = ConversionReport()
    with (_PartitionedTable(out / "snapshots", SNAPSHOT_SCHEMA) as snapshots,
          _PartitionedTable(out / "cards", CARD_SCHEMA) as cards):
        for path in game_files(folder):
            try:
                game = load_game(path)
            except ExceptionGameError:
                report.dropped["exception"] += 1
                continue
            except GameRecordError as e:
                report.rejected.append((path, str(e)))
                continue
            reason = game.metadata["end_reason"]
            if reason != "win":
                report.dropped[reason] += 1
                continue
            report.kept[reason] += 1
            batch_id = game.metadata["batch_id"]
            game_snapshots, game_cards = snapshot_rows(game), card_rows(game)
            snapshots.write(batch_id, game_snapshots)
            cards.write(batch_id, game_cards)
            report.snapshot_rows += len(game_snapshots)
            report.card_rows += len(game_cards)
    return report


def format_report(report: ConversionReport) -> str:
    lines = [f"kept {report.kept.total()}"]
    lines += [f"  {reason}: {count}" for reason, count in sorted(report.kept.items())]
    lines.append(f"dropped {report.dropped.total()}")
    lines += [f"  {reason}: {count}" for reason, count in sorted(report.dropped.items())]
    lines.append(f"rejected {len(report.rejected)}")
    lines += [f"  {path}: {why}" for path, why in report.rejected]
    lines += [f"snapshot rows {report.snapshot_rows}", f"card rows {report.card_rows}"]
    return "\n".join(lines) + "\n"
