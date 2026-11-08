"""A pool batch: many deck pairs from the manifest, played in chunks.

A chunk is one `batch` of one pair in one JVM, with its own seed, so the games it plays
don't depend on how many workers run or which chunks ran before it. Its files sit under
`chunks/<name>` in the run folder, and all its game records go to the one shared folder
under the pool's batch id. A chunk that ends writes `result.json`, and that file is how a
restart knows what to skip and how the report can cover games from an earlier start.

A train deck only ever meets a train deck, and a held-out deck a held-out deck. See
"Pool batches" in docs/forge-build.md.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from deckpool import manifest as mf
from simrunner.batch import (STATUS_FILE, BatchConfig, BatchResult, SetupError, check_setup, find_jar, run_batch, stamp,
                             write_json)
from simrunner.output import DrawCause
from simrunner.report import format_report, table, tripwire_rows, worker_note
from simrunner.seeds import derive_seed, split_games
from simrunner.worker import any_seat_searches, remove_folder

DEFAULT_CHUNK_GAMES = 50  # a JVM takes 24 to 45 seconds to start, so a chunk has to outlast that by a long way
DEFAULT_HELD_OUT_SHARE = 0.15
PLAN_FILE = "plan.json"
CHUNKS_FOLDER = "chunks"
RESULT_FILE = "result.json"
POLL_S = 0.5  # a wait with no timeout can't see Ctrl-C on Windows

FINISHED = "finished"
FAILED = "failed"
STOPPED = "stopped"


@dataclass(frozen=True)
class Chunk:
    index: int
    split: str  # both decks have it
    seat1: str
    seat2: str
    games: int
    seed: int

    @property
    def name(self) -> str:
        return f"chunk-{self.index:04d}"


@dataclass(frozen=True)
class ChunkResult:
    """What a chunk's last attempt left. Only `finished` lets a restart skip it."""

    index: int
    status: str
    games_finished: int = 0
    wins: int = 0
    draws: dict[str, int] = field(default_factory=dict)  # by DrawCause value
    tripwire_hits: list[int] = field(default_factory=list)  # one per game that printed a count
    stack_traces: int = 0
    records_written: int = 0
    note: str = ""


@dataclass(frozen=True)
class PoolConfig:
    batch: BatchConfig  # the whole pool's settings, with its total games, seed, workers and run folder
    manifest: Path
    chunk_games: int = DEFAULT_CHUNK_GAMES
    held_out_share: float = DEFAULT_HELD_OUT_SHARE


