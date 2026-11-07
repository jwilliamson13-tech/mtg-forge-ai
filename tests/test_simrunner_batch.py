"""Batches run against `tests/stub_java.py`, so nothing here needs Java or a built Forge."""

import gzip
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from simrunner import window as window_module
from simrunner import worker as worker_module
from simrunner.__main__ import main
from simrunner.batch import BatchConfig, BatchResult, SetupError, run_batch
from simrunner.output import AiLimits, DrawCause, GameResult
from simrunner.report import format_report, format_summary, format_workers
from simrunner.seeds import derive_seed
from simrunner.window import Window
from simrunner.worker import DEFAULT_SEARCH_BUDGET, RecordPlan, Seat, WorkerPlan, WorkerResult, sim_args

STUB = Path(__file__).parent / "stub_java.py"
REPO = Path(__file__).parents[1]


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
def records(tmp_path, monkeypatch):
    """Return a function that reads back what each stub run saw, keyed by seed."""
    folder = tmp_path / "records"
    folder.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(folder))

    def read():
        return {int(path.stem): json.loads(path.read_text(encoding="utf-8")) for path in folder.glob("*.json")}

    return read


@pytest.fixture
def config(forge, decks, tmp_path):
    def build(games=6, workers=2, seed=42, profiles=(None, None), modes=(None, None),
              records=False, records_dir=None, turn_cap=None, honest=True, **overrides):
        settings = dict(
            records=RecordPlan(records_dir, turn_cap, honest) if records else None,
            seats=(Seat(decks[0], profiles[0], modes[0]), Seat(decks[1], profiles[1], modes[1])),
            games=games, batch_seed=seed, workers=workers, forge_root=forge,
            run_dir=tmp_path / "run", clock_s=60, java=(sys.executable, str(STUB)),
        )
        return BatchConfig(**{**settings, **overrides})

    return build


def test_one_jvm_per_worker_with_derived_seeds_and_a_share_of_the_games(config, records):
    run_batch(config(games=5, workers=2))
    seen = records()
    assert set(seen) == {derive_seed(42, 0), derive_seed(42, 1)}
    games = [seen[derive_seed(42, index)]["argv"] for index in (0, 1)]
    assert [argv[argv.index("-n") + 1] for argv in games] == ["3", "2"]


def test_every_invocation_carries_the_required_flags(config, records):
    run_batch(config())
    for run in records().values():
        argv = run["argv"]
        assert "-Dsentry.enabled=false" in argv
        assert "-Dfile.encoding=UTF-8" in argv
        assert "-q" in argv
        assert argv[argv.index("-c") + 1] == "60"
        assert argv[argv.index("-jar") + 2] == "sim"


def test_the_ai_profile_passes_through_per_seat(config, records):
    run_batch(config(profiles=("Default", "Reckless")))
    for run in records().values():
        argv = run["argv"]
        assert argv[argv.index("-a") + 1:argv.index("-a") + 3] == ["Default", "Reckless"]


def test_one_named_seat_still_gives_forge_both_profiles(config, records):
    run_batch(config(profiles=(None, "Reckless")))
    for run in records().values():
        argv = run["argv"]
        assert argv[argv.index("-a") + 1:argv.index("-a") + 3] == ["Default", "Reckless"]


def test_no_profile_leaves_the_flag_out(config, records):
    run_batch(config())
    assert all("-a" not in run["argv"] for run in records().values())


def test_the_sim_mode_passes_through_per_seat(config, records):
    run_batch(config(modes=("full", "hybrid")))
    for run in records().values():
        argv = run["argv"]
        assert argv[argv.index("-sim") + 1:argv.index("-sim") + 3] == ["full", "hybrid"]


def test_one_named_seat_still_gives_forge_both_modes(config, records):
    run_batch(config(modes=(None, "full")))
    for run in records().values():
        argv = run["argv"]
        assert argv[argv.index("-sim") + 1:argv.index("-sim") + 3] == ["heuristic", "full"]


def test_no_sim_mode_leaves_the_flag_out(config, records):
    run_batch(config())
    assert all("-sim" not in run["argv"] for run in records().values())


def test_a_seat_that_searches_gets_the_default_budget(config, records):
    run_batch(config(modes=("full", None)))
    for run in records().values():
        assert run["argv"][run["argv"].index("-budget") + 1] == str(DEFAULT_SEARCH_BUDGET)


def test_a_heuristic_batch_gets_no_budget(config, records):
    run_batch(config(modes=("heuristic", "heuristic")))
    assert all("-budget" not in run["argv"] for run in records().values())


def test_no_sim_mode_gets_no_budget(config, records):
    run_batch(config())
    assert all("-budget" not in run["argv"] for run in records().values())


def test_an_asked_for_budget_passes_through(config, records):
    run_batch(config(modes=("full", "full"), search_budget=50))
    assert all(run["argv"][run["argv"].index("-budget") + 1] == "50" for run in records().values())


def test_a_budget_of_zero_leaves_the_flag_off(config, records):
    run_batch(config(modes=("full", None), search_budget=0))
    assert all("-budget" not in run["argv"] for run in records().values())


def test_a_negative_budget_stops_the_batch(config, records):
    with pytest.raises(SetupError):
        run_batch(config(modes=("full", None), search_budget=-1))
    assert records() == {}


def test_each_worker_gets_its_own_throwaway_profile(config, records, tmp_path):
    run_batch(config(workers=3))
    runs = list(records().values())
    run_dir = (tmp_path / "run").resolve()
    assert len({run["cwd"] for run in runs}) == 3
    assert len({run["profile"]["userDir"] for run in runs}) == 3
    assert len({run["profile"]["cacheDir"] for run in runs}) == 3
    for run in runs:
        cwd = Path(run["cwd"]).resolve()
        assert cwd.parent == run_dir
        assert Path(run["profile"]["userDir"]).parent == cwd
        assert Path(run["profile"]["cacheDir"]).parent == cwd


def test_the_real_profile_is_fenced_off_even_if_forge_falls_back(config, records):
    run_batch(config())
    for run in records().values():
        cwd = Path(run["cwd"]).resolve()
        assert Path(run["appdata"]).parent == cwd
        assert Path(run["localappdata"]).parent == cwd


def test_workers_see_forges_res_folder(config, records):
    run_batch(config())
    assert all(run["res_marker"] for run in records().values())


