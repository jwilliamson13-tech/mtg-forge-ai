"""Seat configurations with a model generation, wired to `tests/stub_java.py` as Forge's ONNX evaluator would be."""

import json
import re
import sys
from pathlib import Path

import pytest

from simrunner.__main__ import main
from simrunner.batch import BatchConfig, SetupError, run_batch
from simrunner.generations import Generation, GenerationError, resolve
from simrunner.matchup import MatchupConfig, run_matchup
from simrunner.report import format_report, format_summary, format_workers
from simrunner.worker import ONNX, RecordPlan, Seat, evaluator_wiring

STUB = Path(__file__).parent / "stub_java.py"


def make_bundle(folder: Path, generation: int = 1, weights: bytes = b"weights") -> Path:
    folder.mkdir(parents=True)
    (folder / "contract.json").write_text(json.dumps({"version": 1, "bundle": {"generation": generation}}),
                                          encoding="utf-8")
    (folder / "model.onnx").write_bytes(weights)
    return folder


@pytest.fixture
def models(tmp_path):
    root = tmp_path / "models"
    make_bundle(root / "gen1", 1)
    make_bundle(root / "gen2", 2, b"other weights")
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
def launches(tmp_path, monkeypatch):
    """Return a function that reads back each stub launch, keyed by its worker folder's run folder and seed."""
    folder = tmp_path / "launches"
    folder.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(folder))

    def read():
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]

    return read


@pytest.fixture
def config(forge, decks, models, tmp_path):
    def build(first=("full", ONNX, "gen1"), second=("heuristic", None, None), games=4, workers=2, **overrides):
        def seat(deck, mode, evaluator, generation):
            return Seat(deck, None, mode, evaluator, resolve(generation, models) if generation else None)

        settings = dict(seats=(seat(decks[0], *first), seat(decks[1], *second)), games=games, batch_seed=5,
                        workers=workers, forge_root=forge, run_dir=tmp_path / "run", clock_s=60,
                        java=(sys.executable, str(STUB)))
        return BatchConfig(**{**settings, **overrides})

    return build


def jvm_part(argv):
    return argv[:argv.index("-jar")]


def test_a_generation_name_is_a_bundle_folder_under_models(models):
    generation = resolve("gen1", models)
    assert generation.bundle == (models / "gen1").resolve()
    assert generation.number == 1
    assert generation.label == "onnx:gen1"
    assert len(generation.model_digest) == 12
    assert generation.model_digest != resolve("gen2", models).model_digest


def test_a_path_is_taken_as_a_bundle_folder(tmp_path, models):
    bundle = make_bundle(tmp_path / "dryrun" / "bundle", 0)
    generation = resolve(str(bundle), models)
    assert generation.bundle == bundle.resolve() and generation.number == 0


def test_a_missing_generation_names_the_ones_there_are(models):
    with pytest.raises(GenerationError, match=r"no export bundle for gen9: .*gen9 has no contract.json or model.onnx; "
                                              r"generations under .*: gen1, gen2"):
        resolve("gen9", models)


def test_a_numbered_name_has_to_hold_that_generation(models):
    make_bundle(models / "gen3", 2)
    with pytest.raises(GenerationError, match="gen3 holds a bundle of generation 2"):
        resolve("gen3", models)


def test_a_generation_goes_to_forge_as_jvm_properties_and_leaves_eval_off(config, launches):
    batch = run_batch(config())
    bundle = str(batch.config.seats[0].generation.bundle)
    for launch in launches():
        argv = launch["argv"]
        assert f"-Dforge.onnx.bundle={bundle}" in jvm_part(argv)
        assert "-Dforge.onnx.seats=1" in jvm_part(argv)
        assert "-eval" not in argv
        assert argv[argv.index("-sim") + 1:argv.index("-sim") + 3] == ["full", "heuristic"]
    assert batch.clean
    assert all(worker.onnx_seats == ((1, bundle),) for worker in batch.workers)


def test_the_report_names_the_generation_and_what_the_evaluator_did(config):
    batch = run_batch(config())
    report = format_summary(batch)
    assert "burn (AI default, sim full, eval onnx:gen1)" in report
    generation = batch.config.seats[0].generation
    assert re.search(rf"generation +onnx:gen1, bundle {re.escape(str(generation.bundle))}, generation 1, "
                     rf"model {generation.model_digest}$", report, re.M)
    assert "ONNX worker 0" in report and "calls, 5.000 ms per call" in report


def test_the_same_generation_on_both_seats_names_both(config, launches):
    batch = run_batch(config(second=("full", ONNX, "gen1")))
    assert all("-Dforge.onnx.seats=1,2" in jvm_part(launch["argv"]) for launch in launches())
    assert batch.clean


