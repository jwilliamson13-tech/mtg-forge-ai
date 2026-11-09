"""Scores from Forge against PyTorch's, and the fixture Forge's ONNX evaluator tests on."""

import gzip
import json

import pytest

from setmodel.__main__ import main
from setmodel.dataset import load_corpus
from setmodel.export import CARD_TABLE, CONTRACT, MODEL, SCALES, VOCAB
from setmodel.parity import corpus_scores, parity, read_scores, write_scores
from setmodel.train import TrainConfig, save_run, train
from tests import forge_fixture
from tests.setmodel_corpus import (card, card_table, game, manifest, smoke_games, snapshot, vocabulary, write_dump,
                                   write_manifest, write_parquet)


@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    root = tmp_path_factory.mktemp("parity")
    vocab = vocabulary()
    corpus = load_corpus(write_parquet(smoke_games(), root), card_table(), vocab, manifest())
    model = train(corpus, vocab, TrainConfig(epochs=1, width=8, device="cpu")).model.cpu().eval()
    return model, vocab, corpus


def test_a_row_knows_its_line_in_the_game_file_after_a_drop(tmp_path):
    lines = [snapshot(1), snapshot(2, battlefield=[card("No Such Card", 2)]), snapshot(1, turn=2), snapshot(2, turn=2)]
    corpus = load_corpus(write_parquet([game(1, lines)], tmp_path), card_table(), vocabulary(), manifest())
    assert corpus.snapshot_index.tolist() == [0, 2, 3]


def test_scores_round_trip_through_the_file(tmp_path):
    scores = {("smoke-7-1", 0): 0.25, ("smoke-7-1", 3): 0.123456789012345}
    write_scores(tmp_path / "scores.tsv", scores)
    assert read_scores(tmp_path / "scores.tsv") == scores


def test_a_file_without_the_header_is_refused(tmp_path):
    (tmp_path / "scores.tsv").write_text("smoke-7-1\t0\t0.5\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_scores(tmp_path / "scores.tsv")


def test_pytorch_against_itself_passes_and_a_drift_fails(smoke):
    model, vocab, corpus = smoke
    scores = corpus_scores(model, vocab, corpus)
    result = parity(model, vocab, corpus, scores)
    assert result.passed and result.matched == len(corpus.labels) and result.max_difference < 1e-6
    key = next(iter(scores))
    assert not parity(model, vocab, corpus, {**scores, key: scores[key] + 1e-3}).passed


def test_snapshots_the_corpus_lacks_are_counted_not_compared(smoke):
    model, vocab, corpus = smoke
    scores = {**corpus_scores(model, vocab, corpus), ("other-1-1", 0): 0.9}
    result = parity(model, vocab, corpus, scores)
    assert result.passed and result.not_in_corpus == 1
    assert not parity(model, vocab, corpus, {("other-1-1", 0): 0.9}).passed


def test_the_command_exits_by_the_check(tmp_path, smoke):
    model, vocab, corpus = smoke
    parquet = write_parquet(smoke_games(), tmp_path)
    cards, deck_manifest = str(write_dump(tmp_path / "cards.jsonl")), str(write_manifest(tmp_path / "manifest.json"))
    trained = train(corpus, vocab, TrainConfig(epochs=1, width=8, device="cpu"))
    save_run(trained, corpus, tmp_path / "model", 0.0)
    scores = corpus_scores(trained.model, vocab, corpus)
    write_scores(tmp_path / "good.tsv", scores)
    write_scores(tmp_path / "bad.tsv", {key: 1 - p for key, p in scores.items()})
    args = [str(tmp_path / "model"), str(parquet), cards, deck_manifest]
    assert main(["parity", *args, str(tmp_path / "good.tsv")]) == 0
    assert main(["parity", *args, str(tmp_path / "bad.tsv")]) == 1


def test_the_forge_fixture_holds_a_bundle_a_game_and_every_snapshot_scored(tmp_path):
    out = forge_fixture.build(tmp_path / "fixture")
    for name in (MODEL, CARD_TABLE, VOCAB, SCALES, CONTRACT):
        assert (out / "bundle" / name).exists()
    lines = gzip.decompress((out / "game.jsonl.gz").read_bytes()).decode("utf-8").splitlines()
    expected = read_scores(out / "expected.tsv")
    assert sorted(index for _, index in expected) == list(range(len(lines) - 1))
    assert all(0 < p < 1 for p in expected.values())
    assert json.loads((out / "bundle" / CONTRACT).read_text(encoding="utf-8"))["version"] == 2
    assert not (out / "work").exists()
