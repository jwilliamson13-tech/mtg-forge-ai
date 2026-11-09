"""Command line: `python -m simrunner batch DECK1 DECK2 --games N --seed S`.

`nightly` takes the same arguments plus `--start` and `--stop`. `pool` takes those without
the two decks, and draws its deck pairs from the manifest. `matchup` takes the two decks and
plays seat-swapped rounds until the win rate's interval is narrow enough. `cardswap` runs two
matchups, deck A and deck A with `--swap OLD=NEW` made, against the same opponent. `ladder` rates
AI configurations against each other over a sample of manifest deck pairs. `converge` runs the
four tracked matchups under two of them and says whether each win rate moved. `generation` runs
all of it in order, from the games one generation plays to the next generation's entry in the
results log.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from simrunner.batch import BatchConfig, SetupError, run_batch
from simrunner.cardswap import CardSwapConfig, Swap, format_cardswap_report, parse_swap, run_cardswap
from simrunner.converge import ConvergeConfig, format_converge_report, run_converge
from simrunner.generations import DEFAULT_MODELS, ONNX, PREFIX, Generation, GenerationError, is_onnx, resolve
from simrunner.ladder import (DEFAULT_DECK_PAIRS, DEFAULT_PAIR_GAMES, DEFAULT_SPLIT, SPLIT_CHOICES, LadderConfig,
                              format_ladder_report, run_ladder)
from simrunner.matchup import (DEFAULT_MAX_GAMES, DEFAULT_ROUND_GAMES, DEFAULT_WIDTH, MatchupConfig,
                               format_matchup_report, run_matchup)
from simrunner.players import Player, PlayerError, parse_player
from simrunner.pool import DEFAULT_CHUNK_GAMES, DEFAULT_HELD_OUT_SHARE, PoolConfig, format_pool_report, run_pool
from simrunner.report import format_report
from simrunner.window import Window, parse_clock, resolve_window
from simrunner.worker import DEFAULT_SEARCH_BUDGET, EVALUATORS, SIM_MODES, RecordPlan, Seat

CORES_RESERVED = 2
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORGE = REPO_ROOT.parent / "forge"
DEFAULT_RUNS = REPO_ROOT / "runs"
DEFAULT_MANIFEST = REPO_ROOT / "decks" / "manifest.json"
DEFAULT_CARDS = REPO_ROOT / "data" / "cards.jsonl"
DEFAULT_LADDER = DEFAULT_RUNS / "ladder"
DEFAULT_CONVERGENCE = DEFAULT_RUNS / "convergence"
DEFAULT_GENERATIONS = DEFAULT_RUNS / "generations"
DEFAULT_RESULTS = REPO_ROOT / "results"


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


def _evaluator_value(text: str) -> str:
    if text in EVALUATORS or (is_onnx(text) and len(text) > len(PREFIX)):
        return text
    raise argparse.ArgumentTypeError(f"invalid choice: {text!r} (choose from {', '.join(EVALUATORS)} or "
                                     f"{PREFIX}GENERATION)")


def _seat_evaluators(args: argparse.Namespace) -> list[tuple[str | None, Generation | None]]:
    """Each seat's evaluator, with the generation an `onnx:NAME` names resolved to its bundle."""
    seats = []
    for value in args.evaluator or (None, None):
        if value and is_onnx(value):
            try:
                seats.append((ONNX, resolve(value.removeprefix(PREFIX), Path(args.models))))
            except GenerationError as e:
                args.parser.error(str(e))
        else:
            seats.append((value, None))
    return seats


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
    evaluators = _seat_evaluators(args)
    run_dir = args.run_dir or DEFAULT_RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-seed{args.seed}"
    records = None
    if args.records or args.records_dir is not None:
        records = RecordPlan(Path(args.records_dir) if args.records_dir else None, args.turn_cap, not args.no_honest)
    elif args.turn_cap is not None or args.no_honest:
        args.parser.error("--turn-cap and --no-honest need records")
    seats = (Seat(decks[0], profiles[0], modes[0], *evaluators[0]), Seat(decks[1], profiles[1], modes[1], *evaluators[1]))
    return _run_config(args, seats, workers, window, Path(run_dir), records)


