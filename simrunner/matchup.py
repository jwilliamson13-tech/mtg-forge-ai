"""A matchup: two decks, played in seat-swapped rounds until deck A's win rate is known well enough.

Side A is the first deck with its seat settings (AI profile, simulation mode), side B the second.
The settings travel with the deck when the seats swap. A round is two batches with the same seed
and the same game count: A in seat 1, then A in seat 2. The same seed and worker count give both
batches the same worker seeds, so the two halves of a round draw from the same random streams.

After each round the interval over every decisive game so far is compared with the target. The
run stops when it is at or under the target, or when the game budget is spent. A run that spends
its budget refuses to give a rate and says how many more games the current estimate needs.

Draws of every kind leave the win rate: it is A's wins over decisive games, and the draws are
counted beside it. See "Matchups" in docs/forge-build.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from simrunner.batch import BatchConfig, BatchResult, SetupError, check_setup, find_jar, run_batch, write_json
from simrunner.interval import DEFAULT_CONFIDENCE, Interval, games_needed, wilson
from simrunner.output import DrawCause
from simrunner.report import describe_seat, format_report, table, tripwire_rows, worker_note
from simrunner.seeds import derive_seed
from simrunner.worker import Seat, any_seat_searches

DEFAULT_WIDTH = 5.0  # points either side of the rate. HANDOFF.md: about 400 games at even odds
DEFAULT_ROUND_GAMES = 100
DEFAULT_MAX_GAMES = 1000
RESULT_FILE = "matchup.json"
ROUNDS_FOLDER = "rounds"
ORDERS = ("ab", "ba")  # A in seat 1, then A in seat 2

RUNNING = "running"
MET = "met"
BUDGET_SPENT = "budget spent"
FAILED = "failed"
STOPPED = "stopped"


@dataclass(frozen=True)
class Tally:
    """Games from side A's point of view."""

    a_wins: int = 0
    b_wins: int = 0
    draws: dict[str, int] = field(default_factory=dict)  # by DrawCause value
    not_finished: int = 0

    @property
    def decisive(self) -> int:
        return self.a_wins + self.b_wins

    @property
    def draw_count(self) -> int:
        return sum(self.draws.values())

    @property
    def finished(self) -> int:
        return self.decisive + self.draw_count

    def __add__(self, other: Tally) -> Tally:
        draws = {cause.value: self.draws.get(cause.value, 0) + other.draws.get(cause.value, 0) for cause in DrawCause}
        return Tally(self.a_wins + other.a_wins, self.b_wins + other.b_wins, draws,
                     self.not_finished + other.not_finished)

    def interval(self, confidence: float = DEFAULT_CONFIDENCE) -> Interval | None:
        """A's win rate over decisive games. None when there are none."""
        return wilson(self.a_wins, self.decisive, confidence) if self.decisive else None


def tally_of(batch: BatchResult, a_seat: int) -> Tally:
    """What a batch says about side A, which sat in seat `a_seat` for all of it."""
    return Tally(batch.wins(a_seat), batch.wins(3 - a_seat), {cause.value: batch.draws(cause) for cause in DrawCause},
                 batch.not_finished)


@dataclass(frozen=True)
class MatchupConfig:
    batch: BatchConfig  # seats are (A, B), batch_seed is the matchup's seed, run_dir its folder; games is unused
    width: float = DEFAULT_WIDTH  # the target half-width, in points
    round_games: int = DEFAULT_ROUND_GAMES  # half with A in each seat
    max_games: int = DEFAULT_MAX_GAMES
    confidence: float = DEFAULT_CONFIDENCE


@dataclass(frozen=True)
class OrderResult:
    """One batch of a round: A in seat `a_seat`."""

    a_seat: int
    batch_id: str
    tally: Tally
    clean: bool
    stopped: bool
    note: str
    digest: str
    record_digest: str | None
    tripwire_hits: tuple[int, ...]
    wall_s: float
    tripwire_copies: tuple[int, ...] = ()  # search copies checked, per game that said


