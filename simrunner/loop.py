"""The generation loop: one command that takes the project from generation k to generation k+1.

Generation k+1 trains on games generation k played, with k in both seats, so every training
row comes from the AI being improved on. Generation 0 is the exception: it trains on the stock
heuristic AI's games, which is the "from heuristic" step. The steps, in order:

| Step | Does | Gate |
|---|---|---|
| play | `simrunner pool` with the source player in both seats, records on | every chunk finished |
| check | reads the pool folder (`simrunner.poolcheck`) | tripwire-hit games at most `max_tripwire_share` |
| hiddencheck | `gamerecord hiddencheck` | record ids name no hidden card |
| convert | `gamerecord convert --drop-tripwire-hits` | no file rejected |
| join | `setmodel join` | unmatched-card drops at most `max_drop_share` of snapshots |
| train | `setmodel train --generation k+1` | metrics written for that generation |
| ablate | `setmodel ablate` | kill criterion 2: full beats no_cards by 5 points |
| pairtest | `setmodel pairtest` | none, the verdict is recorded |
| export | `setmodel export --out models/gen{k+1}` | the export check passes, the bundle resolves |
| ladder | `simrunner ladder` with every generation so far | every batch Forge can seat finished |
| converge | `simrunner converge` source against gen{k+1} | every tracked matchup ended |
| log | appends one entry to `results/log.jsonl` and renders `results/log.md` | |

Each step that finishes writes `steps/<step>.json` in the generation's folder, with its command,
log, time, peak memory and what its gate read. A later start skips a step whose record and
outputs are there, unless a step before it ran again in this start. So a crash, a Ctrl-C, the
hard stop or a memory kill costs only the step it cut off. A gate that fails is a result, so it
appends an entry saying where and why, and stops. Everything else that stops the loop leaves the
log alone, and the same command continues.

See "Generation loop" in docs/forge-build.md.
"""

from __future__ import annotations

import ctypes
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from simrunner.batch import STATUS_FILE, write_json
from simrunner.generations import GenerationError, resolve
from simrunner.pool import PLAN_FILE
from simrunner.poolcheck import PoolCheckError, check_pool
from simrunner.results import append_entry, read_entries
from simrunner.window import Window
from simrunner.worker import kill_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
STEPS = ("play", "check", "hiddencheck", "convert", "join", "train", "ablate", "pairtest", "export", "ladder",
         "converge", "log")
FORGE_STEPS = ("play", "ladder", "converge")  # they play games, take the window themselves and stop at it
MEMORY_STEPS = ("convert", "join", "train", "ablate", "pairtest", "export")  # they hold a corpus or a model
STEPS_FOLDER = "steps"
LOGS_FOLDER = "logs"
CHECK_FILE = "check.json"
LOOP_LOG = "loop.log"
HEURISTIC = "heuristic"
GENERATION = re.compile(r"gen(\d+)")
KILL_BAR_POINTS = 5.0  # kill criterion 2, HANDOFF.md
DEFAULT_MAX_TRIPWIRE_SHARE = 0.01
DEFAULT_MAX_DROP_SHARE = 0.01
DEFAULT_MIN_FREE_MB = 4096
DEFAULT_MEMORY_FLOOR_MB = 1024
DEFAULT_LADDER_PLAYERS = ("heuristic", "full")
DEFAULT_LADDER_SEED = 41
DEFAULT_CONVERGE_SEED = 42
FORGE_GRACE_S = 300  # past the hard stop, a Forge step that hasn't stopped itself is killed
POLL_S = 1.0
ENDED = ("met", "budget spent")  # a matchup status that gives a verdict or says why there is none


class LoopStop(Exception):
    """Why the loop stopped. `result` is True for a gate that failed, which the log records."""

    result = False

    def __init__(self, step: str, why: str):
        super().__init__(f"{step}: {why}")
        self.step = step
        self.why = why


class GateFailed(LoopStop):
    result = True


class StepFailed(LoopStop):
    """A step that crashed, was cut off or was killed. Running the same command again continues it."""


class LoopError(ValueError):
    """Settings the loop refuses before anything runs."""


# Memory


