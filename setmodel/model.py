"""The two-level set model: tokens into abilities, abilities into cards, cards into zones, zones into a win probability.

Every level is a mean over a set followed by a small MLP, so the order of tokens,
abilities or cards can't matter, and a set of any size goes through the same weights.

`arch` picks what happens between a placed card and its zone's vector. `mean` is the
model above. `count` pools each zone to its mean, its mean times log(1 + count) and
log(1 + count), so two copies of a card read differently from one. `attention` also runs
one self-attention block over all of a state's cards, every zone together, before that
pooling, so a card's vector can depend on the rest of the board. All three take the same
contract inputs: only the network inside changes.

Inputs are raw game values. The constants that scale them live in the model as buffers,
so the exported file carries them and Forge has nothing to keep in step.

`card_text` depends only on the card, not on the game, so training runs it once per
distinct card in a batch. `score` takes it from there. `StateModel` joins the two for one
state, which is the shape the tensor contract gives Forge.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import math

import torch
from torch import Tensor, nn

from setmodel.config import ARCHES, ATTENTION, MEAN
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
    arch: str = MEAN  # artifacts from before the field are all `mean`
    heads: int = 4  # attention heads, `attention` only

    def __post_init__(self):
        if self.arch not in ARCHES:
            raise ValueError(f"unknown arch {self.arch!r}, expected one of {ARCHES}")
        if self.arch == ATTENTION and self.width % self.heads:
            raise ValueError(f"width {self.width} doesn't split into {self.heads} heads")

    def to_dict(self) -> dict:
        return asdict(self)


def _mlp(inputs: int, outputs: int, hidden: int) -> nn.Sequential:
    return nn.Sequential(nn.Linear(inputs, hidden), nn.ReLU(), nn.Linear(hidden, outputs))


def _masked_mean(values: Tensor, mask: Tensor, dim: int) -> Tensor:
    """The mean over `dim` of the entries `mask` keeps. Zeros if it keeps none."""
    return (values * mask.unsqueeze(-1)).sum(dim) / mask.sum(dim).clamp(min=1).unsqueeze(-1)


MASKED = -1e4  # an attention score for a padded card: its weight is exactly 0 in float32, and an all-padding row stays finite


class CardAttention(nn.Module):
    """One pre-norm transformer block over each state's cards. Permuting the cards permutes the output."""

    def __init__(self, width: int, heads: int):
        super().__init__()
        self.heads = heads
        self.norm = nn.LayerNorm(width)
        self.qkv = nn.Linear(width, 3 * width)
        self.out = nn.Linear(width, width)
        self.ff_norm = nn.LayerNorm(width)
        self.ff = _mlp(width, width, 2 * width)

    def forward(self, cards: Tensor, keep: Tensor | None = None) -> Tensor:
        """[B, N, W] cards to [B, N, W]. `keep` is [B, N], 1 for a real card and 0 for padding, None for no padding."""
        batch, count, width = cards.shape
        heads = self.heads
        q, k, v = self.qkv(self.norm(cards)).reshape(batch, count, 3, heads, width // heads).permute(2, 0, 3, 1, 4)
        scores = q @ k.transpose(-1, -2) / math.sqrt(width // heads)  # [B, H, N, N]
        if keep is not None:
            scores = scores.masked_fill(keep[:, None, None, :] == 0, MASKED)
        mixed = (scores.softmax(-1) @ v).transpose(1, 2).reshape(batch, count, width)
        cards = cards + self.out(mixed)
        return cards + self.ff(self.ff_norm(cards))


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
        if config.arch == ATTENTION:
            self.attention = CardAttention(width, config.heads)
        pooled = width if config.arch == MEAN else 2 * width + 1
        self.head = _mlp(len(ZONE_NAMES) * pooled + len(SCOREBOARD_FIELDS), 1, 2 * width)
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

    def score(self, text: Tensor, live: Tensor, zone: Tensor, row: Tensor, scoreboard: Tensor,
              one_state: bool = False) -> Tensor:
        """Cards of a batch of states to one logit per state. `row` says which state each card is in.

        For `attention`, a state's cards must be contiguous with states in row order, as `Corpus.cards_of` gives
        them. `one_state` says every card is in state 0, so attention needs no padding. It is the path the export
        traces, and it gives what the batch path gives for the same state.

        An empty zone pools to zeros, and a state with no cards is fine.
        """
        rows, width = scoreboard.shape[0], self.config.width
        placed = self.placed(torch.cat([text + self.zones(zone), live / self.live_scale], dim=1))
        if self.config.arch == ATTENTION:
            placed = self._attend(placed, row, rows, one_state)
        segment = row * len(ZONE_NAMES) + zone  # one bucket per state and zone
        total = torch.zeros(rows * len(ZONE_NAMES), width, dtype=placed.dtype, device=placed.device)
        count = torch.zeros(rows * len(ZONE_NAMES), dtype=placed.dtype, device=placed.device)
        total = total.index_add(0, segment, placed)
        count = count.index_add(0, segment, torch.ones_like(segment, dtype=placed.dtype))
        mean = total / count.clamp(min=1).unsqueeze(1)
        if self.config.arch == MEAN:
            zones = mean
        else:
            scale = torch.log1p(count).unsqueeze(1)
            zones = torch.cat([mean, mean * scale, scale], dim=1)
        zones = zones.reshape(rows, -1)
        return self.head(torch.cat([zones, scoreboard / self.scoreboard_scale], dim=1)).squeeze(1)

    def _attend(self, placed: Tensor, row: Tensor, rows: int, one_state: bool) -> Tensor:
        """Run attention within each state: pad the states to the longest, attend with the padding masked, unpad."""
        if one_state:
            return self.attention(placed.unsqueeze(0))[0]
        if placed.shape[0] == 0:
            return placed
        per_row = torch.bincount(row, minlength=rows)
        first = torch.cumsum(per_row, 0) - per_row
        position = torch.arange(placed.shape[0], device=placed.device) - first[row]
        longest = int(per_row.max())
        padded = placed.new_zeros(rows, longest, placed.shape[1]).index_put((row, position), placed)
        keep = placed.new_zeros(rows, longest).index_put((row, position), torch.ones_like(position, dtype=placed.dtype))
        return self.attention(padded, keep)[row, position]


class StateModel(nn.Module):
    """One state in, one probability out: the shape of the tensor contract."""

    def __init__(self, model: SetModel):
        super().__init__()
        self.model = model

    def forward(self, scoreboard: Tensor, token_ids: Tensor, token_mask: Tensor, numbers: Tensor, number_mask: Tensor,
                live: Tensor, zone: Tensor) -> Tensor:
        text = self.model.card_text(token_ids, token_mask, numbers, number_mask)
        logit = self.model.score(text, live, zone, torch.zeros_like(zone), scoreboard.unsqueeze(0), one_state=True)
        return torch.sigmoid(logit)


def weighted_loss(logits: Tensor, labels: Tensor, weights: Tensor, scale: float) -> Tensor:
    """Weighted binary cross-entropy over a fixed `scale`, the corpus's mean weight times the batch size.

    The scale is the same for every batch, so a row pulls as hard in a short last batch as
    in a full one, and a batch of rows from long games doesn't get scaled back up. That
    keeps a game at weight 1 over an epoch.
    """
    losses = nn.functional.binary_cross_entropy_with_logits(logits, labels, reduction="none")
    return (losses * weights).sum() / scale
