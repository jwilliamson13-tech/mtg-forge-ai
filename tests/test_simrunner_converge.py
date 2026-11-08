"""The convergence check: verdicts on fixed intervals, and runs over the tracked matchups against `tests/stub_java.py`.

The players here are `full` and `full:random`. The stub's `DECK&eval=random` subject lets one deck
play differently under the random evaluator, so a tracked matchup can move or stay put.
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from simrunner.__main__ import main
from simrunner.batch import BatchConfig, SetupError
from simrunner.converge import (CONVERGED, INCOMPLETE, MOVED, NO_VERDICT, RESULT_FILE, ConvergeConfig, Estimate,
                                Verdict, format_converge_report, overall, run_converge)
from simrunner.interval import wilson
from simrunner.matchup import BUDGET_SPENT, MET, MatchupConfig
from simrunner.players import parse_player
from simrunner.window import Window
from simrunner.worker import RecordPlan, Seat

STUB = Path(__file__).parent / "stub_java.py"
RATE = r"\d+\.\d% \(\d+\.\d% to \d+\.\d%\)"
POINTS = r"[+-]\d+\.\d points \([+-]\d+\.\d to [+-]\d+\.\d points\)"
CONFIDENCE_LABEL = r"\d+(\.\d+)?% confidence"
TRACKED = [["held0", "held1"], ["held0", "held2"], ["held3", "held4"], ["held3", "held5"]]
# held0 wins 9 in 10 under the random evaluator and half its games otherwise: slots 1 and 2 move.
MOVES_TWO = "held0&eval=random:WWWWWWWWWL;seat1:WL"


def estimate(player, wins, games, status=MET, more=None):
    interval = wilson(wins, games) if status == MET else None
    return Estimate(player, Path(player), status, interval, wins, games - wins, 0, games, more, False)


def verdict(before, after):
    return Verdict(1, "a", "b", before, after)


# Verdicts on fixed tallies


def test_overlapping_intervals_converge():
    # 200 of 400 is 45.1% to 54.9%, and 215 of 400 is 48.8% to 58.6%.
    result = verdict(estimate("gen0", 200, 400), estimate("gen1", 215, 400))
    assert result.verdict == CONVERGED and result.why == "the intervals overlap"
    assert result.difference.estimate == pytest.approx(0.0375)


def test_intervals_apart_have_moved():
    # 200 of 400 against 250 of 400: 45.1% to 54.9% and 57.6% to 67.2%.
    result = verdict(estimate("gen0", 200, 400), estimate("gen1", 250, 400))
    assert result.verdict == MOVED and result.why == "the intervals don't overlap"
    assert result.difference.excludes_zero and result.difference.estimate == pytest.approx(0.125)


def test_overlap_is_looser_than_the_difference_interval():
    # 200 of 400 against 230 of 400 overlap, but the difference's interval leaves out 0.
    result = verdict(estimate("gen0", 200, 400), estimate("gen1", 230, 400))
    assert result.verdict == CONVERGED
    assert result.difference.excludes_zero


def test_a_side_without_a_rate_gives_no_verdict_and_says_why():
    spent = estimate("gen1", 100, 200, status=BUDGET_SPENT, more=181)
    result = verdict(estimate("gen0", 200, 400), spent)
    assert result.verdict == NO_VERDICT and result.difference is None
    assert result.why == "gen1 spent its budget, about 181 more games needed"
    assert verdict(estimate("gen0", 200, 400), None).why == "not played, the check stopped first"


def test_any_move_is_a_move_and_converged_needs_all_four():
    converged, moved, missing = (verdict(estimate("a", 200, 400), estimate("b", 210, 400)),
                                 verdict(estimate("a", 200, 400), estimate("b", 260, 400)),
                                 verdict(estimate("a", 200, 400), None))
    assert overall([converged] * 4) == CONVERGED
    assert overall([converged, converged, converged, missing]) == INCOMPLETE
    assert overall([converged, moved, missing, missing]) == MOVED


# Runs through the stub


@pytest.fixture
def manifest(tmp_path):
    """Six held-out decks with the four tracked matchups filled, and two train decks."""
    root = tmp_path / "pool"
    (root / "decks" / "forge").mkdir(parents=True)
    decks = []
    for deck, split in [(f"held{n}", "held_out") for n in range(6)] + [(f"train{n}", "train") for n in range(2)]:
        (root / "decks" / "forge" / f"{deck}.dck").write_text(f"[metadata]\nName={deck}\n[Main]\n60 Island\n",
                                                             encoding="utf-8")
        decks.append({"id": deck, "file": f"decks/forge/{deck}.dck", "source": "test", "archetype": deck, "split": split})
    path = root / "decks" / "manifest.json"
    path.write_text(json.dumps({"version": 1, "decks": decks, "tracked_matchups": TRACKED}), encoding="utf-8")
    return path


@pytest.fixture
def converge(forge, manifest, tmp_path):
    def build(before="full", after="full:random", width=10.0, round_games=100, max_games=1000, seed=42, **overrides):
        settings = dict(seats=(Seat(Path()), Seat(Path())), games=0, batch_seed=seed, workers=1, forge_root=forge,
                        run_dir=tmp_path / "convergence", clock_s=60, java=(sys.executable, str(STUB)),
                        records=RecordPlan())
        return ConvergeConfig(MatchupConfig(BatchConfig(**{**settings, **overrides}), width, round_games, max_games),
                              parse_player(before), parse_player(after), manifest)

    return build


def pattern(monkeypatch, text):
    monkeypatch.setenv("STUB_PATTERN", text)


def assert_no_bare_percentage(report):
    rest = re.sub(CONFIDENCE_LABEL, "", re.sub(RATE, "", report))
    assert "%" not in rest, rest


def test_each_tracked_matchup_gets_both_estimates_and_a_verdict(converge, monkeypatch, tmp_path):
    pattern(monkeypatch, MOVES_TWO)
    result = run_converge(converge())
    assert [v.verdict for v in result.verdicts] == [MOVED, MOVED, CONVERGED, CONVERGED]
    assert result.status == MOVED and result.counts == {CONVERGED: 2, MOVED: 2, NO_VERDICT: 0}
    first = result.verdicts[0]
    assert (first.deck_a, first.deck_b) == ("held0", "held1")
    assert first.before.interval == wilson(50, 100) and first.after.interval == wilson(90, 100)
    assert first.difference.estimate == pytest.approx(0.4)

    saved = json.loads((tmp_path / "convergence" / RESULT_FILE).read_text(encoding="utf-8"))
    assert saved["status"] == MOVED and saved["rule"] == "converged when the two Wilson intervals overlap"
    assert saved["before"]["label"] == "full" and saved["after"]["label"] == "full:random"
    row = saved["matchups"][0]
    assert (row["slot"], row["deck_a"], row["deck_b"], row["verdict"]) == (1, "held0", "held1", MOVED)
    assert row["before"]["interval"]["rate"] == 0.5 and row["after"]["interval"]["rate"] == 0.9
    assert row["difference"]["excludes_zero"]
    assert [row["verdict"] for row in saved["matchups"]] == [MOVED, MOVED, CONVERGED, CONVERGED]
    # One folder per player and matchup, each a plain matchup folder.
    assert (tmp_path / "convergence" / "full" / "held0--held1" / "matchup.json").is_file()
    assert (tmp_path / "convergence" / "full-random" / "held3--held5" / "rounds").is_dir()


def test_both_seats_play_the_one_player(converge, monkeypatch, tmp_path):
    pattern(monkeypatch, "seat1:WL")
    launches = tmp_path / "launches"
    launches.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(launches))
    run_converge(converge())
    evals = {tuple(argv[argv.index("-eval") + 1:argv.index("-eval") + 3]) if "-eval" in argv else None
             for argv in (json.loads(path.read_text(encoding="utf-8"))["argv"] for path in launches.glob("*.json"))}
    assert evals == {None, ("random", "random")}


def test_the_report_lists_both_estimates_and_a_verdict_with_no_bare_percentage(converge, monkeypatch):
    pattern(monkeypatch, MOVES_TWO)
    report = format_converge_report(run_converge(converge()))
    assert re.search(r"status\s+moved: 2 converged, 2 moved, 0 with no verdict", report)
    assert re.search(r"1\s+held0\s+held1\s+50\.0% \(40\.4% to 59\.6%\)\s+90\.0% \(82\.6% to 94\.5%\)\s+"
                     + POINTS + r"\s+moved", report)
    assert re.search(r"3\s+held3\s+held4\s+50\.0% \(.*\)\s+50\.0% \(.*\)\s+\+0\.0 points .*converged", report)
    assert re.search(r"rule\s+converged when the two 95% confidence Wilson intervals overlap", report)
    assert_no_bare_percentage(report)


def test_the_next_generation_reuses_the_matchups_already_played(converge, monkeypatch, tmp_path):
    pattern(monkeypatch, MOVES_TWO)
    run_converge(converge("full", "full:random"))
    launches = tmp_path / "launches"
    launches.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(launches))
    result = run_converge(converge("full:random", "hybrid:random"))
    assert all(v.before.reused and not v.after.reused for v in result.verdicts)
    played = {Path(json.loads(path.read_text(encoding="utf-8"))["cwd"]).parents[3].name for path in launches.glob("*.json")}
    assert played == {"hybrid-random"}
    assert "reused" in format_converge_report(result)


def test_a_folder_played_under_other_settings_is_refused(converge, monkeypatch):
    pattern(monkeypatch, "seat1:WL")
    run_converge(converge())
    with pytest.raises(SetupError, match=r"holds full's matchup under other settings, in: width_points"):
        run_converge(converge(width=8.0))
    with pytest.raises(SetupError, match=r"under other settings, in: seed"):
        run_converge(converge(seed=43))


def test_a_spent_budget_gives_no_verdict_and_exits_1(forge, manifest, tmp_path, monkeypatch, capsys):
    pattern(monkeypatch, "seat1:WL")
    args = ["converge", "full", "full:random", "--manifest", str(manifest), "--seed", "4", "--workers", "1",
            "--max-games", "100", "--clock", "60", "--forge", str(forge), "--run-dir", str(tmp_path / "convergence"),
            "--java", sys.executable, str(STUB)]
    assert main(args) == 1
    captured = capsys.readouterr()
    assert re.search(r"status\s+incomplete: 0 converged, 0 moved, 4 with no verdict", captured.out)
    assert "full spent its budget, about 281 more games needed" in captured.out
    assert "4 matchups with no verdict" in captured.err
    assert not re.search(RATE, captured.out)
    assert_no_bare_percentage(captured.out)


def test_the_command_exits_0_with_every_verdict_in(forge, manifest, tmp_path, monkeypatch, capsys):
    pattern(monkeypatch, MOVES_TWO)
    args = ["converge", "full", "full:random", "--manifest", str(manifest), "--seed", "4", "--workers", "1",
            "--width", "10", "--clock", "60", "--forge", str(forge), "--run-dir", str(tmp_path / "convergence"),
            "--java", sys.executable, str(STUB)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "convergence" / "report.txt").read_text(encoding="utf-8") == out
    assert re.search(r"status\s+moved: 2 converged, 2 moved", out)


def test_a_matchup_cut_off_by_the_hard_stop_is_set_aside_and_played_again(converge, monkeypatch, tmp_path):
    pattern(monkeypatch, "seat1:WL")
    now = datetime.now()
    stopped = run_converge(converge(window=Window(now - timedelta(minutes=1), now + timedelta(seconds=1))))
    assert stopped.status == INCOMPLETE
    assert stopped.verdicts[0].before.status == "stopped" and stopped.verdicts[1].before is None
    again = run_converge(converge())
    assert again.status == CONVERGED and len(again.set_aside) == 1
    assert again.set_aside[0].name == "held0--held1-unfinished-1" and again.set_aside[0].is_dir()


def test_empty_tracked_matchup_slots_are_refused(converge, manifest, tmp_path):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["tracked_matchups"] = [TRACKED[0], None, TRACKED[2], None]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SetupError, match="tracked-matchup slots 2, 4 of 4 are empty"):
        run_converge(converge())
    assert not (tmp_path / "convergence").exists()


def test_the_same_player_twice_is_refused(converge):
    with pytest.raises(SetupError, match="both are full"):
        run_converge(converge("full", "full:forge"))


def test_bad_matchup_settings_fail_before_anything_launches(converge, tmp_path):
    with pytest.raises(SetupError, match="even number"):
        run_converge(converge(round_games=99))
    assert not (tmp_path / "convergence" / "full").exists()