def free_memory_mb() -> float | None:
    """Physical memory free for new work, in MB, or None where this can't tell."""
    if sys.platform == "win32":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ("total", "available", "total_page", "available_page",
                                                        "total_virtual", "available_virtual", "extended")]
        status = Status()
        status.length = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.available / 2**20
        return None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def _tree_peak_mb(pid: int, seen: dict[int, float]) -> float | None:
    """The peak working set of a process and its descendants, summed, on Windows.

    A venv's python.exe is a launcher that starts the real interpreter as a child, so the
    launcher's own peak is a few MB whatever the step does. Each process's peak is its own
    lifetime peak, so sampling now and then while they live loses nothing.
    """
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", ctypes.c_long), ("flags", wintypes.DWORD),
                    ("exe", ctypes.c_char * 260)]

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak", "working", "quota_peak_paged", "quota_paged",
                                                 "quota_peak_nonpaged", "quota_nonpaged", "pagefile", "peak_pagefile")]

    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return None
    parents = {}
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(Entry)
        more = kernel32.Process32First(wintypes.HANDLE(snapshot), ctypes.byref(entry))
        while more:
            parents[entry.pid] = entry.parent
            more = kernel32.Process32Next(wintypes.HANDLE(snapshot), ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(snapshot))
    tree, grew = {pid}, True
    while grew:
        grew = False
        for child, parent in parents.items():
            if parent in tree and child not in tree and child != parent:
                tree.add(child)
                grew = True
    for member in tree:
        handle = kernel32.OpenProcess(0x1000 | 0x0010, False, member)  # query limited information, vm read
        if not handle:
            continue
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if psapi.GetProcessMemoryInfo(wintypes.HANDLE(handle), ctypes.byref(counters), counters.cb):
                seen[member] = max(seen.get(member, 0.0), counters.peak / 2**20)
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
    return sum(seen.values()) if seen else None


# Running one command


@dataclass
class Ran:
    """How one command went."""

    exit: int | None  # None when the loop killed it
    seconds: float
    peak_mb: float | None  # the process tree's peak working set, Windows only
    lowest_free_mb: float | None  # the least free memory seen while it ran
    killed: str | None = None  # why the loop killed it


@dataclass
class Runner:
    """Starts a command below normal priority, logs its output, and kills it at the hard stop or a memory floor."""

    poll_s: float = POLL_S
    free_mb: Callable[[], float | None] = free_memory_mb
    clock: Callable[[], float] = time.time

    def run(self, argv: list[str], log: Path, kill_at: float | None, memory_floor_mb: float | None) -> Ran:
        log.parent.mkdir(parents=True, exist_ok=True)
        flags = 0x4000 if sys.platform == "win32" else 0  # BELOW_NORMAL_PRIORITY_CLASS; children inherit it
        began = time.monotonic()
        seen: dict[int, float] = {}
        peak = lowest = None
        killed = None
        with open(log, "w", encoding="utf-8", errors="replace") as out:
            out.write(f"$ {subprocess.list2cmdline(argv)}\n")
            out.flush()
            process = subprocess.Popen(argv, cwd=REPO_ROOT, stdout=out, stderr=subprocess.STDOUT,
                                       creationflags=flags)
            interrupted = False
            while True:
                try:
                    code = process.wait(timeout=self.poll_s)
                    break
                except subprocess.TimeoutExpired:
                    pass
                except KeyboardInterrupt:
                    interrupted = True  # the child took the same Ctrl-C; wait for it to end
                    killed = "interrupted"
                    continue
                peak = _tree_peak_mb(process.pid, seen) or peak
                free = self.free_mb()
                if free is not None:
                    lowest = free if lowest is None else min(lowest, free)
                if kill_at is not None and self.clock() >= kill_at and not interrupted:
                    killed = "the hard stop"
                elif memory_floor_mb is not None and free is not None and free < memory_floor_mb:
                    killed = f"free memory fell to {free:.0f} MB, under the floor of {memory_floor_mb:.0f} MB"
                if killed and not interrupted:
                    kill_tree(process)
                    process.wait()
                    code = None
                    break
            if killed:
                out.write(f"\n# killed by the generation loop: {killed}\n")
        return Ran(None if killed else code, time.monotonic() - began, peak, lowest, killed)


# Settings


@dataclass(frozen=True)
class TrainOptions:
    epochs: int | None = None
    batch_size: int | None = None
    device: str | None = None
    seed: int | None = None

    def arguments(self) -> list[str]:
        out = []
        for flag, value in (("--epochs", self.epochs), ("--batch-size", self.batch_size), ("--device", self.device),
                            ("--seed", self.seed)):
            if value is not None:
                out += [flag, str(value)]
        return out


