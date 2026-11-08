"""Elo ratings from head-to-head results: a Bradley-Terry fit with profile-likelihood intervals.

The model: player i beats player j with probability 1 / (1 + 10^((R_j - R_i) / 400)), which is
Elo's curve. A difference of 100 points is a 64% expected score, 200 points 76%. Only decisive
games count, as in a matchup's win rate, so a draw moves no rating.

The ratings are the maximum-likelihood fit. One player, the anchor, is held at a fixed rating,
because the likelihood only sees differences. Every other rating is then a difference from the
anchor, and its interval says how well the games pin that difference down.

The interval is the profile-likelihood one: the ratings for this player at which the best fit of
everyone else stays within the chi-squared cut-off (3.84 at 95%) of the overall best. With two
players it is the likelihood-ratio interval for a binomial share, put on the Elo scale. It is
lopsided where the data are, which a symmetric interval from the curvature would not be.

The fit has no finite answer when the players split into two groups and nobody in one group ever
beat anybody in the other (Ford, 1957), a group that only ever won or a player with no games. The fit
then refuses and says which group, rather than give a rating that grows without limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from simrunner.interval import DEFAULT_CONFIDENCE, z_score

ELO_PER_NAT = 400 / math.log(10)  # Elo points per unit of log-odds
TOLERANCE = 1e-10
MAX_STEPS = 200
BISECTIONS = 80
FAR = 60.0  # in log-odds, about 10,000 Elo points: an interval end past this is reported as having none


class RatingError(ValueError):
    """Results that can't be fitted as asked."""


@dataclass(frozen=True)
class Rating:
    player: str
    rating: float
    low: float | None  # None for the anchor, or an end the data never reach
    high: float | None
    wins: float  # decisive games only
    losses: float

    @property
    def decisive(self) -> float:
        return self.wins + self.losses


@dataclass(frozen=True)
class Fit:
    anchor: str
    anchor_rating: float
    confidence: float
    ratings: tuple[Rating, ...]  # best first; empty when the fit has no finite answer
    problem: str | None = None  # why there are no ratings

    def of(self, player: str) -> Rating:
        for rating in self.ratings:
            if rating.player == player:
                return rating
        raise KeyError(player)


def expected_score(rating: float, other: float) -> float:
    """Elo's curve: how often a player rated `rating` beats one rated `other`."""
    return 1 / (1 + 10 ** ((other - rating) / 400))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def _log_sigmoid(x: float) -> float:
    return -math.log1p(math.exp(-x)) if x >= 0 else x - math.log1p(math.exp(x))


class _Games:
    """Decisive games between players by index: `wins[i][j]` is how often i beat j."""

    def __init__(self, players: list[str], results: dict[tuple[str, str], tuple[float, float]]):
        self.players = players
        index = {player: number for number, player in enumerate(players)}
        size = len(players)
        self.wins = [[0.0] * size for _ in range(size)]
        for (first, second), (first_wins, second_wins) in results.items():
            if first == second:
                raise RatingError(f"{first} can't play itself")
            for player in (first, second):
                if player not in index:
                    raise RatingError(f"results for {player}, who isn't one of the players")
            if first_wins < 0 or second_wins < 0:
                raise RatingError(f"{first} against {second}: a negative count")
            self.wins[index[first]][index[second]] += first_wins
            self.wins[index[second]][index[first]] += second_wins

    def log_likelihood(self, theta: list[float]) -> float:
        total = 0.0
        size = len(theta)
        for i in range(size):
            for j in range(size):
                if self.wins[i][j]:
                    total += self.wins[i][j] * _log_sigmoid(theta[i] - theta[j])
        return total

    def gradient_and_curvature(self, theta: list[float]) -> tuple[list[float], list[list[float]]]:
        """The gradient, and the negated Hessian, which is positive definite once one player is fixed."""
        size = len(theta)
        gradient = [0.0] * size
        curvature = [[0.0] * size for _ in range(size)]
        for i in range(size):
            for j in range(i + 1, size):
                games = self.wins[i][j] + self.wins[j][i]
                if not games:
                    continue
                p = _sigmoid(theta[i] - theta[j])
                gradient[i] += self.wins[i][j] - games * p
                gradient[j] -= self.wins[i][j] - games * p
                weight = games * p * (1 - p)
                curvature[i][i] += weight
                curvature[j][j] += weight
                curvature[i][j] -= weight
                curvature[j][i] -= weight
        return gradient, curvature

    def unbeaten_group(self) -> list[str] | None:
        """A group of players nobody outside it ever beat, or None when there is none (the fit exists)."""
        size = len(self.players)
        for start in range(size):
            # Everyone `start` beat, everyone they beat, and so on.
            reached = {start}
            frontier = [start]
            while frontier:
                i = frontier.pop()
                for j in range(size):
                    if j not in reached and self.wins[i][j] > 0:
                        reached.add(j)
                        frontier.append(j)
            if len(reached) < size:
                # Nobody reached beat anybody left out, so the left-out never lost to anybody else.
                return [self.players[i] for i in range(size) if i not in reached]
        return None


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting, for the handful of players a ladder has."""
    size = len(vector)
    rows = [list(matrix[i]) + [vector[i]] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-300:
            raise RatingError("the results don't pin the ratings down")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for row in range(column + 1, size):
            factor = rows[row][column] / rows[column][column]
            for k in range(column, size + 1):
                rows[row][k] -= factor * rows[column][k]
    answer = [0.0] * size
    for row in reversed(range(size)):
        answer[row] = (rows[row][size] - sum(rows[row][k] * answer[k] for k in range(row + 1, size))) / rows[row][row]
    return answer


def _maximize(games: _Games, theta: list[float], free: list[int]) -> tuple[list[float], float]:
    """Newton's method over the `free` players, the rest held where `theta` has them."""
    theta = list(theta)
    best = games.log_likelihood(theta)
    if not free:
        return theta, best
    for _ in range(MAX_STEPS):
        gradient, curvature = games.gradient_and_curvature(theta)
        step = _solve([[curvature[i][j] for j in free] for i in free], [gradient[i] for i in free])
        scale = 1.0
        while True:
            trial = list(theta)
            for i, delta in zip(free, step):
                trial[i] += scale * delta
            value = games.log_likelihood(trial)
            if value >= best - 1e-12:
                break
            scale /= 2
            if scale < 1e-9:
                return theta, best  # no step helps: this is the top, to rounding
        theta, best = trial, max(value, best)
        if max(abs(scale * delta) for delta in step) < TOLERANCE:
            break
    return theta, best


