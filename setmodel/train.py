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
from setmodel.config import CONTRACT_VERSION, FULL, TrainConfig
from setmodel.dataset import HELD_OUT_DECKS, HELD_OUT_GAMES, SPLITS, TRAIN, ZONE_IDS, ZONE_NAMES, Corpus
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
    curve: list[dict] = field(default_factory=list)  # one point per validation score
    kept: dict | None = None  # the curve point whose weights the model holds, None without validation
    stopped_early: bool = False
    val_rows: int = 0
    val_games: int = 0


def pick_device(device: str) -> str:
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def ablated(corpus: Corpus, ablation: str, key_scale: np.ndarray) -> Corpus:
    """This corpus with one input group gone. The rest of the run doesn't change.

    Nothing here copies the card arrays: dropped cards are skipped where a batch is built,
    and the live state is replaced by a zero view.
    """
    if ablation == FULL:
        return corpus
    if ablation == NO_CARDS:
        return replace(corpus, dropped_zones=tuple(range(len(ZONE_NAMES))))
    if ablation == NO_GRAVEYARD_EXILE:
        return replace(corpus, dropped_zones=tuple(ZONE_IDS[zone] for zone in GRAVEYARD_EXILE))
    if ablation == NO_LIVE_STATE:
        return replace(corpus, card_flags=_zeros_like(corpus.card_flags), card_counts=_zeros_like(corpus.card_counts))
    if ablation == NO_NUMBERS:
        # 1 after scaling: the key stays, as a token would
        return replace(corpus, numbers=corpus.number_mask * key_scale)
    raise ValueError(f"unknown ablation {ablation!r}")


def _zeros_like(array: np.ndarray) -> np.ndarray:
    """Zeros of this shape and type that take no memory. Indexing it gives real zeros."""
    return np.broadcast_to(np.zeros((), dtype=array.dtype), array.shape)


def _batch(corpus: Corpus, rows: np.ndarray, device: str) -> dict[str, torch.Tensor]:
    """Tensors for these rows. Each distinct card appears once, and `slot` points the card rows at it."""
    positions, owner = corpus.cards_of(rows)
    used, slot = np.unique(corpus.card_feature[positions], return_inverse=True)

    def tensor(array: np.ndarray, dtype: torch.dtype = torch.float32) -> torch.Tensor:
        return torch.as_tensor(array, dtype=dtype, device=device)

    return {"token_ids": tensor(corpus.token_ids[used], torch.int64), "token_mask": tensor(corpus.token_mask[used]),
            "numbers": tensor(corpus.numbers[used]), "number_mask": tensor(corpus.number_mask[used]),
            "slot": tensor(slot, torch.int64), "live": tensor(corpus.live(positions)),
            "zone": tensor(corpus.card_zone[positions], torch.int64), "row": tensor(owner, torch.int64),
            "scoreboard": tensor(corpus.scoreboard[rows]), "labels": tensor(corpus.labels[rows]),
            "weights": tensor(corpus.weights[rows])}


def _logits(model: SetModel, batch: dict[str, torch.Tensor]) -> torch.Tensor:
    text = model.card_text(batch["token_ids"], batch["token_mask"], batch["numbers"], batch["number_mask"])
    return model.score(text[batch["slot"]], batch["live"], batch["zone"], batch["row"], batch["scoreboard"])


