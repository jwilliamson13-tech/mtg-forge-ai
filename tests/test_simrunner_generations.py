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


def eval_part(argv):
    return argv[argv.index("-eval") + 1:argv.index("-eval") + 3]


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


def test_a_generation_goes_to_forge_as_an_onnx_eval_name(config, launches):
    batch = run_batch(config())
    bundle = str(batch.config.seats[0].generation.bundle)
    for launch in launches():
        argv = launch["argv"]
        assert eval_part(argv) == [f"onnx:{bundle}", "forge"]
        assert not any(arg.startswith("-Dforge.onnx") for arg in jvm_part(argv))
        assert argv[argv.index("-sim") + 1:argv.index("-sim") + 3] == ["full", "heuristic"]
    assert batch.clean
    assert all(worker.evaluators == ("onnx", "forge") for worker in batch.workers)
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
    bundle = str(batch.config.seats[0].generation.bundle)
    assert all(eval_part(launch["argv"]) == [f"onnx:{bundle}"] * 2 for launch in launches())
    assert batch.clean
    assert all(worker.onnx_seats == ((1, bundle), (2, bundle)) for worker in batch.workers)


def test_two_generations_sit_at_one_table(config, launches):
    batch = run_batch(config(second=("full", ONNX, "gen2")))
    first, second = (str(seat.generation.bundle) for seat in batch.config.seats)
    assert first != second
    assert all(eval_part(launch["argv"]) == [f"onnx:{first}", f"onnx:{second}"] for launch in launches())
    assert batch.clean
    assert all(worker.onnx_seats == ((1, first), (2, second)) for worker in batch.workers)
    # one summary line per bundle, and the report keeps both
    assert all(worker.onnx_summary.count("calls, 5.000 ms per call") == 2 for worker in batch.workers)
    report = format_summary(batch)
    assert "onnx:gen1, bundle" in report and "onnx:gen2, bundle" in report


@pytest.mark.parametrize("other", ["random", "passthrough"])
def test_a_generation_sits_across_from_the_test_evaluators(config, launches, other):
    batch = run_batch(config(second=("full", other, None)))
    bundle = str(batch.config.seats[0].generation.bundle)
    assert all(eval_part(launch["argv"]) == [f"onnx:{bundle}", other] for launch in launches())
    assert batch.clean
    assert all(worker.evaluators == ("onnx", other) for worker in batch.workers)


def test_a_jar_without_the_onnx_name_finishes_nothing(config, monkeypatch):
    # A jar with #23 alone knows -eval but not onnx:DIR, and says so before any game.
    monkeypatch.setenv("STUB_SCENARIO", "no_onnx_jar")
    batch = run_batch(config())
    assert batch.finished == 0
    assert not batch.clean
    assert all(worker.fatal and worker.fatal.startswith("Unknown evaluator - onnx:") for worker in batch.workers)


def test_a_jar_that_ignores_eval_fails_the_batch(config, monkeypatch):
    # It would play Forge's evaluator under the generation's name, and say nothing.
    monkeypatch.setenv("STUB_SCENARIO", "no_eval_jar")
    batch = run_batch(config())
    assert batch.finished == 4
    assert not batch.clean
    workers = format_workers(batch)
    assert "Forge did not name the evaluators asked for" in workers
    assert "Forge did not put the ONNX evaluator on the seats asked for" in workers


def test_a_bundle_that_wont_load_finishes_nothing(config):
    built = config()
    (built.seats[0].generation.bundle / "contract.json").unlink()  # gone between resolving and the JVM
    batch = run_batch(built)
    assert batch.finished == 0 and not batch.clean
    assert all(worker.fatal and worker.fatal.startswith("-eval onnx:") and "can't load the ONNX bundle" in worker.fatal
               for worker in batch.workers)


@pytest.mark.parametrize("first, second, message", [
    (("heuristic", ONNX, "gen1"), ("full", None, None), "the onnx:gen1 evaluator needs a seat that searches, and seat 1 is heuristic"),
    (("full", ONNX, None), ("full", None, None), "seat 1 asks for the ONNX evaluator with no generation"),
])
def test_seat_configurations_forge_cant_play_are_refused(config, first, second, message, tmp_path):
    with pytest.raises(SetupError, match=message):
        run_batch(config(first=first, second=second))
    assert not list((tmp_path / "run").glob("worker-*"))


def test_without_a_generation_the_wiring_is_the_eval_flag():
    seats = (Seat(Path("a.dck"), None, "full", "random"), Seat(Path("b.dck"), None, "full"))
    assert evaluator_wiring(seats).eval_values == ("random", "forge")
    assert evaluator_wiring(seats).printed == ("random", "forge")
    assert evaluator_wiring(seats).onnx_seats == ()
    assert evaluator_wiring((Seat(Path("a.dck")), Seat(Path("b.dck")))).eval_values == ()


def test_a_generation_is_wired_by_its_bundle_folder(models):
    gen1, gen2 = resolve("gen1", models), resolve("gen2", models)
    wiring = evaluator_wiring((Seat(Path("a.dck"), None, "full", ONNX, gen1),
                               Seat(Path("b.dck"), None, "full", ONNX, gen2)))
    assert wiring.eval_values == (f"onnx:{gen1.bundle}", f"onnx:{gen2.bundle}")
    assert wiring.printed == ("onnx", "onnx")
    assert wiring.onnx_seats == ((1, str(gen1.bundle)), (2, str(gen2.bundle)))


def test_the_onnx_seat_moves_with_its_deck_when_a_matchup_swaps_seats(config, launches, monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_PATTERN", "Burn:WL")
    base = config(games=0, workers=1, records=RecordPlan())
    result = run_matchup(MatchupConfig(base, width=10, round_games=100, max_games=100))
    assert result.met
    bundle = f"onnx:{base.seats[0].generation.bundle}"
    seats = {Path(launch["cwd"]).parent.name: eval_part(launch["argv"]) for launch in launches()}
    assert seats == {"round-001-ab": [bundle, "forge"], "round-001-ba": ["forge", bundle]}
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
        assert eval_part(launch["argv"]) == ["forge", f"onnx:{(models / 'gen2').resolve()}"]
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
