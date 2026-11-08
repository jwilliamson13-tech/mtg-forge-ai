"""The Elo ladder: every pair of players over a fixed sample of deck pairs, both decks and both seats.

A player is an AI configuration (`simrunner.players`). For each pair of players and each deck
pair, four batches: the first player on the pair's first deck, then on its second, and each of
those with the first player in seat 1 and in seat 2. So neither the decks nor the seats can favor
a player. Both halves of a deck assignment share a seed, as a matchup round's halves do, and every
pair of players gets the same seeds for the same deck pair, so they face the same shuffles.

A batch is one JVM. The batches run side by side, as many at once as there are workers, and each
one that ends writes `result.json`. That file is how a later run knows what is already played: a
new generation adds only its own batches against the players already there, and the ratings are
refit over everything. The settings that decide which games get played are written to `plan.json`
by the first run, and a later run with other settings is refused.

The ratings are a Bradley-Terry fit on decisive games (`simrunner.rating`), with one player held
at the anchor rating. See "Elo ladder" in docs/forge-build.md.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
from itertools import combinations
from pathlib import Path

from deckpool import manifest as mf
from simrunner.batch import BatchConfig, BatchResult, SetupError, check_setup, find_jar, run_batch, write_json
from simrunner.interval import DEFAULT_CONFIDENCE
from simrunner.matchup import Tally, format_rate
from simrunner.output import DrawCause
from simrunner.players import Player
from simrunner.rating import ELO_PER_NAT, Fit, RatingError, fit, format_rating
from simrunner.report import format_report, table, tripwire_rows, worker_note
from simrunner.seeds import derive_seed
from simrunner.worker import onnx_problem, remove_folder, seat_searches

DEFAULT_DECK_PAIRS = 6
DEFAULT_PAIR_GAMES = 40  # per pair of players and deck pair: 10 in each of the four batches
DEFAULT_ANCHOR = "heuristic"
SPLIT_CHOICES = (mf.HELD_OUT, mf.TRAIN, "both")
DEFAULT_SPLIT = mf.HELD_OUT
PLAN_FILE = "plan.json"
RESULT_FILE = "ladder.json"
UNIT_RESULT = "result.json"
UNITS_FOLDER = "batches"
GAMES_FOLDER = "games"
ASSIGNMENTS = ("x1", "x2")  # the first player on the deck pair's first deck, then on its second
ORDERS = ("ab", "ba")  # the first player in seat 1, then in seat 2
POLL_S = 0.5

FINISHED = "finished"
FAILED = "failed"
STOPPED = "stopped"
COMPLETE = "complete"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class LadderConfig:
    base: BatchConfig  # run settings; run_dir is the ladder folder, batch_seed its seed, workers the batches at once
    players: tuple[Player, ...]
    manifest: Path
    deck_pairs: int = DEFAULT_DECK_PAIRS
    pair_games: int = DEFAULT_PAIR_GAMES
    split: str = DEFAULT_SPLIT
    anchor: str | None = None  # a player's label; None takes heuristic if it plays, else the first player
    anchor_rating: float = 0.0
    confidence: float = DEFAULT_CONFIDENCE

    @property
    def run_dir(self) -> Path:
        return self.base.run_dir

    @property
    def anchor_label(self) -> str:
        if self.anchor:
            return self.anchor
        labels = [player.label for player in self.players]
        return DEFAULT_ANCHOR if DEFAULT_ANCHOR in labels else labels[0]

    @property
    def batch_games(self) -> int:
        return self.pair_games // (len(ASSIGNMENTS) * len(ORDERS))


@dataclass(frozen=True)
class Unit:
    """One batch of the ladder: two players, one deck pair, who has which deck, and who sits first."""

    first: Player  # the pair's players in label order, so a pair is the same whichever way it's named
    second: Player
    pair_index: int  # from 1
    decks: tuple[str, str]  # the deck pair, as the plan has it
    assignment: str
    order: str
    games: int
    seed: int

    @property
    def folder(self) -> str:
        return f"{self.first.slug}--{self.second.slug}/pair-{self.pair_index:02d}-{self.assignment}-{self.order}"

    @property
    def first_deck(self) -> str:
        return self.decks[ASSIGNMENTS.index(self.assignment)]

    @property
    def second_deck(self) -> str:
        return self.decks[1 - ASSIGNMENTS.index(self.assignment)]

    @property
    def first_seat(self) -> int:
        return ORDERS.index(self.order) + 1

    @property
    def players(self) -> tuple[str, str]:
        return self.first.label, self.second.label


@dataclass(frozen=True)
class UnitResult:
    """What a batch's last attempt left, from the first player's side. Only `finished` counts toward a rating."""

    folder: str
    status: str
    first: str
    second: str
    first_deck: str
    second_deck: str
    first_seat: int
    seed: int
    games: int
    first_wins: int = 0
    second_wins: int = 0
    draws: dict[str, int] = field(default_factory=dict)  # by DrawCause value
    not_finished: int = 0
    tripwire_hits: list[int] = field(default_factory=list)
    digest: str | None = None
    record_digest: str | None = None
    note: str = ""
    tripwire_copies: list[int] = field(default_factory=list)  # search copies checked, per game that said

    @property
    def tally(self) -> Tally:
        return Tally(self.first_wins, self.second_wins, dict(self.draws), self.not_finished)

    @property
    def games_finished(self) -> int:
        return self.tally.finished


def unit_seed(seed: int, pair_index: int, assignment: str) -> int:
    """The same for every pair of players and both seat orders, so all of them meet the same shuffles."""
    return derive_seed(derive_seed(seed, pair_index), ASSIGNMENTS.index(assignment))


def sample_deck_pairs(splits: dict[str, str], count: int, seed: int, split: str = DEFAULT_SPLIT) -> list[tuple[str, str]]:
    """`count` deck pairs with no deck in two of them, drawn with the seed from the decks in `split`."""
    decks = sorted(deck for deck, where in splits.items() if split == "both" or where == split)
    if len(decks) < 2 * count:
        raise SetupError(f"{count} deck pairs need {2 * count} {split} decks, and the manifest has {len(decks)}")
    drawn = random.Random(f"{seed}:ladder").sample(decks, 2 * count)
    return [(drawn[2 * index], drawn[2 * index + 1]) for index in range(count)]


def plan_units(players: tuple[Player, ...], deck_pairs: list[tuple[str, str]], batch_games: int, seed: int) -> list[Unit]:
    """Every batch among these players, in playing order: deck pair by deck pair, so a stop leaves every pair some games."""
    pairs = list(combinations(sorted(players, key=lambda player: player.label), 2))
    return [Unit(first, second, index, decks, assignment, order, batch_games, unit_seed(seed, index, assignment))
            for index, decks in enumerate(deck_pairs, 1) for assignment in ASSIGNMENTS for order in ORDERS
            for first, second in pairs]


def _plan_fields(config: LadderConfig, deck_pairs: list[tuple[str, str]]) -> dict:
    """What decides which games the ladder folder holds. The players are added to as generations come."""
    base = config.base
    return {"seed": base.batch_seed, "split": config.split, "deck_pairs": [list(pair) for pair in deck_pairs],
            "pair_games": config.pair_games, "clock_s": base.clock_s, "search_budget": base.search_budget,
            "turn_cap": base.records.turn_cap, "honest": base.records.honest, "tripwire": base.tripwire}


def _hold_plan(config: LadderConfig, splits: dict[str, str]) -> list[tuple[str, str]]:
    """The deck pairs. A new folder gets a plan, and a folder with one refuses other settings or changed players."""
    path = config.run_dir / PLAN_FILE
    held = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    if held is None:
        pairs = sample_deck_pairs(splits, config.deck_pairs, config.base.batch_seed, config.split)
    else:
        pairs = [tuple(pair) for pair in held["deck_pairs"]]
        if len(pairs) != config.deck_pairs:
            raise SetupError(f"{config.run_dir} holds a ladder over {len(pairs)} deck pairs, not {config.deck_pairs}")
    fields = _plan_fields(config, pairs)
    if held is not None:
        differs = sorted(key for key in fields if held.get(key) != fields[key])
        if differs:
            raise SetupError(f"{config.run_dir} holds a ladder with other settings, in: {', '.join(differs)}")
        unknown = [deck for pair in pairs for deck in pair if deck not in splits]
        if unknown:
            raise SetupError(f"the ladder's deck pairs name decks the manifest doesn't have: {', '.join(unknown)}")
    known = dict(held.get("players", {})) if held else {}
    for player in config.players:
        before = known.get(player.label)
        if before is not None and before != player.fields():
            raise SetupError(f"{player.label} isn't the player this ladder knows by that name: it had "
                             f"{json.dumps(before)}, and now {json.dumps(player.fields())}. Give the new one "
                             "another name, or start another ladder folder")
        known[player.label] = player.fields()
    config.run_dir.mkdir(parents=True, exist_ok=True)
    write_json(path, {**fields, "players": known})
    return pairs


def unit_config(config: LadderConfig, unit: Unit, decks: dict[str, Path]) -> BatchConfig:
    seats = (unit.first.seat(decks[unit.first_deck]), unit.second.seat(decks[unit.second_deck]))
    if unit.first_seat == 2:
        seats = seats[::-1]
    base = config.base
    name = f"{base.batch_id}-{unit.first.slug}-{unit.second.slug}-p{unit.pair_index:02d}-{unit.assignment}-{unit.order}"
    return replace(base, seats=seats, games=unit.games, batch_seed=unit.seed, workers=1,
                   run_dir=config.run_dir / UNITS_FOLDER / unit.folder, batch_name=name,
                   records=replace(base.records, folder=config.run_dir / GAMES_FOLDER))


def _unit_result(unit: Unit, status: str, tally: Tally = Tally(), **rest) -> UnitResult:
    return UnitResult(unit.folder, status, unit.first.label, unit.second.label, unit.first_deck, unit.second_deck,
                      unit.first_seat, unit.seed, unit.games, tally.a_wins, tally.b_wins, dict(tally.draws),
                      tally.not_finished, **rest)


def result_of(unit: Unit, batch: BatchResult) -> UnitResult:
    """A batch's outcome from the first player's side."""
    first = unit.first_seat
    tally = Tally(batch.wins(first), batch.wins(3 - first), {cause.value: batch.draws(cause) for cause in DrawCause},
                  batch.not_finished)
    notes = [] if batch.complete else [f"{batch.finished} of {unit.games} games finished"]
    notes += [note for worker in batch.workers if (note := worker_note(batch, worker))]
    status = FINISHED if batch.clean else STOPPED if batch.stopped else FAILED
    return _unit_result(unit, status, tally, tripwire_hits=list(batch.tripwire_hits), tripwire_copies=list(batch.tripwire_copies), digest=batch.digest,
                        record_digest=batch.record_digest, note="; ".join(notes))


def play_unit(config: BatchConfig, unit: Unit) -> UnitResult | None:
    """Play one batch through Forge. None means it never started, because the window had closed."""
    batch = run_batch(config)
    if not batch.workers:
        return None
    (config.run_dir / "report.txt").write_text(format_report(batch), encoding="utf-8", newline="\n")
    return result_of(unit, batch)


PlayUnit = Callable[[BatchConfig, Unit], "UnitResult | None"]


def _play(config: BatchConfig, unit: Unit, play: PlayUnit, halted: threading.Event) -> UnitResult | None:
    """Play one batch and record how it ended. Nothing here ends the ladder."""
    window = config.window
    if halted.is_set() or (window and window.closed):
        return None
    try:
        result = play(config, unit)
        if result is None:
            return None
    except Exception as e:
        result = _unit_result(unit, FAILED, note=f"{type(e).__name__}: {e}")
    config.run_dir.mkdir(parents=True, exist_ok=True)
    write_json(config.run_dir / UNIT_RESULT, asdict(result))
    return result


def _read_results(config: LadderConfig, units: list[Unit]) -> dict[str, UnitResult]:
    results = {}
    for unit in units:
        path = config.run_dir / UNITS_FOLDER / unit.folder / UNIT_RESULT
        if path.is_file():
            results[unit.folder] = UnitResult(**json.loads(path.read_text(encoding="utf-8")))
    return results


@dataclass
class PairRecord:
    """Everything the finished batches say about one pair of players, from the first's side."""

    first: str
    second: str
    tally: Tally
    batches_finished: int
    batches_planned: int


