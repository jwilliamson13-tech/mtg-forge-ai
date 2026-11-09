"""Tensor contract version 2 and game-record schema version 2, end to end on hand-built games.

Three changes, decided 2026-11-08:

1. The initiative, the monarch and speed are scoreboard fields, written by the recorder.
2. A stack ability with no card behind it (an effect, The Initiative, The Monarch, speed) is left
   out of the state instead of dropping the snapshot.
3. The search's `hidden` placeholder is a hidden card in any zone.
"""

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from gamerecord import GameRecordError, load_game
from gamerecord.convert import convert_folder
from gamerecord.record import SCHEMA_VERSION, V1_DEFAULTS
from setmodel.config import CONTRACT_VERSION
from setmodel.dataset import LIVE, PLACEHOLDER_NAME, SCOREBOARD_FIELDS, ZONE_IDS, load_corpus
from setmodel.export import build_contract
from setmodel.features import Caps
from setmodel.train import MODEL_FILE, TrainConfig, load_model, save_run, train
from simrunner.generations import GenerationError, resolve
from tests.setmodel_corpus import (card, card_table, game, manifest, smoke_games, snapshot, vocabulary, write_games,
                                   write_parquet)

EFFECT = "Tamiyo, Seasoned Scholar (48)'s Effect"


def stack_item(name, controller, kind="ability", *, id=90):
    return {"id": id, "name": name, "face": "Original", "face_down": False, "controller": controller, "kind": kind,
            "targets": []}


def corpus_of(games, root):
    return load_corpus(write_parquet(games, root), card_table(), vocabulary(), manifest())


def as_version_1(lines):
    """The same game as a schema version 1 file: no initiative, monarch or speed fields."""
    lines = json.loads(json.dumps(lines))
    lines[0]["schema_version"] = 1
    for line in lines[1:]:
        for name in V1_DEFAULTS:
            del line["scoreboard"][name]
    return lines


def marker_game():
    first = snapshot(1, battlefield=[card("Grizzly Bears", 1, id=3, power=2, toughness=2)])
    first["scoreboard"].update(me_initiative=True, opp_monarch=True, me_speed=3, opp_speed=1)
    second = snapshot(2)
    second["scoreboard"].update(opp_initiative=True, me_monarch=True, me_speed=1, opp_speed=3)
    return game(1, [first, second])


# Part 1: scoreboard fields


def test_the_versions_are_two():
    assert SCHEMA_VERSION == 2
    assert CONTRACT_VERSION == 2
    assert build_contract(Caps(), 10)["version"] == 2


def test_the_scoreboard_ends_with_the_six_new_fields_in_order():
    assert SCOREBOARD_FIELDS[-6:] == ("me_initiative", "opp_initiative", "me_monarch", "opp_monarch", "me_speed",
                                      "opp_speed")
    assert len(SCOREBOARD_FIELDS) == build_contract(Caps(), 10)["sizes"]["S"] == 16


def test_a_version_2_game_carries_the_markers_into_the_rows(tmp_path):
    corpus = corpus_of([marker_game()], tmp_path)
    board = {name: corpus.scoreboard[:, i].tolist() for i, name in enumerate(SCOREBOARD_FIELDS)}
    assert board["me_initiative"] == [1.0, 0.0]
    assert board["opp_initiative"] == [0.0, 1.0]
    assert board["me_monarch"] == [0.0, 1.0]
    assert board["opp_monarch"] == [1.0, 0.0]
    assert board["me_speed"] == [3.0, 1.0]
    assert board["opp_speed"] == [1.0, 3.0]


def test_a_version_1_file_still_loads_with_the_markers_off(tmp_path):
    folder = write_games([as_version_1(marker_game())], tmp_path)
    loaded = load_game(next(folder.glob("*.jsonl.gz")))
    assert loaded.schema_version == 1
    assert all(line["scoreboard"][name] == value for line in loaded.snapshots for name, value in V1_DEFAULTS.items())
    report = convert_folder(folder, tmp_path / "parquet")
    assert report.version_1_games == 1 and not report.rejected


