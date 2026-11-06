"""A batch: plan the workers, run them side by side, collect the results."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gamerecord import GameRecordError, game_digest
from simrunner.output import DrawCause, GameResult
from simrunner.seeds import derive_seed, split_games
from simrunner.window import Window
from simrunner.worker import (JVM_PROPERTIES, RecordPlan, Seat, WorkerPlan, WorkerResult, any_seat_searches, kill_tree,
                              prepare_folder, remove_folder, requested_sim_modes, resolve_search_budget,
                              run_worker, sim_args)

JAR_PATTERN = "forge-gui-desktop-*-jar-with-dependencies.jar"
STATUS_FILE = "batch.json"
GAMES_FOLDER = "games"  # Forge's game records. See docs/forge-build.md.
# The hook writes the batch id into each record, and the schema limits it. See docs/game-record.md.
BATCH_ID = re.compile(r"[A-Za-z0-9_-]+")


class SetupError(Exception):
    """The batch can't start. Nothing has been launched."""


@dataclass(frozen=True)
class BatchConfig:
    seats: tuple[Seat, Seat]
    games: int
    batch_seed: int
    workers: int
    forge_root: Path
    run_dir: Path
    clock_s: int = 2400
    heap: str = "2048m"
    java: tuple[str, ...] = ("java",)
    keep_profiles: bool = False
    search_budget: int | None = None  # None picks the default for the seats, and 0 leaves it off
    window: Window | None = None  # None runs the batch now, with no hard stop
    records: bool = False  # have Forge write a game record per game
    records_dir: Path | None = None  # None keeps them in the run folder's games folder
    turn_cap: int | None = None  # None takes Forge's default
    tripwire: bool = True  # catch forge-ai reading hidden cards
    honest: bool = True  # with records, keep the AI to what a person in its seat could know

    @property
    def batch_id(self) -> str:
        return self.run_dir.name

    @property
    def games_folder(self) -> Path:
        return self.records_dir or self.run_dir / GAMES_FOLDER