@dataclass(frozen=True)
class LoopConfig:
    source: str  # "heuristic" or "genK", the player whose games gen N trains on
    target: str  # "genN"
    games: int
    seed: int
    workers: int
    window: Window
    window_args: tuple[str, str]  # --start and --stop as given, for the Forge steps
    run_dir: Path  # this generation's folder
    data_dir: Path  # the pool run folder; inside run_dir unless an existing pool is reused
    models: Path
    manifest: Path
    cards: Path
    results: Path
    ladder_dir: Path
    converge_dir: Path
    python: tuple[str, ...] = (sys.executable,)
    ladder_players: tuple[str, ...] = DEFAULT_LADDER_PLAYERS
    ladder_seed: int = DEFAULT_LADDER_SEED
    converge_seed: int = DEFAULT_CONVERGE_SEED
    pool_options: tuple[str, ...] = ()  # passed through to `simrunner pool`
    ladder_options: tuple[str, ...] = ()
    converge_options: tuple[str, ...] = ()
    forge_options: tuple[str, ...] = ()  # passed to all three Forge steps
    train: TrainOptions = TrainOptions()
    max_tripwire_share: float = DEFAULT_MAX_TRIPWIRE_SHARE
    max_drop_share: float = DEFAULT_MAX_DROP_SHARE
    min_free_mb: float = DEFAULT_MIN_FREE_MB
    memory_floor_mb: float = DEFAULT_MEMORY_FLOOR_MB
    ignore_kill_criterion: bool = False
    redo: str | None = None

    @property
    def number(self) -> int:
        return int(GENERATION.fullmatch(self.target)[1])

    @property
    def source_player(self) -> str:
        return HEURISTIC if self.source == HEURISTIC else f"onnx:{self.source}"

    @property
    def target_player(self) -> str:
        return f"onnx:{self.target}"


def check_config(config: LoopConfig) -> None:
    """Refuse a generation step that doesn't follow from the one before."""
    target = GENERATION.fullmatch(config.target)
    if not target:
        raise LoopError(f"--to is a generation such as gen1, not {config.target!r}")
    number = int(target[1])
    if config.source == HEURISTIC:
        if number != 0:
            raise LoopError(f"only gen0 trains on the heuristic AI's games; {config.target} trains on gen{number - 1}'s")
    else:
        source = GENERATION.fullmatch(config.source)
        if not source:
            raise LoopError(f"--from is heuristic or a generation such as gen0, not {config.source!r}")
        if int(source[1]) != number - 1:
            raise LoopError(f"{config.target} trains on gen{number - 1}'s games, not {config.source}'s")
        try:
            resolve(config.source, config.models)
        except GenerationError as e:
            raise LoopError(str(e)) from e
    if config.redo is not None and config.redo not in STEPS:
        raise LoopError(f"--redo takes a step: {', '.join(STEPS)}")
    if config.games < 1:
        raise LoopError("--games needs at least 1 game")
    for name, share in (("--max-tripwire-share", config.max_tripwire_share), ("--max-drop-share", config.max_drop_share)):
        if not 0 <= share <= 1:
            raise LoopError(f"{name} is a fraction from 0 to 1")


# The loop


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _numbers(text: str) -> dict[str, int]:
    """`key words 12` lines, as `hiddencheck` prints them."""
    found = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([a-z][a-z ]*?) (\d+)", line.strip())
        if match:
            found[match[1]] = int(match[2])
    return found


