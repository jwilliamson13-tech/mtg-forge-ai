"""Matchups: the interval arithmetic against published values, the runs against `tests/stub_java.py`.

STUB_PATTERN fixes each game's outcome by its number in the JVM, so these runs know their
counts in advance. Each test picks round sizes that split evenly over the workers.
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from simrunner.__main__ import build_parser, main
from simrunner.batch import BatchConfig, SetupError
from simrunner.interval import Interval, games_needed, wilson, z_score
from simrunner.matchup import (BUDGET_SPENT, FAILED, MET, STOPPED, MatchupConfig, Tally, format_matchup_report,
                               format_rate, round_seed, round_sizes, run_matchup)
from simrunner.output import HEADER_LINE
from simrunner.seeds import derive_seed
from simrunner.window import Window
from simrunner.worker import RecordPlan, Seat

STUB = Path(__file__).parent / "stub_java.py"
RATE = r"\d+\.\d% \(\d+\.\d% to \d+\.\d%\)"
CONFIDENCE_LABEL = r"\d+(\.\d+)?% confidence"


# Newcombe, "Two-sided confidence intervals for the single proportion: comparison of seven
# methods", Statistics in Medicine 17 (1998) 857-872, Table I, method 3 (Wilson score, no continuity correction).
@pytest.mark.parametrize("wins, games, low, high", [
    (81, 263, 0.2553, 0.3662),
    (15, 148, 0.0624, 0.1605),
    (0, 20, 0.0, 0.1611),
    (1, 29, 0.0061, 0.1718),
])
def test_the_wilson_interval_matches_published_values(wins, games, low, high):
    interval = wilson(wins, games)
    assert interval.low == pytest.approx(low, abs=5e-5)
    assert interval.high == pytest.approx(high, abs=5e-5)
    assert interval.rate == wins / games


def test_the_interval_stays_inside_zero_and_one():
    assert wilson(0, 20).low == 0.0
    assert wilson(20, 20).high == 1.0
    assert wilson(20, 20).low == pytest.approx(1 - 0.1611, abs=5e-5)


def test_the_confidence_level_sets_the_z_score():
    assert z_score(0.95) == pytest.approx(1.959964, abs=1e-6)
    assert z_score(0.99) == pytest.approx(2.575829, abs=1e-6)
    assert wilson(50, 100, 0.99).half_width > wilson(50, 100).half_width


def test_an_interval_needs_a_game_and_a_possible_count():
    with pytest.raises(ValueError):
        wilson(0, 0)
    with pytest.raises(ValueError):
        wilson(5, 4)


def test_overlap_is_symmetric_and_touching_counts():
    one = Interval(0.5, 0.40, 0.60, 100)
    assert one.overlaps(Interval(0.7, 0.60, 0.80, 100)) and Interval(0.7, 0.60, 0.80, 100).overlaps(one)
    assert not one.overlaps(Interval(0.7, 0.61, 0.80, 100))


def test_the_games_needed_match_the_handoff_figures():
    # HANDOFF.md: 5 points is about 400 games per arm at even odds, 2 points about 2,400.
    assert games_needed(0.5, 0.05) == 381
    assert games_needed(0.5, 0.02) == 2398


@pytest.mark.parametrize("rate, half_width", [(0.5, 0.05), (0.6, 0.05), (0.9, 0.03), (0.5, 0.1), (1.0, 0.05)])
def test_the_games_needed_are_the_fewest_that_meet_the_width(rate, half_width):
    games = games_needed(rate, half_width)
    assert wilson(rate * games, games).half_width <= half_width
    if games > 1:
        assert wilson(rate * (games - 1), games - 1).half_width > half_width


def test_a_round_budget_cuts_the_last_round_to_an_even_count():
    assert round_sizes(100, 1000) == [100] * 10
    assert round_sizes(100, 250) == [100, 100, 50]
    assert round_sizes(100, 251) == [100, 100, 50]
    assert round_sizes(100, 203) == [100, 100, 2]
    assert round_sizes(100, 201) == [100, 100]
    assert round_sizes(100, 40) == [40]


def test_tallies_add_up_by_cause():
    one = Tally(3, 2, {"rules": 1}, 0)
    two = Tally(1, 4, {"turn_cap": 2}, 1)
    total = one + two
    assert (total.a_wins, total.b_wins, total.draw_count, total.not_finished) == (4, 6, 3, 1)
    assert total.draws["rules"] == 1 and total.draws["turn_cap"] == 2
    assert total.decisive == 10 and total.finished == 13
    assert Tally().interval() is None


@pytest.fixture
def decks(tmp_path):
    folder = tmp_path / "decks"
    folder.mkdir()
    one = folder / "burn.dck"
    two = folder / "merfolk.dck"
    one.write_text("[metadata]\nName=Burn\n[Main]\n60 Mountain\n", encoding="utf-8")
    two.write_text("[metadata]\nName=Merfolk\n[Main]\n60 Island\n", encoding="utf-8")
    return one, two


@pytest.fixture
def matchup(forge, decks, tmp_path):
    def build(width=5.0, round_games=100, max_games=1000, workers=1, seed=7, **overrides):
        settings = dict(
            seats=(Seat(decks[0], None, "heuristic"), Seat(decks[1], None, "heuristic")),
            games=0, batch_seed=seed, workers=workers, forge_root=forge, run_dir=tmp_path / "run", clock_s=60,
            java=(sys.executable, str(STUB)), records=RecordPlan(),
        )
        return MatchupConfig(BatchConfig(**{**settings, **overrides}), width, round_games, max_games)

    return build


def pattern(monkeypatch, text):
    monkeypatch.setenv("STUB_PATTERN", text)


def headers(run_dir):
    """Each batch's deck order and worker seeds, read from what Forge printed."""
    seen = {}
    for log in sorted((run_dir / "rounds").glob("*/worker-*.out.log")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if match := HEADER_LINE.match(line):
                decks = re.findall(r"Ai\(\d\)-(\w+)", line)
                seen.setdefault(log.parent.name, []).append((tuple(decks), int(match[1])))
    return seen


def assert_no_bare_percentage(report):
    """Every percentage is a rate with its interval, or the interval's confidence level."""
    rest = re.sub(CONFIDENCE_LABEL, "", re.sub(RATE, "", report))
    assert "%" not in rest, rest


def test_the_run_stops_at_the_first_round_that_meets_the_width(matchup, monkeypatch):
    pattern(monkeypatch, "Burn:WWWLL")  # 60%: 300 games give a half-width of 5.5 points, 400 give 4.8
    result = run_matchup(matchup())
    assert result.status == MET
    assert len(result.rounds) == 4 and result.attempted == 400
    assert (result.tally.a_wins, result.tally.b_wins) == (240, 160)
    assert result.interval == wilson(240, 400)
    report = format_matchup_report(result)
    assert re.search(r"deck A win rate\s+60\.0% \(55\.1% to 64\.7%\)", report)
    assert re.search(r"status\s+met, a half-width of 4\.8 points is at or under the target of 5\.0 points", report)
    assert_no_bare_percentage(report)


def test_the_width_is_a_parameter(matchup, monkeypatch):
    pattern(monkeypatch, "Burn:WL")  # 50%: 100 games give a half-width of 9.6 points
    result = run_matchup(matchup(width=10))
    assert result.status == MET and len(result.rounds) == 1


def test_the_default_width_is_five_points():
    args = build_parser().parse_args(["matchup", "a.dck", "b.dck", "--seed", "1"])
    assert args.width == 5.0
    assert MatchupConfig(None).width == 5.0


def test_each_round_plays_both_seat_orders_on_the_same_seeds(matchup, monkeypatch, tmp_path):
    pattern(monkeypatch, "Burn:WWWLL")
    run_matchup(matchup(seed=7, workers=2))  # 25 games per worker, five rounds of the pattern
    seen = headers(tmp_path / "run")
    assert sorted(seen) == [f"round-{index:03d}-{order}" for index in range(1, 5) for order in ("ab", "ba")]
    for index in range(1, 5):
        seeds = [derive_seed(round_seed(7, index), worker) for worker in (0, 1)]
        assert seen[f"round-{index:03d}-ab"] == [(("Burn", "Merfolk"), seed) for seed in seeds]
        assert seen[f"round-{index:03d}-ba"] == [(("Merfolk", "Burn"), seed) for seed in seeds]
    assert len({round_seed(7, index) for index in range(1, 5)}) == 4


def test_seat_swapping_cancels_a_seat_edge(matchup, monkeypatch):
    pattern(monkeypatch, "seat1:WWWLL")  # seat 1 wins 60% whatever deck sits there
    result = run_matchup(matchup())
    assert result.tally.a_wins == result.tally.b_wins
    assert result.seat_tally(1).a_wins == 0.6 * result.seat_tally(1).decisive
    assert result.seat_tally(2).a_wins == 0.4 * result.seat_tally(2).decisive
    report = format_matchup_report(result)
    assert re.search(r"deck A win rate\s+50\.0%", report)
    assert re.search(r"with A in seat 1\s+60\.0% \(", report) and re.search(r"with A in seat 2\s+40\.0% \(", report)


def test_settings_travel_with_their_deck_when_seats_swap(matchup, monkeypatch, decks, tmp_path):
    pattern(monkeypatch, "Burn:WL")
    launches = tmp_path / "launches"
    launches.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(launches))
    run_matchup(matchup(width=10, seats=(Seat(decks[0], "Reckless", "full"), Seat(decks[1], "Default", "heuristic"))))
    seen = {}
    for path in launches.glob("*.json"):
        launch = json.loads(path.read_text(encoding="utf-8"))
        argv = launch["argv"]
        seen[Path(launch["cwd"]).parent.name] = (argv[argv.index("-a") + 1:argv.index("-a") + 3],
                                                 argv[argv.index("-sim") + 1:argv.index("-sim") + 3])
    assert seen == {"round-001-ab": (["Reckless", "Default"], ["full", "heuristic"]),
                    "round-001-ba": (["Default", "Reckless"], ["heuristic", "full"])}
    saved = json.loads((tmp_path / "run" / "matchup.json").read_text(encoding="utf-8"))
    assert saved["side_a"]["ai_profile"] == "Reckless" and saved["side_b"]["sim_mode"] == "heuristic"