@dataclass
class BatchResult:
    config: BatchConfig
    workers: list[WorkerResult]
    wall_s: float

    @property
    def games(self) -> list[GameResult]:
        return [game for worker in self.workers for game in worker.games]

    @property
    def attempted(self) -> int:
        return self.config.games

    @property
    def finished(self) -> int:
        return len(self.games)

    @property
    def not_finished(self) -> int:
        return self.attempted - self.finished

    @property
    def complete(self) -> bool:
        """True when every game was played. Clean asks for more than this."""
        return self.not_finished == 0

    @property
    def stopped(self) -> bool:
        """True when the hard stop killed a worker, or came before any could launch."""
        return bool(self.config.window) and (not self.workers or any(worker.stopped for worker in self.workers))

    def wins(self, seat: int) -> int:
        return sum(game.winner_seat == seat for game in self.games)

    @property
    def usable(self) -> int:
        """Decisive games. Draws of every kind get dropped before training."""
        return sum(game.is_win for game in self.games)

    def draws(self, cause: DrawCause) -> int:
        return sum(game.draw_cause == cause for game in self.games)

    @property
    def draw_count(self) -> int:
        return self.finished - self.usable

    @property
    def games_per_hour(self) -> float:
        return self.finished / self.wall_s * 3600 if self.wall_s else 0.0

    @property
    def usable_per_hour(self) -> float:
        return self.usable / self.wall_s * 3600 if self.wall_s else 0.0

    @property
    def startup_times(self) -> list[float]:
        return [worker.ready_s for worker in self.workers if worker.ready_s is not None]

    @property
    def steady_games_per_hour(self) -> float:
        """The rate with startup left out: each worker's games over its time playing, summed."""
        rate = 0.0
        for worker in self.workers:
            if worker.games and worker.ready_s is not None and worker.last_result_s > worker.ready_s:
                rate += len(worker.games) / (worker.last_result_s - worker.ready_s)
        return rate * 3600

    @property
    def steady_usable_per_hour(self) -> float:
        return self.steady_games_per_hour * self.usable / self.finished if self.finished else 0.0

    @property
    def search_budget(self) -> int:
        return resolve_search_budget(self.config.seats, self.config.search_budget)

    def _limit_total(self, field: str) -> int | None:
        """The sum over the games Forge reported on. None when it reported on none of them."""
        reported = [getattr(game.ai_limits, field) for game in self.games if game.ai_limits]
        reported = [value for value in reported if value is not None]
        return sum(reported) if reported else None

    @property
    def deadline_expiries(self) -> int | None:
        return self._limit_total("deadline_expiries")

    @property
    def budget_stops(self) -> int | None:
        return self._limit_total("budget_stops")

    @property
    def stops_played(self) -> int | None:
        return self._limit_total("stops_played")

    @property
    def stops_passed(self) -> int | None:
        return self._limit_total("stops_passed")

    @property
    def stops_with_land(self) -> int | None:
        return self._limit_total("stops_with_land")

    @property
    def land_drops_missed(self) -> int | None:
        return self._limit_total("land_drops_missed")

    @property
    def digest(self) -> str:
        """A fingerprint of who won what, leaving out timing.

        Forge's random draw count goes in when it prints one. Two games can share a winner
        and a final turn and still have played differently, but they rarely share the draws.
        It says nothing about what happened on the way, so a claim that two runs played the
        same games goes by `record_digest`.
        """
        rows = []
        for worker in self.workers:
            for game in worker.games:
                row = f"{worker.plan.index}:{game.number}:{game.winner_seat}:{game.draw_cause}:{game.turn}"
                rows.append(f"{row}:{game.ai_limits.random_draws}" if game.ai_limits else row)
        return hashlib.sha256("\n".join(rows).encode("ascii")).hexdigest()[:12]

    @property
    def record_digest(self) -> str | None:
        """A fingerprint of every game file's content. Same digest, the same games card by card.

        It leaves out what differs between two runs of the same games: the run folder's name
        and the tripwire's report. None when the batch wrote no files.
        """
        rows = [f"{worker.plan.index}:{number}:{digest}" for worker in self.workers
                for number, digest in sorted(_file_digest(path) for path in worker.record_files)]
        return hashlib.sha256("\n".join(rows).encode("ascii")).hexdigest()[:12] if rows else None

    @property
    def records_written(self) -> int:
        return sum(len(worker.record_files) for worker in self.workers)

    def wrote_records(self, worker: WorkerResult) -> bool:
        """Forge skips flags it doesn't know, so a jar without `-record` would play on and write nothing.

        A game that threw writes no file, and neither does a clock stop before the first priority pass.
        """
        expected = sum(game.draw_cause not in (DrawCause.CLOCK, DrawCause.EXCEPTION) for game in worker.games)
        return not self.config.records or len(worker.record_files) >= expected

    @property
    def tripwire_on(self) -> bool:
        return self.config.tripwire

    def reported_tripwire(self, worker: WorkerResult) -> bool:
        """Forge skips flags it doesn't know, so a jar without the tripwire would play on and report nothing."""
        return not self.tripwire_on or all(game.tripwire_hits is not None for game in worker.games)

    @property
    def tripwire_hits(self) -> list[int]:
        """Hits per game, for the games that reported any count at all."""
        return [game.tripwire_hits for game in self.games if game.tripwire_hits is not None]

    @property
    def tripwire_clean(self) -> bool:
        """True when every finished game ran the tripwire and it caught nothing. A game that said nothing proves nothing.

        This covers the real game. With a searching seat, `search_unchecked` says what it leaves out.
        """
        return (self.tripwire_on and self.finished > 0 and len(self.tripwire_hits) == self.finished
                and not any(self.tripwire_hits))

    @property
    def search_unchecked(self) -> bool:
        """True when a seat searches: the tripwire watches the real game only, so copies with hidden cards go unseen."""
        return self.tripwire_on and any_seat_searches(self.config.seats)

    def tripwire_signatures(self) -> list[tuple[str, int, int]]:
        """Each signature with its hits and the games it showed up in, most hits first."""
        hits = Counter()
        games = Counter()
        for game in self.games:
            for signature, count in game.tripwire_signatures:
                hits[signature] += count
                games[signature] += 1
        return sorted(((signature, count, games[signature]) for signature, count in hits.items()),
                      key=lambda row: (-row[1], row[0]))

    def played_requested_modes(self, worker: WorkerResult) -> bool:
        """Forge skips flags it doesn't know, so only its own header proves the modes took effect."""
        return worker.sim_modes == requested_sim_modes(self.config.seats)

    def applied_search_budget(self, worker: WorkerResult) -> bool:
        """Forge skips flags it doesn't know, so a jar without `-budget` would play on the wall clock."""
        return not self.search_budget or all(game.ai_limits for game in worker.games)

    @property
    def clean(self) -> bool:
        """True when every game was played, in the modes asked for, and every worker stayed in its own profile."""
        return self.complete and all(worker.profile_used and not worker.left_behind
                                     and self.played_requested_modes(worker)
                                     and self.applied_search_budget(worker)
                                     and self.wrote_records(worker)
                                     and self.reported_tripwire(worker) for worker in self.workers)


def _file_digest(path: Path) -> tuple[int, str]:
    """A file that can't be read still changes the digest, and costs the report nothing else."""
    try:
        return game_digest(path)
    except GameRecordError:
        return 0, f"unreadable {path.name}"


def plan_workers(games: int, batch_seed: int, workers: int) -> list[WorkerPlan]:
    """One plan per worker that has games to play."""
    return [WorkerPlan(index, derive_seed(batch_seed, index), share)
            for index, share in enumerate(split_games(games, workers)) if share]