def _commit() -> str | None:
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        return done.stdout.strip() or None if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class Loop:
    config: LoopConfig
    runner: Runner = field(default_factory=Runner)
    say: Callable[[str], None] = lambda text: print(text, file=sys.stderr, flush=True)
    summaries: dict[str, dict] = field(default_factory=dict)
    records: list[dict] = field(default_factory=list)
    logged: dict | None = None  # the entry this start appended

    # Paths

    @property
    def run_dir(self) -> Path:
        return self.config.run_dir

    @property
    def parquet(self) -> Path:
        return self.run_dir / "parquet"

    @property
    def model(self) -> Path:
        return self.run_dir / "model"

    @property
    def ablation(self) -> Path:
        return self.run_dir / "ablation"

    @property
    def bundle(self) -> Path:
        return self.config.models / self.config.target

    def log_path(self, step: str) -> Path:
        return self.run_dir / LOGS_FOLDER / f"{step}.log"

    def marker(self, step: str) -> Path:
        return self.run_dir / STEPS_FOLDER / f"{step}.json"

    def _note(self, text: str) -> None:
        self.say(text)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self.run_dir / LOOP_LOG, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{_now()} {text}\n")

    # Commands

    def _module(self, module: str, *args: str) -> list[str]:
        return [*self.config.python, "-m", module, *(str(arg) for arg in args)]

    def _forge_args(self) -> list[str]:
        c = self.config
        return ["--workers", str(c.workers), "--manifest", str(c.manifest), "--models", str(c.models),
                "--start", c.window_args[0], "--stop", c.window_args[1], *c.forge_options]

    def command(self, step: str) -> list[str] | None:
        c = self.config
        inputs = (self.parquet, c.cards, c.manifest)
        if step == "play":
            seats = [] if c.source == HEURISTIC else ["--sim-mode", "full", "full", "--evaluator", c.source_player,
                                                      c.source_player]
            return self._module("simrunner", "pool", "--games", c.games, "--seed", c.seed, "--run-dir", c.data_dir,
                                *seats, *self._forge_args(), *c.pool_options)
        if step == "hiddencheck":
            return self._module("gamerecord", "hiddencheck", c.data_dir / "games")
        if step == "convert":
            return self._module("gamerecord", "convert", c.data_dir / "games", self.parquet, "--drop-tripwire-hits")
        if step == "join":
            return self._module("setmodel", "join", *inputs)
        if step == "train":
            return self._module("setmodel", "train", *inputs, "--out", self.model, "--generation", c.number,
                                *c.train.arguments())
        if step == "ablate":
            return self._module("setmodel", "ablate", *inputs, "--out", self.ablation, "--generation", c.number,
                                *c.train.arguments())
        if step == "pairtest":
            return self._module("setmodel", "pairtest", self.model, c.cards)
        if step == "export":
            return self._module("setmodel", "export", self.model, *inputs, "--out", self.bundle)
        if step == "ladder":
            return self._module("simrunner", "ladder", "--players", *self.ladder_players(), "--seed", c.ladder_seed,
                                "--run-dir", c.ladder_dir, *self._forge_args(), *c.ladder_options)
        if step == "converge":
            return self._module("simrunner", "converge", c.source_player, c.target_player, "--seed", c.converge_seed,
                                "--run-dir", c.converge_dir, *self._forge_args(), *c.converge_options)
        return None  # check and log run inside the loop

    def ladder_players(self) -> list[str]:
        """The fixed rungs, then every generation from gen0 to this one that has a bundle."""
        generations = [f"onnx:gen{n}" for n in range(self.config.number + 1)
                       if (self.config.models / f"gen{n}" / "contract.json").is_file()]
        return [*self.config.ladder_players, *(g for g in generations if g not in self.config.ladder_players)]

    # Validity: is what a step made still there?

    def outputs(self, step: str) -> list[Path]:
        c = self.config
        return {
            "play": [c.data_dir / STATUS_FILE, c.data_dir / PLAN_FILE],
            "check": [self.run_dir / CHECK_FILE],
            "hiddencheck": [self.log_path("hiddencheck")],
            "convert": [self.parquet / "snapshots"],
            "join": [self.log_path("join")],
            "train": [self.model / "metrics.json", self.model / "model.pt"],
            "ablate": [self.ablation / "ablation.json"],
            "pairtest": [self.log_path("pairtest")],
            "export": [self.bundle / "contract.json", self.bundle / "model.onnx"],
            "ladder": [c.ladder_dir / "ladder.json"],
            "converge": [c.converge_dir / "convergence.json"],
            "log": [c.results / "log.jsonl"],
        }[step]

    def _plan_matches(self) -> bool:
        """Whether the data folder's plan is the pool this step would play. Raises when it is another pool."""
        c = self.config
        path = c.data_dir / PLAN_FILE
        if not path.is_file():
            return False
        plan = json.loads(path.read_text(encoding="utf-8"))
        searching = c.source != HEURISTIC
        expected = {"seed": c.seed, "games": c.games,
                    "sim_modes": ["full", "full"] if searching else ["heuristic", "heuristic"],
                    "generation names": [c.source, c.source] if searching else None}
        held = {"seed": plan.get("seed"), "games": plan.get("games"), "sim_modes": plan.get("sim_modes"),
                "generation names": [g and g.get("name") for g in plan["generations"]] if plan.get("generations") else None}
        differs = [key for key in expected if expected[key] != held[key]]
        if differs:
            raise LoopError(f"{c.data_dir} holds another pool, in: {', '.join(differs)} "
                            f"({', '.join(f'{k} {held[k]}' for k in differs)})")
        return True

    def _pool_complete(self) -> bool:
        path = self.config.data_dir / STATUS_FILE
        return (path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("status") == "complete"
                and self._plan_matches())

    def _valid(self, step: str, record: dict | None) -> bool:
        if not all(path.exists() for path in self.outputs(step)):
            return False
        if step == "play":
            return self._pool_complete()
        if step == "export" and record:
            try:
                return resolve(str(self.bundle), self.config.models).model_digest == record["summary"]["model_digest"]
            except (GenerationError, KeyError):
                return False
        return record is not None

    # Gates: read what a step made, return its summary, or raise

    def gate(self, step: str, ran: Ran | None) -> dict:
        return getattr(self, f"_gate_{step}")(ran)

    def _log_text(self, step: str) -> str:
        path = self.log_path(step)
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    def _require_exit(self, step: str, ran: Ran | None, codes: tuple[int, ...] = (0,)) -> None:
        if ran is not None and ran.exit not in codes:
            raise StepFailed(step, f"exit {ran.exit}, see {self.log_path(step)}")

    def _gate_play(self, ran: Ran | None) -> dict:
        c = self.config
        path = c.data_dir / STATUS_FILE
        status = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if status.get("status") != "complete":
            why = [f"pool {status.get('status', 'not written')}"]
            if status.get("chunks_failed"):
                why.append(f"{status['chunks_failed']} chunks failed (listed in {c.data_dir / 'report.txt'})")
            if status.get("stopped_at_hard_stop"):
                why.append("stopped at the hard stop")
            why.append(f"{status.get('games_finished', 0)} of {status.get('games_attempted', c.games)} games finished")
            raise StepFailed("play", ", ".join(why) + "; the same command continues the pool")
        self._plan_matches()
        return {"run_dir": str(c.data_dir), **{k: status.get(k) for k in ("games_finished", "chunks", "wall_s")}}

    def _gate_check(self, ran: Ran | None) -> dict:
        summary = json.loads((self.run_dir / CHECK_FILE).read_text(encoding="utf-8"))
        tripwire, finished = summary["tripwire"], summary["games_finished"]
        if summary.get("tripwire", {}).get("enabled") is False:
            raise GateFailed("check", "the pool ran with the tripwire off, so nothing shows the AI played fair")
        share = tripwire["games_with_hits"] / finished if finished else 0.0
        self.summaries["check"] = summary  # a stopped entry still carries the data
        if share > self.config.max_tripwire_share:
            raise GateFailed("check", f"{tripwire['games_with_hits']} of {finished} games had tripwire hits, over "
                                      f"{self.config.max_tripwire_share:.2%}; fix the peeks first. Signatures: "
                                      + "; ".join(s["signature"] for s in tripwire["signatures"][:5]))
        return summary

    def _gate_hiddencheck(self, ran: Ran | None) -> dict:
        if ran is not None and ran.exit == 1:
            raise GateFailed("hiddencheck", f"record ids name hidden cards, see {self.log_path('hiddencheck')}")
        self._require_exit("hiddencheck", ran)
        return _numbers(self._log_text("hiddencheck"))

    def _gate_convert(self, ran: Ran | None) -> dict:
        text = self._log_text("convert")
        counts = _numbers(text)
        dropped = {m[1]: int(m[2]) for m in re.finditer(r"^  ([a-z_]+): (\d+)$", text.split("dropped", 1)[-1]
                                                        .split("rejected", 1)[0], re.M)}
        summary = {"kept": counts.get("kept"), "dropped": dropped, "rejected": counts.get("rejected"),
                   "snapshot_rows": counts.get("snapshot rows"), "card_rows": counts.get("card rows"),
                   "stack_rows": counts.get("stack rows"), "parquet": str(self.parquet)}
        if ran is not None and ran.exit == 1 and summary["rejected"]:
            raise GateFailed("convert", f"{summary['rejected']} game files rejected, see {self.log_path('convert')}")
        self._require_exit("convert", ran)
        return summary

    def _gate_join(self, ran: Ran | None) -> dict:
        self._require_exit("join", ran)
        text = self._log_text("join")
        summary = {}
        if m := re.search(r"^games (\d+), (\d+) with rows left$", text, re.M):
            summary.update(games=int(m[1]), games_left=int(m[2]))
        if m := re.search(r"^snapshots (\d+), (\d+) dropped for an unmatched card$", text, re.M):
            summary.update(snapshots=int(m[1]), snapshots_dropped=int(m[2]))
        if m := re.search(r"^unmatched names (\d+), (\d+) rows$", text, re.M):
            summary.update(unmatched_names=int(m[1]), unmatched_rows=int(m[2]))
            after = text[m.end():].splitlines()[1:]
            top = []
            for line in after:
                row = re.fullmatch(r"  (\d+)  (.*)", line)
                if not row:
                    break
                top.append({"rows": int(row[1]), "name": row[2]})
            summary["top_unmatched"] = top[:10]
        if "snapshots" not in summary:
            raise StepFailed("join", f"no join report in {self.log_path('join')}")
        share = summary["snapshots_dropped"] / summary["snapshots"] if summary["snapshots"] else 1.0
        summary["drop_share"] = share
        self.summaries["join"] = summary
        if share > self.config.max_drop_share:
            raise GateFailed("join", f"{summary['snapshots_dropped']} of {summary['snapshots']} snapshots dropped for an "
                                     f"unmatched card, over {self.config.max_drop_share:.2%}; the top names are in "
                                     f"{self.log_path('join')}")
        return summary

    def _gate_train(self, ran: Ran | None) -> dict:
        self._require_exit("train", ran)
        metrics = json.loads((self.model / "metrics.json").read_text(encoding="utf-8"))
        if metrics.get("generation") != self.config.number:
            raise GateFailed("train", f"{self.model} holds generation {metrics.get('generation')}, "
                                      f"not {self.config.number}")
        config = metrics.get("config") or {}
        return {"generation": metrics["generation"], "model": str(self.model), "train_rows": metrics.get("train_rows"),
                "train_games": metrics.get("train_games"), "batch_ids": metrics.get("batch_ids"),
                "held_out": metrics.get("held_out"), "held_out_games": metrics.get("held_out_games"),
                "held_out_decks": metrics.get("held_out_decks"), "device": metrics.get("device"),
                "torch": metrics.get("torch"), "seconds": metrics.get("seconds"), "epochs": config.get("epochs"),
                "seed": config.get("seed"), "config": config,
                "last_epoch_loss": (metrics.get("epoch_loss") or [None])[-1]}

    def _gate_ablate(self, ran: Ran | None) -> dict:
        self._require_exit("ablate", ran)
        table = json.loads((self.ablation / "ablation.json").read_text(encoding="utf-8"))
        gap = next((row["vs_full"] for row in table["rows"] if row["run"] == "no_cards"), None)
        margin = None if gap is None else round(-gap, 6)
        passed = margin is not None and margin >= KILL_BAR_POINTS
        summary = {"rows": table["rows"], "verdict": table["verdict"],
                   "kill_criterion_2": {"margin_points": margin, "bar_points": KILL_BAR_POINTS, "passed": passed}}
        self.summaries["ablate"] = summary
        if not passed and not self.config.ignore_kill_criterion:
            raise GateFailed("ablate", f"kill criterion 2: {table['verdict']} Redesign the encoding, don't scale data. "
                                       "--ignore-kill-criterion goes on regardless")
        return summary

    def _gate_pairtest(self, ran: Ran | None) -> dict:
        self._require_exit("pairtest", ran)
        text = self._log_text("pairtest")
        alone = re.findall(r"^(.+?) alone\s+([-\d.]+)$", text, re.M)
        lifts = re.search(r"^lifts\s+([-+\d.]+), ([-+\d.]+), pair ([-+\d.]+)$", text, re.M)
        verdict = re.search(r"^verdict\s+(.+)$", text, re.M)
        if not (len(alone) == 2 and lifts and verdict):
            raise StepFailed("pairtest", f"no pair test result in {self.log_path('pairtest')}")
        return {"names": [name for name, _ in alone],
                "lifts": {"first": float(lifts[1]), "second": float(lifts[2]), "pair": float(lifts[3])},
                "sees_interaction": "sees the interaction" in verdict[1], "verdict": verdict[1]}

    def _gate_export(self, ran: Ran | None) -> dict:
        text = self._log_text("export")
        check = re.search(r"export check: (\d+) states, max difference (\S+), threshold (\S+)", text)
        if ran is not None and ran.exit == 1 and check:
            raise GateFailed("export", f"the export check failed: max difference {check[2]} over {check[3]}")
        self._require_exit("export", ran)
        try:
            generation = resolve(str(self.bundle), self.config.models)
        except GenerationError as e:
            raise GateFailed("export", str(e)) from e
        if generation.number != self.config.number:
            raise GateFailed("export", f"{self.bundle} holds a bundle of generation {generation.number}")
        return {"bundle": str(self.bundle), "model_digest": generation.model_digest,
                "states": int(check[1]) if check else None, "max_difference": float(check[2]) if check else None,
                "threshold": float(check[3]) if check else None}

    def _gate_ladder(self, ran: Ran | None) -> dict:
        path = self.config.ladder_dir / "ladder.json"
        ladder = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if ladder.get("status") != "complete":
            batches = ladder.get("batches") or {}
            raise StepFailed("ladder", f"ladder {ladder.get('status', 'not written')}: {len(batches.get('failed', []))} "
                                       f"failed, {len(batches.get('stopped', []))} cut off, "
                                       f"{len(batches.get('not_started', []))} not started; the same command continues")
        labels = [player["label"] for player in ladder.get("players", [])]
        if self.config.target_player not in labels:
            raise StepFailed("ladder", f"{path} doesn't rate {self.config.target_player}")
        return {"run_dir": str(self.config.ladder_dir), "seed": ladder.get("seed"), "split": ladder.get("split"),
                "deck_pairs": ladder.get("deck_pairs"), "pair_games": ladder.get("pair_games"),
                "anchor": (ladder.get("anchor") or {}).get("player"), "confidence": ladder.get("confidence"),
                "method": ladder.get("method"), "ratings_problem": ladder.get("ratings_problem"),
                "players": [{"player": p["label"], "rank": p.get("rank"), "rating": p.get("rating"), "low": p.get("low"),
                             "high": p.get("high"), "decisive_games": p.get("decisive_games"),
                             **{k: p.get(k) for k in ("wins", "losses", "draws")}} for p in ladder.get("players", [])],
                "pairs": ladder.get("pairs"), "games_finished": ladder.get("games_finished"),
                "unplayable_batches": len((ladder.get("batches") or {}).get("unplayable", [])),
                "tripwire_hits": ladder.get("tripwire_hits"), "tripwire_clean": ladder.get("tripwire_clean")}

    def _gate_converge(self, ran: Ran | None) -> dict:
        path = self.config.converge_dir / "convergence.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        labels = ((result.get("before") or {}).get("label"), (result.get("after") or {}).get("label"))
        if labels != (self.config.source_player, self.config.target_player):
            raise StepFailed("converge", f"{path} doesn't hold {self.config.source_player} against "
                                         f"{self.config.target_player}; see {self.log_path('converge')}")
        unended = [f"slot {m['slot']} {side}" for m in result.get("matchups", []) for side in ("before", "after")
                   if not m.get(side) or m[side].get("status") not in ENDED]
        if unended:
            raise StepFailed("converge", f"not every matchup ended ({', '.join(unended)}); the same command continues")
        return {"run_dir": str(self.config.converge_dir),
                **{k: result.get(k) for k in ("status", "rule", "seed", "width_points", "confidence", "max_games",
                                              "counts", "matchups")},
                "before": labels[0], "after": labels[1]}

    # Steps that run inside the loop

    def _inline(self, step: str) -> None:
        if step == "check":
            try:
                summary = check_pool(self.config.data_dir)
            except PoolCheckError as e:
                raise StepFailed("check", str(e)) from e
            write_json(self.run_dir / CHECK_FILE, summary)
        elif step == "log":
            self.logged = self.entry("complete")
            append_entry(self.config.results, self.logged)

    # The entry

    def entry(self, status: str, stopped: LoopStop | None = None) -> dict:
        c = self.config
        s = self.summaries
        data = None
        if "check" in s:
            data = {**s["check"], "player": c.source_player, "search_unchecked": c.source != HEURISTIC,
                    "hiddencheck": s.get("hiddencheck")}
        return {
            "log_version": 1,
            "generation": c.number,
            "name": c.target,
            "source": c.source_player,
            "status": status,
            "stopped_at": stopped.step if stopped else None,
            "why": stopped.why if stopped else None,
            "finished_at": _now(),
            "commit": _commit(),
            "run_dir": str(c.run_dir),
            "seeds": {"data": c.seed, "train": (s.get("train") or {}).get("seed", c.train.seed),
                      "ladder": c.ladder_seed, "converge": c.converge_seed},
            "data": data,
            "conversion": s.get("convert"),
            "join": s.get("join"),
            "training": s.get("train"),
            "ablation": s.get("ablate"),
            "pair_test": s.get("pairtest"),
            "export": s.get("export"),
            "ladder": s.get("ladder"),
            "convergence": s.get("converge"),
            "steps": list(self.records),
        }

    # Running

    def _record(self, step: str, status: str, command: list[str] | None, ran: Ran | None, summary: dict,
                saved: dict | None = None) -> dict:
        if saved:
            record = {**saved, "status": status}
        else:
            record = {"step": step, "status": status, "command": command,
                      "log": str(self.log_path(step)) if command else None,
                      "outputs": [str(path) for path in self.outputs(step)],
                      "exit": ran.exit if ran else None, "seconds": round(ran.seconds, 1) if ran else None,
                      "peak_mb": round(ran.peak_mb) if ran and ran.peak_mb else None,
                      "lowest_free_mb": round(ran.lowest_free_mb) if ran and ran.lowest_free_mb else None,
                      "finished_at": _now()}
        self.records.append({k: v for k, v in record.items() if k != "summary"})
        return {**record, "summary": summary}

    def _save(self, step: str, record: dict) -> None:
        self.marker(step).parent.mkdir(parents=True, exist_ok=True)
        write_json(self.marker(step), record)

    def _halt_if_closed(self, step: str) -> None:
        if self.config.window.closed:
            raise StepFailed(step, f"the hard stop at {self.config.window.stop} has come; the same command continues "
                                   "in the next window")

    def _run_step(self, step: str) -> None:
        c = self.config
        for later in STEPS[STEPS.index(step) + 1:]:  # made from what this step replaces, so no longer good
            self.marker(later).unlink(missing_ok=True)
        command = self.command(step)
        if command is not None:  # check and log only read files, so they may finish a step after the stop
            self._halt_if_closed(step)
        if step in MEMORY_STEPS:
            free = self.runner.free_mb()
            if free is not None and free < c.min_free_mb:
                raise StepFailed(step, f"only {free:.0f} MB of memory free, and the step starts at {c.min_free_mb:.0f} "
                                       "MB or more; close something, or lower --min-free-mb")
        if command is None:
            self._note(f"[{step}] running inside the loop, output {', '.join(map(str, self.outputs(step)))}")
            began = time.monotonic()
            self._inline(step)
            ran = Ran(0, time.monotonic() - began, None, None)
        else:
            self._note(f"[{step}] running: {subprocess.list2cmdline(command)}")
            self._note(f"[{step}] log {self.log_path(step)}, output {', '.join(map(str, self.outputs(step)))}")
            forge = step in FORGE_STEPS
            kill_at = c.window.stop.timestamp() + FORGE_GRACE_S if forge else c.window.kill_at
            ran = self.runner.run(command, self.log_path(step), kill_at, None if forge else c.memory_floor_mb)
            self._note(f"[{step}] exit {ran.exit}, {ran.seconds:.0f} s"
                       + (f", peak {ran.peak_mb:.0f} MB" if ran.peak_mb else "")
                       + (f", least free {ran.lowest_free_mb:.0f} MB" if ran.lowest_free_mb else "")
                       + (f", killed: {ran.killed}" if ran.killed else ""))
            if ran.killed:
                raise StepFailed(step, f"killed: {ran.killed}; the same command runs it again")
        summary = self.gate(step, ran) if step != "log" else {}
        self.summaries[step] = summary
        self._save(step, self._record(step, "ran", command, ran, summary))

    def run(self) -> dict:
        """Every step in order. Returns the entry the log got, or raises `LoopStop`."""
        c = self.config
        check_config(c)
        if c.data_dir.exists():
            self._plan_matches()  # refuse another pool's folder before anything runs
        if time.time() < c.window.start.timestamp():
            self._note(f"waiting for the window, {c.window.start} to {c.window.stop}")
            c.window.wait_for_start()
        self._note(f"{c.source_player} to {c.target_player}, folder {self.run_dir}")
        rerun = False
        for step in STEPS:
            rerun = rerun or step == c.redo
            saved = json.loads(self.marker(step).read_text(encoding="utf-8")) if self.marker(step).is_file() else None
            if not rerun and self._valid(step, saved):
                if saved is None:  # a finished pool from before the loop, adopted as it is
                    summary = self.gate(step, None)
                    saved = self._record(step, "adopted", None, None, summary)
                    self._save(step, saved)
                    self._note(f"[{step}] adopted the finished pool in {c.data_dir}")
                else:
                    self._record(step, "skipped", None, None, saved["summary"], saved)
                    self._note(f"[{step}] skipped, done {saved.get('finished_at')}: "
                               f"{', '.join(saved.get('outputs') or [])}")
                self.summaries[step] = saved["summary"]
                continue
            try:
                self._run_step(step)
            except LoopStop as stop:
                self._note(f"[{step}] stopped: {stop.why}")
                if stop.result:
                    self._log_stop(stop)
                raise
            rerun = True  # everything after a step that ran is made again from its output
        return self.logged or self.entry("complete")

    def _log_stop(self, stop: LoopStop) -> None:
        """A failed gate gets one entry, not one per start that hits it again."""
        for old in reversed(read_entries(self.config.results)):
            if old.get("run_dir") == str(self.config.run_dir) and old.get("name") == self.config.target:
                if (old.get("stopped_at"), old.get("why")) == (stop.step, stop.why):
                    return
                break
        append_entry(self.config.results, self.entry("stopped", stop))
