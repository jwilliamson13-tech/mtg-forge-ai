"""The two-level set model: tokens into abilities, abilities into cards, cards into zones, zones into a win probability.

Every level is a mean over a set followed by a small MLP, so the order of tokens,
abilities or cards can't matter, and a set of any size goes through the same weights.

Inputs are raw game values. The constants that scale them live in the model as buffers,
so the exported file carries them and Forge has nothing to keep in step.

`card_text` depends only on the card, not on the game, so training runs it once per
distinct card in a batch. `score` takes it from there. `StateModel` joins the two for one
state, which is the shape the tensor contract gives Forge.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import Tensor, nn

from setmodel.dataset import LIVE_FIELDS, SCOREBOARD_FIELDS, ZONE_NAMES

# What a typical value is, so each input starts near 1. These are fixed, not learned from a corpus.
SCOREBOARD_SCALE = {"me_life": 20, "opp_life": 20, "me_poison": 10, "opp_poison": 10, "me_library": 60,
                    "opp_library": 60, "opp_hand": 7, "turn": 20, "me_active": 1, "me_on_play": 1,
                    "me_initiative": 1, "opp_initiative": 1, "me_monarch": 1, "opp_monarch": 1, "me_speed": 4,
                    "opp_speed": 4}
LIVE_SCALE = {"power": 5, "toughness": 5, "damage": 5, "counters_p1p1": 5, "counters_m1m1": 5, "counters_loyalty": 5,
              "counters_other": 5}


@dataclass(frozen=True)
class ModelConfig:
    token_count: int
    key_count: int
    width: int = 64

    def to_dict(self) -> dict:
        return asdict(self)


def _mlp(inputs: int, outputs: int, hidden: int) -> nn.Sequential:
    return nn.Sequential(nn.Linear(inputs, hidden), nn.ReLU(), nn.Linear(hidden, outputs))


def _masked_mean(values: Tensor, mask: Tensor, dim: int) -> Tensor:
    """The mean over `dim` of the entries `mask` keeps. Zeros if it keeps none."""
    return (values * mask.unsqueeze(-1)).sum(dim) / mask.sum(dim).clamp(min=1).unsqueeze(-1)


class SetModel(nn.Module):
    def __init__(self, config: ModelConfig, key_scale: Tensor):
        super().__init__()
        width = config.width
        self.config = config
        self.tokens = nn.Embedding(config.token_count, width)
        self.keys = nn.Embedding(config.key_count, width)
        self.zones = nn.Embedding(len(ZONE_NAMES), width)
        self.ability = _mlp(width, width, width)
        self.card = _mlp(width, width, width)
        self.placed = _mlp(width + len(LIVE_FIELDS), width, width)  # a card in its zone, with its live state
        self.head = _mlp(len(ZONE_NAMES) * width + len(SCOREBOARD_FIELDS), 1, 2 * width)
        self.register_buffer("key_scale", key_scale.float())
        self.register_buffer("scoreboard_scale", torch.tensor([float(SCOREBOARD_SCALE[f]) for f in SCOREBOARD_FIELDS]))
        self.register_buffer("live_scale", torch.tensor([float(LIVE_SCALE.get(f, 1)) for f in LIVE_FIELDS]))

    def card_text(self, token_ids: Tensor, token_mask: Tensor, numbers: Tensor, number_mask: Tensor) -> Tensor:
        """[C, A, T] tokens and [C, A, K] numbers to one vector per card, [C, width]."""
        tokens = _masked_mean(self.tokens(token_ids), token_mask, dim=2)
        scaled = numbers * number_mask / self.key_scale
        abilities = self.ability(tokens + scaled @ self.keys.weight)
        present = ((token_mask.sum(2) + number_mask.sum(2)) > 0).to(abilities.dtype)
        return self.card(_masked_mean(abilities, present, dim=1))

    def score(self, text: Tensor, live: Tensor, zone: Tensor, row: Tensor, scoreboard: Tensor) -> Tensor:
        """Cards of a batch of states to one logit per state. `row` says which state each card is in.

        An empty zone pools to zeros, and a state with no cards is fine.
        """
        rows, width = scoreboard.shape[0], self.config.width
        placed = self.placed(torch.cat([text + self.zones(zone), live / self.live_scale], dim=1))
        segment = row * len(ZONE_NAMES) + zone  # one bucket per state and zone
        total = torch.zeros(rows * len(ZONE_NAMES), width, dtype=placed.dtype, device=placed.device)
        count = torch.zeros(rows * len(ZONE_NAMES), dtype=placed.dtype, device=placed.device)
        total = total.index_add(0, segment, placed)
        count = count.index_add(0, segment, torch.ones_like(segment, dtype=placed.dtype))
        zones = (total / count.clamp(min=1).unsqueeze(1)).reshape(rows, len(ZONE_NAMES) * width)
        return self.head(torch.cat([zones, scoreboard / self.scoreboard_scale], dim=1)).squeeze(1)


class StateModel(nn.Module):
    """One state in, one probability out: the shape of the tensor contract."""

    def __init__(self, model: SetModel):
        super().__init__()
        self.model = model

    def forward(self, scoreboard: Tensor, token_ids: Tensor, token_mask: Tensor, numbers: Tensor, number_mask: Tensor,
                live: Tensor, zone: Tensor) -> Tensor:
        text = self.model.card_text(token_ids, token_mask, numbers, number_mask)
        logit = self.model.score(text, live, zone, torch.zeros_like(zone), scoreboard.unsqueeze(0))
        return torch.sigmoid(logit)


def weighted_loss(logits: Tensor, labels: Tensor, weights: Tensor, scale: float) -> Tensor:
    """Weighted binary cross-entropy over a fixed `scale`, the corpus's mean weight times the batch size.

    The scale is the same for every batch, so a row pulls as hard in a short last batch as
    in a full one, and a batch of rows from long games doesn't get scaled back up. That
    keeps a game at weight 1 over an epoch.
    """
    losses = nn.functional.binary_cross_entropy_with_logits(logits, labels, reduction="none")
    return (losses * weights).sum() / scale