@dataclass
class LadderResult:
    config: LadderConfig
    deck_pairs: list[tuple[str, str]]
    units: list[Unit]
    results: dict[str, UnitResult]
    unplayable: dict[str, str]  # a unit's folder, and why Forge can't seat it yet
    skipped: int = 0  # batches this start found finished
    wall_s: float = 0.0

    def _with_status(self, status: str) -> list[Unit]:
        return [unit for unit in self.units if unit.folder in self.results and self.results[unit.folder].status == status]

    @property
    def finished_units(self) -> list[Unit]:
        return self._with_status(FINISHED)

    @property
    def failed(self) -> list[Unit]:
        return self._with_status(FAILED)

    @property
    def stopped_units(self) -> list[Unit]:
        return self._with_status(STOPPED)

    @property
    def not_started(self) -> list[Unit]:
        return [unit for unit in self.units if unit.folder not in self.results and unit.folder not in self.unplayable]

    @property
    def complete(self) -> bool:
        """Every batch Forge can seat has finished."""
        return len(self.finished_units) == len(self.units) - len(self.unplayable)

    @property
    def status(self) -> str:
        return COMPLETE if self.complete else INCOMPLETE

    @property
    def pairs(self) -> list[PairRecord]:
        records = {}
        finished = {unit.folder for unit in self.finished_units}
        for unit in self.units:
            key = unit.players
            record = records.setdefault(key, PairRecord(*key, Tally(), 0, 0))
            record.batches_planned += 1
            if unit.folder in finished:
                record.tally += self.results[unit.folder].tally
                record.batches_finished += 1
        return list(records.values())

    @property
    def fit(self) -> Fit:
        labels = [player.label for player in self.config.players]
        results = {(pair.first, pair.second): (pair.tally.a_wins, pair.tally.b_wins) for pair in self.pairs}
        return fit(labels, results, self.config.anchor_label, self.config.anchor_rating, self.config.confidence)

    def player_tally(self, label: str) -> Tally:
        """Every finished game the player sat in, from its side."""
        total = Tally()
        for pair in self.pairs:
            if pair.first == label:
                total += pair.tally
            elif pair.second == label:
                tally = pair.tally
                total += Tally(tally.b_wins, tally.a_wins, tally.draws, tally.not_finished)
        return total

    @property
    def finished(self) -> int:
        return sum(self.results[unit.folder].games_finished for unit in self.finished_units)

    @property
    def tripwire_on(self) -> bool:
        return self.config.base.tripwire

    @property
    def tripwire_hits(self) -> list[int]:
        return [hits for unit in self.finished_units for hits in self.results[unit.folder].tripwire_hits]

    @property
    def tripwire_clean(self) -> bool:
        return (self.tripwire_on and self.finished > 0 and len(self.tripwire_hits) == self.finished
                and not any(self.tripwire_hits))

    @property
    def tripwire_copies(self) -> list[int]:
        return [copies for unit in self.finished_units for copies in self.results[unit.folder].tripwire_copies]

    @property
    def search_unchecked(self) -> bool:
        """As for a batch: a player searches and some game didn't say its search copies were checked."""
        return (self.tripwire_on and any(seat_searches(player.seat(Path())) for player in self.config.players)
                and len(self.tripwire_copies) < self.finished)