def validation_split(corpus: Corpus, rows: np.ndarray, share: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Split these training rows into (fit, validation) by whole game.

    Rows of one game share a label and are near copies, so a split by row would score the model on games it
    trained on. `share` of the games, at least one and never all, go to validation, drawn from a seeded generator
    of their own so the shuffler's stream doesn't change. A corpus with under two training games, or a share of 0,
    has no validation rows.
    """
    if not 0 <= share < 1:
        raise ValueError(f"val_share must be in [0, 1), got {share}")
    games = np.unique(corpus.game[rows])
    if share == 0 or len(games) < 2:
        return rows, rows[:0]
    count = min(max(1, round(share * len(games))), len(games) - 1)
    chosen = np.random.default_rng([seed, 1]).permutation(games)[:count]
    held = np.isin(corpus.game[rows], chosen)
    return rows[~held], rows[held]


def train(corpus: Corpus, vocab: Vocabulary, config: TrainConfig) -> Trained:
    """Train on the corpus's training rows. Held-out rows are never read here.

    `val_share` of the training games are held back, and their log loss picks when to stop: it is scored every
    `eval_every` batches and at each epoch's end, the best state is kept, and the run stops after `patience`
    scores without an improvement. The kept state is restored before this returns.
    """
    device = pick_device(config.device)
    torch.manual_seed(config.seed)
    shuffler = np.random.default_rng(config.seed)
    key_scale = corpus.key_scales()
    corpus = ablated(corpus, config.ablation, key_scale)
    all_rows = corpus.rows(TRAIN)
    if not len(all_rows):
        raise ValueError("no training rows")
    rows, val_rows = validation_split(corpus, all_rows, config.val_share, config.seed)
    loss_scale = float(corpus.weights[rows].mean()) * config.batch_size

    model_config = ModelConfig(len(vocab.tokens), len(vocab.numeric_keys), config.width)
    model = SetModel(model_config, torch.as_tensor(key_scale)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    trained = Trained(model, vocab, config, device)
    trained.val_rows, trained.val_games = int(len(val_rows)), int(len(np.unique(corpus.game[val_rows])))
    best_state, best_loss, since_best, step = None, float("inf"), 0, 0
    window_total, window_rows = 0.0, 0

    def evaluate(epoch: float) -> bool:
        """Score the validation games, keep the state if it is the best yet, and say whether to stop."""
        nonlocal best_state, best_loss, since_best, window_total, window_rows
        scores = _scores(corpus, val_rows, probabilities(trained, corpus, val_rows))
        point = {"step": step, "epoch": round(epoch, 4), "train_loss": window_total / window_rows,
                 "val_log_loss": scores["log_loss"], "val_accuracy": scores["accuracy"]}
        trained.curve.append(point)
        window_total, window_rows = 0.0, 0
        if scores["log_loss"] < best_loss:
            best_loss, since_best = scores["log_loss"], 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            trained.kept = point
        else:
            since_best += 1
        model.train()
        return since_best >= config.patience

    stop = False
    for epoch in range(config.epochs):
        model.train()
        total, seen = 0.0, 0
        order = shuffler.permutation(rows)
        for start in range(0, len(order), config.batch_size):
            chunk = order[start:start + config.batch_size]
            batch = _batch(corpus, chunk, device)
            loss = weighted_loss(_logits(model, batch), batch["labels"], batch["weights"], loss_scale)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += loss.item() * config.batch_size
            seen += len(chunk)
            window_total += loss.item() * config.batch_size
            window_rows += len(chunk)
            step += 1
            if len(val_rows) and config.eval_every and step % config.eval_every == 0 and start + config.batch_size < len(order):
                if evaluate(epoch + (start + len(chunk)) / len(order)):
                    stop = True
                    break
        trained.epoch_losses.append(total / seen)
        if stop:
            break
        if len(val_rows) and evaluate(epoch + 1):
            stop = True
            break
    trained.stopped_early = stop
    if best_state is not None:
        model.load_state_dict(best_state)
    return trained


def probabilities(trained: Trained, corpus: Corpus, rows: np.ndarray) -> np.ndarray:
    """The model's P(win) for these rows, in order."""
    model, device = trained.model, trained.device
    model.eval()
    out = []
    with torch.no_grad():
        for start in range(0, len(rows), EVAL_BATCH):
            batch = _batch(corpus, rows[start:start + EVAL_BATCH], device)
            out.append(torch.sigmoid(_logits(model, batch)).cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


def _scores(corpus: Corpus, rows: np.ndarray, p: np.ndarray) -> dict:
    """Log loss and accuracy over these rows. Null when there are none, because 0 would read as a result."""
    if not len(rows):
        return {"log_loss": None, "accuracy": None, "rows": 0, "games": 0}
    labels, weights = corpus.labels[rows], corpus.weights[rows]
    clipped = np.clip(p.astype(np.float64), 1e-7, 1 - 1e-7)
    losses = -(labels * np.log(clipped) + (1 - labels) * np.log(1 - clipped))
    right = (p >= 0.5) == (labels >= 0.5)
    return {"log_loss": float((losses * weights).sum() / weights.sum()),
            "accuracy": float((right * weights).sum() / weights.sum()),
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
    metadata = {"contract": CONTRACT_VERSION, "generation": config.generation, "batch_ids": corpus.batch_ids,
                "ablation": config.ablation,
                "train_rows": int(len(corpus.rows(TRAIN))),
                "train_games": int(len(np.unique(corpus.game[corpus.rows(TRAIN)]))),
                "torch": str(torch.__version__), "device": _device_name(trained.device)}
    metrics = {**metadata, **report(trained, corpus), "epoch_loss": trained.epoch_losses,
               "validation": {"share": config.val_share, "games": trained.val_games, "rows": trained.val_rows,
                              "eval_every": config.eval_every, "patience": config.patience,
                              "stopped_early": trained.stopped_early, "kept": trained.kept, "curve": trained.curve},
               "seconds": seconds,
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
    version = artifact["metadata"].get("contract", 1)  # artifacts from before the field were all version 1
    if version != CONTRACT_VERSION:
        raise ValueError(f"{folder}: the model was trained under tensor contract version {version}, and this code "
                         f"builds version {CONTRACT_VERSION} inputs. Use a checkout of that version to export, "
                         f"review or check it.")
    state = artifact["state_dict"]
    model = SetModel(ModelConfig(**artifact["model_config"]), state["key_scale"])
    model.load_state_dict(state)
    model.eval()
    return model, Vocabulary.from_dict(artifact["vocab"]), Caps(**artifact["caps"]), artifact["metadata"]


def run(corpus: Corpus, vocab: Vocabulary, config: TrainConfig, out: str | Path) -> dict:
    started = time.monotonic()
    trained = train(corpus, vocab, config)
    return save_run(trained, corpus, out, time.monotonic() - started)