def _chunk_count(games: int, chunk_games: int, decks: int) -> int:
    """About `games / chunk_games`, moved to a whole number of rounds once a round fits, so every deck plays alike."""
    wanted = max(1, round(games / chunk_games))
    if wanted < decks:
        return wanted
    return decks * min(max(1, round(wanted / decks)), games // decks)


def _pairs(decks: list[str], count: int, rng: random.Random) -> list[tuple[str, str]]:
    """Seat decks around a random ring, each deck against the next, as often as `count` needs.

    A whole ring gives every deck one chunk in each seat, which balances games and seats at
    once, and no deck meets itself. A cut-off ring takes every other link first, so it
    reaches as many decks as it can.
    """
    pairs = []
    while len(pairs) < count:
        ring = rng.sample(decks, len(decks))
        links = list(zip(ring, ring[1:] + ring[:1]))
        pairs += links[::2] + links[1::2]
    return pairs[:count]


def plan_pool(splits: dict[str, str], games: int, seed: int, chunk_games: int = DEFAULT_CHUNK_GAMES,
              held_out_share: float = DEFAULT_HELD_OUT_SHARE) -> list[Chunk]:
    """Every chunk of a pool batch, in playing order. `splits` maps a deck id to its manifest split.

    The two splits interleave, so a hard stop leaves both with games.
    """
    held_out_games = round(games * held_out_share)
    planned = []
    for split, share in ((mf.TRAIN, games - held_out_games), (mf.HELD_OUT, held_out_games)):
        if not share:
            continue
        decks = sorted(deck for deck, where in splits.items() if where == split)
        if len(decks) < 2:
            raise SetupError(f"{share} games need two {split} decks, and the manifest has {len(decks)}")
        count = _chunk_count(share, chunk_games, len(decks))
        pairs = _pairs(decks, count, random.Random(f"{seed}:{split}"))
        planned += [((place + 0.5) / count, split, pair, size)
                    for place, (pair, size) in enumerate(zip(pairs, split_games(share, count)))]
    planned.sort(key=lambda chunk: chunk[0])
    return [Chunk(index, split, seat1, seat2, size, derive_seed(seed, index))
            for index, (_, split, (seat1, seat2), size) in enumerate(planned)]


def _plan_fields(config: PoolConfig, chunks: list[Chunk]) -> dict:
    """Everything that decides which games a run folder holds. Workers and the window don't."""
    batch = config.batch
    # Only when set, so that a plan written before evaluators existed still matches on a restart.
    evaluators = [seat.evaluator for seat in batch.seats]
    generations = [seat.generation.fields() if seat.generation else None for seat in batch.seats]
    return {**({"evaluators": evaluators} if any(evaluators) else {}),
            **({"generations": generations} if any(generations) else {}), "seed": batch.batch_seed, "games": batch.games, "chunk_games": config.chunk_games,
            "held_out_share": config.held_out_share, "ai": [seat.ai_profile for seat in batch.seats],
            "sim_modes": [seat.sim_mode for seat in batch.seats], "clock_s": batch.clock_s,
            "search_budget": batch.search_budget, "turn_cap": batch.records.turn_cap,
            "honest": batch.records.honest, "tripwire": batch.tripwire, "chunks": [asdict(chunk) for chunk in chunks]}


def _hold_plan(config: PoolConfig, chunks: list[Chunk]) -> None:
    """Write the plan to a new run folder. In one that has a plan, refuse any other."""
    path = config.batch.run_dir / PLAN_FILE
    fields = _plan_fields(config, chunks)
    if path.is_file():
        held = json.loads(path.read_text(encoding="utf-8"))
        differs = sorted(key for key in fields if held.get(key) != fields[key])
        if differs:
            raise SetupError(f"{config.batch.run_dir} holds a run with a different plan, in: {', '.join(differs)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, fields)


def _chunk_folder(config: PoolConfig, chunk: Chunk) -> Path:
    return config.batch.run_dir / CHUNKS_FOLDER / chunk.name


def _chunk_config(config: PoolConfig, chunk: Chunk, decks: dict[str, Path]) -> BatchConfig:
    base = config.batch
    seats = (replace(base.seats[0], deck=decks[chunk.seat1]), replace(base.seats[1], deck=decks[chunk.seat2]))
    return replace(base, seats=seats, games=chunk.games, batch_seed=chunk.seed, workers=1,
                   run_dir=_chunk_folder(config, chunk), batch_name=base.batch_id,
                   records=replace(base.records, folder=base.games_folder))


def _read_results(config: PoolConfig, chunks: list[Chunk]) -> dict[int, ChunkResult]:
    results = {}
    for chunk in chunks:
        path = _chunk_folder(config, chunk) / RESULT_FILE
        if path.is_file():
            results[chunk.index] = ChunkResult(**json.loads(path.read_text(encoding="utf-8")))
    return results


def _clear_leftovers(folder: Path) -> None:
    """A runner that died leaves worker folders, and a batch won't start beside them."""
    for leftover in folder.glob("worker-*/"):
        remove_folder(leftover)


def _result_of(chunk: Chunk, batch: BatchResult) -> ChunkResult:
    notes = [] if batch.complete else [f"{batch.finished} of {chunk.games} games finished"]
    notes += [note for worker in batch.workers if (note := worker_note(batch, worker))]
    status = FINISHED if batch.clean else STOPPED if batch.stopped else FAILED
    return ChunkResult(chunk.index, status, batch.finished, batch.usable,
                       {cause.value: batch.draws(cause) for cause in DrawCause}, batch.tripwire_hits,
                       sum(worker.stderr_traces for worker in batch.workers), batch.records_written, "; ".join(notes))


def _play_chunk(config: PoolConfig, chunk: Chunk, decks: dict[str, Path], halted: threading.Event) -> ChunkResult | None:
    """Play one chunk and record how it ended. None means it never started. Nothing here ends the run."""
    window = config.batch.window
    if halted.is_set() or (window and window.closed):
        return None
    chunk_config = _chunk_config(config, chunk, decks)
    try:
        batch = run_batch(chunk_config)
        if not batch.workers:
            return None
        (chunk_config.run_dir / "report.txt").write_text(format_report(batch), encoding="utf-8", newline="\n")
        result = _result_of(chunk, batch)
    except Exception as e:
        chunk_config.run_dir.mkdir(parents=True, exist_ok=True)
        result = ChunkResult(chunk.index, FAILED, note=f"{type(e).__name__}: {e}")
    write_json(chunk_config.run_dir / RESULT_FILE, asdict(result))
    return result


@dataclass
class PoolResult:
    config: PoolConfig
    chunks: list[Chunk]
    results: dict[int, ChunkResult]
    splits: dict[str, str]
    wall_s: float  # across every start of this run folder
    skipped: int = 0  # chunks this start found finished

    def _with_status(self, status: str) -> list[Chunk]:
        return [chunk for chunk in self.chunks
                if chunk.index in self.results and self.results[chunk.index].status == status]

    @property
    def finished_chunks(self) -> list[Chunk]:
        return self._with_status(FINISHED)

    @property
    def failed(self) -> list[Chunk]:
        return self._with_status(FAILED)

    @property
    def stopped_chunks(self) -> list[Chunk]:
        return self._with_status(STOPPED)

    @property
    def not_started(self) -> list[Chunk]:
        return [chunk for chunk in self.chunks if chunk.index not in self.results]

    @property
    def attempted(self) -> int:
        return sum(chunk.games for chunk in self.chunks)

    @property
    def finished(self) -> int:
        return sum(result.games_finished for result in self.results.values())

    @property
    def not_finished(self) -> int:
        return self.attempted - self.finished

    @property
    def complete(self) -> bool:
        return len(self.finished_chunks) == len(self.chunks)

    @property
    def stopped(self) -> bool:
        """True when the hard stop is why the run ended short."""
        window = self.config.batch.window
        return not self.complete and bool(window) and window.closed and bool(self.stopped_chunks or self.not_started)

    @property
    def wins(self) -> int:
        return sum(result.wins for result in self.results.values())

    def draws(self, cause: DrawCause) -> int:
        return sum(result.draws.get(cause.value, 0) for result in self.results.values())

    def games_in(self, split: str) -> int:
        return sum(self.results[chunk.index].games_finished for chunk in self.chunks
                   if chunk.split == split and chunk.index in self.results)

    def deck_games(self, split: str | None = None) -> list[int]:
        """Finished games per deck, with a deck that played none as 0."""
        games = Counter({deck: 0 for deck, where in self.splits.items() if split in (None, where)})
        for chunk in self.chunks:
            if chunk.index in self.results and split in (None, chunk.split):
                games[chunk.seat1] += self.results[chunk.index].games_finished
                games[chunk.seat2] += self.results[chunk.index].games_finished
        return list(games.values())

    @property
    def stack_traces(self) -> int:
        return sum(result.stack_traces for result in self.results.values())

    @property
    def records_written(self) -> int:
        return sum(result.records_written for result in self.results.values())

    @property
    def games_per_hour(self) -> float:
        return self.finished / self.wall_s * 3600 if self.wall_s else 0.0

    @property
    def tripwire_on(self) -> bool:
        return self.config.batch.tripwire

    @property
    def tripwire_hits(self) -> list[int]:
        return [hits for result in self.results.values() for hits in result.tripwire_hits]

    @property
    def tripwire_clean(self) -> bool:
        """True when every finished game printed a count, and every count is 0."""
        return (self.tripwire_on and self.finished > 0 and len(self.tripwire_hits) == self.finished
                and not any(self.tripwire_hits))

    @property
    def search_unchecked(self) -> bool:
        return self.tripwire_on and any_seat_searches(self.config.batch.seats)


def _write_status(pool: PoolResult, status: str) -> None:
    """The same fields as a batch's `batch.json`, plus the chunks and the wall time a restart adds to."""
    batch = pool.config.batch
    write_json(batch.run_dir / STATUS_FILE, {
        "batch": batch.run_dir.name,
        "status": status,
        "stopped_at_hard_stop": pool.stopped,
        "window_start": stamp(batch.window.start) if batch.window else None,
        "window_stop": stamp(batch.window.stop) if batch.window else None,
        "games_attempted": pool.attempted,
        "games_finished": pool.finished,
        "games_not_finished": pool.not_finished,
        "chunks": len(pool.chunks),
        "chunks_finished": len(pool.finished_chunks),
        "chunks_failed": len(pool.failed),
        "wall_s": round(pool.wall_s, 1),
    })


def _earlier_wall_s(config: PoolConfig) -> float:
    path = config.batch.run_dir / STATUS_FILE
    return json.loads(path.read_text(encoding="utf-8")).get("wall_s", 0.0) if path.is_file() else 0.0


def run_pool(config: PoolConfig) -> PoolResult:
    """Play every chunk a run folder doesn't have yet. A failed chunk stays in the report and the run goes on."""
    batch = config.batch
    try:
        manifest = mf.load(config.manifest)
        splits = {deck.id: deck.split for deck in manifest.decks}
        chunks = plan_pool(splits, batch.games, batch.batch_seed, config.chunk_games, config.held_out_share)
        decks = {deck: mf.deck_path(manifest, deck, mf.root_of(config.manifest))
                 for chunk in chunks for deck in (chunk.seat1, chunk.seat2)}
    except (OSError, mf.ManifestError) as e:
        raise SetupError(str(e))
    if batch.workers < 1:
        raise SetupError("workers must be at least 1")
    _hold_plan(config, chunks)
    results = _read_results(config, chunks)
    waiting = [chunk for chunk in chunks if chunk.index not in results or results[chunk.index].status != FINISHED]
    for chunk in waiting:
        _clear_leftovers(_chunk_folder(config, chunk))
    if waiting:
        check_setup(_chunk_config(config, waiting[0], decks))
        find_jar(batch.forge_root)
    if batch.window:
        batch.window.wait_for_start()

    earlier = _earlier_wall_s(config)
    began = time.monotonic()
    pool = PoolResult(config, chunks, results, splits, earlier, len(chunks) - len(waiting))
    _write_status(pool, "running")
    halted = threading.Event()
    with ThreadPoolExecutor(max_workers=batch.workers) as executor:
        playing = {executor.submit(_play_chunk, config, chunk, decks, halted) for chunk in waiting}
        while playing:
            try:
                ended, playing = wait(playing, timeout=POLL_S, return_when=FIRST_COMPLETED)
            except KeyboardInterrupt:
                halted.set()  # the JVMs took the same Ctrl-C, so what is running ends soon, and nothing new starts
                continue
            for future in ended:
                if result := future.result():
                    results[result.index] = result
            pool.wall_s = earlier + time.monotonic() - began
            if ended:
                _write_status(pool, "running")
    pool.wall_s = earlier + time.monotonic() - began
    _write_status(pool, "complete" if pool.complete else "incomplete")
    return pool


def _spread(games: list[int]) -> str:
    return f"min {min(games)}, max {max(games)}" if games else "no decks"


def _status(pool: PoolResult) -> str:
    if pool.complete:
        return "complete"
    return "incomplete, stopped at the hard stop" if pool.stopped else "incomplete"


def format_pool_report(pool: PoolResult) -> str:
    batch = pool.config.batch
    rows = [
        ("status", _status(pool)),
        ("window", f"{batch.window.start} to {batch.window.stop}" if batch.window else "none"),
        ("batch id", batch.batch_id),
        ("batch seed", str(batch.batch_seed)),
        ("workers", str(batch.workers)),
        ("chunks", str(len(pool.chunks))),
        ("  finished", str(len(pool.finished_chunks))),
        ("  skipped on this start", str(pool.skipped)),
        ("  failed", str(len(pool.failed))),
        ("  cut off by the stop", str(len(pool.stopped_chunks))),
        ("  not started", str(len(pool.not_started))),
        ("games attempted", str(pool.attempted)),
        ("games finished", str(pool.finished)),
        ("games not finished", str(pool.not_finished)),
        ("wins", str(pool.wins)),
        ("draws", str(pool.finished - pool.wins)),
    ]
    rows += [(f"  by {cause.value}", str(pool.draws(cause))) for cause in DrawCause]
    rows += [
        ("train deck games", str(pool.games_in(mf.TRAIN))),
        ("held-out deck games", str(pool.games_in(mf.HELD_OUT))),
        ("games per deck", _spread(pool.deck_games())),
        ("games per train deck", _spread(pool.deck_games(mf.TRAIN))),
        ("games per held-out deck", _spread(pool.deck_games(mf.HELD_OUT))),
        ("game records written", f"{pool.records_written} of {pool.finished} games"),
        *tripwire_rows(pool),
        ("stack traces on stderr", str(pool.stack_traces)),
        ("wall time", f"{pool.wall_s:.1f} s"),
        ("games per hour", f"{pool.games_per_hour:.0f}"),
    ]
    text = table(rows)
    unfinished = pool.failed + pool.stopped_chunks
    if unfinished:
        lines = [("chunk", "status", "split", "seat 1", "seat 2", "note")]
        lines += [(chunk.name, pool.results[chunk.index].status, chunk.split, chunk.seat1, chunk.seat2,
                   pool.results[chunk.index].note) for chunk in sorted(unfinished, key=lambda chunk: chunk.index)]
        text += "\n" + table(lines)
    return text