def check_ladder(config: LadderConfig) -> None:
    labels = [player.label for player in config.players]
    if len(labels) < 2:
        raise SetupError("a ladder needs at least two players")
    if len(set(labels)) != len(labels):
        raise SetupError(f"a player is named twice: {', '.join(sorted(label for label, n in Counter(labels).items() if n > 1))}")
    if len({player.slug for player in config.players}) != len(labels):
        raise SetupError("two players' names make the same folder name")
    if config.anchor_label not in labels:
        raise SetupError(f"the anchor {config.anchor_label} isn't one of the players: {', '.join(labels)}")
    blocks = len(ASSIGNMENTS) * len(ORDERS)
    if config.pair_games < blocks or config.pair_games % blocks:
        raise SetupError(f"the games per pair are split over {blocks} batches, so they need to be a multiple of "
                         f"{blocks}, at least {blocks}")
    if config.deck_pairs < 1:
        raise SetupError("a ladder needs at least one deck pair")
    if config.split not in SPLIT_CHOICES:
        raise SetupError(f"the split is one of {', '.join(SPLIT_CHOICES)}")
    if config.base.workers < 1:
        raise SetupError("workers must be at least 1")
    if config.base.records is None:
        raise SetupError("ladder games need records, for honest play and the result check")


def run_ladder(config: LadderConfig, play: PlayUnit = play_unit) -> LadderResult:
    """Play every batch among the players that the ladder folder doesn't have yet, then refit."""
    check_ladder(config)
    try:
        manifest = mf.load(config.manifest)
    except (OSError, mf.ManifestError) as e:
        raise SetupError(str(e))
    splits = {deck.id: deck.split for deck in manifest.decks}
    deck_pairs = _hold_plan(config, splits)
    try:
        decks = {deck: mf.deck_path(manifest, deck, mf.root_of(config.manifest)) for pair in deck_pairs for deck in pair}
    except mf.ManifestError as e:
        raise SetupError(str(e))
    units = plan_units(config.players, deck_pairs, config.batch_games, config.base.batch_seed)
    results = _read_results(config, units)
    unplayable = {}
    waiting = []
    for unit in units:
        if unit.folder in results and results[unit.folder].status == FINISHED:
            continue
        batch = unit_config(config, unit, decks)
        if problem := onnx_problem(batch.seats):
            unplayable[unit.folder] = problem
            continue
        for leftover in batch.run_dir.glob("worker-*/"):  # a runner that died leaves these, and a batch won't start beside them
            remove_folder(leftover)
        check_setup(batch)
        waiting.append((unit, batch))
    if waiting:
        find_jar(config.base.forge_root)
    if config.base.window:
        config.base.window.wait_for_start()

    ladder = LadderResult(config, deck_pairs, units, results, unplayable, len(units) - len(waiting) - len(unplayable))
    write_json(config.run_dir / RESULT_FILE, ladder_fields(ladder))
    began = time.monotonic()
    halted = threading.Event()
    with ThreadPoolExecutor(max_workers=config.base.workers) as executor:
        playing = {executor.submit(_play, batch, unit, play, halted) for unit, batch in waiting}
        while playing:
            try:
                ended, playing = wait(playing, timeout=POLL_S, return_when=FIRST_COMPLETED)
            except KeyboardInterrupt:
                halted.set()  # the JVMs took the same Ctrl-C, so what is running ends soon, and nothing new starts
                continue
            for future in ended:
                if result := future.result():
                    results[result.folder] = result
            if ended:
                ladder.wall_s = time.monotonic() - began
                write_json(config.run_dir / RESULT_FILE, ladder_fields(ladder))
    ladder.wall_s = time.monotonic() - began
    write_json(config.run_dir / RESULT_FILE, ladder_fields(ladder))
    return ladder