def _profile_end(games: _Games, theta: list[float], best: float, player: int, free: list[int], cutoff: float,
                 direction: int, guess: float) -> float | None:
    """Where the profile likelihood for `player` falls `cutoff / 2` below `best`, on one side of the fit."""
    others = [i for i in free if i != player]
    center = theta[player]

    def drop(value: float) -> float:
        start = list(theta)
        start[player] = value
        return 2 * (best - _maximize(games, start, others)[1])

    near, far = 0.0, max(guess, 1e-3)
    while drop(center + direction * far) < cutoff:
        near, far = far, far * 2
        if far > FAR:
            return None
    for _ in range(BISECTIONS):
        middle = (near + far) / 2
        if drop(center + direction * middle) < cutoff:
            near = middle
        else:
            far = middle
    return center + direction * (near + far) / 2


def fit(players: list[str], results: dict[tuple[str, str], tuple[float, float]], anchor: str,
        anchor_rating: float = 0.0, confidence: float = DEFAULT_CONFIDENCE) -> Fit:
    """Elo ratings for `players` from `results`, which maps (first, second) to (first's wins, second's wins).

    Counts may be fractional. A pair may appear in either order, or both, and adds up.
    """
    if len(set(players)) != len(players):
        raise RatingError("a player is listed twice")
    if anchor not in players:
        raise RatingError(f"the anchor {anchor} isn't one of the players")
    if len(players) < 2:
        raise RatingError("a rating needs at least two players")
    games = _Games(list(players), results)
    size = len(players)
    totals = [(sum(games.wins[i]), sum(games.wins[j][i] for j in range(size))) for i in range(size)]
    idle = [players[i] for i in range(size) if not sum(totals[i])]
    if idle:
        return Fit(anchor, anchor_rating, confidence, (), f"no finite ratings: no decisive games for {', '.join(idle)}")
    group = games.unbeaten_group()
    if group is not None:
        return Fit(anchor, anchor_rating, confidence, (), f"no finite ratings: nobody else ever beat {', '.join(group)}")
    fixed = players.index(anchor)
    free = [i for i in range(size) if i != fixed]
    theta, best = _maximize(games, [0.0] * size, free)
    # The curvature's inverse gives each rating's rough spread, a starting step for the interval search.
    _, curvature = games.gradient_and_curvature(theta)
    reduced = [[curvature[i][j] for j in free] for i in free]
    cutoff = z_score(confidence) ** 2  # the chi-squared quantile with one degree of freedom
    ratings = []
    for i in range(size):
        low = high = None
        if i != fixed:
            unit = [1.0 if j == i else 0.0 for j in free]
            spread = math.sqrt(max(_solve(reduced, unit)[free.index(i)], 1e-12))
            ends = [_profile_end(games, theta, best, i, free, cutoff, direction, spread) for direction in (-1, 1)]
            low, high = (None if end is None else anchor_rating + end * ELO_PER_NAT for end in ends)
        ratings.append(Rating(players[i], anchor_rating + theta[i] * ELO_PER_NAT, low, high, *totals[i]))
    ratings.sort(key=lambda rating: -rating.rating)
    return Fit(anchor, anchor_rating, confidence, tuple(ratings))


def format_elo(value: float, anchor_rating: float = 0.0) -> str:
    """Signed when the anchor is 0, so a rating reads as points above or below it."""
    return f"{value:+.0f}" if anchor_rating == 0 else f"{value:.0f}"


def format_rating(rating: Rating, fit: Fit) -> str:
    """The only way a rating gets printed: with its interval, or as the anchor."""
    if rating.player == fit.anchor:
        return f"{fit.anchor_rating:g} (the anchor)"
    value = format_elo(rating.rating, fit.anchor_rating)
    low = "no lower end" if rating.low is None else format_elo(rating.low, fit.anchor_rating)
    high = "no upper end" if rating.high is None else format_elo(rating.high, fit.anchor_rating)
    return f"{value} ({low} to {high})"
