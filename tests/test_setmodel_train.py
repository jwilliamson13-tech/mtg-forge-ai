"""The training command, end to end on the synthetic corpus: files in, artifact and report out."""

import json
import subprocess
import sys
from dataclasses import replace

import numpy as np
import pytest
import torch

from setmodel import train as training
from setmodel.__main__ import main
from setmodel.dataset import HELD_OUT_DECKS, HELD_OUT_GAMES, SPLITS, TRAIN, ZONE_IDS, load_corpus
from setmodel.train import (GRAVEYARD_EXILE, METRICS_FILE, MODEL_FILE, NO_CARDS, NO_GRAVEYARD_EXILE, NO_LIVE_STATE,
                            TrainConfig, ablated, load_model, probabilities, report, train, validation_split)
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


def test_the_command_line_loads_without_torch():
    """`join` runs without PyTorch, so nothing the parser needs may import it."""
    code = "import sys, setmodel.__main__; sys.exit('torch' in sys.modules or 'onnx' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


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


def test_a_short_last_batch_gives_each_row_the_same_gradient_scale_as_a_full_batch(tmp_path, monkeypatch):
    """Spec: every game weighs 1 in the loss whatever its row count, so a row's batch can't change its pull."""
    seen = []
    real_loss = training.weighted_loss

    def spy(logits, labels, weights, scale):
        logits.retain_grad()
        seen.append((logits, labels, weights))
        return real_loss(logits, labels, weights, scale)

    monkeypatch.setattr(training, "weighted_loss", spy)
    corpus = load_corpus(write_parquet(smoke_games(), tmp_path), card_table(), vocabulary(), manifest(), game_share=0.0)
    rows = len(corpus.rows(TRAIN))
    batch_size = rows - 1  # one full batch, then a batch of one row
    config = TrainConfig(epochs=1, batch_size=batch_size, width=8, device="cpu", val_share=0.0)
    trained = train(corpus, vocabulary(), config)

    assert [len(labels) for _, labels, _ in seen] == [batch_size, 1]
    per_row = [logits.grad / (weights * (torch.sigmoid(logits) - labels)) for logits, labels, weights in seen]
    assert torch.allclose(per_row[1], per_row[0][:1], rtol=1e-4)
    assert torch.allclose(per_row[0], per_row[0][:1].expand_as(per_row[0]), rtol=1e-4)

    # The epoch's loss is still the weighted mean over its rows, tail included.
    bce = torch.nn.functional.binary_cross_entropy_with_logits
    weighted = sum((bce(logits, labels, reduction="none") * weights).sum() for logits, labels, weights in seen)
    mean = weighted.item() / corpus.weights[corpus.rows(TRAIN)].sum()
    assert trained.epoch_losses[0] == pytest.approx(mean, rel=1e-4)


def test_ablating_all_cards_leaves_the_rows_and_empties_every_board(tmp_path):
    corpus = load_corpus(write_parquet(smoke_games(4), tmp_path), card_table(), vocabulary(), manifest())
    bare = ablated(corpus, NO_CARDS, corpus.key_scales())
    assert len(bare.labels) == len(corpus.labels) and corpus.card_offsets[-1] > 0
    assert len(bare.cards_of(np.arange(len(bare.labels)))[0]) == 0


def test_the_zone_and_live_state_ablations_take_away_only_what_they_name(tmp_path):
    corpus = load_corpus(write_parquet(smoke_games(4), tmp_path), card_table(), vocabulary(), manifest())
    rows = np.arange(len(corpus.labels))
    positions, owner = corpus.cards_of(rows)
    gone = np.isin(corpus.card_zone[positions], [ZONE_IDS[zone] for zone in GRAVEYARD_EXILE])
    kept, kept_owner = ablated(corpus, NO_GRAVEYARD_EXILE, corpus.key_scales()).cards_of(rows)
    assert gone.any() and not gone.all()
    assert kept.tolist() == positions[~gone].tolist() and kept_owner.tolist() == owner[~gone].tolist()
    blank = ablated(corpus, NO_LIVE_STATE, corpus.key_scales())
    assert corpus.live(positions).any() and not blank.live(positions).any()


