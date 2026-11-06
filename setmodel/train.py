"""Train the set model on a corpus and score it on what was held out.

Every number in the report weighs a game as 1, the same as the loss, so a long game
doesn't count for more than a short one.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import torch

from cardscript.vocab import Vocabulary
from setmodel.config import FULL, TrainConfig
from setmodel.dataset import HELD_OUT_DECKS, HELD_OUT_GAMES, SPLITS, TRAIN, ZONE_IDS, Corpus
from setmodel.features import Caps
from setmodel.model import ModelConfig, SetModel, weighted_loss

MODEL_FILE = "model.pt"
METRICS_FILE = "metrics.json"
EVAL_BATCH = 1024

NO_CARDS = "no_cards"
NO_GRAVEYARD_EXILE = "no_graveyard_exile"
NO_LIVE_STATE = "no_live_state"
NO_NUMBERS = "no_numbers"
ABLATIONS = (NO_CARDS, NO_GRAVEYARD_EXILE, NO_LIVE_STATE, NO_NUMBERS)
GRAVEYARD_EXILE = ("me_graveyard", "opp_graveyard", "me_exile", "opp_exile")


@dataclass
class Trained:
    model: SetModel
    vocab: Vocabulary
    config: TrainConfig
    device: str
    epoch_losses: list[float] = field(default_factory=list)


def pick_device(device: str) -> str:
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def _keep_cards(corpus: Corpus, keep: np.ndarray) -> Corpus:
    kept_before = np.concatenate([[0], np.cumsum(keep)])
    return replace(corpus, card_offsets=kept_before[corpus.card_offsets], card_feature=corpus.card_feature[keep],
                   card_zone=corpus.card_zone[keep], card_live=corpus.card_live[keep])


def ablated(corpus: Corpus, ablation: str, key_scale: np.ndarray) -> Corpus:
    """This corpus with one input group gone. The rest of the run doesn't change."""
    if ablation == FULL:
        return corpus
    if ablation == NO_CARDS:
        return _keep_cards(corpus, np.zeros(len(corpus.card_feature), dtype=bool))
    if ablation == NO_GRAVEYARD_EXILE:
        return _keep_cards(corpus, ~np.isin(corpus.card_zone, [ZONE_IDS[zone] for zone in GRAVEYARD_EXILE]))
    if ablation == NO_LIVE_STATE:
        return replace(corpus, card_live=np.zeros_like(corpus.card_live))
    if ablation == NO_NUMBERS:
        return replace(corpus, numbers=corpus.number_mask * key_scale)  # 1 after scaling: the key stays, as a token would
    raise ValueError(f"unknown ablation {ablation!r}")


def _batch(corpus: Corpus, rows: np.ndarray, device: str) -> dict[str, torch.Tensor]:
    """Tensors for these rows. Each distinct card appears once, and `slot` points the card rows at it."""
    positions, owner = corpus.cards_of(rows)
    used, slot = np.unique(corpus.card_feature[positions], return_inverse=True)

    def tensor(array: np.ndarray, dtype: torch.dtype = torch.float32) -> torch.Tensor:
        return torch.as_tensor(array, dtype=dtype, device=device)

    return {"token_ids": tensor(corpus.token_ids[used], torch.int64), "token_mask": tensor(corpus.token_mask[used]),
            "numbers": tensor(corpus.numbers[used]), "number_mask": tensor(corpus.number_mask[used]),
            "slot": tensor(slot, torch.int64), "live": tensor(corpus.card_live[positions]),
            "zone": tensor(corpus.card_zone[positions], torch.int64), "row": tensor(owner, torch.int64),
            "scoreboard": tensor(corpus.scoreboard[rows]), "labels": tensor(corpus.labels[rows]),
            "weights": tensor(corpus.weights[rows])}


def _logits(model: SetModel, batch: dict[str, torch.Tensor]) -> torch.Tensor:
    text = model.card_text(batch["token_ids"], batch["token_mask"], batch["numbers"], batch["number_mask"])
    return model.score(text[batch["slot"]], batch["live"], batch["zone"], batch["row"], batch["scoreboard"])


def train(corpus: Corpus, vocab: Vocabulary, config: TrainConfig) -> Trained:
    """Train on the corpus's training rows. Held-out rows are never read here."""
    device = pick_device(config.device)
    torch.manual_seed(config.seed)
    shuffler = np.random.default_rng(config.seed)
    key_scale = corpus.key_scales()
    corpus = ablated(corpus, config.ablation, key_scale)
    rows = corpus.rows(TRAIN)
    if not len(rows):
        raise ValueError("no training rows")
    loss_scale = float(corpus.weights[rows].mean()) * config.batch_size

    model_config = ModelConfig(len(vocab.tokens), len(vocab.numeric_keys), config.width)
    model = SetModel(model_config, torch.as_tensor(key_scale)).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
    trained = Trained(model, vocab, config, device)
    for _ in range(config.epochs):
        model.train()
        total = 0.0
        order = shuffler.permutation(rows)
        for start in range(0, len(order), config.batch_size):
            batch = _batch(corpus, order[start:start + config.batch_size], device)
            loss = weighted_loss(_logits(model, batch), batch["labels"], batch["weights"], loss_scale)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += loss.item() * config.batch_size
        trained.epoch_losses.append(total / len(order))
    return trained


