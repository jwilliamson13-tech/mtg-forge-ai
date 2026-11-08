"""Elo ratings from a Bradley-Terry fit: closed forms, known strengths, and the interval's coverage."""

import math
import random

import pytest

from simrunner.interval import z_score
from simrunner.rating import ELO_PER_NAT, RatingError, expected_score, fit, format_rating


def binomial_lr_interval(wins, games, confidence=0.95):
    """The likelihood-ratio interval for a binomial share, by bisection, written apart from the fit."""
    rate = wins / games
    cutoff = z_score(confidence) ** 2

    def drop(p):
        return 2 * (wins * math.log(rate / p) + (games - wins) * math.log((1 - rate) / (1 - p)))

    def bisect(low, high):  # drop(low) and drop(high) sit on either side of the cut-off
        for _ in range(200):
            middle = (low + high) / 2
            if (drop(middle) < cutoff) == (drop(low) < cutoff):
                low = middle
            else:
                high = middle
        return (low + high) / 2

    return bisect(1e-12, rate), bisect(rate, 1 - 1e-12)


def elo_of(p):
    return 400 * math.log10(p / (1 - p))


def expected_results(ratings, games):
    """Every pair's wins exactly at their expected share, which the fit has to give back exactly."""
    names = list(ratings)
    results = {}
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            p = expected_score(ratings[first], ratings[second])
            results[(first, second)] = (games * p, games * (1 - p))
    return results


def test_elo_scale_is_400_points_per_tenfold_odds():
    assert expected_score(400, 0) == pytest.approx(10 / 11)
    assert expected_score(100, 0) == pytest.approx(0.640, abs=5e-4)
    assert ELO_PER_NAT == pytest.approx(173.7178, abs=1e-4)


def test_two_players_rate_at_the_log_odds_of_their_record():
    rated = fit(["heuristic", "full"], {("full", "heuristic"): (75, 25)}, "heuristic")
    full = rated.of("full")
    assert full.rating == pytest.approx(400 * math.log10(3))  # 190.85
    assert (full.wins, full.losses, full.decisive) == (75, 25, 100)
    assert rated.of("heuristic").rating == 0 and rated.of("heuristic").low is None
    assert [rating.player for rating in rated.ratings] == ["full", "heuristic"]


@pytest.mark.parametrize("wins, games", [(75, 100), (50, 100), (81, 263), (3, 20), (199, 200)])
def test_with_two_players_the_interval_is_the_binomial_likelihood_ratio_interval(wins, games):
    rated = fit(["a", "b"], {("b", "a"): (wins, games - wins)}, "a")
    low, high = binomial_lr_interval(wins, games)
    assert rated.of("b").low == pytest.approx(elo_of(low), abs=1e-6)
    assert rated.of("b").high == pytest.approx(elo_of(high), abs=1e-6)


def test_an_even_record_gives_a_symmetric_interval_and_a_lopsided_one_does_not():
    even = fit(["a", "b"], {("a", "b"): (50, 50)}, "a").of("b")
    assert even.rating == pytest.approx(0, abs=1e-9)
    assert even.high == pytest.approx(-even.low)
    lopsided = fit(["a", "b"], {("a", "b"): (10, 90)}, "a").of("b")
    assert lopsided.high - lopsided.rating > lopsided.rating - lopsided.low


def test_known_strengths_come_back_exactly_from_expected_results():
    truth = {"heuristic": 0, "full": 100, "onnx:gen0": 250, "full:random": -80}
    rated = fit(list(truth), expected_results(truth, 1000), "heuristic")
    for player, rating in truth.items():
        assert rated.of(player).rating == pytest.approx(rating, abs=1e-6)
    assert [rating.player for rating in rated.ratings] == ["onnx:gen0", "full", "heuristic", "full:random"]
    for rating in rated.ratings:
        if rating.player != "heuristic":
            assert rating.low < rating.rating < rating.high


def test_a_chain_of_three_to_one_records_spaces_ratings_evenly():
    # Each step up wins 3 of 4, and two steps 9 of 10, exactly what the Elo curve says.
    results = {("full", "heuristic"): (15, 5), ("heuristic", "full:random"): (15, 5), ("full", "full:random"): (18, 2)}
    rated = fit(["heuristic", "full", "full:random"], results, "heuristic")
    step = 400 * math.log10(3)
    assert rated.of("full").rating == pytest.approx(step)
    assert rated.of("full:random").rating == pytest.approx(-step)