def find_jar(forge_root: Path) -> Path:
    jars = sorted((forge_root / "forge-gui-desktop" / "target").glob(JAR_PATTERN))
    if len(jars) != 1:
        raise SetupError(f"expected one {JAR_PATTERN} under {forge_root}, found {len(jars)}")
    return jars[0].resolve()


def _check(config: BatchConfig) -> None:
    if not (config.forge_root / "forge-gui" / "res").is_dir():
        raise SetupError(f"no forge-gui/res under {config.forge_root}")
    for seat in config.seats:
        if not seat.deck.is_file():
            raise SetupError(f"not a file: {seat.deck}")
    if config.games < 1 or config.workers < 1:
        raise SetupError("games and workers must both be at least 1")
    if config.search_budget is not None and config.search_budget < 0:
        raise SetupError("the search budget can't be negative")
    if config.turn_cap is not None and (not config.records or config.turn_cap < 1):
        raise SetupError("a turn cap needs records and has to be at least 1")
    if not config.honest and not config.records:
        raise SetupError("honest play only goes on with records, so there is nothing to turn off")
    if config.records and not BATCH_ID.fullmatch(config.batch_id):
        raise SetupError(f"the run folder's name is the batch id, which takes letters, digits, _ and -: {config.batch_id}")
    if config.forge_root.resolve() in config.run_dir.resolve().parents:
        # `mvn clean` could follow a leftover link into forge-gui/res.
        raise SetupError(f"the run folder must be outside the Forge clone: {config.run_dir}")
    if any(config.run_dir.glob("worker-*/")):
        raise SetupError(f"the run folder already holds worker folders: {config.run_dir}")


def _stamp(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None


def _write_status(config: BatchConfig, status: str, batch: BatchResult | None = None) -> None:
    """Record where the batch stands. Only `complete` means every game was played.

    It reads `running` until the batch ends, so a runner that dies leaves no false finish behind.
    """
    finished = batch.finished if batch else 0
    fields = {
        "batch": config.run_dir.name,
        "status": status,
        "stopped_at_hard_stop": batch.stopped if batch else False,
        "window_start": _stamp(config.window.start) if config.window else None,
        "window_stop": _stamp(config.window.stop) if config.window else None,
        "games_attempted": config.games,
        "games_finished": finished,
        "games_not_finished": config.games - finished,
    }
    path = config.run_dir / STATUS_FILE
    fresh = path.with_suffix(".tmp")
    fresh.write_text(json.dumps(fields, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(fresh, path)  # a reader never sees half a file


def run_batch(config: BatchConfig) -> BatchResult:
    _check(config)
    jar = find_jar(config.forge_root)
    forge_gui = config.forge_root.resolve() / "forge-gui"
    (config.run_dir / GAMES_FOLDER).mkdir(parents=True, exist_ok=True)
    config.games_folder.mkdir(parents=True, exist_ok=True)
    if config.window:
        config.window.wait_for_start()
    if config.window and config.window.closed:
        batch = BatchResult(config, [], 0.0)
    else:
        _write_status(config, "running")
        batch = _play(config, jar, forge_gui)
    _write_status(config, "complete" if batch.complete else "incomplete", batch)
    return batch


def _play(config: BatchConfig, jar: Path, forge_gui: Path) -> BatchResult:
    folders = []
    jobs = []
    started = []
    pool = None
    # Each worker runs in its own folder, so Forge needs the full path.
    records = (RecordPlan(config.games_folder.resolve(), config.batch_id, config.turn_cap, config.honest)
               if config.records else None)
    try:
        for plan in plan_workers(config.games, config.batch_seed, config.workers):
            folder = config.run_dir / f"worker-{plan.index:02d}"
            folders.append(folder)
            deck_dir = prepare_folder(folder, forge_gui, config.seats)
            command = [*config.java, f"-Xmx{config.heap}", *JVM_PROPERTIES, "-jar", str(jar),
                       *sim_args(config.seats, deck_dir, plan.games, plan.seed, config.clock_s,
                                 resolve_search_budget(config.seats, config.search_budget), records, config.tripwire)]
            jobs.append((plan, command, folder, config.run_dir / folder.name, config.clock_s, started,
                         config.window.kill_at if config.window else None))

        began = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=len(jobs))
        workers = [future.result() for future in [pool.submit(run_worker, *job) for job in jobs]]
        wall_s = time.monotonic() - began
        if records:
            for worker in workers:
                worker.record_files = sorted(records.folder.glob(f"{records.batch_id}_{worker.plan.seed}_*.jsonl.gz"))
        return BatchResult(config, workers, wall_s)
    finally:
        for process in started:
            if process.poll() is None:
                kill_tree(process)
        if pool:
            pool.shutdown()
        if not config.keep_profiles:
            for folder in folders:
                remove_folder(folder)
