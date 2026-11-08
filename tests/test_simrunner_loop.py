"""The generation loop as orchestration: step order, skip and resume, gates, and the results log.

Every step runs `tests/stub_loop.py` in place of the real command, so nothing here plays Forge or
trains a model. The stub writes what each command leaves behind, and its knobs make one step fail.
"""

import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from simrunner.__main__ import main
from simrunner.loop import (STEPS, GateFailed, Loop, LoopConfig, LoopError, Runner, StepFailed, TrainOptions,
                            check_config)
from simrunner.poolcheck import check_pool, game_timeouts, trace_heads
from simrunner.results import LOG_FILE, PAGE_FILE, read_entries, render_entry
from simrunner.window import Window

STUB = Path(__file__).parent / "stub_loop.py"
COMMAND_STEPS = [step for step in STEPS if step not in ("check", "log")]


def open_window(minutes=60):
    now = datetime.now()
    return Window(now - timedelta(minutes=minutes), now + timedelta(minutes=minutes), 0.0)


def bundle(models, name, generation):
    folder = models / name
    folder.mkdir(parents=True)
    (folder / "contract.json").write_text(json.dumps({"bundle": {"generation": generation}}), encoding="utf-8")
    (folder / "model.onnx").write_bytes(f"weights of {name}".encode())


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A gen0 to gen1 step with everything under tmp_path, and a way to set the stub's knobs."""
    models = tmp_path / "models"
    bundle(models, "gen0", 0)
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("STUB_LOOP_CALLS", str(calls))
    monkeypatch.delenv("STUB_LOOP", raising=False)
    config = LoopConfig(
        source="gen0", target="gen1", games=100, seed=7, workers=2, window=open_window(), window_args=("22:00", "06:00"),
        run_dir=tmp_path / "gen1", data_dir=tmp_path / "gen1" / "pool", models=models, manifest=tmp_path / "manifest.json",
        cards=tmp_path / "cards.jsonl", results=tmp_path / "results", ladder_dir=tmp_path / "ladder",
        converge_dir=tmp_path / "convergence", python=(sys.executable, str(STUB)), train=TrainOptions(epochs=2),
        min_free_mb=0, memory_floor_mb=0)

    class Setup:
        def __init__(self):
            self.config = config
            self.tmp = tmp_path

        def knobs(self, **values):
            monkeypatch.setenv("STUB_LOOP", json.dumps(values))

        def calls(self):
            if not calls.is_file():
                return []
            return [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]

        def steps_called(self):
            return [call["step"] for call in self.calls()]

        def argv(self, step):
            return next(call["argv"] for call in self.calls() if call["step"] == step)

        def clear(self):
            calls.unlink(missing_ok=True)

        def loop(self, **changes):
            return Loop(replace(config, **changes), Runner(poll_s=0.02, free_mb=lambda: 8000.0), say=lambda text: None)

        def entries(self):
            return read_entries(config.results)

    return Setup()


# Step order and commands


def test_a_generation_runs_every_step_in_order_and_logs_one_entry(setup):
    entry = setup.loop().run()

    assert setup.steps_called() == COMMAND_STEPS
    assert [step["step"] for step in entry["steps"]] == list(STEPS[:-1])
    assert all(step["status"] == "ran" for step in entry["steps"])
    [logged] = setup.entries()
    assert logged["status"] == "complete" and logged["generation"] == 1 and logged["source"] == "onnx:gen0"
    assert (setup.config.results / PAGE_FILE).is_file()


def test_the_play_step_seats_the_source_generation_in_both_seats_in_full_sim(setup):
    setup.loop().run()

    play = setup.argv("play")
    assert play[play.index("--sim-mode") + 1:play.index("--sim-mode") + 3] == ["full", "full"]
    assert play[play.index("--evaluator") + 1:play.index("--evaluator") + 3] == ["onnx:gen0", "onnx:gen0"]
    assert play[play.index("--games") + 1] == "100" and play[play.index("--seed") + 1] == "7"
    for step in ("play", "ladder", "converge"):  # every Forge step takes the window
        argv = setup.argv(step)
        assert argv[argv.index("--start") + 1:argv.index("--start") + 2] == ["22:00"]
        assert argv[argv.index("--stop") + 1] == "06:00"