def test_cleanup_removes_workers_but_never_touches_res(config, forge, tmp_path):
    run_batch(config())
    assert (forge / "forge-gui" / "res" / "marker.txt").read_text(encoding="utf-8") == "real res"
    assert not list((tmp_path / "run").glob("worker-*/"))
    assert list((tmp_path / "run").glob("*.out.log"))


def test_keep_profiles_leaves_the_folders(config, forge, tmp_path):
    run_batch(config(keep_profiles=True))
    folders = list((tmp_path / "run").glob("worker-*/"))
    assert len(folders) == 2
    for folder in folders:
        worker_module.remove_folder(folder)
    assert (forge / "forge-gui" / "res" / "marker.txt").exists()


def test_a_batch_counts_every_game(config):
    batch = run_batch(config(games=6, workers=2))
    assert (batch.attempted, batch.finished, batch.not_finished) == (6, 6, 0)
    assert batch.wins(1) + batch.wins(2) == 6
    assert batch.usable == 6
    assert batch.clean


def test_the_same_batch_seed_gives_the_same_results(config, tmp_path):
    first = run_batch(config(run_dir=tmp_path / "a"))
    second = run_batch(config(run_dir=tmp_path / "b"))
    assert first.digest == second.digest


def test_a_different_batch_seed_gives_different_results(config, tmp_path):
    first = run_batch(config(games=20, run_dir=tmp_path / "a", seed=1))
    second = run_batch(config(games=20, run_dir=tmp_path / "b", seed=2))
    assert first.digest != second.digest


def test_an_unloadable_deck_finishes_nothing_despite_exit_code_zero(config, decks):
    decks[0].write_text("not a deck\n", encoding="utf-8")
    batch = run_batch(config(games=6, workers=2))
    assert [worker.exit_code for worker in batch.workers] == [0, 0]
    assert (batch.attempted, batch.finished, batch.not_finished) == (6, 0, 6)
    assert batch.workers[0].fatal == "Could not load deck - seat1.dck, match cannot start"
    assert not batch.clean


def test_an_unknown_ai_profile_finishes_nothing_despite_exit_code_zero(config):
    batch = run_batch(config(profiles=("Default", "Bogus")))
    assert batch.finished == 0
    assert batch.workers[0].fatal.startswith("Unknown AI profile - Bogus")
    assert not batch.clean


def test_an_unknown_sim_mode_finishes_nothing_despite_exit_code_zero(config):
    batch = run_batch(config(modes=("Bogus", "full")))
    assert batch.finished == 0
    assert all(worker.fatal.startswith("Unknown simulation mode") for worker in batch.workers)


def test_a_batch_where_forge_names_the_modes_is_clean(config):
    batch = run_batch(config(modes=("full", None)))
    assert batch.clean
    assert all(worker.sim_modes == ("full", "heuristic") for worker in batch.workers)


def test_a_jar_that_ignores_the_flag_fails_the_batch(config, monkeypatch):
    # Forge skips flags it doesn't know, so a stale jar plays heuristic games and looks fine.
    monkeypatch.setenv("STUB_SCENARIO", "stale_jar")
    batch = run_batch(config(modes=("full", None)))
    assert batch.finished == 6
    assert not batch.clean
    assert "did not name the sim modes" in format_workers(batch)


def test_a_jar_that_ignores_the_budget_fails_the_batch(config, monkeypatch):
    # The sim modes come back fine, so only the missing AI limits line gives this jar away.
    monkeypatch.setenv("STUB_SCENARIO", "no_budget_jar")
    batch = run_batch(config(modes=("full", None)))
    assert batch.finished == 6
    assert not batch.clean
    assert "did not report on the search budget" in format_workers(batch)


def test_without_a_budget_the_missing_line_is_not_a_failure(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_budget_jar")
    batch = run_batch(config(modes=("full", None), search_budget=0))
    assert batch.clean


def test_deadline_expiries_are_summed_over_every_game(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "deadlines")
    batch = run_batch(config(modes=("full", None), search_budget=0))
    assert batch.deadline_expiries == 12
    assert "AI deadline expiries              12\n" in format_summary(batch)


def test_what_stops_did_is_summed_over_every_game(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "stops")
    batch = run_batch(config(modes=("full", None)))
    assert (batch.stops_played, batch.stops_passed, batch.stops_with_land, batch.land_drops_missed) == (18, 6, 12, 6)
    summary = format_summary(batch)
    assert "AI stops that played a line       18\n" in summary
    assert "AI land drops missed on stops     6\n" in summary


def test_stop_counts_say_not_reported_without_them(config):
    batch = run_batch(config(modes=("full", None)))
    assert batch.budget_stops == 0
    assert "AI stops that passed              not reported\n" in format_summary(batch)


def test_budget_stops_are_summed_over_every_game(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "budget_stops")
    batch = run_batch(config(modes=("full", None)))
    assert batch.budget_stops == 6
    assert "AI search budget stops            6\n" in format_summary(batch)


def test_a_run_forge_did_not_report_on_says_so(config):
    report = format_summary(run_batch(config()))
    assert "AI deadline expiries              not reported\n" in report
    assert "AI search budget stops            not reported\n" in report


def test_the_digest_covers_forges_random_draws():
    def digest(draws):
        game = GameResult(1, 3000, 7, winner_seat=1, ai_limits=AiLimits(0, 0, draws))
        return BatchResult(finished_batch().config, [WorkerResult(WorkerPlan(0, 111, 1), games=[game])], 1.0).digest

    # Same winner, same final turn: the two seed 1 runs in issue 50 agreed on those and still differed.
    assert digest(100) != digest(101)


def test_the_digest_of_a_run_without_draws_is_what_it_always_was():
    game = GameResult(1, 3000, 7, winner_seat=1)
    batch = BatchResult(finished_batch().config, [WorkerResult(WorkerPlan(0, 111, 1), games=[game])], 1.0)
    row = "0:1:1:None:7"
    assert batch.digest == hashlib.sha256(row.encode("ascii")).hexdigest()[:12]


def test_a_worker_that_dies_midway_keeps_the_games_it_finished(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "dies_after_one")
    batch = run_batch(config(games=6, workers=2))
    assert (batch.finished, batch.not_finished) == (2, 4)
    assert [worker.exit_code for worker in batch.workers] == [1, 1]


@pytest.mark.parametrize("scenario, cause", [
    ("rules_draw", DrawCause.RULES),
    ("clock", DrawCause.CLOCK),
    ("exception", DrawCause.EXCEPTION),
])
def test_draws_are_reported_by_cause_and_are_not_usable(config, monkeypatch, scenario, cause):
    monkeypatch.setenv("STUB_SCENARIO", scenario)
    batch = run_batch(config(games=4, workers=2))
    assert batch.finished == 4
    assert batch.draws(cause) == 4
    assert batch.draw_count == 4
    assert batch.wins(1) + batch.wins(2) == batch.usable == 0


def test_stack_traces_on_stderr_are_counted(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "exception")
    batch = run_batch(config(games=4, workers=2))
    assert sum(worker.stderr_traces for worker in batch.workers) == 4


def test_a_hung_worker_is_killed_at_its_deadline(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "hang")
    monkeypatch.setattr(worker_module, "STARTUP_ALLOWANCE_S", 1)
    monkeypatch.setattr(worker_module, "PER_GAME_ALLOWANCE_S", 0)
    batch = run_batch(config(games=2, workers=2, clock_s=0))
    assert all(worker.timed_out for worker in batch.workers)
    assert batch.not_finished == 2


@pytest.mark.skipif(os.name != "nt", reason="the launcher with a child JVM is a Windows thing")
def test_killing_a_worker_also_kills_the_jvm_its_launcher_started(config, monkeypatch, tmp_path):
    records = tmp_path / "pids"
    records.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(records))
    monkeypatch.setenv("STUB_SCENARIO", "hang_in_child")
    monkeypatch.setattr(worker_module, "STARTUP_ALLOWANCE_S", 2)
    monkeypatch.setattr(worker_module, "PER_GAME_ALLOWANCE_S", 0)
    batch = run_batch(config(games=1, workers=1, clock_s=0))
    assert batch.workers[0].timed_out
    [pid_file] = records.glob("child-*.pid")
    pid = pid_file.read_text(encoding="utf-8")
    running = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    assert pid not in running


