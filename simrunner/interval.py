"""The Wilson score interval for a win rate, and how many games it takes to narrow one.

A width here is a half-width: the interval reaches that far on each side, on average.
HANDOFF.md's "5 points, about 400 games" and "2 points, about 2,400" only work out that way.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import NormalDist

DEFAULT_CONFIDENCE = 0.95


def z_score(confidence: float) -> float:
    """The two-sided normal quantile, 1.96 for 0.95."""
    if not 0 < confidence < 1:
        raise ValueError(f"a confidence level is between 0 and 1, not {confidence}")
    return NormalDist().inv_cdf(0.5 + confidence / 2)


@dataclass(frozen=True)
class Interval:
    rate: float  # the observed share, wins over games
    low: float
    high: float
    games: int
    confidence: float = DEFAULT_CONFIDENCE

    @property
    def half_width(self) -> float:
        """Half the distance from low to high. Wilson is lopsided away from 0.5, so this is the average reach."""
        return (self.high - self.low) / 2

    def overlaps(self, other: Interval) -> bool:
        return self.low <= other.high and other.low <= self.high


def _center_and_reach(rate: float, games: float, z: float) -> tuple[float, float]:
    shrink = 1 + z * z / games
    center = (rate + z * z / (2 * games)) / shrink
    reach = z / shrink * sqrt(rate * (1 - rate) / games + z * z / (4 * games * games))
    return center, reach


def wilson(wins: float, games: int, confidence: float = DEFAULT_CONFIDENCE) -> Interval:
    """The Wilson score interval for `wins` out of `games`. Wins may be fractional."""
    if games < 1:
        raise ValueError("an interval needs at least one game")
    if not 0 <= wins <= games:
        raise ValueError(f"{wins} wins out of {games} games")
    rate = wins / games
    center, reach = _center_and_reach(rate, games, z_score(confidence))
    return Interval(rate, max(0.0, center - reach), min(1.0, center + reach), games, confidence)


def games_needed(rate: float, half_width: float, confidence: float = DEFAULT_CONFIDENCE) -> int:
    """The fewest games whose interval at `rate` is `half_width` or narrower.

    It assumes the rate holds as games are added, which is what "at the current estimate" means.
    """
    if not 0 <= rate <= 1:
        raise ValueError(f"a rate is between 0 and 1, not {rate}")
    if half_width <= 0:
        raise ValueError("the half-width has to be above 0")
    z = z_score(confidence)

    def narrow_enough(games: int) -> bool:
        return _center_and_reach(rate, games, z)[1] <= half_width

    high = 1
    while not narrow_enough(high):
        high *= 2
    low = high // 2  # too few, or 0
    while high - low > 1:
        middle = (low + high) // 2
        if narrow_enough(middle):
            high = middle
        else:
            low = middle
    return high