# Early stopping

def smoke_corpus(tmp_path, **kwargs):
    return load_corpus(write_parquet(smoke_games(), tmp_path), card_table(), vocabulary(), manifest(), **kwargs)


def test_the_validation_split_is_by_game_from_train_games_only_and_fixed_by_seed(tmp_path):
    corpus = smoke_corpus(tmp_path, game_share=0.0)
    rows = corpus.rows(TRAIN)
    fit, val = validation_split(corpus, rows, 0.2, seed=3)
    fit_games, val_games = set(corpus.game[fit].tolist()), set(corpus.game[val].tolist())
    assert val_games and fit_games and not fit_games & val_games
    assert len(fit) + len(val) == len(rows) and set(fit) | set(val) == set(rows)
    assert set(corpus.split[val]) == {SPLITS.index(TRAIN)}
    assert len(val_games) == round(0.2 * len(fit_games | val_games))
    again = validation_split(corpus, rows, 0.2, seed=3)[1]
    other = {tuple(validation_split(corpus, rows, 0.2, seed=n)[1]) for n in range(8)}
    assert val.tolist() == again.tolist() and len(other) > 1


def test_the_validation_split_keeps_at_least_one_game_on_each_side(tmp_path):
    corpus = smoke_corpus(tmp_path, game_share=0.0)
    rows = corpus.rows(TRAIN)
    games = len(np.unique(corpus.game[rows]))
    tiny = validation_split(corpus, rows, 0.001, seed=0)[1]
    huge = validation_split(corpus, rows, 0.999, seed=0)[0]
    assert len(np.unique(corpus.game[tiny])) == 1 and len(np.unique(corpus.game[huge])) == 1 and games > 2
    assert not len(validation_split(corpus, rows, 0.0, seed=0)[1])
    one_game = rows[corpus.game[rows] == corpus.game[rows][0]]
    assert not len(validation_split(corpus, one_game, 0.5, seed=0)[1])
    with pytest.raises(ValueError):
        validation_split(corpus, rows, 1.0, seed=0)


def test_training_and_validation_read_only_train_rows_and_the_weights_never_see_validation_rows(tmp_path, monkeypatch):
    corpus = smoke_corpus(tmp_path)
    held = set(corpus.rows(HELD_OUT_GAMES, HELD_OUT_DECKS).tolist())
    assert held
    seen = {"train": set(), "validation": set()}
    real = training._batch

    def spy(corpus_, rows, device):
        caller = sys._getframe(1).f_code.co_name
        seen["validation" if caller == "probabilities" else "train"].update(rows.tolist())
        return real(corpus_, rows, device)

    monkeypatch.setattr(training, "_batch", spy)
    trained = train(corpus, vocabulary(), TrainConfig(epochs=2, width=8, device="cpu", val_share=0.25))
    assert trained.val_games >= 1 and trained.curve
    assert not (seen["train"] | seen["validation"]) & held
    assert not seen["train"] & seen["validation"]
    assert seen["validation"] and seen["train"] | seen["validation"] == set(corpus.rows(TRAIN).tolist())


def test_the_best_state_is_restored_and_the_run_stops_after_patience(tmp_path, monkeypatch):
    corpus = smoke_corpus(tmp_path)
    script = iter([0.50, 0.40, 0.45, 0.60, 0.70, 0.80, 0.90])
    states = []
    real_probabilities = training.probabilities

    def spy_probabilities(trained, corpus_, rows):
        states.append({k: v.detach().clone() for k, v in trained.model.state_dict().items()})
        return real_probabilities(trained, corpus_, rows)

    monkeypatch.setattr(training, "probabilities", spy_probabilities)
    monkeypatch.setattr(training, "_scores", lambda *_: {"log_loss": next(script), "accuracy": 0.5})
    trained = train(corpus, vocabulary(), TrainConfig(epochs=10, width=8, device="cpu", eval_every=0, patience=2))
    assert [point["val_log_loss"] for point in trained.curve] == [0.50, 0.40, 0.45, 0.60]
    assert trained.stopped_early and trained.kept["val_log_loss"] == 0.40 and trained.kept["epoch"] == 2
    assert len(trained.epoch_losses) == 4
    final = trained.model.state_dict()
    assert all(torch.equal(final[name], states[1][name]) for name in final)
    assert not all(torch.equal(final[name], states[3][name]) for name in final)


