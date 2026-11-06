"""One worker: its throwaway Forge profile, its JVM and what it printed.

Each worker runs in its own folder, so that no two JVMs share a profile and no JVM
can reach the real one in `%APPDATA%/Forge`:

- Forge reads `forge.profile.properties` from its working directory, and nothing
  on the command line can move that file. So the working directory is the worker
  folder, not `forge-gui`, and it holds its own properties file.
- Forge finds `res/` through the same working directory, so the worker folder links
  to the real one. A copy would be 35,000 files per worker.
- `APPDATA` and `LOCALAPPDATA` point inside the worker folder as well. If the
  properties file were ever not read, Forge's fallback would still be throwaway.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from simrunner.output import (AI_TIMEOUT_LINE, HEADER_LINE, GameResult, count_stack_traces,
                              find_fatal, parse_games, printed_sim_modes)

JVM_PROPERTIES = ("-Dfile.encoding=UTF-8", "-Dsentry.enabled=false")
PROFILE_FILE = "forge.profile.properties"
DEFAULT_AI_PROFILE = "Default"
SIM_MODES = ("heuristic", "hybrid", "full")
DEFAULT_SIM_MODE = "heuristic"
DECK_NAMES = ("seat1.dck", "seat2.dck")
# Search steps per AI decision. See "Full-sim replay" in docs/forge-build.md for how this was chosen.
DEFAULT_SEARCH_BUDGET = 100

# Past this, a worker is hung rather than slow, and gets killed. The clock already
# bounds each game, so this is a backstop for a JVM that stops responding.
STARTUP_ALLOWANCE_S = 300
PER_GAME_ALLOWANCE_S = 60
POLL_S = 0.5
# Once a worker has ended, its pipes close as soon as what's left in them is read.
PIPE_DRAIN_S = 10


@dataclass(frozen=True)
class Seat:
    """One side of the table. Per-seat settings for later modes belong here."""

    deck: Path
    ai_profile: str | None = None
    sim_mode: str | None = None


@dataclass(frozen=True)
class RecordPlan:
    """Where a worker's Forge writes game records. A cap of None takes Forge's default."""

    folder: Path
    batch_id: str
    turn_cap: int | None = None
    tripwire: bool = True


@dataclass(frozen=True)
class WorkerPlan:
    index: int
    seed: int
    games: int


@dataclass
class WorkerResult:
    plan: WorkerPlan
    exit_code: int | None = None
    timed_out: bool = False
    stopped: bool = False  # the hard stop killed it
    left_behind: bool = False  # something it started still held its pipes after it ended
    profile_used: bool = False
    ready_s: float | None = None  # seconds from launch until Forge printed its match header
    last_result_s: float | None = None  # seconds from launch until the last Game Result line
    games: list[GameResult] = field(default_factory=list)
    ai_timeouts: int = 0
    stderr_traces: int = 0
    fatal: str | None = None
    sim_modes: tuple[str, ...] = ()  # what Forge's match header says each seat is playing
    records_written: int = 0  # game files with this worker's seed


def requested_sim_modes(seats: tuple[Seat, Seat]) -> tuple[str, ...]:
    """The `-sim` values for these seats. Empty means leave the flag out."""
    if not any(seat.sim_mode for seat in seats):
        return ()
    # `-sim` fills seats in order, so name both.
    return tuple(seat.sim_mode or DEFAULT_SIM_MODE for seat in seats)


def resolve_search_budget(seats: tuple[Seat, Seat], requested: int | None) -> int:
    """The `-budget` for a batch: what was asked for, else the default if any seat searches. 0 leaves it off."""
    if requested is not None:
        return requested
    searches = any((seat.sim_mode or DEFAULT_SIM_MODE) != DEFAULT_SIM_MODE for seat in seats)
    return DEFAULT_SEARCH_BUDGET if searches else 0


def sim_args(seats: tuple[Seat, Seat], deck_dir: Path, games: int, seed: int, clock_s: int,
             search_budget: int = 0, records: RecordPlan | None = None) -> list[str]:
    """Return the `forge sim` arguments for one worker."""
    args = ["sim", "-D", str(deck_dir), "-d", *DECK_NAMES,
            "-n", str(games), "-s", str(seed), "-c", str(clock_s), "-q"]
    if any(seat.ai_profile for seat in seats):
        # `-a` fills seats in order and can't take an empty name, so name both.
        args += ["-a", *(seat.ai_profile or DEFAULT_AI_PROFILE for seat in seats)]
    if modes := requested_sim_modes(seats):
        args += ["-sim", *modes]
    if search_budget:
        args += ["-budget", str(search_budget)]
    if records:
        args += ["-record", str(records.folder), "-batch", records.batch_id]
        if records.turn_cap is not None:
            args += ["-turncap", str(records.turn_cap)]
        if not records.tripwire:
            args += ["-tripwire", "off"]
    return args


def link_folder(link: Path, target: Path) -> None:
    """Make `link` point at `target`. A Windows junction needs no special privilege."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def prepare_folder(folder: Path, forge_gui: Path, seats: tuple[Seat, Seat]) -> Path:
    """Build a worker folder and return its deck folder."""
    folder.mkdir(parents=True)
    link_folder(folder / "res", forge_gui / "res")
    root = folder.resolve().as_posix()  # a .properties file treats a backslash as an escape
    (folder / PROFILE_FILE).write_text(f"userDir={root}/user/\ncacheDir={root}/cache/\n", encoding="utf-8")
    deck_dir = folder / "decks"
    deck_dir.mkdir()
    for seat, name in zip(seats, DECK_NAMES):
        shutil.copyfile(seat.deck, deck_dir / name)
    return deck_dir.resolve()