def test_gen0_trains_on_the_heuristic_ai_and_converges_against_it(setup):
    loop = setup.loop(source="heuristic", target="gen0", run_dir=setup.tmp / "gen0", data_dir=setup.tmp / "gen0" / "pool",
                      models=setup.tmp / "fresh-models")
    entry = loop.run()

    assert "--sim-mode" not in setup.argv("play") and "--evaluator" not in setup.argv("play")
    assert setup.argv("converge")[3:5] == ["heuristic", "onnx:gen0"]
    assert entry["convergence"]["before"] == "heuristic"


def test_the_later_steps_get_the_generation_number_and_the_right_folders(setup):
    setup.loop().run()
    run_dir = setup.config.run_dir

    assert "--drop-tripwire-hits" in setup.argv("convert")
    train = setup.argv("train")
    assert train[train.index("--generation") + 1] == "1" and train[train.index("--epochs") + 1] == "2"
    assert train[train.index("--out") + 1] == str(run_dir / "model")
    ablate = setup.argv("ablate")
    assert ablate[ablate.index("--generation") + 1] == "1"
    export = setup.argv("export")
    assert export[export.index("--out") + 1] == str(setup.config.models / "gen1")
    ladder = setup.argv("ladder")
    assert ladder[ladder.index("--players") + 1:ladder.index("--seed")] == ["heuristic", "full", "onnx:gen0", "onnx:gen1"]
    assert setup.argv("converge")[3:5] == ["onnx:gen0", "onnx:gen1"]


def test_every_step_s_command_and_output_is_on_record(setup):
    setup.loop().run()

    for step in COMMAND_STEPS:
        record = json.loads((setup.config.run_dir / "steps" / f"{step}.json").read_text(encoding="utf-8"))
        assert record["command"][2:4] == ["-m", record["command"][3]] and record["exit"] == 0
        assert Path(record["log"]).is_file() and record["outputs"]
    loop_log = (setup.config.run_dir / "loop.log").read_text(encoding="utf-8")
    assert "[train] running:" in loop_log and "[train] log " in loop_log


# Skip and resume


def test_a_second_start_runs_nothing_and_logs_nothing_more(setup):
    setup.loop().run()
    setup.clear()

    entry = setup.loop().run()

    assert setup.steps_called() == []
    assert len(setup.entries()) == 1
    assert {step["status"] for step in entry["steps"]} == {"skipped"}


def test_a_crash_stops_the_loop_without_an_entry_and_the_next_start_resumes_there(setup):
    setup.knobs(fail="train")
    with pytest.raises(StepFailed) as stopped:
        setup.loop().run()
    assert stopped.value.step == "train" and "exit 3" in stopped.value.why
    assert setup.entries() == []

    setup.knobs()
    setup.clear()
    setup.loop().run()

    assert setup.steps_called() == ["train", "ablate", "pairtest", "export", "ladder", "converge"]
    assert len(setup.entries()) == 1


def test_a_step_whose_output_is_gone_runs_again_and_so_does_everything_after_it(setup):
    setup.loop().run()
    setup.clear()
    (setup.config.run_dir / "ablation" / "ablation.json").unlink()

    setup.loop().run()

    assert setup.steps_called() == ["ablate", "pairtest", "export", "ladder", "converge"]
    assert len(setup.entries()) == 2  # a new entry, since the steps behind it ran again


def test_a_step_that_ran_again_leaves_no_later_record_behind_for_the_next_start(setup):
    setup.loop().run()
    setup.knobs(fail="join")
    with pytest.raises(StepFailed):
        setup.loop(redo="convert").run()  # new Parquet, then join falls over
    setup.knobs()
    setup.clear()

    setup.loop().run()

    assert setup.steps_called()[0] == "join"


def test_redo_runs_that_step_and_every_one_after_it(setup):
    setup.loop().run()
    setup.clear()

    setup.loop(redo="ladder").run()

    assert setup.steps_called() == ["ladder", "converge"]