def test_a_version_1_file_may_not_carry_version_2_fields(tmp_path):
    lines = as_version_1(marker_game())
    lines[1]["scoreboard"]["me_speed"] = 2
    folder = write_games([lines], tmp_path)
    with pytest.raises(GameRecordError, match="unknown field me_speed"):
        load_game(next(folder.glob("*.jsonl.gz")))


@pytest.mark.parametrize("edit, message", [
    ({"me_initiative": True, "opp_initiative": True}, "only one player can have the initiative"),
    ({"me_monarch": True, "opp_monarch": True}, "only one player can have the monarch"),
    ({"me_speed": 5}, "outside 0 to 4"),
    ({"opp_monarch": 1}, "true or false"),
])
def test_impossible_markers_are_refused(tmp_path, edit, message):
    lines = marker_game()
    lines[2]["scoreboard"].update(edit)
    folder = write_games([lines], tmp_path)
    with pytest.raises(GameRecordError, match=message):
        load_game(next(folder.glob("*.jsonl.gz")))


REAL_V2 = Path(__file__).parent / "fixtures" / "games"


@pytest.mark.parametrize("name, marker, holding, on_stack", [
    ("real_game_v2_initiative", "initiative", 54, 6),
    ("real_game_v2_monarch", "monarch", 120, 16),
])
def test_a_real_version_2_file_says_who_holds_a_marker_far_more_often_than_its_stack_does(name, marker, holding,
                                                                                         on_stack):
    """Files the fork's recorder wrote on 2026-11-08 (initiative-stompy-1 vs hogaak-2, seed 81, and dredge-2 vs
    uwx-control-2, seed 82). A version 1 file would show the marker only while its trigger is on the stack."""
    loaded = load_game(REAL_V2 / f"{name}.jsonl.gz")
    assert loaded.schema_version == 2
    boards = [line["scoreboard"] for line in loaded.snapshots]
    assert sum(board[f"me_{marker}"] or board[f"opp_{marker}"] for board in boards) == holding
    title = "The Initiative" if marker == "initiative" else "The Monarch"
    assert sum(item["name"] == title for line in loaded.snapshots for item in line["stack"]) == on_stack
    for line in loaded.snapshots:  # the stack item's controller holds the marker in that same line
        for item in line["stack"]:
            if item["name"] == title:
                me = item["controller"] == line["side"]
                assert line["scoreboard"][f"me_{marker}" if me else f"opp_{marker}"]


# Part 2: stack abilities with no card behind them


@pytest.mark.parametrize("name", ["The Initiative", "The Monarch", "Start Your Engines!", EFFECT])
def test_a_stack_ability_with_no_card_is_left_out_and_the_snapshot_kept(tmp_path, name):
    bolt = stack_item("Lightning Bolt", 2, "spell", id=91)
    lines = game(1, [snapshot(1, stack=[stack_item(name, 1), bolt]), snapshot(2)])
    corpus = corpus_of([lines], tmp_path)
    assert corpus.report.snapshots_dropped == 0
    assert not corpus.report.unmatched
    assert dict(corpus.report.skipped) == {name.replace("(48)", "(N)"): 1}
    row = int(np.flatnonzero(corpus.snapshot_index == 0)[0])
    cards = range(corpus.card_offsets[row], corpus.card_offsets[row + 1])
    assert [int(corpus.card_zone[c]) for c in cards] == [ZONE_IDS["stack"]]  # only the Bolt
    assert corpus.feature_keys[corpus.card_feature[cards[0]]] == ("Lightning Bolt", "front")


def test_a_spell_the_table_lacks_still_drops_its_snapshot(tmp_path):
    lines = game(1, [snapshot(1, stack=[stack_item("A Card From Next Year", 1, "spell")]), snapshot(2)])
    corpus = corpus_of([lines], tmp_path)
    assert corpus.report.snapshots_dropped == 1
    assert dict(corpus.report.unmatched) == {("A Card From Next Year", "Original"): 1}
    assert not corpus.report.skipped