def remove_folder(folder: Path) -> None:
    """Delete a worker folder, unlinking `res` first so the delete can't reach the real one."""
    link = folder / "res"
    if os.path.lexists(link):
        os.unlink(link)
    shutil.rmtree(folder, ignore_errors=True)


def profile_was_used(folder: Path) -> bool:
    """Forge opens a log in its user folder at startup, so a log there proves it read the profile."""
    return any((folder / "user").glob("forge*.log"))


def worker_env(folder: Path) -> dict[str, str]:
    root = folder.resolve()
    return {**os.environ, "APPDATA": str(root / "appdata"), "LOCALAPPDATA": str(root / "localappdata")}


def kill_tree(process: subprocess.Popen) -> None:
    """Kill a worker and its children.

    On Windows the `java` on PATH can be Oracle's launcher, which starts the real
    JVM as a child. Killing the launcher alone would leave that JVM running.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    process.kill()
    process.wait()


def deadline_s(games: int, clock_s: int) -> int:
    return STARTUP_ALLOWANCE_S + games * (clock_s + PER_GAME_ALLOWANCE_S)


def _pump(stream, log_path: Path, launched: float, lines: list[tuple[float, str]]) -> None:
    """Read one pipe to its end, stamping each line as it arrives."""
    with open(log_path, "w", encoding="utf-8", newline="\n") as log:
        for raw in stream:
            line = raw.rstrip("\n")
            lines.append((time.monotonic() - launched, line))
            log.write(line + "\n")
            log.flush()


def _wait(process: subprocess.Popen, result: WorkerResult, backstop_s: int, kill_at: float | None) -> None:
    """Wait for a worker to end, and kill it at the hard stop or its backstop, whichever comes first.

    The hard stop is a wall clock time, so it is checked against the wall clock in short
    waits. One long wait would run late if the machine slept or the clocks changed.
    """
    give_up = time.monotonic() + backstop_s
    while True:
        try:
            process.wait(timeout=POLL_S)
            return
        except subprocess.TimeoutExpired:
            pass
        if kill_at is not None and time.time() >= kill_at:
            result.stopped = True
        elif time.monotonic() >= give_up:
            result.timed_out = True
        else:
            continue
        kill_tree(process)
        return


def run_worker(plan: WorkerPlan, command: list[str], folder: Path, log_stem: Path, clock_s: int,
               started: list[subprocess.Popen], kill_at: float | None = None) -> WorkerResult:
    """Run one JVM to its end and read the results out of what it printed.

    stdout and stderr stay on separate pipes. On one pipe, the stack trace from a
    stopped game's thread lands in the middle of the `Game Result:` line.
    """
    launched = time.monotonic()
    process = subprocess.Popen(command, cwd=folder, env=worker_env(folder), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")
    started.append(process)
    out_lines: list[tuple[float, str]] = []
    err_lines: list[tuple[float, str]] = []
    out_log = log_stem.with_name(log_stem.name + ".out.log")
    err_log = log_stem.with_name(log_stem.name + ".err.log")
    pumps = [threading.Thread(target=_pump, args=(process.stdout, out_log, launched, out_lines), daemon=True),
             threading.Thread(target=_pump, args=(process.stderr, err_log, launched, err_lines), daemon=True)]
    for pump in pumps:
        pump.start()

    result = WorkerResult(plan)
    _wait(process, result, deadline_s(plan.games, clock_s), kill_at)
    for pump in pumps:
        pump.join(timeout=PIPE_DRAIN_S)
    result.left_behind = any(pump.is_alive() for pump in pumps)

    result.exit_code = process.returncode
    result.profile_used = profile_was_used(folder)
    result.stderr_traces = count_stack_traces(line for _, line in err_lines)
    _read_stdout(result, out_lines)
    return result


def _read_stdout(result: WorkerResult, lines: list[tuple[float, str]]) -> None:
    text = [line for _, line in lines]
    result.games = parse_games(text)
    result.fatal = find_fatal(text)
    result.ai_timeouts = text.count(AI_TIMEOUT_LINE)
    for stamp, line in lines:
        if result.ready_s is None and HEADER_LINE.match(line):
            result.ready_s = stamp
            result.sim_modes = printed_sim_modes(line)
        if line.startswith("Game Result:"):
            result.last_result_s = stamp