def test_a_spent_budget_refuses_and_names_the_games_still_needed(matchup, monkeypatch, tmp_path, capsys):
    pattern(monkeypatch, "Burn:WL")
    result = run_matchup(matchup(max_games=200))
    assert result.status == BUDGET_SPENT and result.attempted == 200
    assert not result.met
    # 50% needs 381 decisive games for 5 points, and there are 200.
    assert result.more_games_needed == 181
    report = format_matchup_report(result)
    assert "win rate" not in report
    assert re.search(r"games still needed\s+about 181 more at the current estimate, 2 more rounds of 100", report)
    assert re.search(r"refused, the 200-game budget is spent and the half-width is 6\.9 points, over the target of 5\.0 points",
                     report)
    assert not re.search(RATE, report)
    assert_no_bare_percentage(report)
    saved = json.loads((tmp_path / "run" / "matchup.json").read_text(encoding="utf-8"))
    assert saved["status"] == BUDGET_SPENT and saved["interval"] is None and saved["more_games_needed"] == 181


def test_draws_leave_the_rate_and_are_counted_beside_it(matchup, monkeypatch):
    pattern(monkeypatch, "Burn:WLWLDWWLLT")  # per 10 games: 4 wins, 4 losses, a rules draw and a turn-cap draw
    result = run_matchup(matchup(max_games=200))
    tally = result.tally
    assert (tally.a_wins, tally.b_wins, tally.draws["rules"], tally.draws["turn_cap"]) == (80, 80, 20, 20)
    assert tally.decisive == 160 and tally.finished == 200
    # 381 decisive games at 50%, 221 more decisive ones, and a fifth of games to come are draws.
    assert result.more_games_needed == 277
    report = format_matchup_report(result)
    assert re.search(r"decisive games\s+160", report) and re.search(r"draws\s+40", report)
    assert re.search(r"by rules\s+20", report) and re.search(r"by turn_cap\s+20", report)
    assert_no_bare_percentage(report)