def test_a_re_exported_bundle_is_exported_again(setup):
    setup.loop().run()
    setup.clear()
    (setup.config.models / "gen1" / "model.onnx").write_bytes(b"other weights")

    setup.loop().run()

    assert setup.steps_called()[0] == "export"


def test_a_finished_pool_from_before_the_loop_is_adopted_not_replayed(setup, tmp_path):
    # Play a pool as `simrunner pool` would, outside the loop.
    import subprocess
    data = tmp_path / "gen0-pool"
    subprocess.run([sys.executable, str(STUB), "-m", "simrunner", "pool", "--games", "100", "--seed", "20261107",
                    "--run-dir", str(data)], check=True)
    setup.clear()

    entry = setup.loop(source="heuristic", target="gen0", seed=20261107, data_dir=data, run_dir=tmp_path / "gen0",
                       models=tmp_path / "fresh-models").run()

    assert setup.steps_called()[0] == "hiddencheck"
    assert entry["steps"][0]["status"] == "adopted"
    assert entry["data"]["run_dir"] == str(data)


def test_a_data_folder_with_another_pool_is_refused_before_anything_runs(setup, tmp_path):
    import subprocess
    data = tmp_path / "other-pool"
    subprocess.run([sys.executable, str(STUB), "-m", "simrunner", "pool", "--games", "100", "--seed", "1",
                    "--run-dir", str(data)], check=True)
    setup.clear()

    with pytest.raises(LoopError, match="holds another pool, in: seed, sim_modes, generation names"):
        setup.loop(data_dir=data).run()
    assert setup.steps_called() == []


# Gates


def test_an_incomplete_pool_stops_the_loop_and_says_the_command_continues_it(setup):
    setup.knobs(pool_incomplete=True)

    with pytest.raises(StepFailed) as stopped:
        setup.loop().run()

    assert stopped.value.step == "play"
    assert "1 chunks failed" in stopped.value.why and "the same command continues" in stopped.value.why
    assert setup.steps_called() == ["play"] and setup.entries() == []


def test_tripwire_games_under_the_bar_are_counted_and_left_out(setup):
    entry = setup.loop().run()

    tripwire = entry["data"]["tripwire"]
    assert tripwire["games_with_hits"] == 1 and tripwire["hits"] == 1
    assert tripwire["games"][0]["signatures"] == ["forge.ai.ability.PlayAi.chooseSingleCard:173"]
    assert entry["conversion"]["dropped"] == {"tripwire": 1}


def test_too_many_tripwire_games_stop_the_loop_with_their_signatures_logged(setup):
    setup.knobs(tripwire_games=5)  # 5 of 100 is over 1%

    with pytest.raises(GateFailed) as stopped:
        setup.loop().run()

    assert stopped.value.step == "check" and "PlayAi.chooseSingleCard:173" in stopped.value.why
    [entry] = setup.entries()
    assert entry["status"] == "stopped" and entry["stopped_at"] == "check"
    assert entry["data"]["tripwire"]["games_with_hits"] == 5
    assert setup.steps_called() == ["play"]


def test_record_ids_that_name_hidden_cards_stop_the_loop(setup):
    setup.knobs(hidden_named=True)

    with pytest.raises(GateFailed, match="record ids name hidden cards"):
        setup.loop().run()


def test_rejected_game_files_stop_the_loop(setup):
    setup.knobs(rejected=2)

    with pytest.raises(GateFailed, match="2 game files rejected"):
        setup.loop().run()


def test_join_drops_over_the_bar_stop_the_loop_before_training(setup):
    setup.knobs(dropped=20)  # 20 of 1,000 snapshots is over 1%

    with pytest.raises(GateFailed, match="20 of 1000 snapshots dropped"):
        setup.loop().run()

    assert "train" not in setup.steps_called()
    assert setup.entries()[0]["join"]["snapshots_dropped"] == 20