def _interval_fields(tally: Tally, confidence: float) -> dict | None:
    interval = tally.interval(confidence)
    return interval and {"rate": interval.rate, "low": interval.low, "high": interval.high, "games": interval.games}


def _tally_fields(tally: Tally) -> dict:
    return {"wins": tally.a_wins, "losses": tally.b_wins, "draws": tally.draws, "not_finished": tally.not_finished}


def ladder_fields(ladder: LadderResult) -> dict:
    """`ladder.json`: the ratings, the head-to-head records behind them, and what is left to play."""
    config = ladder.config
    base = config.base
    try:
        rated = ladder.fit
    except RatingError as e:
        rated = Fit(config.anchor_label, config.anchor_rating, config.confidence, (), str(e))
    by_label = {player.label: player for player in config.players}
    order = [rating.player for rating in rated.ratings] or [player.label for player in config.players]
    players = []
    for rank, label in enumerate(order, 1):
        rating = rated.of(label) if rated.ratings else None
        tally = ladder.player_tally(label)
        players.append({
            **by_label[label].fields(),
            "rank": rank if rating else None,
            "rating": rating and rating.rating,
            "low": rating and rating.low,
            "high": rating and rating.high,
            "decisive_games": tally.decisive,
            **_tally_fields(tally),
        })
    return {
        "status": ladder.status,
        "seed": base.batch_seed,
        "split": config.split,
        "deck_pairs": [list(pair) for pair in ladder.deck_pairs],
        "pair_games": config.pair_games,
        "batch_games": config.batch_games,
        "workers": base.workers,
        "clock_s": base.clock_s,
        "search_budget": base.search_budget,
        "turn_cap": base.records.turn_cap,
        "honest": base.records.honest,
        "tripwire": base.tripwire,
        "confidence": config.confidence,
        "anchor": {"player": config.anchor_label, "rating": config.anchor_rating},
        "method": ("Bradley-Terry maximum likelihood over decisive games, on the Elo scale "
                   f"({ELO_PER_NAT:.2f} points per unit of log-odds), with profile-likelihood intervals"),
        "ratings_problem": rated.problem,
        "players": players,
        "pairs": [{"first": pair.first, "second": pair.second, **_tally_fields(pair.tally),
                   "interval": _interval_fields(pair.tally, config.confidence),
                   "batches_finished": pair.batches_finished, "batches_planned": pair.batches_planned}
                  for pair in ladder.pairs],
        "games_finished": ladder.finished,
        "batches": {"planned": len(ladder.units), "finished": len(ladder.finished_units),
                    "skipped_on_this_start": ladder.skipped,
                    "failed": [unit.folder for unit in ladder.failed],
                    "stopped": [unit.folder for unit in ladder.stopped_units],
                    "not_started": [unit.folder for unit in ladder.not_started],
                    "unplayable": [{"batch": folder, "why": why} for folder, why in sorted(ladder.unplayable.items())]},
        "tripwire_hits": sum(ladder.tripwire_hits),
        "tripwire_clean": ladder.tripwire_clean,
        "wall_s": round(ladder.wall_s, 1),
    }


