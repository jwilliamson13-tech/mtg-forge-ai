"""Command line: `python -m simrunner batch DECK1 DECK2 --games N --seed S`.

`nightly` takes the same arguments plus `--start` and `--stop`. `pool` takes those without
the two decks, and draws its deck pairs from the manifest.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from simrunner.batch import BatchConfig, SetupError, run_batch
from simrunner.pool import DEFAULT_CHUNK_GAMES, DEFAULT_HELD_OUT_SHARE, PoolConfig, format_pool_report, run_pool
from simrunner.report import format_report
from simrunner.window import Window, parse_clock, resolve_window
from simrunner.worker import DEFAULT_SEARCH_BUDGET, SIM_MODES, RecordPlan, Seat

CORES_RESERVED = 2
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORGE = REPO_ROOT.parent / "forge"
DEFAULT_RUNS = REPO_ROOT / "runs"
DEFAULT_MANIFEST = REPO_ROOT / "decks" / "manifest.json"


def _cores_minus(reserve: int) -> int:
    return max(1, (os.cpu_count() or 1) - reserve)


def _clock_time(text: str):
    try:
        return parse_clock(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _deck_path(arg: str, manifest_path: Path | None, parser: argparse.ArgumentParser) -> Path:
    """A deck argument is a file if one exists at that path, otherwise an id in the deck manifest."""
    if Path(arg).is_file():
        return Path(arg)
    from deckpool import manifest as mf
    path = Path(manifest_path) if manifest_path else mf.DEFAULT_PATH
    if not path.is_file():
        parser.error(f"not a file: {arg} (and no deck manifest at {path} to look it up in)")
    try:
        return mf.deck_path(mf.load(path), arg, mf.root_of(path))
    except mf.ManifestError as e:
        parser.error(f"not a file: {arg}, and {e}")


def _batch(args: argparse.Namespace) -> int:
    return _run(args, args.workers)


def _window_and_workers(args: argparse.Namespace) -> tuple[Window, int]:
    if args.reserve < 0:
        args.parser.error("the reserve can't be negative")
    try:
        window = resolve_window(args.start, args.stop, datetime.now())
    except ValueError as e:
        args.parser.error(str(e))
    print(f"window {window.start} to {window.stop}", file=sys.stderr)
    return window, _cores_minus(args.reserve) if args.workers is None else args.workers


def _nightly(args: argparse.Namespace) -> int:
    window, workers = _window_and_workers(args)
    return _run(args, workers, window)


def _config(args: argparse.Namespace, decks: tuple[Path, Path], workers: int, window: Window | None) -> BatchConfig:
    profiles = args.ai or (None, None)
    modes = args.sim_mode or (None, None)
    run_dir = args.run_dir or DEFAULT_RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-seed{args.seed}"
    records = None
    if args.records or args.records_dir is not None:
        records = RecordPlan(Path(args.records_dir) if args.records_dir else None, args.turn_cap, not args.no_honest)
    elif args.turn_cap is not None or args.no_honest:
        args.parser.error("--turn-cap and --no-honest need records")
    return BatchConfig(
        seats=(Seat(decks[0], profiles[0], modes[0]), Seat(decks[1], profiles[1], modes[1])),
        games=args.games, batch_seed=args.seed, workers=workers,
        forge_root=Path(args.forge), run_dir=Path(run_dir), clock_s=args.clock, heap=args.heap,
        java=tuple(args.java), keep_profiles=args.keep_profiles, search_budget=args.search_budget, window=window,
        records=records, tripwire=not args.no_tripwire,
    )


def _write_report(run_dir: Path, report: str) -> None:
    (run_dir / "report.txt").write_text(report, encoding="utf-8", newline="\n")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    sys.stdout.write(report)


def _run(args: argparse.Namespace, workers: int, window: Window | None = None) -> int:
    decks = (_deck_path(args.deck1, args.manifest, args.parser), _deck_path(args.deck2, args.manifest, args.parser))
    config = _config(args, decks, workers, window)
    try:
        batch = run_batch(config)
    except SetupError as e:
        args.parser.error(str(e))
    _write_report(config.run_dir, format_report(batch))
    if batch.stopped and not batch.complete:
        print(f"batch incomplete: stopped at the hard stop, {batch.not_finished} of {batch.attempted} "
              f"games not finished, logs in {config.run_dir}", file=sys.stderr)
        return 1
    if not batch.clean:
        print(f"batch not clean: {batch.not_finished} of {batch.attempted} games not finished, "
              f"logs in {config.run_dir}", file=sys.stderr)
        return 1
    return 0


def _pool(args: argparse.Namespace) -> int:
    if args.chunk_games < 1:
        args.parser.error("a chunk needs at least 1 game")
    if not 0 <= args.held_out_share <= 1:
        args.parser.error("the held-out share is a fraction from 0 to 1")
    window, workers = _window_and_workers(args)
    # A chunk fills in its own two decks.
    config = PoolConfig(_config(args, (Path(), Path()), workers, window), Path(args.manifest or DEFAULT_MANIFEST),
                        args.chunk_games, args.held_out_share)
    try:
        pool = run_pool(config)
    except SetupError as e:
        args.parser.error(str(e))
    _write_report(config.batch.run_dir, format_pool_report(pool))
    if not pool.complete:
        why = "stopped at the hard stop" if pool.stopped else f"{len(pool.failed)} chunks failed"
        print(f"pool batch incomplete: {why}, {pool.not_finished} of {pool.attempted} games not finished. The same "
              f"--run-dir and --seed continue it. Logs in {config.batch.run_dir}", file=sys.stderr)
        return 1
    return 0


def _add_deck_arguments(batch: argparse.ArgumentParser) -> None:
    batch.add_argument("deck1", help="path to the .dck file for seat 1, or a deck id from the manifest")
    batch.add_argument("deck2", help="path to the .dck file for seat 2, or a deck id from the manifest")


def _add_window_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=_clock_time, required=True,
                        help="local time to start, HH:MM or HH:MM:SS; inside the window, it starts at once")
    parser.add_argument("--stop", type=_clock_time, required=True,
                        help="local time by which every worker is gone; at or before --start means the next day")
    count = parser.add_mutually_exclusive_group()
    count.add_argument("--reserve", type=int, default=CORES_RESERVED,
                       help="logical cores to leave free; workers are the rest (default: 2)")
    count.add_argument("--workers", type=int, help="parallel JVMs, in place of --reserve")


def _add_batch_arguments(batch: argparse.ArgumentParser) -> None:
    batch.add_argument("--manifest", help="deck manifest for looking up deck ids (default: decks/manifest.json)")
    batch.add_argument("--games", type=int, required=True, help="total games in the batch")
    batch.add_argument("--seed", type=int, required=True, help="batch seed; worker seeds derive from it")
    batch.add_argument("--ai", nargs=2, metavar=("SEAT1", "SEAT2"),
                       help="AI profile per seat, such as Default Reckless")
    batch.add_argument("--sim-mode", nargs=2, metavar=("SEAT1", "SEAT2"), choices=SIM_MODES,
                       help="simulation mode per seat: heuristic, hybrid or full (default: heuristic)")
    batch.add_argument("--search-budget", type=int,
                       help="search steps per AI decision, in place of Forge's wall-clock limit (default: "
                            f"{DEFAULT_SEARCH_BUDGET} when a seat searches, otherwise off; 0 turns it off)")
    batch.add_argument("--clock", type=int, default=2400, help="seconds before Forge stops a game (default: 2400)")
    batch.add_argument("--heap", default="2048m", help="-Xmx per JVM (default: 2048m)")
    batch.add_argument("--forge", default=str(DEFAULT_FORGE), help="Forge clone (default: ../forge)")
    batch.add_argument("--run-dir", help="folder for logs and the report (default: runs/<time>-seed<seed>)")
    batch.add_argument("--java", nargs="+", default=["java"], help="command that starts Java (default: java)")
    batch.add_argument("--keep-profiles", action="store_true",
                       help="keep the throwaway worker folders for debugging")
    batch.add_argument("--records", action="store_true", help="have Forge write one game record per game")
    batch.add_argument("--records-dir", help="folder for game records, which turns them on "
                                             "(default: games in the run folder)")
    batch.add_argument("--turn-cap", type=int, help="with records, the last turn a game may play before it "
                                                    "stops as a draw (default: Forge's 50)")
    batch.add_argument("--no-tripwire", action="store_true",
                       help="leave out the check for forge-ai reading hidden cards (default: on)")
    batch.add_argument("--no-honest", action="store_true",
                       help="with records, let the AI keep its peeks at hidden cards (default: honest play, "
                            "because records are for training)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m simrunner")
    commands = parser.add_subparsers(dest="command", required=True)

    batch = commands.add_parser("batch", help="run headless forge sim games in parallel and report throughput")
    _add_deck_arguments(batch)
    _add_batch_arguments(batch)
    batch.add_argument("--workers", type=int, default=_cores_minus(CORES_RESERVED),
                       help="parallel JVMs (default: logical cores minus 2)")
    batch.set_defaults(handler=_batch, parser=batch)

    nightly = commands.add_parser("nightly", help="run a batch inside a time window, with a hard stop at its end")
    _add_deck_arguments(nightly)
    _add_batch_arguments(nightly)
    _add_window_arguments(nightly)
    nightly.set_defaults(handler=_nightly, parser=nightly)

    pool = commands.add_parser("pool", help="spread a windowed batch over many deck pairs from the manifest, "
                                            "in chunks that a restart can skip")
    _add_batch_arguments(pool)
    _add_window_arguments(pool)
    pool.add_argument("--chunk-games", type=int, default=DEFAULT_CHUNK_GAMES,
                      help=f"about how many games one pair plays in one JVM (default: {DEFAULT_CHUNK_GAMES})")
    pool.add_argument("--held-out-share", type=float, default=DEFAULT_HELD_OUT_SHARE,
                      help="share of the games played held-out deck against held-out deck "
                           f"(default: {DEFAULT_HELD_OUT_SHARE})")
    pool.set_defaults(handler=_pool, parser=pool, sim_mode=["heuristic", "heuristic"], records=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