def test_join_drops_under_the_bar_are_recorded(setup):
    entry = setup.loop().run()

    join = entry["join"]
    assert join["snapshots"] == 1000 and join["snapshots_dropped"] == 3 and join["drop_share"] == 0.003
    assert join["top_unmatched"] == [{"rows": 3, "name": "Summon: Fenrir (58)'s Effect (Original)"}]


def test_kill_criterion_2_failing_stops_before_export_and_logs_the_table_once(setup):
    setup.knobs(margin=3.0)
    for _ in range(2):  # the second start hits the same gate
        with pytest.raises(GateFailed, match="kill criterion 2"):
            setup.loop().run()

    [entry] = setup.entries()
    assert entry["ablation"]["kill_criterion_2"] == {"margin_points": 3.0, "bar_points": 5.0, "passed": False}
    assert entry["training"]["generation"] == 1
    assert "export" not in setup.steps_called()


def test_kill_criterion_2_can_be_overruled(setup):
    setup.knobs(margin=3.0)

    entry = setup.loop(ignore_kill_criterion=True).run()

    assert entry["status"] == "complete" and not entry["ablation"]["kill_criterion_2"]["passed"]


def test_kill_criterion_2_passes_at_exactly_five_points(setup):
    setup.knobs(margin=5.0)

    assert setup.loop().run()["ablation"]["kill_criterion_2"]["passed"]


def test_a_failed_export_check_stops_the_loop(setup):
    setup.knobs(export_fail=True)

    with pytest.raises(GateFailed, match="export check failed: max difference 0.5"):
        setup.loop().run()
    assert "ladder" not in setup.steps_called()


def test_an_unfinished_ladder_or_convergence_stops_without_an_entry(setup):
    setup.knobs(ladder_incomplete=True)
    with pytest.raises(StepFailed, match="1 failed"):
        setup.loop().run()
    setup.knobs(converge_unended=True)
    with pytest.raises(StepFailed, match="slot 4 after"):
        setup.loop().run()
    assert setup.entries() == []


# Memory and the window


def test_too_little_free_memory_stops_before_a_memory_step(setup):
    loop = Loop(replace(setup.config, min_free_mb=4096), Runner(poll_s=0.02, free_mb=lambda: 1000.0), say=lambda t: None)

    with pytest.raises(StepFailed, match="only 1000 MB of memory free"):
        loop.run()

    assert setup.steps_called() == ["play", "hiddencheck"]


def test_a_step_is_killed_when_free_memory_falls_under_the_floor(tmp_path):
    readings = iter([8000.0, 8000.0] + [500.0] * 1000)
    runner = Runner(poll_s=0.02, free_mb=lambda: next(readings))
    began = time.monotonic()

    ran = runner.run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "step.log", None, 1024)

    assert ran.exit is None and "free memory fell to 500 MB" in ran.killed
    assert ran.lowest_free_mb == 500.0 and time.monotonic() - began < 20
    assert "killed by the generation loop" in (tmp_path / "step.log").read_text(encoding="utf-8")


def test_a_step_still_running_at_the_hard_stop_is_killed(tmp_path):
    runner = Runner(poll_s=0.02, free_mb=lambda: None)

    ran = runner.run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "step.log", time.time() + 0.3, None)

    assert ran.exit is None and ran.killed == "the hard stop"


def test_a_step_killed_mid_run_stops_the_loop_and_runs_again_next_time(setup):
    setup.knobs(sleep={"train": 30})

    def free():  # plenty until train has started, then almost none
        return 100.0 if setup.steps_called()[-1:] == ["train"] else 8000.0

    loop = Loop(replace(setup.config, memory_floor_mb=1024), Runner(poll_s=0.02, free_mb=free), say=lambda text: None)
    with pytest.raises(StepFailed, match="killed: free memory fell to 100 MB"):
        loop.run()
    assert not (setup.config.run_dir / "steps" / "train.json").exists()
    assert setup.entries() == []


def test_a_closed_window_stops_before_the_next_step(setup):
    past = datetime.now() - timedelta(hours=3)
    loop = setup.loop(window=Window(past - timedelta(hours=1), past, 0.0))

    with pytest.raises(StepFailed, match="the hard stop at .* has come"):
        loop.run()
    assert setup.steps_called() == []