@dataclass(frozen=True)
class RoundResult:
    index: int  # from 1
    seed: int
    games: int  # attempted, over both orders
    orders: tuple[OrderResult, ...]  # one when the first batch ended the run

    @property
    def tally(self) -> Tally:
        return sum((order.tally for order in self.orders), Tally())

    @property
    def clean(self) -> bool:
        return len(self.orders) == len(ORDERS) and all(order.clean for order in self.orders)

    @property
    def stopped(self) -> bool:
        return any(order.stopped for order in self.orders)


def round_seed(seed: int, index: int) -> int:
    return derive_seed(seed, index)


def round_sizes(round_games: int, max_games: int) -> list[int]:
    """Games per round, all of them `round_games` but a last one cut to what the budget has left.

    Every round splits evenly between the two orders, so a cut round loses an odd game.
    """
    full, left = divmod(max_games, round_games)
    return [round_games] * full + ([left - left % 2] if left >= 2 else [])


def round_name(index: int, order: str) -> str:
    return f"round-{index:03d}-{order}"


def round_config(config: MatchupConfig, index: int, order: str, games: int) -> BatchConfig:
    """The batch for one half of a round. B sits in seat 1 for the `ba` half."""
    base = config.batch
    seats = base.seats if order == ORDERS[0] else base.seats[::-1]
    records = replace(base.records, folder=base.games_folder) if base.records else None
    return replace(base, seats=seats, games=games // 2, batch_seed=round_seed(base.batch_seed, index),
                   run_dir=base.run_dir / ROUNDS_FOLDER / round_name(index, order),
                   batch_name=f"{base.batch_id}-r{index:03d}-{order}", records=records)


def _order_result(order: str, batch: BatchResult) -> OrderResult:
    a_seat = ORDERS.index(order) + 1
    notes = [] if batch.complete else [f"{batch.finished} of {batch.attempted} games finished"]
    notes += [note for worker in batch.workers if (note := worker_note(batch, worker))]
    return OrderResult(a_seat, batch.config.batch_id, tally_of(batch, a_seat), batch.clean, batch.stopped,
                       "; ".join(notes), batch.digest, batch.record_digest, tuple(batch.tripwire_hits), batch.wall_s,
                       tuple(batch.tripwire_copies))


def play_round(config: MatchupConfig, index: int, games: int) -> RoundResult:
    """Play both orders of one round, the second only if the first was clean."""
    orders = []
    for order in ORDERS:
        batch_config = round_config(config, index, order, games)
        batch = run_batch(batch_config)
        (batch_config.run_dir / "report.txt").write_text(format_report(batch), encoding="utf-8", newline="\n")
        orders.append(_order_result(order, batch))
        if not batch.clean or batch.stopped:
            break
    return RoundResult(index, round_seed(config.batch.batch_seed, index), games, tuple(orders))


@dataclass
class MatchupResult:
    config: MatchupConfig
    rounds: list[RoundResult]
    status: str = RUNNING

    @property
    def tally(self) -> Tally:
        return sum((rnd.tally for rnd in self.rounds), Tally())

    def seat_tally(self, a_seat: int) -> Tally:
        """Every game with A in seat `a_seat`."""
        return sum((order.tally for rnd in self.rounds for order in rnd.orders if order.a_seat == a_seat), Tally())

    @property
    def interval(self) -> Interval | None:
        return self.tally.interval(self.config.confidence)

    @property
    def met(self) -> bool:
        interval = self.interval
        return interval is not None and interval.half_width * 100 <= self.config.width

    @property
    def attempted(self) -> int:
        return sum(order.tally.finished + order.tally.not_finished for rnd in self.rounds for order in rnd.orders)

    @property
    def finished(self) -> int:
        return self.tally.finished

    @property
    def wall_s(self) -> float:
        return sum(order.wall_s for rnd in self.rounds for order in rnd.orders)

    @property
    def more_games_needed(self) -> int | None:
        """Games past these that the current estimate needs to meet the target. None with no decisive game."""
        tally = self.tally
        if not tally.decisive:
            return None
        decisive = games_needed(tally.a_wins / tally.decisive, self.config.width / 100, self.config.confidence)
        # Draws keep their share of the games to come.
        return math.ceil(max(0, decisive - tally.decisive) * tally.finished / tally.decisive)

    @property
    def failed_order(self) -> tuple[RoundResult, OrderResult] | None:
        for rnd in self.rounds:
            for order in rnd.orders:
                if not order.clean:
                    return rnd, order
        return None

    @property
    def tripwire_on(self) -> bool:
        return self.config.batch.tripwire

    @property
    def tripwire_hits(self) -> list[int]:
        return [hits for rnd in self.rounds for order in rnd.orders for hits in order.tripwire_hits]

    @property
    def tripwire_clean(self) -> bool:
        return (self.tripwire_on and self.finished > 0 and len(self.tripwire_hits) == self.finished
                and not any(self.tripwire_hits))

    @property
    def tripwire_copies(self) -> list[int]:
        return [copies for rnd in self.rounds for order in rnd.orders for copies in order.tripwire_copies]

    @property
    def search_unchecked(self) -> bool:
        """As for a batch: a seat searches and some game didn't say its search copies were checked."""
        return (self.tripwire_on and any_seat_searches(self.config.batch.seats)
                and len(self.tripwire_copies) < self.finished)


