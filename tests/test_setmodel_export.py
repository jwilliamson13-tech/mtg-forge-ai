"""The export bundle from the smoke model: ONNX against PyTorch, the table, and the committed contract."""

import gzip
import json
import re
from pathlib import Path

import numpy as np
import onnxruntime
import pytest

from setmodel.__main__ import main
from setmodel.dataset import LIVE_FIELDS, SCOREBOARD_FIELDS, load_corpus
from setmodel.export import (CARD_TABLE, CONTRACT, MODEL, SCALES, VOCAB, build_contract, export_bundle, export_check,
                             read_card_table)
from setmodel.features import Caps, encode
from setmodel.train import TrainConfig, train
from tests.setmodel_corpus import (card, card_table, game, manifest, smoke_games, snapshot, vocabulary, write_dump,
                                   write_manifest, write_parquet)

DOCS = Path(__file__).parent.parent / "docs"
COMMITTED = DOCS / "tensor-contract-v1.json"


def hidden_game():
    morph = card(None, 2, power=2, toughness=2, hidden=True)
    return game(99, [snapshot(1, battlefield=[morph], hand=[card("Fire // Ice", 1)])], decks=("Blue", "Red"))


@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    root = tmp_path_factory.mktemp("export")
    vocab, table = vocabulary(), card_table()
    corpus = load_corpus(write_parquet([*smoke_games(), hidden_game()], root), table, vocab, manifest())
    model = train(corpus, vocab, TrainConfig(epochs=2, width=16, device="cpu")).model.cpu().eval()
    contract = export_bundle(model, vocab, corpus.caps, {"generation": 1, "batch_ids": ["smoke"]}, table, corpus, root / "bundle")
    return model, corpus, contract, root / "bundle"


def test_onnx_matches_pytorch_on_held_out_states_built_from_the_bundle_table(smoke):
    model, corpus, _, bundle = smoke
    check = export_check(model, corpus, bundle)
    assert check.states == len(corpus.rows("held_out_games", "held_out_decks"))
    assert check.passed and check.max_difference < 1e-5


def test_the_check_fails_when_the_table_disagrees_with_the_model(smoke, tmp_path):
    model, corpus, _, bundle = smoke
    for name in (MODEL, CARD_TABLE):
        (tmp_path / name).write_bytes((bundle / name).read_bytes())
    text = gzip.decompress((tmp_path / CARD_TABLE).read_bytes()).decode("utf-8")
    lines = [json.loads(line) for line in text.splitlines()]
    for line in lines:
        line["numbers"] = [[[key, value + 5] for key, value in row] for row in line["numbers"]]
    (tmp_path / CARD_TABLE).write_bytes(gzip.compress("".join(json.dumps(line) + "\n" for line in lines).encode("utf-8")))
    assert not export_check(model, corpus, tmp_path).passed


def test_the_exported_card_axis_is_dynamic_and_takes_an_empty_board(smoke):
    _, _, contract, bundle = smoke
    session = onnxruntime.InferenceSession(bundle / MODEL, providers=["CPUExecutionProvider"])
    sizes = contract["sizes"]
    assert [item.name for item in session.get_inputs()] == [item["name"] for item in contract["inputs"]]
    for cards in (0, 60):
        inputs = {"scoreboard": np.zeros(sizes["S"], dtype=np.float32),
                  "token_ids": np.zeros((cards, sizes["A"], sizes["T"]), dtype=np.int64),
                  "token_mask": np.zeros((cards, sizes["A"], sizes["T"]), dtype=np.float32),
                  "numbers": np.zeros((cards, sizes["A"], sizes["K"]), dtype=np.float32),
                  "number_mask": np.zeros((cards, sizes["A"], sizes["K"]), dtype=np.float32),
                  "live": np.zeros((cards, sizes["L"]), dtype=np.float32), "zone": np.zeros(cards, dtype=np.int64)}
        p = session.run(None, inputs)[0]
        assert p.shape == (1,) and 0 < p[0] < 1


def test_the_bundle_holds_every_file_and_the_contract_round_trips(smoke):
    _, _, contract, bundle = smoke
    for name in (MODEL, CARD_TABLE, VOCAB, SCALES, CONTRACT):
        assert (bundle / name).exists()
    assert json.loads((bundle / CONTRACT).read_text(encoding="utf-8")) == contract
    assert contract["bundle"]["generation"] == 1
    assert set(contract["bundle"]["truncation_rates"]) >= {"abilities", "tokens", "numeric_keys", "live_state"}


def test_the_card_table_covers_whole_split_cards_and_shared_token_names(smoke):
    entries = read_card_table(smoke[3] / CARD_TABLE)
    assert ("Lightning Bolt", "front") in entries
    assert ("Fire // Ice", "front") in entries and ("Fire // Ice", "empty_room") in entries
    assert len(entries[("Elemental Token", "front")].tokens) == 1


def test_the_card_table_gives_a_whole_split_name_what_training_resolves_on_every_face(smoke):
    _, corpus, _, bundle = smoke
    entries = read_card_table(bundle / CARD_TABLE)
    for face, slot in (("Original", "front"), ("Backside", "back"), ("EmptyRoom", "empty_room")):
        trained_on = encode(card_table().resolve("Fire // Ice", face).rows, vocabulary(), corpus.caps)
        exported = entries[("Fire // Ice", slot)]
        assert (exported.tokens, exported.numbers) == (trained_on.tokens, trained_on.numbers)


def test_the_committed_contract_is_the_one_the_code_builds():
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed == build_contract(Caps(), committed["sizes"]["K"])
    assert committed["version"] == 1


def test_the_contract_doc_names_every_input_and_field():
    doc = (DOCS / "tensor-contract.md").read_text(encoding="utf-8")
    contract = json.loads(COMMITTED.read_text(encoding="utf-8"))
    named = set(re.findall(r"`([a-z_0-9]+)`", doc))
    expected = {item["name"] for item in contract["inputs"]} | set(LIVE_FIELDS) | set(SCOREBOARD_FIELDS) | set(contract["zones"])
    assert expected <= named


def test_the_command_writes_the_bundle_and_exits_zero_on_a_clean_check(tmp_path):
    parquet = write_parquet(smoke_games(), tmp_path)
    cards, deck_manifest = str(write_dump(tmp_path / "cards.jsonl")), str(write_manifest(tmp_path / "manifest.json"))
    assert main(["train", str(parquet), cards, deck_manifest, "--out", str(tmp_path / "model"), "--epochs", "1", "--width", "8",
                 "--min-count", "1", "--device", "cpu"]) == 0
    assert main(["export", str(tmp_path / "model"), str(parquet), cards, deck_manifest, "--out", str(tmp_path / "bundle")]) == 0
    assert (tmp_path / "bundle" / MODEL).exists()