def test_the_anchor_rating_shifts_every_rating_and_end_alike():
    truth = {"a": 0, "b": 120, "c": -60}
    results = expected_results(truth, 200)
    at_zero = fit(list(truth), results, "a")
    at_1500 = fit(list(truth), results, "a", anchor_rating=1500)
    for player in truth:
        assert at_1500.of(player).rating == pytest.approx(at_zero.of(player).rating + 1500)
    assert at_1500.of("b").low == pytest.approx(at_zero.of("b").low + 1500)
    other_anchor = fit(list(truth), results, "b")
    assert other_anchor.of("a").rating == pytest.approx(-120, abs=1e-6)


def test_a_pair_named_both_ways_adds_up():
    split = fit(["a", "b"], {("a", "b"): (30, 10), ("b", "a"): (10, 30)}, "a")
    whole = fit(["a", "b"], {("a", "b"): (60, 20)}, "a")
    assert split.of("b").rating == pytest.approx(whole.of("b").rating)
    assert split.of("b").low == pytest.approx(whole.of("b").low)


def test_simulated_games_land_the_true_ratings_inside_their_intervals():
    rng = random.Random(41)
    truth = {"heuristic": 0, "full": 60, "onnx:gen0": 180, "onnx:gen1": 240}
    names = list(truth)
    results = {}
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            p = expected_score(truth[first], truth[second])
            wins = sum(rng.random() < p for _ in range(240))
            results[(first, second)] = (wins, 240 - wins)
    rated = fit(names, results, "heuristic")
    for player in names[1:]:
        assert rated.of(player).low < truth[player] < rated.of(player).high
    assert [rating.player for rating in rated.ratings] == ["onnx:gen1", "onnx:gen0", "full", "heuristic"]


def test_the_95_percent_interval_covers_the_truth_about_95_percent_of_the_time():
    rng = random.Random(6)
    truth = {"a": 0, "b": 100, "c": -50}
    names = list(truth)
    covered = trials = 0
    for _ in range(300):
        results = {}
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                p = expected_score(truth[first], truth[second])
                wins = sum(rng.random() < p for _ in range(60))
                results[(first, second)] = (wins, 60 - wins)
        rated = fit(names, results, "a")
        for player in ("b", "c"):
            trials += 1
            covered += rated.of(player).low <= truth[player] <= rated.of(player).high
    assert 0.92 <= covered / trials <= 0.98


def test_a_player_nobody_beat_has_no_finite_rating():
    rated = fit(["heuristic", "full", "onnx:gen0"],
                {("onnx:gen0", "heuristic"): (20, 0), ("onnx:gen0", "full"): (20, 0), ("full", "heuristic"): (12, 8)},
                "heuristic")
    assert rated.ratings == ()
    assert rated.problem == "no finite ratings: nobody else ever beat onnx:gen0"


def test_a_group_that_only_lost_to_the_rest_has_no_finite_rating():
    rated = fit(["a", "b", "c"], {("a", "b"): (5, 5), ("a", "c"): (4, 0), ("b", "c"): (3, 0)}, "a")
    assert rated.problem == "no finite ratings: nobody else ever beat a, b"


def test_a_player_with_no_decisive_games_has_no_rating():
    rated = fit(["a", "b", "c"], {("a", "b"): (5, 5)}, "a")
    assert rated.problem == "no finite ratings: no decisive games for c"


@pytest.mark.parametrize("players, results, anchor, message", [
    (["a"], {}, "a", "at least two players"),
    (["a", "a"], {}, "a", "listed twice"),
    (["a", "b"], {}, "c", "isn't one of the players"),
    (["a", "b"], {("a", "z"): (1, 1)}, "a", "isn't one of the players"),
    (["a", "b"], {("a", "a"): (1, 1)}, "a", "can't play itself"),
    (["a", "b"], {("a", "b"): (-1, 1)}, "a", "negative"),
])
def test_results_that_cannot_be_fitted_are_refused(players, results, anchor, message):
    with pytest.raises(RatingError, match=message):
        fit(players, results, anchor)


def test_a_rating_prints_with_its_interval_or_as_the_anchor():
    rated = fit(["heuristic", "full"], {("full", "heuristic"): (75, 25)}, "heuristic")
    assert format_rating(rated.of("full"), rated) == "+191 (+115 to +273)"
    assert format_rating(rated.of("heuristic"), rated) == "0 (the anchor)"
    shifted = fit(["heuristic", "full"], {("full", "heuristic"): (75, 25)}, "heuristic", anchor_rating=1500)
    assert format_rating(shifted.of("full"), shifted) == "1691 (1615 to 1773)"