def check_matchup(config: MatchupConfig) -> None:
    if config.width <= 0:
        raise SetupError("the target width has to be above 0 points")
    if config.round_games < 2 or config.round_games % 2:
        raise SetupError("a round needs an even number of games, at least 2, half with deck A in each seat")
    if config.max_games < 2:
        raise SetupError("the game budget has to be at least 2 games")
    if not 0 < config.confidence < 1:
        raise SetupError("the confidence level is between 0 and 1")
    run_dir = config.batch.run_dir
    if (run_dir / RESULT_FILE).exists() or (run_dir / ROUNDS_FOLDER).exists():
        raise SetupError(f"the run folder already holds a matchup: {run_dir}")


def run_matchup(config: MatchupConfig) -> MatchupResult:
    """Play rounds until the interval meets the target, the budget is spent, or a batch fails."""
    check_matchup(config)
    sizes = round_sizes(config.round_games, config.max_games)
    check_setup(round_config(config, 1, ORDERS[0], sizes[0]))
    find_jar(config.batch.forge_root)
    config.batch.run_dir.mkdir(parents=True, exist_ok=True)
    result = MatchupResult(config, [])
    for index, games in enumerate(sizes, 1):
        write_json(config.batch.run_dir / RESULT_FILE, result_fields(result))
        played = play_round(config, index, games)
        result.rounds.append(played)
        if played.stopped:
            result.status = STOPPED
        elif not played.clean:
            result.status = FAILED
        elif result.met:
            result.status = MET
        if result.status != RUNNING:
            break
    else:
        result.status = BUDGET_SPENT
    write_json(config.batch.run_dir / RESULT_FILE, result_fields(result))
    return result


def _side(seat: Seat) -> dict:
    return {"deck": seat.deck.stem, "file": str(seat.deck), "ai_profile": seat.ai_profile, "sim_mode": seat.sim_mode}


def _tally_fields(tally: Tally) -> dict:
    return {"a_wins": tally.a_wins, "b_wins": tally.b_wins, "draws": tally.draws, "not_finished": tally.not_finished}


def result_fields(result: MatchupResult) -> dict:
    """Everything a later results log needs. The interval is there only when the target was met."""
    config = result.config
    batch = config.batch
    interval = result.interval if result.status == MET else None
    return {
        "status": result.status,
        "side_a": _side(batch.seats[0]),
        "side_b": _side(batch.seats[1]),
        "seed": batch.batch_seed,
        "workers": batch.workers,
        "width_points": config.width,
        "confidence": config.confidence,
        "round_games": config.round_games,
        "max_games": config.max_games,
        "clock_s": batch.clock_s,
        "search_budget": batch.search_budget,
        "records": batch.records is not None,
        "honest": bool(batch.records and batch.records.honest),
        "turn_cap": batch.records.turn_cap if batch.records else None,
        "tripwire": batch.tripwire,
        "games_attempted": result.attempted,
        "games_finished": result.finished,
        **_tally_fields(result.tally),
        "interval": interval and {"rate": interval.rate, "low": interval.low, "high": interval.high,
                                  "games": interval.games},
        "more_games_needed": result.more_games_needed if result.status in (BUDGET_SPENT, STOPPED) else None,
        "rounds": [{"index": rnd.index, "seed": rnd.seed, "games": rnd.games,
                    "orders": [{"a_seat": order.a_seat, "batch_id": order.batch_id, **_tally_fields(order.tally),
                                "clean": order.clean, "digest": order.digest, "record_digest": order.record_digest}
                               for order in rnd.orders]} for rnd in result.rounds],
    }


