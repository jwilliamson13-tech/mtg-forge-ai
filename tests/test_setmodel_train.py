"""The training command, end to end on the synthetic corpus: files in, artifact and report out."""

import json

import numpy as np
import pytest
import torch

from setmodel.__main__ import main
from setmodel.dataset import HELD_OUT_DECKS, load_corpus
from setmodel.train import METRICS_FILE, MODEL_FILE, NO_CARDS, TrainConfig, ablated, load_model, probabilities, report, train
from tests.setmodel_corpus import (BATCH, HELD_OUT_DECK, card_table, manifest, smoke_games, vocabulary, write_dump,
                                   write_manifest, write_parquet)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One real command run, shared by the tests that read what it wrote."""
    root = tmp_path_factory.mktemp("run")
    parquet = write_parquet(smoke_games(), root)
    code = main(["train", str(parquet), str(write_dump(root / "cards.jsonl")), str(write_manifest(root / "manifest.json")),
                 "--out", str(root / "model"), "--generation", "3", "--epochs", "2", "--width", "16", "--min-count", "1",
                 "--device", "cpu"])
    assert code == 0
    return root / "model"


def test_the_report_has_the_fields_the_spec_names(run):
    metrics = json.loads((run / METRICS_FILE).read_text(encoding="utf-8"))
    assert set(metrics["held_out"]) == {"log_loss", "accuracy", "rows", "games"}
    assert metrics["held_out"]["rows"] > 0 and 0 <= metrics["held_out"]["accuracy"] <= 1
    assert metrics["held_out_decks"]["games"] == 5  # games 3, 6, 9, 12, 15
    assert set(metrics["per_deck_accuracy"]) == {HELD_OUT_DECK}
    assert metrics["per_deck_accuracy"][HELD_OUT_DECK]["rows"] > 0
    assert metrics["join"]["unmatched"] == []
    assert metrics["device"] == "cpu"


def test_the_artifact_names_its_generation_and_batches_and_loads_back(run):
    model, vocab, caps, metadata = load_model(run)
    assert metadata["generation"] == 3 and metadata["batch_ids"] == [BATCH]
    assert model.tokens.num_embeddings == len(vocab.tokens)
    assert (run / MODEL_FILE).exists() and caps.abilities == 16


def test_the_join_command_prints_the_report_with_no_training(tmp_path, capsys):
    parquet = write_parquet(smoke_games(4), tmp_path)
    assert main(["join", str(parquet), str(write_dump(tmp_path / "cards.jsonl")), str(write_manifest(tmp_path / "m.json")),
                 "--min-count", "1"]) == 0
    assert "unmatched names 0" in capsys.readouterr().out


def test_accuracy_weighs_a_game_as_one_whatever_its_length(tmp_path):
    corpus = load_corpus(write_parquet(smoke_games(), tmp_path), card_table(), vocabulary(), manifest())
    trained = train(corpus, vocabulary(), TrainConfig(epochs=1, width=8, device="cpu"))
    metrics = report(trained, corpus)
    rows = corpus.rows(HELD_OUT_DECKS)
    p = probabilities(trained, corpus, rows)
    by_game = {g: ((p >= 0.5) == (corpus.labels[rows] >= 0.5))[corpus.game[rows] == g].mean() for g in set(corpus.game[rows].tolist())}
    assert metrics[HELD_OUT_DECKS]["accuracy"] == pytest.approx(np.mean(list(by_game.values())))


def test_held_out_rows_never_touch_the_weights(tmp_path):
    """Training on the same train rows gives the same model, whatever the held-out rows hold."""
    def trained_on(games, root):
        corpus = load_corpus(write_parquet(games, root), card_table(), vocabulary(), manifest(), game_share=0.0)
        return train(corpus, vocabulary(), TrainConfig(epochs=2, width=8, device="cpu")).model.state_dict()

    games = smoke_games()
    changed = smoke_games()
    changed[2][1]["scoreboard"]["me_life"] = 1  # game 3 has the held-out deck
    before, after = trained_on(games, tmp_path / "a"), trained_on(changed, tmp_path / "b")
    assert all(torch.equal(before[name], after[name]) for name in before)


def test_ablating_all_cards_leaves_the_rows_and_empties_every_board(tmp_path):
    corpus = load_corpus(write_parquet(smoke_games(4), tmp_path), card_table(), vocabulary(), manifest())
    bare = ablated(corpus, NO_CARDS, corpus.key_scales())
    assert len(bare.labels) == len(corpus.labels) and bare.card_offsets[-1] == 0