def probabilities(trained: Trained, corpus: Corpus, rows: np.ndarray) -> np.ndarray:
    """The model's P(win) for these rows, in order."""
    model, device = trained.model, trained.device
    model.eval()
    out = []
    with torch.no_grad():
        for start in range(0, len(rows), EVAL_BATCH):
            out.append(torch.sigmoid(_logits(model, _batch(corpus, rows[start:start + EVAL_BATCH], device))).cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


def _scores(corpus: Corpus, rows: np.ndarray, p: np.ndarray) -> dict:
    """Log loss and accuracy over these rows. Null when there are none, because 0 would read as a result."""
    if not len(rows):
        return {"log_loss": None, "accuracy": None, "rows": 0, "games": 0}
    labels, weights = corpus.labels[rows], corpus.weights[rows]
    clipped = np.clip(p.astype(np.float64), 1e-7, 1 - 1e-7)
    losses = -(labels * np.log(clipped) + (1 - labels) * np.log(1 - clipped))
    right = (p >= 0.5) == (labels >= 0.5)
    return {"log_loss": float((losses * weights).sum() / weights.sum()), "accuracy": float((right * weights).sum() / weights.sum()),
            "rows": int(len(rows)), "games": int(len(np.unique(corpus.game[rows])))}


def report(trained: Trained, corpus: Corpus) -> dict:
    """Held-out scores. `held_out` covers both held-out splits, and it is the number ablations compare."""
    corpus = ablated(corpus, trained.config.ablation, trained.model.key_scale.cpu().numpy())
    rows = corpus.rows(HELD_OUT_GAMES, HELD_OUT_DECKS)
    p = probabilities(trained, corpus, rows)
    in_split = {split: corpus.split[rows] == SPLITS.index(split) for split in (HELD_OUT_GAMES, HELD_OUT_DECKS)}
    per_deck = {}
    for deck in corpus.held_out_decks:
        mine = np.zeros(len(rows), dtype=bool)
        if deck in corpus.decks:
            mine = corpus.own_deck[rows] == corpus.decks.index(deck)
        per_deck[deck] = _scores(corpus, rows[mine], p[mine])
    return {"held_out": _scores(corpus, rows, p),
            **{split: _scores(corpus, rows[mask], p[mask]) for split, mask in in_split.items()},
            "per_deck_accuracy": per_deck}


def save_run(trained: Trained, corpus: Corpus, out: str | Path, seconds: float) -> dict:
    """Write the artifact and the metrics report. The artifact holds all a later step needs: nothing is re-derived."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    config = trained.config
    metadata = {"generation": config.generation, "batch_ids": corpus.batch_ids, "ablation": config.ablation,
                "train_rows": int(len(corpus.rows(TRAIN))), "train_games": int(len(np.unique(corpus.game[corpus.rows(TRAIN)]))),
                "torch": str(torch.__version__), "device": _device_name(trained.device)}
    metrics = {**metadata, **report(trained, corpus), "epoch_loss": trained.epoch_losses, "seconds": seconds,
               "truncation": corpus.truncation(), "join": corpus.report.to_dict(), "config": asdict(config)}
    (out / METRICS_FILE).write_text(json.dumps(metrics, indent=1) + "\n", encoding="utf-8")
    torch.save({"state_dict": {k: v.cpu() for k, v in trained.model.state_dict().items()},
                "model_config": trained.model.config.to_dict(), "vocab": trained.vocab.to_dict(),
                "caps": asdict(corpus.caps), "metadata": metadata}, out / MODEL_FILE)
    return metrics


def _device_name(device: str) -> str:
    return torch.cuda.get_device_name(device) if device.startswith("cuda") else device


def load_model(folder: str | Path) -> tuple[SetModel, Vocabulary, Caps, dict]:
    artifact = torch.load(Path(folder) / MODEL_FILE, map_location="cpu", weights_only=True)
    state = artifact["state_dict"]
    model = SetModel(ModelConfig(**artifact["model_config"]), state["key_scale"])
    model.load_state_dict(state)
    model.eval()
    return model, Vocabulary.from_dict(artifact["vocab"]), Caps(**artifact["caps"]), artifact["metadata"]


def run(corpus: Corpus, vocab: Vocabulary, config: TrainConfig, out: str | Path) -> dict:
    started = time.monotonic()
    trained = train(corpus, vocab, config)
    return save_run(trained, corpus, out, time.monotonic() - started)