def test_a_worker_that_never_opens_its_profile_fails_the_batch(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_log")
    batch = run_batch(config())
    assert batch.finished == 6
    assert not batch.clean
    assert "no log in its throwaway profile" in format_workers(batch)


def test_startup_and_last_game_times_are_measured(config):
    batch = run_batch(config())
    for worker in batch.workers:
        assert 0 < worker.ready_s <= worker.last_result_s <= batch.wall_s


def test_stdout_and_stderr_land_in_separate_logs(config, monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_SCENARIO", "exception")
    run_batch(config())
    out = (tmp_path / "run" / "worker-00.out.log").read_text(encoding="utf-8")
    err = (tmp_path / "run" / "worker-00.err.log").read_text(encoding="utf-8")
    assert "Game Result:" in out and "NullPointerException" not in out
    assert "NullPointerException" in err and "Game Result:" not in err


def test_a_missing_deck_stops_the_batch_before_any_launch(config, decks, records):
    decks[0].unlink()
    with pytest.raises(SetupError, match="not a file"):
        run_batch(config())
    assert records() == {}


def test_a_missing_jar_stops_the_batch(config, forge):
    for jar in (forge / "forge-gui-desktop" / "target").glob("*.jar"):
        jar.unlink()
    with pytest.raises(SetupError, match="expected one"):
        run_batch(config())


def test_a_run_folder_inside_forge_is_refused(config, forge):
    with pytest.raises(SetupError, match="outside the Forge clone"):
        run_batch(config(run_dir=forge / "forge-gui" / "target" / "run"))


def test_a_used_run_folder_with_worker_folders_is_refused(config, tmp_path):
    run_batch(config(keep_profiles=True))
    with pytest.raises(SetupError, match="already holds worker folders"):
        run_batch(config())
    for folder in (tmp_path / "run").glob("worker-*/"):
        worker_module.remove_folder(folder)


def window_closing_in(seconds, lead_s=0.0):
    now = datetime.now()
    return Window(now - timedelta(minutes=1), now + timedelta(seconds=seconds), lead_s)


def test_workers_stop_at_the_hard_stop_and_the_batch_is_incomplete(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "cut_off_after_one")
    began = time.monotonic()
    batch = run_batch(config(games=6, workers=2, window=window_closing_in(4)))
    assert time.monotonic() - began < 30  # the stub would sleep for 600
    assert all(worker.stopped and not worker.timed_out for worker in batch.workers)
    assert batch.stopped
    assert not batch.complete
    assert not batch.clean


def test_a_stopped_batch_keeps_finished_games_and_drops_the_one_cut_off(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "cut_off_after_one")
    batch = run_batch(config(games=6, workers=2, window=window_closing_in(4)))
    assert (batch.attempted, batch.finished, batch.not_finished) == (6, 2, 4)
    assert [game.number for game in batch.games] == [1, 1]


@pytest.mark.skipif(os.name != "nt", reason="the launcher with a child JVM is a Windows thing")
def test_the_hard_stop_also_kills_the_jvm_its_launcher_started(config, monkeypatch, tmp_path):
    pids = tmp_path / "pids"
    pids.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(pids))
    monkeypatch.setenv("STUB_SCENARIO", "hang_in_child")
    batch = run_batch(config(games=2, workers=2, window=window_closing_in(4)))
    assert all(worker.stopped for worker in batch.workers)
    found = [path.read_text(encoding="utf-8") for path in pids.glob("child-*.pid")]
    assert len(found) == 2
    listing = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    assert not [pid for pid in found if f'"{pid}"' in listing]
    assert not list((tmp_path / "run").glob("worker-*/"))


def test_killing_starts_early_enough_that_workers_are_gone_by_the_stop(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "hang")
    window = window_closing_in(5, lead_s=3.0)
    batch = run_batch(config(games=2, workers=2, window=window))
    assert all(worker.stopped for worker in batch.workers)
    assert datetime.now() < window.stop


def test_a_process_left_holding_the_pipes_cant_hang_the_runner(config, monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_SCENARIO", "launcher_dies")
    monkeypatch.setattr(worker_module, "PIPE_DRAIN_S", 1)
    began = time.monotonic()
    batch = run_batch(config(games=2, workers=2, window=window_closing_in(120)))
    assert time.monotonic() - began < 6  # the leftover process holds the pipes for 8
    assert all(worker.left_behind for worker in batch.workers)
    assert not batch.clean
    assert "left a process running" in format_workers(batch)
    assert read_status(tmp_path)["status"] == "incomplete"


def read_status(tmp_path):
    return json.loads((tmp_path / "run" / "batch.json").read_text(encoding="utf-8"))


def test_a_stopped_batch_says_so_on_disk(config, monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_SCENARIO", "cut_off_after_one")
    window = window_closing_in(4)
    run_batch(config(games=6, workers=2, window=window))
    assert read_status(tmp_path) == {
        "batch": "run", "status": "incomplete", "stopped_at_hard_stop": True,
        "window_start": window.start.isoformat(timespec="seconds"),
        "window_stop": window.stop.isoformat(timespec="seconds"),
        "games_attempted": 6, "games_finished": 2, "games_not_finished": 4,
    }


def test_a_batch_that_beats_the_stop_is_complete(config, tmp_path):
    batch = run_batch(config(window=window_closing_in(120)))
    assert batch.complete and batch.clean and not batch.stopped
    status = read_status(tmp_path)
    assert (status["status"], status["stopped_at_hard_stop"]) == ("complete", False)


def test_a_batch_without_a_window_gets_a_status_too(config, tmp_path):
    run_batch(config())
    status = read_status(tmp_path)
    assert (status["status"], status["window_start"], status["window_stop"]) == ("complete", None, None)


def test_a_worker_lost_to_a_crash_also_marks_the_batch_incomplete(config, monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_SCENARIO", "dies_after_one")
    run_batch(config(games=6, workers=2))
    status = read_status(tmp_path)
    assert (status["status"], status["stopped_at_hard_stop"]) == ("incomplete", False)


def test_the_status_reads_running_while_workers_run(config, records):
    # A runner that dies mid-batch never rewrites this, so it can't leave a batch looking complete.
    run_batch(config())
    assert {run["status_at_launch"]["status"] for run in records().values()} == {"running"}


def test_the_batch_waits_for_the_window_to_open(config, records):
    now = datetime.now()
    began = time.monotonic()
    batch = run_batch(config(window=Window(now + timedelta(seconds=2), now + timedelta(minutes=2), 0.0)))
    assert time.monotonic() - began >= 2
    assert batch.complete
    assert len(records()) == 2


def test_a_window_already_closed_launches_nothing(config, records, tmp_path):
    batch = run_batch(config(games=6, window=window_closing_in(-1)))
    assert records() == {}
    assert batch.stopped and not batch.complete
    assert not list((tmp_path / "run").glob("worker-*/"))
    status = read_status(tmp_path)
    assert (status["status"], status["stopped_at_hard_stop"], status["games_not_finished"]) == ("incomplete", True, 6)


def test_setup_errors_show_at_once_and_not_when_the_window_opens(config, decks):
    decks[0].unlink()
    now = datetime.now()
    began = time.monotonic()
    with pytest.raises(SetupError, match="not a file"):
        run_batch(config(window=Window(now + timedelta(minutes=5), now + timedelta(minutes=10), 0.0)))
    assert time.monotonic() - began < 5


def test_the_run_folder_keeps_a_place_for_game_records(config, tmp_path):
    run_batch(config())
    assert (tmp_path / "run" / "games").is_dir()


def finished_batch():
    """A hand-built result, so the timing rows of the report can be pinned exactly."""
    config = BatchConfig(
        seats=(Seat(Path("burn.dck"), "Default"), Seat(Path("merfolk.dck"))),
        games=6, batch_seed=42, workers=2, forge_root=Path("forge"), run_dir=Path("run"), clock_s=600, tripwire=False,
    )
    good = WorkerResult(WorkerPlan(0, 111, 3), exit_code=0, profile_used=True, ready_s=20.0, last_result_s=30.0, games=[
        GameResult(1, 3000, 7, winner_seat=1),
        GameResult(2, 3000, 8, winner_seat=2),
        GameResult(3, 3000, 9, winner_seat=2),
    ])
    bad = WorkerResult(WorkerPlan(1, 222, 3), exit_code=0, profile_used=True, ready_s=40.0, last_result_s=50.0, games=[
        GameResult(1, 600000, 30, draw_cause=DrawCause.CLOCK),
        GameResult(2, 3000, 5, draw_cause=DrawCause.RULES),
    ], ai_timeouts=2, stderr_traces=1)
    return BatchResult(config, [good, bad], wall_s=60.0)


def test_the_report_table():
    assert format_summary(finished_batch()) == (
        "seat 1                            burn (AI Default, sim heuristic)\n"
        "seat 2                            merfolk (AI default, sim heuristic)\n"
        "batch seed                        42\n"
        "workers                           2\n"
        "clock                             600 s\n"
        "search budget                     off\n"
        "games attempted                   6\n"
        "games finished                    5\n"
        "games not finished                1\n"
        "wins seat 1                       1\n"
        "wins seat 2                       2\n"
        "draws                             2\n"
        "  by rules                        1\n"
        "  by clock                        1\n"
        "  by turn_cap                     0\n"
        "  by exception                    0\n"
        "tripwire                          off\n"
        "wall time                         60.0 s\n"
        "games per hour                    300\n"
        "usable games per hour             180\n"
        "startup per worker                mean 30.0 s, max 40.0 s\n"
        "steady-state games per hour       1800\n"
        "usable games in 8 h, steady rate  8631 (kill criterion 1 needs about 500)\n"
        "AI decision timeouts              2\n"
        "AI deadline expiries              not reported\n"
        "AI search budget stops            not reported\n"
        "AI stops that played a line       not reported\n"
        "AI stops that passed              not reported\n"
        "AI stops with a land candidate    not reported\n"
        "AI land drops missed on stops     not reported\n"
        "stack traces on stderr            1\n"
        f"result digest                     {finished_batch().digest}\n"
    )


def windowed(batch, *workers):
    window = Window(datetime(2026, 11, 5, 23, 0), datetime(2026, 11, 6, 7, 0))
    return BatchResult(replace(batch.config, window=window), list(workers or batch.workers), batch.wall_s)


def test_the_report_of_a_stopped_batch_says_it_is_incomplete():
    finished = finished_batch()
    cut_off = replace(finished.workers[1], stopped=True, exit_code=1)
    batch = windowed(finished, finished.workers[0], cut_off)
    summary = format_summary(batch)
    assert "window                            2026-11-05 23:00:00 to 2026-11-06 07:00:00\n" in summary
    assert "status                            incomplete, stopped at the hard stop\n" in summary
    assert "killed at hard stop" in format_workers(batch)


def test_the_report_of_a_window_batch_that_finished_says_complete():
    finished = finished_batch()
    done = replace(finished.workers[1], games=[*finished.workers[1].games, GameResult(3, 3000, 6, winner_seat=1)])
    assert "status                            complete\n" in format_summary(windowed(finished, finished.workers[0], done))


def test_the_report_of_a_window_batch_that_lost_a_worker_blames_no_stop():
    assert "status                            incomplete\n" in format_summary(windowed(finished_batch()))


def test_the_report_of_a_batch_with_no_window_has_no_window_rows():
    summary = format_summary(finished_batch())
    assert "status" not in summary
    assert "window" not in summary


def test_the_report_of_a_window_that_closed_before_launch_still_prints():
    batch = BatchResult(windowed(finished_batch()).config, [], 0.0)
    assert "status                            incomplete, stopped at the hard stop\n" in format_report(batch)


def test_the_report_names_each_seats_sim_mode():
    config = BatchConfig(
        seats=(Seat(Path("burn.dck"), None, "full"), Seat(Path("merfolk.dck"), "Reckless", "hybrid")),
        games=1, batch_seed=1, workers=1, forge_root=Path("forge"), run_dir=Path("run"),
    )
    report = format_summary(BatchResult(config, [], wall_s=0.0))
    assert "burn (AI default, sim full)\n" in report
    assert "merfolk (AI Reckless, sim hybrid)\n" in report


def test_the_report_names_the_budget():
    config = BatchConfig(
        seats=(Seat(Path("burn.dck"), None, "full"), Seat(Path("merfolk.dck"))),
        games=1, batch_seed=1, workers=1, forge_root=Path("forge"), run_dir=Path("run"),
    )
    report = format_summary(BatchResult(config, [], wall_s=0.0))
    assert f"search budget                     {DEFAULT_SEARCH_BUDGET} steps per decision\n" in report


def test_the_worker_table_shows_seed_and_timing():
    assert format_workers(finished_batch()) == (
        "worker  seed  games  finished  startup s  last game s  exit  note\n"
        "0       111   3      3         20.0       30.0         0\n"
        "1       222   3      2         40.0       50.0         0\n"
    )


def test_steady_state_leaves_startup_out():
    batch = finished_batch()
    assert batch.steady_games_per_hour == pytest.approx((3 / 10 + 2 / 10) * 3600)
    assert batch.steady_usable_per_hour == pytest.approx(1800 * 3 / 5)
    assert batch.games_per_hour == pytest.approx(5 / 60 * 3600)


def test_a_batch_with_no_games_reports_without_dividing_by_zero():
    config = finished_batch().config
    failed = BatchResult(config, [WorkerResult(WorkerPlan(0, 111, 6), exit_code=0, fatal="Could not load deck - x")], 30.0)
    report = format_report(failed)
    assert "games not finished                6" in report
    assert "Could not load deck - x" in report


def cli_args(decks, forge, run_dir, *extra):
    return ["batch", str(decks[0]), str(decks[1]), "--games", "4", "--seed", "42", "--workers", "2",
            "--clock", "60", "--forge", str(forge), "--run-dir", str(run_dir),
            "--java", sys.executable, str(STUB), *extra]


def test_the_command_prints_and_saves_the_report(decks, forge, tmp_path, capsys):
    assert main(cli_args(decks, forge, tmp_path / "run")) == 0
    out = capsys.readouterr().out
    assert "games finished                    4" in out
    assert (tmp_path / "run" / "report.txt").read_text(encoding="utf-8") == out


def test_the_command_exits_nonzero_when_games_did_not_finish(decks, forge, tmp_path, capsys):
    decks[1].write_text("not a deck\n", encoding="utf-8")
    assert main(cli_args(decks, forge, tmp_path / "run")) == 1
    assert "4 of 4 games not finished" in capsys.readouterr().err


def test_the_ai_flag_takes_one_profile_per_seat(decks, forge, tmp_path, records):
    main(cli_args(decks, forge, tmp_path / "run", "--ai", "Cautious", "Reckless"))
    for run in records().values():
        argv = run["argv"]
        assert argv[argv.index("-a") + 1:argv.index("-a") + 3] == ["Cautious", "Reckless"]


def test_the_sim_mode_flag_takes_one_mode_per_seat(decks, forge, tmp_path, records):
    assert main(cli_args(decks, forge, tmp_path / "run", "--sim-mode", "full", "heuristic")) == 0
    for run in records().values():
        argv = run["argv"]
        assert argv[argv.index("-sim") + 1:argv.index("-sim") + 3] == ["full", "heuristic"]


def test_the_search_budget_flag_reaches_forge(decks, forge, tmp_path, records):
    args = cli_args(decks, forge, tmp_path / "run", "--sim-mode", "full", "heuristic", "--search-budget", "75")
    assert main(args) == 0
    assert all(run["argv"][run["argv"].index("-budget") + 1] == "75" for run in records().values())


def test_the_sim_mode_flag_refuses_an_unknown_mode(decks, forge, tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        main(cli_args(decks, forge, tmp_path / "run", "--sim-mode", "fast", "heuristic"))
    assert stop.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_a_setup_error_is_a_usage_error(decks, forge, tmp_path, capsys):
    decks[0].unlink()
    with pytest.raises(SystemExit) as stop:
        main(cli_args(decks, forge, tmp_path / "run"))
    assert stop.value.code == 2
    assert "not a file" in capsys.readouterr().err


def clock(moment):
    return moment.strftime("%H:%M:%S")


def nightly_args(decks, forge, run_dir, start, stop, *extra):
    return ["nightly", str(decks[0]), str(decks[1]), "--games", "4", "--seed", "42", "--clock", "60",
            "--start", clock(start), "--stop", clock(stop), "--forge", str(forge), "--run-dir", str(run_dir),
            "--java", sys.executable, str(STUB), *extra]


def test_a_nightly_run_that_beats_the_stop_exits_zero(decks, forge, tmp_path, capsys):
    now = datetime.now()
    args = nightly_args(decks, forge, tmp_path / "run", now - timedelta(minutes=1), now + timedelta(minutes=5),
                        "--workers", "2")
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "status                            complete\n" in out
    assert (tmp_path / "run" / "report.txt").read_text(encoding="utf-8") == out


def test_a_nightly_run_cut_off_by_the_stop_exits_nonzero_and_says_why(decks, forge, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "cut_off_after_one")
    monkeypatch.setattr(window_module, "STOP_LEAD_S", 0.0)
    now = datetime.now()
    args = nightly_args(decks, forge, tmp_path / "run", now - timedelta(minutes=1), now + timedelta(seconds=4),
                        "--workers", "2")
    assert main(args) == 1
    captured = capsys.readouterr()
    assert "status                            incomplete, stopped at the hard stop\n" in captured.out
    assert "batch incomplete: stopped at the hard stop, 2 of 4 games not finished" in captured.err
    assert read_status(tmp_path)["status"] == "incomplete"


@pytest.mark.parametrize("extra, workers", [((), 3), (("--reserve", "3"), 2), (("--reserve", "9"), 1)])
def test_the_worker_count_is_cores_minus_the_reserve(decks, forge, tmp_path, monkeypatch, records, extra, workers):
    monkeypatch.setattr(os, "cpu_count", lambda: 5)
    now = datetime.now()
    args = nightly_args(decks, forge, tmp_path / "run", now - timedelta(minutes=1), now + timedelta(minutes=5), *extra)
    assert main(args) == 0
    assert len(records()) == workers


@pytest.mark.parametrize("extra, message", [
    (("--reserve", "-1"), "reserve can't be negative"),
    (("--reserve", "2", "--workers", "2"), "not allowed with argument"),
])
def test_bad_worker_settings_are_usage_errors(decks, forge, tmp_path, capsys, extra, message):
    now = datetime.now()
    with pytest.raises(SystemExit) as stop:
        main(nightly_args(decks, forge, tmp_path / "run", now, now + timedelta(minutes=5), *extra))
    assert stop.value.code == 2
    assert message in capsys.readouterr().err


def test_a_start_equal_to_the_stop_is_a_usage_error(decks, forge, tmp_path, capsys):
    now = datetime.now()
    with pytest.raises(SystemExit) as stop:
        main(nightly_args(decks, forge, tmp_path / "run", now, now))
    assert stop.value.code == 2
    assert "can't be the same" in capsys.readouterr().err


def test_a_bad_clock_time_is_a_usage_error(decks, forge, tmp_path, capsys):
    args = nightly_args(decks, forge, tmp_path / "run", datetime.now(), datetime.now())
    args[args.index("--stop") + 1] = "7am"
    with pytest.raises(SystemExit) as stop:
        main(args)
    assert stop.value.code == 2
    assert "--stop" in capsys.readouterr().err


def test_the_module_runs_as_a_script(decks, forge, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    result = subprocess.run([sys.executable, "-m", "simrunner", *cli_args(decks, forge, tmp_path / "run")],
                            capture_output=True, text=True, encoding="utf-8", cwd=REPO, env=env)
    assert result.returncode == 0
    assert "games attempted" in result.stdout


def game_files(folder):
    return sorted(path.name for path in folder.glob("*.jsonl.gz"))


def test_a_batch_without_records_passes_no_record_flags(config, records):
    run_batch(config())
    for run in records().values():
        assert not {"-record", "-batch", "-turncap"} & set(run["argv"])


def test_records_go_to_the_games_folder_under_the_batch_id(config, records, tmp_path):
    run_batch(config(records=True))
    for run in records().values():
        argv = run["argv"]
        assert Path(argv[argv.index("-record") + 1]) == (tmp_path / "run" / "games").resolve()
        assert argv[argv.index("-batch") + 1] == "run"
        assert "-turncap" not in argv


def test_each_game_leaves_a_file_and_the_report_counts_them(config, records, tmp_path):
    batch = run_batch(config(games=5, workers=2, records=True))
    assert len(game_files(tmp_path / "run" / "games")) == 5
    assert batch.records_written == 5
    assert batch.clean
    assert "5 of 5 games" in format_summary(batch)


def test_the_report_says_nothing_about_records_when_they_are_off(config, records):
    summary = format_summary(run_batch(config()))
    assert "game records" not in summary
    assert "record digest" not in summary


def test_the_record_digest_replays_under_another_batch_id(config, records, tmp_path):
    first = run_batch(config(records=True, run_dir=tmp_path / "one"))
    second = run_batch(config(records=True, run_dir=tmp_path / "two"))
    assert first.record_digest == second.record_digest
    assert f"record digest                     {first.record_digest}" in format_summary(first)


def test_the_record_digest_sees_what_the_result_digest_cant(config, records, tmp_path):
    first = run_batch(config(records=True, run_dir=tmp_path / "one"))
    second = run_batch(config(records=True, run_dir=tmp_path / "two"))
    path = second.workers[0].record_files[0]
    lines = gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()
    path.write_bytes(gzip.compress("\n".join(lines[:-1]).encode("utf-8")))
    assert first.digest == second.digest
    assert first.record_digest != second.record_digest


def test_an_unreadable_game_file_changes_the_record_digest_and_breaks_nothing(config, records, tmp_path):
    batch = run_batch(config(records=True))
    before = batch.record_digest
    batch.workers[0].record_files[0].write_bytes(b"cut off")
    assert batch.record_digest != before


def test_a_batch_with_no_files_has_no_record_digest(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_game_files")
    batch = run_batch(config(games=2, records=True))
    assert batch.record_digest is None
    assert "record digest                     no files" in format_summary(batch)


def test_records_can_go_to_another_folder(config, records, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    batch = run_batch(config(games=2, records=True, records_dir=elsewhere))
    assert len(game_files(elsewhere)) == 2
    assert game_files(tmp_path / "run" / "games") == []
    assert batch.records_written == 2


def test_records_from_another_batch_in_the_folder_do_not_count(config, records, tmp_path):
    folder = tmp_path / "shared"
    folder.mkdir()
    (folder / "other_1_1_a_vs_b.jsonl.gz").write_bytes(b"")
    batch = run_batch(config(games=2, records=True, records_dir=folder))
    assert batch.records_written == 2


def test_the_turn_cap_passes_through(config, records):
    run_batch(config(records=True, turn_cap=30))
    for run in records().values():
        argv = run["argv"]
        assert argv[argv.index("-turncap") + 1] == "30"


def test_a_capped_game_counts_as_a_draw_by_turn_cap(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "turn_cap")
    batch = run_batch(config(games=2, records=True))
    assert batch.draws(DrawCause.TURN_CAP) == 2
    assert batch.usable == 0
    assert "by turn_cap" in format_summary(batch)
    assert batch.clean


def test_a_stale_jar_that_writes_no_records_is_not_clean(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "stale_jar")
    batch = run_batch(config(games=2, records=True))
    assert batch.complete
    assert not batch.clean
    assert "no game record" in format_workers(batch)


def test_a_game_with_no_file_is_not_clean_even_when_others_have_one(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_game_files")
    assert not run_batch(config(games=2, records=True)).clean


def test_a_clock_draw_needs_no_file_because_it_may_have_no_snapshots(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "clock")
    assert run_batch(config(games=2, records=True)).clean


def test_an_exception_game_needs_no_file(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "exception")
    assert run_batch(config(games=2, records=True)).clean


def test_files_that_agree_with_the_results_are_counted(config, records):
    batch = run_batch(config(games=5, workers=2, records=True))
    assert batch.record_mismatches == []
    assert "5 match, 0 no file by design, 0 mismatches\n" in format_summary(batch)


@pytest.mark.parametrize("scenario", ["clock", "exception"])
def test_a_game_with_no_file_by_design_is_counted_and_is_no_mismatch(config, records, monkeypatch, scenario):
    monkeypatch.setenv("STUB_SCENARIO", scenario)
    batch = run_batch(config(games=2, records=True))
    assert batch.clean
    assert "0 match, 2 no file by design, 0 mismatches" in format_summary(batch)


@pytest.mark.parametrize("scenario, says", [("file_names_other_winner", "printed winner"), ("file_says_clock", "the file says draw_clock"),
                                            ("exception_with_file", "but it has a file"), ("no_game_files", "but it has no file")])
def test_a_file_that_disagrees_with_the_result_fails_the_batch(config, records, monkeypatch, scenario, says):
    monkeypatch.setenv("STUB_SCENARIO", scenario)
    batch = run_batch(config(games=2, workers=1, records=True))
    assert batch.complete
    assert not batch.clean
    assert len(batch.record_mismatches) == 2
    assert says in format_report(batch)


def test_the_worker_table_says_when_files_disagree(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "file_names_other_winner")
    batch = run_batch(config(games=2, workers=1, records=True))
    assert "game records that disagree with the results" in format_workers(batch)


def test_a_file_with_no_result_line_is_a_mismatch(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "extra_file")
    batch = run_batch(config(games=2, workers=1, records=True))
    assert not batch.clean
    assert batch.record_mismatches == ["worker 0, game 3: a file with no result line"]


def test_a_game_the_hard_stop_cut_off_may_leave_a_file_with_no_result(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "extra_file")
    batch = run_batch(config(games=2, workers=1, records=True))
    batch.workers[0].stopped = True
    assert batch.record_mismatches == []


def test_an_unreadable_file_is_a_mismatch(config, records):
    batch = run_batch(config(games=2, workers=1, records=True))
    worker = batch.workers[0]
    worker.records[0] = replace(worker.records[0], metadata=None)
    assert any("can't be read" in problem for problem in batch.record_mismatches)
    assert not batch.clean


def test_only_a_batch_with_records_is_checked_against_them(config):
    batch = run_batch(config())
    assert batch.clean
    assert "records against results" not in format_summary(batch)


def test_a_long_list_of_mismatches_is_cut_short(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_game_files")
    report = format_report(run_batch(config(games=25, workers=1, records=True)))
    assert "and 5 more\n" in report


def test_a_batch_id_the_schema_refuses_stops_before_launch(config, records, tmp_path):
    with pytest.raises(SetupError, match="batch id"):
        run_batch(config(records=True, run_dir=tmp_path / "night.1"))
    assert records() == {}


def test_a_folder_name_that_is_no_batch_id_is_fine_without_records(config, records, tmp_path):
    run_batch(config(run_dir=tmp_path / "night.1"))


@pytest.mark.parametrize("flags", [["--turn-cap", "30"], ["--no-honest"]])
def test_what_only_goes_with_records_is_refused_without_them(forge, decks, records, tmp_path, capsys, flags):
    with pytest.raises(SystemExit):
        main(["batch", str(decks[0]), str(decks[1]), "--games", "1", "--seed", "7", "--workers", "1", "--forge", str(forge),
              "--run-dir", str(tmp_path / "cli"), "--java", sys.executable, str(STUB), *flags])
    assert "need records" in capsys.readouterr().err
    assert records() == {}


def test_a_turn_cap_must_be_above_zero(config, records):
    with pytest.raises(SetupError, match="turn cap"):
        run_batch(config(records=True, turn_cap=0))


def test_the_command_line_turns_records_on(forge, decks, records, tmp_path, capsys):
    run_dir = tmp_path / "cli"
    code = main(["batch", str(decks[0]), str(decks[1]), "--games", "2", "--seed", "7", "--workers", "1",
                 "--forge", str(forge), "--run-dir", str(run_dir), "--java", sys.executable, str(STUB),
                 "--records", "--turn-cap", "40"])
    assert code == 0
    assert len(game_files(run_dir / "games")) == 2
    (run,) = records().values()
    assert run["argv"][run["argv"].index("-turncap") + 1] == "40"


def test_a_records_folder_on_the_command_line_turns_records_on(forge, decks, records, tmp_path, capsys):
    elsewhere = tmp_path / "elsewhere"
    code = main(["batch", str(decks[0]), str(decks[1]), "--games", "1", "--seed", "7", "--workers", "1",
                 "--forge", str(forge), "--run-dir", str(tmp_path / "cli"), "--java", sys.executable, str(STUB),
                 "--records-dir", str(elsewhere)])
    assert code == 0
    assert len(game_files(elsewhere)) == 1


def test_records_turn_the_tripwire_on_by_leaving_the_flag_out(config, records):
    run_batch(config(records=True))
    assert all("-tripwire" not in run["argv"] for run in records().values())


def test_no_tripwire_passes_off_through_to_forge(config, records):
    batch = run_batch(config(games=2, records=True, tripwire=False))
    assert all(run["argv"][run["argv"].index("-tripwire") + 1] == "off" for run in records().values())
    assert batch.clean
    assert not batch.tripwire_clean
    assert "tripwire                          off\n" in format_summary(batch)


def test_the_tripwire_runs_without_records(config, records):
    batch = run_batch(config(games=2))
    assert all("-tripwire" not in run["argv"] for run in records().values())
    assert batch.tripwire_clean
    assert batch.clean
    assert "tripwire clean                    yes\n" in format_summary(batch)


def test_the_tripwire_can_be_turned_off_without_records(config, records):
    batch = run_batch(config(games=2, tripwire=False))
    assert all(run["argv"][run["argv"].index("-tripwire") + 1] == "off" for run in records().values())
    assert batch.clean
    assert "tripwire                          off\n" in format_summary(batch)


def test_a_jar_with_no_tripwire_fails_a_batch_without_records_too(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_tripwire_jar")
    assert not run_batch(config(games=2)).clean


def test_records_turn_honest_play_on(config, records):
    run_batch(config(records=True))
    assert all(run["argv"][run["argv"].index("-honest") + 1] == "on" for run in records().values())


def test_a_batch_that_asked_for_honest_play_and_got_it_is_clean(config, records):
    assert run_batch(config(games=2, records=True)).clean


def test_a_jar_that_ignores_honest_fails_the_batch(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_honest_jar")
    batch = run_batch(config(games=2, records=True))
    assert batch.complete
    assert not batch.clean
    assert "doesn't show honest play" in format_workers(batch)


def test_a_batch_that_asked_for_no_honest_play_doesnt_need_it(config, records, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_honest_jar")
    assert run_batch(config(games=2, records=True, honest=False)).clean


def test_an_unreadable_game_file_shows_no_honest_play(config, records):
    batch = run_batch(config(games=2, workers=1, records=True))
    worker = batch.workers[0]
    worker.records[0] = replace(worker.records[0], metadata=None)
    assert "a game record that doesn't show honest play" in batch.ignored_flags(worker)


def test_no_honest_leaves_the_flag_out_so_forge_keeps_its_default(config, records):
    run_batch(config(games=2, records=True, honest=False))
    assert all("-honest" not in run["argv"] for run in records().values())


def test_a_batch_without_records_says_nothing_about_honest_play(config, tmp_path):
    assert "-honest" not in sim_args(config().seats, tmp_path, 1, 1, 60)


def test_with_no_seat_searching_a_clean_tripwire_just_says_yes(config):
    batch = run_batch(config(games=2, records=True))
    assert not batch.search_unchecked
    assert "tripwire clean                    yes\n" in format_summary(batch)


def test_a_searching_seat_is_clean_for_the_real_game_only(config):
    batch = run_batch(config(games=2, records=True, modes=("full", "heuristic")))
    assert batch.tripwire_clean
    assert batch.search_unchecked
    assert "tripwire clean                    the real game is clean, search copies unchecked until #25\n" in format_summary(batch)


def test_a_searching_seat_with_hits_still_says_what_was_unchecked(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "tripwire_hits")
    batch = run_batch(config(games=2, records=True, modes=("full", "heuristic")))
    assert "no, 2 of 2 games had hits, search copies unchecked until #25\n" in format_summary(batch)


def test_the_report_gives_max_and_mean_hits_per_game(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "tripwire_hits")
    batch = run_batch(config(games=4, workers=1, records=True))
    assert batch.tripwire_hits == [5, 2, 5, 2]
    summary = format_summary(batch)
    assert "tripwire hits per game            max 5, mean 3.50\n" in summary
    assert "tripwire clean                    no, 4 of 4 games had hits\n" in summary


def test_the_report_ranks_signatures_by_hits_across_the_batch(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "tripwire_hits")
    batch = run_batch(config(games=4, workers=2, records=True))
    assert batch.tripwire_signatures() == [
        ("forge.ai.ComputerUtil.hasAFogEffect:1582", 10, 4),
        ("forge.ai.ability.ClashAi.chooseSinglePlayer:75", 4, 4),
    ]
    report = format_report(batch)
    assert "hits  games  signature\n" in report
    assert "10    4      forge.ai.ComputerUtil.hasAFogEffect:1582\n" in report


def test_a_long_signature_list_is_cut_and_says_so():
    games = [GameResult(1, 3000, 7, winner_seat=1, tripwire_hits=25,
                        tripwire_signatures=tuple((f"forge.ai.Site.m:{line}", 1) for line in range(25)))]
    config = BatchConfig(seats=(Seat(Path("a.dck")), Seat(Path("b.dck"))), games=1, batch_seed=1, workers=1,
                         forge_root=Path("forge"), run_dir=Path("run"), records=RecordPlan())
    report = format_report(BatchResult(config, [WorkerResult(WorkerPlan(0, 1, 1), games=games)], wall_s=1.0))
    assert "and 5 more\n" in report


def test_zero_hits_in_every_game_is_clean(config):
    batch = run_batch(config(games=2, records=True))
    assert batch.tripwire_clean
    assert "tripwire hits per game            max 0, mean 0.00\n" in format_summary(batch)
    assert "tripwire clean                    yes\n" in format_summary(batch)
    assert batch.tripwire_signatures() == []


def test_a_jar_without_the_tripwire_is_not_a_clean_zero(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_tripwire_jar")
    batch = run_batch(config(games=2, records=True))
    assert not batch.tripwire_clean
    assert not batch.clean
    assert "no game reported" in format_summary(batch)
    assert "no tripwire count" in format_workers(batch)


def test_one_silent_game_keeps_a_batch_from_being_tripwire_clean():
    config = BatchConfig(seats=(Seat(Path("a.dck")), Seat(Path("b.dck"))), games=2, batch_seed=1, workers=1,
                         forge_root=Path("forge"), run_dir=Path("run"), records=RecordPlan())
    games = [GameResult(1, 3000, 7, winner_seat=1, tripwire_hits=0), GameResult(2, 3000, 7, winner_seat=1)]
    batch = BatchResult(config, [WorkerResult(WorkerPlan(0, 1, 2), games=games)], wall_s=1.0)
    assert not batch.tripwire_clean
    assert "1 reported nothing" in format_summary(batch)


def test_a_jar_with_no_tripwire_says_so_in_the_report(config, monkeypatch):
    monkeypatch.setenv("STUB_SCENARIO", "no_tripwire_jar")
    assert "no game reported, so the jar may be stale" in format_summary(run_batch(config(games=2)))