# Settings


@pytest.mark.parametrize("source, target, message", [
    ("heuristic", "gen1", "only gen0 trains on the heuristic AI's games"),
    ("gen0", "gen2", "gen2 trains on gen1's games, not gen0's"),
    ("gen0", "one", "--to is a generation such as gen1"),
    ("full", "gen1", "--from is heuristic or a generation"),
])
def test_a_step_that_does_not_follow_from_the_last_is_refused(setup, source, target, message):
    with pytest.raises(LoopError, match=message):
        check_config(replace(setup.config, source=source, target=target))


def test_a_source_generation_with_no_bundle_is_refused(setup):
    with pytest.raises(LoopError, match="no export bundle for gen1"):
        check_config(replace(setup.config, source="gen1", target="gen2"))


# The pool check


def test_the_pool_check_counts_timeouts_per_game_and_traces_by_exception(setup):
    setup.loop().run()

    summary = check_pool(setup.config.data_dir)
    assert summary["ai_timeouts"] == {"games": 1, "timeouts": 2,
                                      "worst": [{"chunk": "chunk-0000", "game_number": 2, "timeouts": 2}]}
    assert summary["stack_traces"] == {"total": 2, "ai_timeouts": 2, "other": 0,
                                       "by_exception": {"java.util.concurrent.TimeoutException": 2}}
    assert summary["deck_ids"] == ["burn-1", "elves-1", "held0", "held1"]
    assert summary["records"]["files"] == 100 and summary["games_finished"] == 100


def test_timeouts_after_the_last_result_line_belong_to_no_finished_game(tmp_path):
    log = tmp_path / "worker-00.out.log"
    log.write_text("AI eval thread at timeout:\nGame Result: Game 1 ended in 5 ms. Ai(1)-a has won!\n"
                   "Game Result: Game 2 ended in 5 ms. Ai(1)-a has won!\nAI eval thread at timeout:\n"
                   "AI eval thread at timeout:\nGame Result: Game 3 ended in a Draw! Took 5 ms.\n"
                   "AI eval thread at timeout:\n", encoding="utf-8")

    assert game_timeouts(log) == {1: 1, 3: 2}


def test_trace_heads_name_the_exception_without_its_message(tmp_path):
    log = tmp_path / "worker-00.err.log"
    log.write_text("java.lang.IllegalStateException: no card\n\tat forge.X\njava.util.concurrent.TimeoutException\n"
                   "SVar 'X' not defined\n", encoding="utf-8")

    assert trace_heads(log) == {"java.lang.IllegalStateException": 1, "java.util.concurrent.TimeoutException": 1}


# The results log


def test_each_entry_carries_generation_decks_game_counts_and_seeds(setup):
    entry = setup.loop().run()

    assert entry["generation"] == 1 and entry["name"] == "gen1"
    assert entry["data"]["deck_ids"] == ["burn-1", "elves-1", "held0", "held1"]
    assert entry["data"]["games_finished"] == 100 and entry["training"]["train_games"] == 8
    assert entry["seeds"] == {"data": 7, "train": 0, "ladder": 41, "converge": 42}
    assert [m["deck_a"] + "/" + m["deck_b"] for m in entry["convergence"]["matchups"]][0] == "held0/held1"
    assert entry["ladder"]["players"][-1]["player"] == "onnx:gen1"
    assert entry["pair_test"]["sees_interaction"] and entry["export"]["model_digest"]


