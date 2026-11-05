"""A batch: plan the workers, run them side by side, collect the results."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from simrunner.output import DrawCause, GameResult
from simrunner.seeds import derive_seed, split_games
from simrunner.worker import (JVM_PROPERTIES, Seat, WorkerPlan, WorkerResult, kill_tree,
                              prepare_folder, remove_folder, requested_sim_modes, resolve_search_budget,
                              run_worker, sim_args)

JAR_PATTERN = "forge-gui-desktop-*-jar-with-dependencies.jar"


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
        reported = [game.ai_limits for game in self.games if game.ai_limits]
        return sum(getattr(limits, field) for limits in reported) if reported else None

    @property
    def deadline_expiries(self) -> int | None:
        return self._limit_total("deadline_expiries")

    @property
    def budget_stops(self) -> int | None:
        return self._limit_total("budget_stops")

    @property
    def digest(self) -> str:
        """A fingerprint of who won what, leaving out timing. Same games, same digest.

        Forge's random draw count goes in when it prints one. Two games can share a winner
        and a final turn and still have played differently, but they can't share the draws.
        """
        rows = []
        for worker in self.workers:
            for game in worker.games:
                row = f"{worker.plan.index}:{game.number}:{game.winner_seat}:{game.draw_cause}:{game.turn}"
                rows.append(f"{row}:{game.ai_limits.random_draws}" if game.ai_limits else row)
        return hashlib.sha256("\n".join(rows).encode("ascii")).hexdigest()[:12]

    def played_requested_modes(self, worker: WorkerResult) -> bool:
        """Forge skips flags it doesn't know, so only its own header proves the modes took effect."""
        return worker.sim_modes == requested_sim_modes(self.config.seats)

    def applied_search_budget(self, worker: WorkerResult) -> bool:
        """Forge skips flags it doesn't know, so a jar without `-budget` would play on the wall clock."""
        return not self.search_budget or all(game.ai_limits for game in worker.games)

    @property
    def clean(self) -> bool:
        """True when every game was played, in the modes asked for, and every worker stayed in its own profile."""
        return self.not_finished == 0 and all(worker.profile_used and self.played_requested_modes(worker)
                                              and self.applied_search_budget(worker) for worker in self.workers)


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
    if config.forge_root.resolve() in config.run_dir.resolve().parents:
        # `mvn clean` could follow a leftover link into forge-gui/res.
        raise SetupError(f"the run folder must be outside the Forge clone: {config.run_dir}")
    if any(config.run_dir.glob("worker-*/")):
        raise SetupError(f"the run folder already holds worker folders: {config.run_dir}")


def run_batch(config: BatchConfig) -> BatchResult:
    _check(config)
    jar = find_jar(config.forge_root)
    forge_gui = config.forge_root.resolve() / "forge-gui"
    config.run_dir.mkdir(parents=True, exist_ok=True)

    folders = []
    jobs = []
    started = []
    pool = None
    try:
        for plan in plan_workers(config.games, config.batch_seed, config.workers):
            folder = config.run_dir / f"worker-{plan.index:02d}"
            folders.append(folder)
            deck_dir = prepare_folder(folder, forge_gui, config.seats)
            command = [*config.java, f"-Xmx{config.heap}", *JVM_PROPERTIES, "-jar", str(jar),
                       *sim_args(config.seats, deck_dir, plan.games, plan.seed, config.clock_s,
                                 resolve_search_budget(config.seats, config.search_budget))]
            jobs.append((plan, command, folder, config.run_dir / folder.name, config.clock_s, started))

        began = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=len(jobs))
        workers = [future.result() for future in [pool.submit(run_worker, *job) for job in jobs]]
        return BatchResult(config, workers, time.monotonic() - began)
    finally:
        for process in started:
            if process.poll() is None:
                kill_tree(process)
        if pool:
            pool.shutdown()
        if not config.keep_profiles:
            for folder in folders:
                remove_folder(folder)