def _run_config(args: argparse.Namespace, seats: tuple[Seat, Seat], workers: int, window: Window | None,
                run_dir: Path, records: RecordPlan | None) -> BatchConfig:
    return BatchConfig(
        seats=seats, games=getattr(args, "games", 0), batch_seed=args.seed, workers=workers,
        forge_root=Path(args.forge), run_dir=run_dir, clock_s=args.clock, heap=args.heap,
        java=tuple(args.java), keep_profiles=args.keep_profiles, search_budget=args.search_budget, window=window,
        records=records, tripwire=not args.no_tripwire, prune=not args.no_prune,
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
        why = "stopped at the hard stop" if pool.stopped else f"{len(pool.failed)} chunks failed, {len(pool.not_started)} not started"
        print(f"pool batch incomplete: {why}, {pool.not_finished} of {pool.attempted} games not finished. The same "
              f"--run-dir and --seed continue it. Logs in {config.batch.run_dir}", file=sys.stderr)
        return 1
    return 0


def _matchup(args: argparse.Namespace) -> int:
    decks = (_deck_path(args.deck1, args.manifest, args.parser), _deck_path(args.deck2, args.manifest, args.parser))
    config = MatchupConfig(_config(args, decks, args.workers, None), args.width, args.round_games, args.max_games)
    try:
        matchup = run_matchup(config)
    except SetupError as e:
        args.parser.error(str(e))
    _write_report(config.batch.run_dir, format_matchup_report(matchup))
    if not matchup.met:
        print(f"no win rate ({matchup.status}), logs in {config.batch.run_dir}", file=sys.stderr)
        return 1
    return 0


def _swap_value(text: str) -> Swap:
    try:
        return parse_swap(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _checked_swap(args: argparse.Namespace) -> Swap:
    """The swap with the new card spelled as Forge spells it, when the card list is at hand."""
    swap = args.swap
    cards = Path(args.cards)
    if not cards.is_file():
        print(f"no card list at {cards}, so {swap.new!r} is unchecked", file=sys.stderr)
        return swap
    from deckpool.names import load_name_set
    known = load_name_set(cards, cardsfolder=None).resolve(swap.new)
    if known is None:
        args.parser.error(f"not a card Forge knows: {swap.new} (checked against {cards})")
    return Swap(swap.old, known, swap.count)


def _cardswap(args: argparse.Namespace) -> int:
    decks = (_deck_path(args.deck1, args.manifest, args.parser), _deck_path(args.deck2, args.manifest, args.parser))
    matchup = MatchupConfig(_config(args, decks, args.workers, None), args.width, args.round_games, args.max_games)
    config = CardSwapConfig(matchup, _checked_swap(args))
    try:
        result = run_cardswap(config)
    except SetupError as e:
        args.parser.error(str(e))
    _write_report(config.run_dir, format_cardswap_report(result))
    if not result.met:
        print(f"no difference ({result.status}), logs in {config.run_dir}", file=sys.stderr)
        return 1
    return 0


def _players(args: argparse.Namespace, names: list[str]) -> tuple[Player, ...]:
    try:
        return tuple(parse_player(name, Path(args.models)) for name in names)
    except PlayerError as e:
        args.parser.error(str(e))


def _optional_window(args: argparse.Namespace) -> Window | None:
    if (args.start is None) != (args.stop is None):
        args.parser.error("--start and --stop go together")
    if args.start is None:
        return None
    window = resolve_window(args.start, args.stop, datetime.now())
    print(f"window {window.start} to {window.stop}", file=sys.stderr)
    return window


def _ladder(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else DEFAULT_LADDER
    records = RecordPlan(Path(run_dir) / "games", args.turn_cap, not args.no_honest)
    base = _run_config(args, (Seat(Path()), Seat(Path())), args.workers, _optional_window(args), run_dir, records)
    config = LadderConfig(base, _players(args, args.players), Path(args.manifest or DEFAULT_MANIFEST), args.deck_pairs,
                          args.pair_games, args.split, args.anchor, args.anchor_rating)
    try:
        ladder = run_ladder(config)
    except SetupError as e:
        args.parser.error(str(e))
    _write_report(run_dir, format_ladder_report(ladder))
    if not ladder.complete or not ladder.fit.ratings:
        print(f"ladder {ladder.status}, logs in {run_dir}", file=sys.stderr)
        return 1
    return 0


def _converge(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else DEFAULT_CONVERGENCE
    records = RecordPlan(None, args.turn_cap, not args.no_honest)
    base = _run_config(args, (Seat(Path()), Seat(Path())), args.workers, _optional_window(args), run_dir, records)
    before, after = _players(args, [args.before, args.after])
    config = ConvergeConfig(MatchupConfig(base, args.width, args.round_games, args.max_games), before, after,
                            Path(args.manifest or DEFAULT_MANIFEST))
    try:
        result = run_converge(config)
    except SetupError as e:
        args.parser.error(str(e))
    _write_report(run_dir, format_converge_report(result))
    if result.counts["no verdict"]:
        print(f"convergence {result.status}: {result.counts['no verdict']} matchups with no verdict, logs in {run_dir}",
              file=sys.stderr)
        return 1
    return 0


def _generation(args: argparse.Namespace) -> int:
    from simrunner import loop

    try:
        window = resolve_window(args.start, args.stop, datetime.now())
    except ValueError as e:
        args.parser.error(str(e))
    run_dir = Path(args.run_dir).resolve() if args.run_dir else DEFAULT_GENERATIONS / args.to
    options = {"pool": [], "ladder": [], "converge": [], "forge": []}
    for kind, flag, value in (("pool", "--chunk-games", args.chunk_games), ("pool", "--held-out-share", args.held_out_share),
                              ("ladder", "--deck-pairs", args.deck_pairs), ("ladder", "--pair-games", args.pair_games),
                              ("converge", "--width", args.width), ("converge", "--round-games", args.round_games),
                              ("converge", "--max-games", args.max_games), ("forge", "--search-budget", args.search_budget),
                              ("forge", "--clock", args.clock), ("forge", "--heap", args.heap),
                              ("forge", "--forge", args.forge and Path(args.forge).resolve())):
        if value is not None:
            options[kind] += [flag, str(value)]
    config = loop.LoopConfig(
        source=args.source, target=args.to, games=args.games, seed=args.seed, workers=args.workers, window=window,
        window_args=(args.start.isoformat(), args.stop.isoformat()), run_dir=run_dir,
        # The steps run from the repository root, so every path goes to them absolute.
        data_dir=Path(args.data_dir).resolve() if args.data_dir else run_dir / "pool", models=Path(args.models).resolve(),
        manifest=Path(args.manifest or DEFAULT_MANIFEST).resolve(), cards=Path(args.cards).resolve(),
        results=Path(args.results).resolve(), ladder_dir=Path(args.ladder_dir).resolve(),
        converge_dir=Path(args.converge_dir).resolve(), python=tuple(args.python),
        ladder_players=tuple(args.ladder_players), ladder_seed=args.ladder_seed, converge_seed=args.converge_seed,
        pool_options=tuple(options["pool"]), ladder_options=tuple(options["ladder"]),
        converge_options=tuple(options["converge"]), forge_options=tuple(options["forge"]),
        train=loop.TrainOptions(args.epochs, args.batch_size, args.device, args.train_seed),
        max_tripwire_share=args.max_tripwire_share, max_drop_share=args.max_drop_share,
        max_timeout_share=args.max_timeout_share, min_free_mb=args.min_free_mb,
        memory_floor_mb=args.memory_floor_mb, ignore_kill_criterion=args.ignore_kill_criterion, redo=args.redo)
    try:
        entry = loop.Loop(config).run()
    except loop.LoopError as e:
        args.parser.error(str(e))
    except loop.LoopStop as stop:
        logged = f" The results log has an entry for it in {config.results}." if stop.result else ""
        print(f"generation loop stopped at {stop.step}: {stop.why}.{logged} Logs in {run_dir}", file=sys.stderr)
        return 1
    print(f"{args.to} done: entry {len(loop.read_entries(config.results))} in {config.results / 'log.jsonl'}, "
          f"convergence {entry['convergence']['status']}", file=sys.stderr)
    return 0


def _add_generation_arguments(parser: argparse.ArgumentParser) -> None:
    from simrunner import loop

    parser.add_argument("--from", dest="source", required=True, metavar="SOURCE",
                        help="whose games the new generation trains on: heuristic for gen0, otherwise the "
                             "generation before, such as gen0, which plays both seats in full sim")
    parser.add_argument("--to", required=True, metavar="TARGET", help="the generation to make, such as gen1")
    parser.add_argument("--games", type=int, required=True, help="games in the data pool")
    parser.add_argument("--seed", type=int, required=True, help="the data pool's seed")
    parser.add_argument("--workers", type=int, default=_cores_minus(CORES_RESERVED),
                        help="parallel JVMs for every Forge step (default: logical cores minus 2)")
    parser.add_argument("--start", type=_clock_time, required=True,
                        help="local time the window opens, HH:MM; inside it, the loop starts at once")
    parser.add_argument("--stop", type=_clock_time, required=True,
                        help="local time of the hard stop: Forge steps stop at it, and any other step running then "
                             "is killed and runs again next time")
    parser.add_argument("--run-dir", help="this generation's folder (default: runs/generations/TARGET)")
    parser.add_argument("--data-dir", help="the pool run folder, to reuse a pool that already played (default: "
                                           "pool in the generation's folder)")
    parser.add_argument("--models", default=str(DEFAULT_MODELS), help="folder of generations (default: models)")
    parser.add_argument("--manifest", help="deck manifest (default: decks/manifest.json)")
    parser.add_argument("--cards", default=str(DEFAULT_CARDS), help="parsed card dump (default: data/cards.jsonl)")
    parser.add_argument("--results", default=str(DEFAULT_RESULTS), help="folder of the results log (default: results)")
    parser.add_argument("--ladder-dir", default=str(DEFAULT_LADDER), help="the ladder folder (default: runs/ladder)")
    parser.add_argument("--converge-dir", default=str(DEFAULT_CONVERGENCE),
                        help="the convergence folder (default: runs/convergence)")
    parser.add_argument("--ladder-players", nargs="+", default=list(loop.DEFAULT_LADDER_PLAYERS), metavar="PLAYER",
                        help="the ladder's fixed rungs; every generation with a bundle joins them "
                             "(default: heuristic full)")
    parser.add_argument("--ladder-seed", type=int, default=loop.DEFAULT_LADDER_SEED,
                        help=f"the ladder's seed, the same every generation (default: {loop.DEFAULT_LADDER_SEED})")
    parser.add_argument("--converge-seed", type=int, default=loop.DEFAULT_CONVERGE_SEED,
                        help=f"the convergence seed, the same every generation (default: {loop.DEFAULT_CONVERGE_SEED})")
    passed = parser.add_argument_group("passed through", "left out, each takes the command's own default")
    passed.add_argument("--chunk-games", type=int, help="to pool")
    passed.add_argument("--held-out-share", type=float, help="to pool")
    passed.add_argument("--deck-pairs", type=int, help="to ladder")
    passed.add_argument("--pair-games", type=int, help="to ladder")
    passed.add_argument("--width", type=float, help="to converge")
    passed.add_argument("--round-games", type=int, help="to converge")
    passed.add_argument("--max-games", type=int, help="to converge")
    passed.add_argument("--search-budget", type=int, help="to pool, ladder and converge")
    passed.add_argument("--clock", type=int, help="to pool, ladder and converge")
    passed.add_argument("--heap", help="to pool, ladder and converge")
    passed.add_argument("--forge", help="to pool, ladder and converge")
    passed.add_argument("--epochs", type=int, help="to train and ablate")
    passed.add_argument("--batch-size", type=int, help="to train and ablate")
    passed.add_argument("--device", help="to train and ablate")
    passed.add_argument("--train-seed", type=int, help="to train and ablate, as --seed")
    gates = parser.add_argument_group("gates")
    gates.add_argument("--max-tripwire-share", type=float, default=loop.DEFAULT_MAX_TRIPWIRE_SHARE,
                       help="stop if more of the pool's games than this had tripwire hits; those under it are left "
                            f"out of training (default: {loop.DEFAULT_MAX_TRIPWIRE_SHARE})")
    gates.add_argument("--max-timeout-share", type=float, default=loop.DEFAULT_MAX_TIMEOUT_SHARE,
                       help="stop if more of the pool's games than this had a Forge AI timeout; those under it are "
                            f"left out of training (default: {loop.DEFAULT_MAX_TIMEOUT_SHARE})")
    gates.add_argument("--max-drop-share", type=float, default=loop.DEFAULT_MAX_DROP_SHARE,
                       help="stop if join drops more of the snapshots than this for an unmatched card "
                            f"(default: {loop.DEFAULT_MAX_DROP_SHARE})")
    gates.add_argument("--min-free-mb", type=float, default=loop.DEFAULT_MIN_FREE_MB,
                       help="free memory a convert, join, train, ablate, pairtest or export step needs to start "
                            f"(default: {loop.DEFAULT_MIN_FREE_MB})")
    gates.add_argument("--memory-floor-mb", type=float, default=loop.DEFAULT_MEMORY_FLOOR_MB,
                       help="kill such a step if free memory falls under this while it runs "
                            f"(default: {loop.DEFAULT_MEMORY_FLOOR_MB})")
    gates.add_argument("--ignore-kill-criterion", action="store_true",
                       help="go on past ablate when full doesn't beat no_cards by 5 points")
    parser.add_argument("--redo", choices=loop.STEPS, help="run this step and every one after it again")
    parser.add_argument("--python", nargs="+", default=[sys.executable],
                        help="command that starts Python for each step (default: this Python)")


def _add_optional_window(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=_clock_time,
                        help="local time to start, HH:MM or HH:MM:SS, with --stop; inside the window, it starts at "
                             "once (default: now, with no hard stop)")
    parser.add_argument("--stop", type=_clock_time,
                        help="local time by which every worker is gone, with --start; at or before --start means "
                             "the next day")


def _add_matchup_arguments(parser: argparse.ArgumentParser, deck_arguments: bool = True) -> None:
    if deck_arguments:
        _add_batch_arguments(parser)
    else:
        _add_run_arguments(parser, "runs/convergence")
    parser.add_argument("--workers", type=int, default=_cores_minus(CORES_RESERVED),
                        help="parallel JVMs per batch (default: logical cores minus 2)")
    parser.add_argument("--width", type=float, default=DEFAULT_WIDTH,
                        help="stop once the 95%% interval reaches no more than this many points either side of "
                             f"the rate (default: {DEFAULT_WIDTH:g})")
    parser.add_argument("--round-games", type=int, default=DEFAULT_ROUND_GAMES,
                        help="games per round, an even number, half with each deck in seat 1 "
                             f"(default: {DEFAULT_ROUND_GAMES})")
    parser.add_argument("--max-games", type=int, default=DEFAULT_MAX_GAMES,
                        help=f"the game budget; past it the matchup refuses to give a rate (default: {DEFAULT_MAX_GAMES})")
    # The base batch's game count is unused: each round sets its own.
    parser.set_defaults(sim_mode=["heuristic", "heuristic"], records=True, games=0)


def _add_deck_arguments(batch: argparse.ArgumentParser, first: str = "seat 1", second: str = "seat 2") -> None:
    batch.add_argument("deck1", help=f"path to the .dck file for {first}, or a deck id from the manifest")
    batch.add_argument("deck2", help=f"path to the .dck file for {second}, or a deck id from the manifest")


def _add_window_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=_clock_time, required=True,
                        help="local time to start, HH:MM or HH:MM:SS; inside the window, it starts at once")
    parser.add_argument("--stop", type=_clock_time, required=True,
                        help="local time by which every worker is gone; at or before --start means the next day")
    count = parser.add_mutually_exclusive_group()
    count.add_argument("--reserve", type=int, default=CORES_RESERVED,
                       help="logical cores to leave free; workers are the rest (default: 2)")
    count.add_argument("--workers", type=int, help="parallel JVMs, in place of --reserve")


def _add_games_argument(batch: argparse.ArgumentParser) -> None:
    batch.add_argument("--games", type=int, required=True, help="total games in the batch")


def _add_batch_arguments(batch: argparse.ArgumentParser) -> None:
    batch.add_argument("--manifest", help="deck manifest for looking up deck ids (default: decks/manifest.json)")
    batch.add_argument("--seed", type=int, required=True, help="batch seed; worker seeds derive from it")
    _add_seat_arguments(batch)
    _add_run_arguments(batch)
    _add_record_arguments(batch)


def _add_seat_arguments(batch: argparse.ArgumentParser) -> None:
    batch.add_argument("--ai", nargs=2, metavar=("SEAT1", "SEAT2"),
                       help="AI profile per seat, such as Default Reckless")
    batch.add_argument("--sim-mode", nargs=2, metavar=("SEAT1", "SEAT2"), choices=SIM_MODES,
                       help="simulation mode per seat: heuristic, hybrid or full (default: heuristic)")
    batch.add_argument("--evaluator", nargs=2, metavar=("SEAT1", "SEAT2"), type=_evaluator_value,
                       help="what each seat's search scores states with: forge, random, passthrough, or "
                            "onnx:GENERATION for a model generation, a folder under --models or a bundle path "
                            "(default: forge, with -eval left off the command); any but forge needs hybrid or full")


def _add_run_arguments(batch: argparse.ArgumentParser, run_dir: str = "runs/<time>-seed<seed>") -> None:
    batch.add_argument("--models", default=str(DEFAULT_MODELS),
                       help="folder of generations, one export bundle per folder, named for its generation "
                            "(default: models)")
    batch.add_argument("--search-budget", type=int,
                       help="search steps per AI decision, in place of Forge's wall-clock limit (default: "
                            f"{DEFAULT_SEARCH_BUDGET} when a seat searches, otherwise off; 0 turns it off)")
    batch.add_argument("--clock", type=int, default=2400, help="seconds before Forge stops a game (default: 2400)")
    batch.add_argument("--heap", default="2048m", help="-Xmx per JVM (default: 2048m)")
    batch.add_argument("--forge", default=str(DEFAULT_FORGE), help="Forge clone (default: ../forge)")
    batch.add_argument("--run-dir", help=f"folder for logs and the report (default: {run_dir})")
    batch.add_argument("--java", nargs="+", default=["java"], help="command that starts Java (default: java)")
    batch.add_argument("--keep-profiles", action="store_true",
                       help="keep the throwaway worker folders for debugging")
    batch.add_argument("--turn-cap", type=int, help="with records, the last turn a game may play before it "
                                                    "stops as a draw (default: Forge's 50)")
    batch.add_argument("--no-tripwire", action="store_true",
                       help="leave out the check for forge-ai reading hidden cards (default: on)")
    batch.add_argument("--no-honest", action="store_true",
                       help="with records, let the AI keep its peeks at hidden cards (default: honest play, "
                            "because records are for training)")
    batch.add_argument("--no-prune", action="store_true",
                       help="let a searching seat's copies of the game hold the hidden cards it can't know "
                            "(default: Forge prunes them)")


def _add_record_arguments(batch: argparse.ArgumentParser) -> None:
    batch.add_argument("--records", action="store_true", help="have Forge write one game record per game")
    batch.add_argument("--records-dir", help="folder for game records, which turns them on "
                                             "(default: games in the run folder)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m simrunner")
    commands = parser.add_subparsers(dest="command", required=True)

    batch = commands.add_parser("batch", help="run headless forge sim games in parallel and report throughput")
    _add_deck_arguments(batch)
    _add_games_argument(batch)
    _add_batch_arguments(batch)
    batch.add_argument("--workers", type=int, default=_cores_minus(CORES_RESERVED),
                       help="parallel JVMs (default: logical cores minus 2)")
    batch.set_defaults(handler=_batch, parser=batch)

    nightly = commands.add_parser("nightly", help="run a batch inside a time window, with a hard stop at its end")
    _add_deck_arguments(nightly)
    _add_games_argument(nightly)
    _add_batch_arguments(nightly)
    _add_window_arguments(nightly)
    nightly.set_defaults(handler=_nightly, parser=nightly)

    pool = commands.add_parser("pool", help="spread a windowed batch over many deck pairs from the manifest, "
                                            "in chunks that a restart can skip")
    _add_games_argument(pool)
    _add_batch_arguments(pool)
    _add_window_arguments(pool)
    pool.add_argument("--chunk-games", type=int, default=DEFAULT_CHUNK_GAMES,
                      help=f"about how many games one pair plays in one JVM (default: {DEFAULT_CHUNK_GAMES})")
    pool.add_argument("--held-out-share", type=float, default=DEFAULT_HELD_OUT_SHARE,
                      help="share of the games played held-out deck against held-out deck "
                           f"(default: {DEFAULT_HELD_OUT_SHARE})")
    pool.set_defaults(handler=_pool, parser=pool, sim_mode=["heuristic", "heuristic"], records=True)

    matchup = commands.add_parser("matchup", help="play two decks in seat-swapped rounds until deck A's win rate "
                                                  "has a narrow enough interval",
                                  description="--ai, --sim-mode and --evaluator name deck A's setting first, and "
                                              "each setting moves with its deck when the seats swap.")
    _add_deck_arguments(matchup, "deck A, whose win rate is reported", "deck B")
    _add_matchup_arguments(matchup)
    matchup.set_defaults(handler=_matchup, parser=matchup)

    cardswap = commands.add_parser("cardswap", help="compare deck A with deck A with one card swapped, as two "
                                                    "matchups against the same opponent on the same seed",
                                   description="Each arm is a matchup run to the target width. The report gives "
                                               "both rates and A' minus A with its interval. --ai, --sim-mode and "
                                               "--evaluator name deck A's setting first, as in matchup.")
    _add_deck_arguments(cardswap, "deck A, the original", "the opponent")
    cardswap.add_argument("--swap", type=_swap_value, required=True, metavar="[N ]OLD=NEW",
                          help="the main-deck swap that makes deck A': every copy of OLD, or N of them, for NEW")
    cardswap.add_argument("--cards", default=str(DEFAULT_CARDS),
                          help="card list from the sweep, to check NEW's spelling (default: data/cards.jsonl)")
    _add_matchup_arguments(cardswap)
    cardswap.set_defaults(handler=_cardswap, parser=cardswap)

    ladder = commands.add_parser("ladder", help="rate AI configurations against each other: every pair of players "
                                                "over a fixed sample of deck pairs, each player on each deck in "
                                                "each seat",
                                 description="A player is heuristic, full, hybrid, MODE:EVALUATOR such as "
                                             "full:random, or onnx:GENERATION, which plays full sim with that "
                                             "generation. The ladder folder keeps every batch, so a later run with "
                                             "another generation plays only that generation's games and refits.")
    ladder.add_argument("--players", nargs="+", required=True, metavar="PLAYER",
                        help="the players to rate, such as heuristic full onnx:gen0")
    ladder.add_argument("--manifest", help="deck manifest to draw deck pairs from (default: decks/manifest.json)")
    ladder.add_argument("--seed", type=int, required=True,
                        help="the ladder's seed: it draws the deck pairs and every batch's seed derives from it")
    ladder.add_argument("--deck-pairs", type=int, default=DEFAULT_DECK_PAIRS,
                        help=f"deck pairs to play, no deck in two of them (default: {DEFAULT_DECK_PAIRS})")
    ladder.add_argument("--split", choices=SPLIT_CHOICES, default=DEFAULT_SPLIT,
                        help=f"which manifest decks the pairs come from (default: {DEFAULT_SPLIT})")
    ladder.add_argument("--pair-games", type=int, default=DEFAULT_PAIR_GAMES,
                        help="games per pair of players per deck pair, a multiple of 4: a batch for each player on "
                             f"each deck in each seat (default: {DEFAULT_PAIR_GAMES})")
    ladder.add_argument("--anchor", help="the player held at --anchor-rating (default: heuristic if it plays, "
                                         "otherwise the first player)")
    ladder.add_argument("--anchor-rating", type=float, default=0.0,
                        help="the anchor's rating, so the others read as points above or below it (default: 0)")
    ladder.add_argument("--workers", type=int, default=_cores_minus(CORES_RESERVED),
                        help="batches at once, one JVM each (default: logical cores minus 2)")
    _add_run_arguments(ladder, "runs/ladder")
    _add_optional_window(ladder)
    ladder.set_defaults(handler=_ladder, parser=ladder)

    converge = commands.add_parser("converge", help="run the manifest's four tracked matchups under two players to "
                                                    "the same width, and say for each whether the win rate moved",
                                   description="Each matchup puts one player in both seats. A matchup converged when "
                                               "the 95% Newcombe interval for the difference, after minus before, "
                                               "contains 0, and moved when it excludes 0. Players are named as for ladder. "
                                               "A matchup already in the folder under the same settings is reused, "
                                               "so the next generation's check plays only the new player.")
    converge.add_argument("before", metavar="BEFORE", help="the player before, such as onnx:gen0")
    converge.add_argument("after", metavar="AFTER", help="the player after, such as onnx:gen1")
    converge.add_argument("--manifest", help="deck manifest with the tracked matchups (default: decks/manifest.json)")
    converge.add_argument("--seed", type=int, required=True, help="every matchup's seed, the same for both players")
    _add_matchup_arguments(converge, deck_arguments=False)
    _add_optional_window(converge)
    converge.set_defaults(handler=_converge, parser=converge)

    generation = commands.add_parser("generation", help="one generation step end to end: play with the source, "
                                                        "check, convert, join, train, ablate, pair-test, export, "
                                                        "ladder, converge, and one entry in the results log",
                                     description="Each step that finishes leaves a record in the generation's "
                                                 "steps folder, and a later start skips it, so the same command "
                                                 "continues after a crash, a stop or a kill. A failed gate stops "
                                                 "the loop and is logged. See 'Generation loop' in "
                                                 "docs/forge-build.md.")
    _add_generation_arguments(generation)
    generation.set_defaults(handler=_generation, parser=generation)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