def test_the_log_is_one_json_line_per_entry_appended(setup):
    setup.loop().run()
    setup.loop(redo="converge").run()

    lines = (setup.config.results / LOG_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(json.loads(line)["name"] == "gen1" for line in lines)
    page = (setup.config.results / PAGE_FILE).read_text(encoding="utf-8")
    assert page.count("## gen1 from onnx:gen0, complete") == 2


def test_the_page_gives_every_win_rate_with_its_interval_and_games(setup):
    setup.knobs(moved=[2])
    entry = setup.loop().run()

    page = render_entry(entry)
    assert "| 1 | held0 | held1 | 50.0% (45.1% to 54.9%, 400 games) | 52.5% (47.6% to 57.4%, 400 games) | converged |" in page
    assert "| 2 | held0 | held2 | 50.0% (45.1% to 54.9%, 400 games) | 62.5% (57.6% to 67.4%, 400 games) | moved |" in page
    assert "| 4 | onnx:gen1 | +160 (+130 to +190) | 240 |" in page
    assert "| 1 | heuristic | +0 (anchor) | 240 |" in page
    assert "Kill criterion 2 passed: full beats no_cards by 7.50 points, the bar is 5.0." in page
    assert "Tripwire: 1 of 100 (1.00%) games had hits (1 hit), all left out of training." in page
    assert "`forge.ai.ability.PlayAi.chooseSingleCard:173`: 1 game" in page
    assert "Forge AI timeouts: 2 in 1 game." in page
    assert page.startswith("## gen1 from onnx:gen0, complete\n")
    assert "Held-out accuracy: 62.00% over 2 games (100 rows), log loss 0.6000." in page
    assert "Join: 3 of 1,000 (0.30%) snapshots dropped for an unmatched card" in page


def test_a_rate_with_no_interval_says_why(setup):
    entry = setup.loop().run()
    entry["convergence"]["matchups"][0]["after"] = {"status": "budget spent", "interval": None}

    assert "| no rate (budget spent) |" in render_entry(entry)


def test_a_stopped_entry_says_where_and_why(setup):
    setup.knobs(margin=1.0)
    with pytest.raises(GateFailed):
        setup.loop().run()

    page = (setup.config.results / PAGE_FILE).read_text(encoding="utf-8")
    assert "## gen1 from onnx:gen0, stopped" in page
    assert "Stopped at `ablate`: kill criterion 2: Full model beats no cards by 1.00 points." in page
    assert "| no_cards | 61.00 |" in page


# The command


def test_the_command_runs_a_generation_end_to_end(setup, tmp_path, capsys):
    now = datetime.now()
    start, stop = (now - timedelta(hours=1)).strftime("%H:%M"), (now + timedelta(hours=1)).strftime("%H:%M")
    code = main(["generation", "--from", "gen0", "--to", "gen1", "--games", "100", "--seed", "7", "--workers", "1",
                 "--start", start, "--stop", stop, "--run-dir", str(tmp_path / "cli"), "--models", str(tmp_path / "models"),
                 "--results", str(tmp_path / "cli-results"), "--ladder-dir", str(tmp_path / "cli-ladder"),
                 "--converge-dir", str(tmp_path / "cli-convergence"), "--width", "2.5", "--min-free-mb", "0",
                 "--memory-floor-mb", "0", "--python", sys.executable, str(STUB)])

    assert code == 0
    assert "gen1 done: entry 1" in capsys.readouterr().err
    converge = setup.argv("converge")
    assert converge[converge.index("--width") + 1] == "2.5"
    assert len(read_entries(tmp_path / "cli-results")) == 1


def test_the_command_exits_1_and_names_the_step_when_a_gate_stops_it(setup, tmp_path, capsys):
    setup.knobs(export_fail=True)
    now = datetime.now()
    start, stop = (now - timedelta(hours=1)).strftime("%H:%M"), (now + timedelta(hours=1)).strftime("%H:%M")
    code = main(["generation", "--from", "gen0", "--to", "gen1", "--games", "100", "--seed", "7", "--workers", "1",
                 "--start", start, "--stop", stop, "--run-dir", str(tmp_path / "cli"), "--models", str(tmp_path / "models"),
                 "--results", str(tmp_path / "cli-results"), "--min-free-mb", "0", "--memory-floor-mb", "0",
                 "--ladder-dir", str(tmp_path / "l"), "--converge-dir", str(tmp_path / "c"),
                 "--python", sys.executable, str(STUB)])

    assert code == 1
    err = capsys.readouterr().err
    assert "generation loop stopped at export: the export check failed" in err
    assert "The results log has an entry for it" in err