def test_a_run_that_keeps_improving_goes_to_the_epoch_cap(tmp_path, monkeypatch):
    corpus = smoke_corpus(tmp_path)
    losses = iter([0.5, 0.4, 0.3])
    monkeypatch.setattr(training, "_scores", lambda *_: {"log_loss": next(losses), "accuracy": 0.5})
    trained = train(corpus, vocabulary(), TrainConfig(epochs=3, width=8, device="cpu", eval_every=0, patience=1))
    assert not trained.stopped_early and trained.kept["epoch"] == 3 and len(trained.epoch_losses) == 3


def test_validation_can_score_inside_an_epoch(tmp_path):
    corpus = smoke_corpus(tmp_path)
    config = TrainConfig(epochs=2, batch_size=4, width=8, device="cpu", eval_every=5, patience=1000)
    trained = train(corpus, vocabulary(), config)
    steps = [point["step"] for point in trained.curve]
    assert steps[:3] == [5, 10, 14] and len(steps) > 4  # 14 batches an epoch, so the epoch's end scores at 14
    epochs = [point["epoch"] for point in trained.curve]
    assert epochs == sorted(epochs) and epochs[-1] == 2.0 and epochs[0] < 1
    assert all(point["val_accuracy"] is not None and point["train_loss"] > 0 for point in trained.curve)


def test_without_validation_every_epoch_runs_and_the_last_state_is_kept(tmp_path):
    corpus = smoke_corpus(tmp_path)
    trained = train(corpus, vocabulary(), TrainConfig(epochs=3, width=8, device="cpu", val_share=0.0))
    assert trained.curve == [] and trained.kept is None and not trained.stopped_early and trained.val_rows == 0
    assert len(trained.epoch_losses) == 3


def test_weight_decay_changes_the_weights_and_zero_matches_the_default(tmp_path):
    corpus = smoke_corpus(tmp_path)
    base = TrainConfig(epochs=1, width=8, device="cpu", val_share=0.0)
    plain = train(corpus, vocabulary(), base).model.state_dict()
    zero = train(corpus, vocabulary(), replace(base, weight_decay=0.0)).model.state_dict()
    decayed = train(corpus, vocabulary(), replace(base, weight_decay=0.5)).model.state_dict()
    assert all(torch.equal(plain[name], zero[name]) for name in plain)
    assert not all(torch.equal(plain[name], decayed[name]) for name in plain)


def test_the_metrics_record_the_curve_and_the_kept_point(run):
    metrics = json.loads((run / METRICS_FILE).read_text(encoding="utf-8"))
    validation = metrics["validation"]
    assert validation["share"] == 0.1 and validation["patience"] == 4 and validation["games"] >= 1
    assert validation["rows"] > 0 and len(validation["curve"]) == 2  # 2 epochs, and eval_every 2000 is past them
    assert set(validation["curve"][0]) == {"step", "epoch", "train_loss", "val_log_loss", "val_accuracy"}
    assert validation["kept"] in validation["curve"]
    assert metrics["config"]["eval_every"] == 2000 and metrics["config"]["weight_decay"] == 0.0


def test_the_command_line_takes_the_stopping_flags():
    from setmodel.__main__ import _config, build_parser
    args = build_parser().parse_args(["train", "p", "c", "m", "--out", "o", "--val-share", "0.2", "--patience", "7",
                                      "--eval-every", "50", "--weight-decay", "0.01"])
    config = _config(args)
    assert (config.val_share, config.patience, config.eval_every, config.weight_decay) == (0.2, 7, 50, 0.01)
    defaults = _config(build_parser().parse_args(["ablate", "p", "c", "m", "--out", "o"]))
    assert (defaults.val_share, defaults.patience) == (0.1, 4)