def _pair_rate(tally: Tally, confidence: float) -> str:
    interval = tally.interval(confidence)
    return format_rate(interval) if interval else "no decisive games"


def _unplayable_pairs(ladder: LadderResult) -> list[tuple[str, str, str]]:
    seen = {}
    for unit in ladder.units:
        if unit.folder in ladder.unplayable:
            seen.setdefault(unit.players, ladder.unplayable[unit.folder])
    return [(*players, why) for players, why in seen.items()]


def format_ladder_report(ladder: LadderResult) -> str:
    """Ratings with their intervals and game counts, then each pair's head-to-head rate with its interval."""
    config = ladder.config
    try:
        rated = ladder.fit
    except RatingError as e:
        rated = Fit(config.anchor_label, config.anchor_rating, config.confidence, (), str(e))
    unfinished = len(ladder.units) - len(ladder.unplayable) - len(ladder.finished_units)
    status = "complete" if ladder.complete else f"incomplete, {unfinished} batches not finished"
    if ladder.unplayable:
        status += f", {len(ladder.unplayable)} that Forge can't seat yet"
    split = {"both": "decks from both splits", mf.HELD_OUT: "held-out decks", mf.TRAIN: "train decks"}[config.split]
    rows = [
        ("status", status),
        ("players", ", ".join(player.label for player in config.players)),
        ("anchor", f"{config.anchor_label} at {config.anchor_rating:g}"),
        ("ratings", "Bradley-Terry fit over decisive games, Elo scale"),
        ("rating interval", f"profile likelihood, {config.confidence * 100:g}% confidence"),
        ("deck pairs", f"{len(ladder.deck_pairs)}, {split}, drawn with seed {config.base.batch_seed}"),
        ("games per player pair", f"{config.pair_games} per deck pair: each player on each deck, in each seat, "
                                  f"{config.batch_games} game{'s' if config.batch_games != 1 else ''} a batch"),
        ("batches", f"{len(ladder.finished_units)} of {len(ladder.units)} finished, {ladder.skipped} of them "
                    "before this start"),
        ("games finished", str(ladder.finished)),
        *tripwire_rows(ladder),
        ("wall time", f"{ladder.wall_s:.1f} s"),
    ]
    text = table(rows)
    if rated.ratings:
        lines = [("rank", "player", "rating", "decisive games", "wins", "losses", "draws")]
        for rank, rating in enumerate(rated.ratings, 1):
            tally = ladder.player_tally(rating.player)
            lines.append((str(rank), rating.player, format_rating(rating, rated), str(tally.decisive),
                          str(tally.a_wins), str(tally.b_wins), str(tally.draw_count)))
        text += "\n" + table(lines)
    else:
        text += f"\nno ratings: {rated.problem}\n"
    lines = [("player", "against", "win rate", "decisive games", "draws", "batches")]
    for pair in ladder.pairs:
        lines.append((pair.first, pair.second, _pair_rate(pair.tally, config.confidence), str(pair.tally.decisive),
                      str(pair.tally.draw_count), f"{pair.batches_finished} of {pair.batches_planned}"))
    text += "\n" + table(lines)
    if ladder.unplayable:
        lines = [("player", "against", "not played, because")]
        lines += _unplayable_pairs(ladder)
        text += "\n" + table(lines)
    problems = ladder.failed + ladder.stopped_units
    if problems:
        lines = [("batch", "status", "note")]
        lines += [(unit.folder, ladder.results[unit.folder].status, ladder.results[unit.folder].note) for unit in problems]
        text += "\n" + table(lines)
    return text
