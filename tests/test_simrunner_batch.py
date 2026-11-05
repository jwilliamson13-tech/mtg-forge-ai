"""Batches run against `tests/stub_java.py`, so nothing here needs Java or a built Forge."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from simrunner import worker as worker_module
from simrunner.__main__ import main
from simrunner.batch import BatchConfig, BatchResult, SetupError, run_batch
from simrunner.output import AiLimits, DrawCause, GameResult
from simrunner.report import format_report, format_summary, format_workers
from simrunner.seeds import derive_seed
from simrunner.worker import DEFAULT_SEARCH_BUDGET, Seat, WorkerPlan, WorkerResult

STUB = Path(__file__).parent / "stub_java.py"
REPO = Path(__file__).parents[1]


@pytest.fixture
def forge(tmp_path):
    """A folder laid out like a built Forge clone."""
    root = tmp_path / "forge"
    (root / "forge-gui" / "res").mkdir(parents=True)
    (root / "forge-gui" / "res" / "marker.txt").write_text("real res", encoding="utf-8")
    target = root / "forge-gui-desktop" / "target"
    target.mkdir(parents=True)
    (target / "forge-gui-desktop-2.0.16-SNAPSHOT-jar-with-dependencies.jar").write_bytes(b"")
    return root


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
    def build(games=6, workers=2, seed=42, profiles=(None, None), modes=(None, None), **overrides):
        settings = dict(
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


def finished_batch():
    """A hand-built result, so the timing rows of the report can be pinned exactly."""
    config = BatchConfig(
        seats=(Seat(Path("burn.dck"), "Default"), Seat(Path("merfolk.dck"))),
        games=6, batch_seed=42, workers=2, forge_root=Path("forge"), run_dir=Path("run"), clock_s=600,
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
        "  by exception                    0\n"
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


def test_the_module_runs_as_a_script(decks, forge, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    result = subprocess.run([sys.executable, "-m", "simrunner", *cli_args(decks, forge, tmp_path / "run")],
                            capture_output=True, text=True, encoding="utf-8", cwd=REPO, env=env)
    assert result.returncode == 0
    assert "games attempted" in result.stdout