def test_a_met_rate_counts_decisive_games_only(matchup, monkeypatch):
    pattern(monkeypatch, "Burn:WWWLD")  # 75% of decisive games
    result = run_matchup(matchup(width=10))
    assert result.status == MET
    assert result.interval == wilson(60, 80)
    assert "75.0% (" in format_matchup_report(result)


def test_a_batch_that_is_not_clean_fails_the_run_with_no_rate(matchup, monkeypatch, decks):
    decks[1].write_text("[Main]\n60 Island\n", encoding="utf-8")  # won't load
    result = run_matchup(matchup())
    assert result.status == FAILED
    assert len(result.rounds) == 1 and len(result.rounds[0].orders) == 1
    report = format_matchup_report(result)
    assert re.search(r"failed, round 1 with deck A in seat 1 was not clean: 0 of 50 games finished", report)
    assert "Could not load deck" in report
    assert "win rate" not in report and "games still needed" not in report
    assert_no_bare_percentage(report)


def test_the_hard_stop_refuses(matchup, monkeypatch):
    pattern(monkeypatch, "Burn:WL")
    now = datetime.now()
    result = run_matchup(matchup(window=Window(now - timedelta(minutes=1), now + timedelta(seconds=1))))
    assert result.status == STOPPED
    report = format_matchup_report(result)
    assert "stopped at the hard stop" in report and "win rate" not in report
    assert re.search(r"refused, stopped at the hard stop and no game was decisive", report)
    assert re.search(r"games still needed\s+not known, with no decisive games", report)
    assert_no_bare_percentage(report)