def test_a_jar_without_the_onnx_evaluator_fails_the_batch(config, monkeypatch):
    # It would play Forge's evaluator under the generation's name, and say nothing.
    monkeypatch.setenv("STUB_SCENARIO", "no_onnx_jar")
    batch = run_batch(config())
    assert batch.finished == 4
    assert not batch.clean
    assert "Forge did not put the ONNX evaluator on the seats asked for, so the jar may be stale" in format_workers(batch)


def test_a_bundle_that_wont_load_finishes_nothing(config):
    built = config()
    (built.seats[0].generation.bundle / "contract.json").unlink()  # gone between resolving and the JVM
    batch = run_batch(built)
    assert batch.finished == 0 and not batch.clean
    assert sum(worker.stderr_traces for worker in batch.workers) == 2


@pytest.mark.parametrize("first, second, message", [
    (("heuristic", ONNX, "gen1"), ("full", None, None), "the onnx:gen1 evaluator needs a seat that searches, and seat 1 is heuristic"),
    (("full", ONNX, "gen1"), ("full", ONNX, "gen2"), "both seats name a generation, and they differ"),
    (("full", ONNX, "gen1"), ("full", "random", None), "can't sit across from the random evaluator yet"),
    (("full", ONNX, None), ("full", None, None), "seat 1 asks for the ONNX evaluator with no generation"),
])
def test_seat_configurations_forge_cant_play_are_refused(config, first, second, message, tmp_path):
    with pytest.raises(SetupError, match=message):
        run_batch(config(first=first, second=second))
    assert not list((tmp_path / "run").glob("worker-*"))


def test_without_a_generation_the_wiring_is_the_eval_flag():
    seats = (Seat(Path("a.dck"), None, "full", "random"), Seat(Path("b.dck"), None, "full"))
    assert evaluator_wiring(seats).eval_values == ("random", "forge")
    assert evaluator_wiring(seats).jvm_properties == ()
    assert evaluator_wiring((Seat(Path("a.dck")), Seat(Path("b.dck")))).eval_values == ()


def test_the_onnx_seat_moves_with_its_deck_when_a_matchup_swaps_seats(config, launches, monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_PATTERN", "Burn:WL")
    base = config(games=0, workers=1, records=RecordPlan())
    result = run_matchup(MatchupConfig(base, width=10, round_games=100, max_games=100))
    assert result.met
    seats = {Path(launch["cwd"]).parent.name: [arg for arg in jvm_part(launch["argv"]) if "onnx.seats" in arg]
             for launch in launches()}
    assert seats == {"round-001-ab": ["-Dforge.onnx.seats=1"], "round-001-ba": ["-Dforge.onnx.seats=2"]}
    saved = json.loads((tmp_path / "run" / "matchup.json").read_text(encoding="utf-8"))
    assert saved["side_a"]["evaluator"] == "onnx"
    assert saved["side_a"]["generation"]["name"] == "gen1" and saved["side_a"]["generation"]["generation"] == 1
    assert saved["side_b"]["generation"] is None


def cli_args(decks, forge, run_dir, *more):
    return ["batch", str(decks[0]), str(decks[1]), "--games", "2", "--seed", "9", "--workers", "1", "--clock", "60",
            "--forge", str(forge), "--run-dir", str(run_dir), "--java", sys.executable, str(STUB), *more]


def test_the_evaluator_flag_takes_a_generation(decks, forge, models, tmp_path, launches):
    args = cli_args(decks, forge, tmp_path / "run", "--sim-mode", "heuristic", "full",
                    "--evaluator", "forge", "onnx:gen2", "--models", str(models))
    assert main(args) == 0
    for launch in launches():
        assert f"-Dforge.onnx.bundle={(models / 'gen2').resolve()}" in launch["argv"]
        assert "-Dforge.onnx.seats=2" in launch["argv"]
    assert "eval onnx:gen2" in (tmp_path / "run" / "report.txt").read_text(encoding="utf-8")


def test_the_evaluator_flag_refuses_a_generation_with_no_bundle(decks, forge, models, tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        main(cli_args(decks, forge, tmp_path / "run", "--sim-mode", "full", "full",
                      "--evaluator", "onnx:gen7", "forge", "--models", str(models)))
    assert stop.value.code == 2
    assert "no export bundle for gen7" in capsys.readouterr().err


def test_a_generation_object_is_hashable_and_carries_its_fields(models):
    generation = resolve("gen1", models)
    assert {generation, resolve("gen1", models)} == {generation}
    assert set(generation.fields()) == {"name", "bundle", "generation", "model_digest"}
    assert isinstance(generation, Generation)