def format_rate(interval: Interval) -> str:
    """The only way a rate gets printed: with its interval."""
    return f"{interval.rate:.1%} ({interval.low:.1%} to {interval.high:.1%})"


def _rate_or_none(interval: Interval | None) -> str:
    return format_rate(interval) if interval else "no decisive games"


def _points(half_width: float) -> str:
    return f"{half_width * 100:.1f} points"


def _status(result: MatchupResult) -> str:
    config = result.config
    interval = result.interval
    target = _points(config.width / 100)
    where = (f"the half-width is {_points(interval.half_width)}, over the target of {target}" if interval
             else "no game was decisive")
    if result.status == MET:
        return f"met, a half-width of {_points(interval.half_width)} is at or under the target of {target}"
    if result.status == BUDGET_SPENT:
        return f"refused, the {config.max_games}-game budget is spent and {where}"
    if result.status == STOPPED:
        return f"refused, stopped at the hard stop and {where}"
    if result.status == FAILED:
        rnd, order = result.failed_order
        return f"failed, round {rnd.index} with deck A in seat {order.a_seat} was not clean: {order.note or 'see its report'}"
    return result.status


def _games_still_needed(result: MatchupResult) -> str:
    more = result.more_games_needed
    if more is None:
        return "not known, with no decisive games"
    rounds = math.ceil(more / result.config.round_games)
    return (f"about {more} more at the current estimate, {rounds} more round{'s' if rounds != 1 else ''} "
            f"of {result.config.round_games}")


def format_matchup_report(result: MatchupResult) -> str:
    """The report. A rate shows only when the target was met, and never without its interval."""
    config = result.config
    tally = result.tally
    rows = [
        ("status", _status(result)),
        ("deck A", describe_seat(config.batch.seats[0])),
        ("deck B", describe_seat(config.batch.seats[1])),
    ]
    if result.status == MET:
        rows += [
            ("deck A win rate", format_rate(result.interval)),
            ("  with A in seat 1", _rate_or_none(result.seat_tally(1).interval(config.confidence))),
            ("  with A in seat 2", _rate_or_none(result.seat_tally(2).interval(config.confidence))),
        ]
    elif result.status in (BUDGET_SPENT, STOPPED):
        rows.append(("games still needed", _games_still_needed(result)))
    rows += [
        ("interval", f"Wilson score, {config.confidence * 100:g}% confidence, over decisive games"),
        ("target", f"a half-width of {_points(config.width / 100)}"),
        ("seed", str(config.batch.batch_seed)),
        ("rounds", f"{len(result.rounds)}, each half with deck A in seat 1 and half in seat 2"),
        ("games per round", str(config.round_games)),
        ("game budget", str(config.max_games)),
        ("games attempted", str(result.attempted)),
        ("games finished", str(result.finished)),
        ("decisive games", str(tally.decisive)),
        ("deck A wins", str(tally.a_wins)),
        ("deck B wins", str(tally.b_wins)),
        ("draws", str(tally.draw_count)),
    ]
    rows += [(f"  by {cause.value}", str(tally.draws.get(cause.value, 0))) for cause in DrawCause]
    rows += [
        *tripwire_rows(result),
        ("wall time", f"{result.wall_s:.1f} s"),
    ]
    lines = [("round", "seed", "A seat", "games", "A wins", "B wins", "draws", "half-width after", "note")]
    running = Tally()
    for rnd in result.rounds:
        for order in rnd.orders:
            running += order.tally
            interval = running.interval(config.confidence)
            lines.append((str(rnd.index), str(rnd.seed), str(order.a_seat), str(order.tally.finished),
                          str(order.tally.a_wins), str(order.tally.b_wins), str(order.tally.draw_count),
                          _points(interval.half_width) if interval else "-", order.note))
    return table(rows) + "\n" + table(lines)