@pytest.mark.parametrize("settings, message", [
    (dict(width=0), "above 0 points"),
    (dict(round_games=99), "even number"),
    (dict(max_games=1), "at least 2 games"),
])
def test_bad_settings_fail_before_anything_launches(matchup, settings, message, tmp_path):
    with pytest.raises(SetupError, match=message):
        run_matchup(matchup(**settings))
    assert not (tmp_path / "run" / "rounds").exists()


def test_a_run_folder_with_a_matchup_in_it_is_refused(matchup, monkeypatch):
    pattern(monkeypatch, "Burn:WL")
    run_matchup(matchup(width=10))
    with pytest.raises(SetupError, match="already holds a matchup"):
        run_matchup(matchup(width=10))


def test_the_command_exits_by_whether_the_target_was_met(forge, decks, tmp_path, monkeypatch, capsys):
    pattern(monkeypatch, "Burn:WL")

    def args(run, *more):
        return ["matchup", str(decks[0]), str(decks[1]), "--seed", "3", "--workers", "1", "--clock", "60",
                "--forge", str(forge), "--run-dir", str(tmp_path / run), "--java", sys.executable, str(STUB), *more]

    assert main(args("met", "--width", "10")) == 0
    out = capsys.readouterr().out
    assert re.search(r"deck A win rate\s+50\.0% \(40\.4% to 59\.6%\)", out)
    assert (tmp_path / "met" / "report.txt").read_text(encoding="utf-8") == out
    assert len(list((tmp_path / "met" / "games").glob("*.jsonl.gz"))) == 100

    assert main(args("short", "--max-games", "100")) == 1
    captured = capsys.readouterr()
    assert "refused" in captured.out and "no win rate (budget spent)" in captured.err
    assert_no_bare_percentage(captured.out)

    with pytest.raises(SystemExit):
        main(args("odd", "--round-games", "7"))
    assert "even number" in capsys.readouterr().err


def test_format_rate_always_carries_the_interval():
    assert format_rate(wilson(81, 263)) == "30.8% (25.5% to 36.6%)"
