"""Ablation and pair-test commands on the smoke model. These check the tables, not the numbers."""

import json

import pytest

from setmodel.__main__ import main
from setmodel.review import ABLATION_JSON, ABLATION_TABLE, PairResult, ablation_rows, format_table, kill_verdict
from tests.setmodel_corpus import smoke_games, write_dump, write_manifest, write_parquet


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("review")
    parquet = write_parquet(smoke_games(), root)
    return root, str(parquet), str(write_dump(root / "cards.jsonl")), str(write_manifest(root / "manifest.json"))


def scored(accuracy):
    return {"held_out": {"accuracy": accuracy, "log_loss": 0.6}}


def test_the_ablation_command_writes_the_full_model_and_four_rows(inputs, capsys):
    root, parquet, cards, deck_manifest = inputs
    out = root / "ablation"
    assert main(["ablate", parquet, cards, deck_manifest, "--out", str(out), "--epochs", "1", "--width", "8", "--min-count", "1",
                 "--device", "cpu"]) == 0
    rows = json.loads((out / ABLATION_JSON).read_text(encoding="utf-8"))["rows"]
    assert [row["run"] for row in rows] == ["full", "no_cards", "no_graveyard_exile", "no_live_state", "no_numbers"]
    assert rows[0]["vs_full"] == 0
    assert (out / ABLATION_TABLE).read_text(encoding="utf-8") in capsys.readouterr().out


def test_kill_criterion_two_turns_on_five_points():
    keeps = ablation_rows({"full": scored(0.60), "no_cards": scored(0.55)})
    fails = ablation_rows({"full": scored(0.60), "no_cards": scored(0.56)})
    assert "carries" in kill_verdict(keeps) and "does not carry" not in kill_verdict(keeps)
    assert "does not carry" in kill_verdict(fails)


def test_a_run_with_nothing_held_out_gives_no_verdict_and_no_fake_number():
    rows = ablation_rows({"full": scored(None), "no_cards": scored(None)})
    assert kill_verdict(rows).startswith("No verdict")
    assert "n/a" in format_table(rows, "")


def test_the_pair_verdict_compares_the_lift_with_the_sum_of_the_singles():
    assert PairResult(("a", "b"), 0.5, 0.55, 0.55, 0.7).sees_interaction
    assert not PairResult(("a", "b"), 0.5, 0.55, 0.55, 0.6).sees_interaction


def test_the_pair_command_prints_four_probabilities_and_a_verdict_for_configured_cards(inputs, capsys):
    root, parquet, cards, deck_manifest = inputs
    assert main(["train", parquet, cards, deck_manifest, "--out", str(root / "model"), "--epochs", "1", "--width", "8",
                 "--min-count", "1", "--device", "cpu"]) == 0
    fixture = root / "pair.json"
    fixture.write_text(json.dumps({"cards": [{"name": "Platinum Angel", "live": {"power": 4, "toughness": 4, "is_creature": 1}},
                                             {"name": "Grizzly Bears"}]}), encoding="utf-8")
    capsys.readouterr()
    assert main(["pairtest", str(root / "model"), cards, "--fixture", str(fixture)]) == 0
    out = capsys.readouterr().out
    assert "Platinum Angel alone" in out and "Grizzly Bears alone" in out and "both" in out and "verdict" in out
    assert "empty board" in out


def test_a_pair_card_outside_the_dump_is_refused(inputs):
    root, _, cards, _ = inputs
    fixture = root / "bad.json"
    fixture.write_text(json.dumps({"cards": [{"name": "No Such Card"}, {"name": "Grizzly Bears"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="No Such Card"):
        main(["pairtest", str(root / "model"), cards, "--fixture", str(fixture)])