def test_an_unmatched_card_in_a_zone_still_drops_its_snapshot(tmp_path):
    lines = game(1, [snapshot(1, battlefield=[card("The Monarch", 1)]), snapshot(2)])
    corpus = corpus_of([lines], tmp_path)
    assert corpus.report.snapshots_dropped == 1


def test_the_report_lists_left_out_abilities(tmp_path):
    lines = game(1, [snapshot(1, stack=[stack_item(EFFECT, 1)]), snapshot(2, stack=[stack_item(EFFECT, 1)])])
    corpus = corpus_of([lines], tmp_path)
    text = corpus.report.format()
    assert "stack abilities left out, no card behind them: 2 rows, 1 names" in text
    assert "Tamiyo, Seasoned Scholar (N)'s Effect" in text
    assert corpus.report.to_dict()["skipped"] == [{"name": "Tamiyo, Seasoned Scholar (N)'s Effect", "rows": 2}]


# Part 3: the search's placeholder


def test_the_placeholder_in_a_public_zone_is_a_hidden_card(tmp_path):
    placeholder = card(PLACEHOLDER_NAME, 2, id=7, tapped=True)
    in_graveyard = card(PLACEHOLDER_NAME, 1, id=8)
    lines = game(1, [snapshot(1, battlefield=[placeholder], graveyard=[in_graveyard],
                              stack=[stack_item(PLACEHOLDER_NAME, 2, "spell")]), snapshot(2)])
    corpus = corpus_of([lines], tmp_path)
    assert corpus.report.snapshots_dropped == 0 and not corpus.report.unmatched and not corpus.report.skipped
    row = int(np.flatnonzero(corpus.snapshot_index == 0)[0])
    cards = np.arange(corpus.card_offsets[row], corpus.card_offsets[row + 1])
    assert len(cards) == 3
    assert (corpus.card_feature[cards] == 0).all()  # the empty card: every mask 0
    live = corpus.live(cards)
    assert live[:, LIVE["hidden"]].tolist() == [1.0, 1.0, 1.0]
    assert live[:, LIVE["face_down"]].tolist() == [0.0, 0.0, 0.0]
    assert live[0, LIVE["tapped"]] == 1.0
    assert corpus.report.hidden_cards == 3


# Contract mechanics


def test_a_model_records_its_contract_and_a_version_1_artifact_is_refused(tmp_path):
    vocab = vocabulary()
    corpus = corpus_of(smoke_games(), tmp_path)
    trained = train(corpus, vocab, TrainConfig(epochs=1, width=8, device="cpu"))
    save_run(trained, corpus, tmp_path / "model", 0.0)
    _, _, _, metadata = load_model(tmp_path / "model")
    assert metadata["contract"] == 2

    artifact = torch.load(tmp_path / "model" / MODEL_FILE, weights_only=True)
    del artifact["metadata"]["contract"]  # what every artifact before version 2 looks like
    torch.save(artifact, tmp_path / "model" / MODEL_FILE)
    with pytest.raises(ValueError, match="tensor contract version 1"):
        load_model(tmp_path / "model")


def bundle(folder, version):
    folder.mkdir(parents=True)
    (folder / "contract.json").write_text(json.dumps({"version": version, "bundle": {"generation": 0}}), encoding="utf-8")
    (folder / "model.onnx").write_bytes(b"weights")
    return folder


@pytest.mark.parametrize("version", [1, 2])
def test_a_generation_of_either_version_resolves(tmp_path, version):
    bundle(tmp_path / "models" / "gen0", version)
    assert resolve("gen0", tmp_path / "models").number == 0


@pytest.mark.parametrize("version", [3, 0, "2", None])
def test_a_generation_of_another_version_is_refused(tmp_path, version):
    bundle(tmp_path / "models" / "gen0", version)
    with pytest.raises(GenerationError, match="tensor contract version"):
        resolve("gen0", tmp_path / "models")
