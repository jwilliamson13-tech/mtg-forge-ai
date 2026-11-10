"""Run settings and their defaults.

The command line reads these, and `join` has to run without torch, so this module imports none.
"""

from __future__ import annotations

from dataclasses import dataclass

FULL = "full"  # the run with no ablation

# The tensor contract this code trains and exports, docs/tensor-contract.md. A model artifact records it, and
# `train.load_model` refuses one trained under another version, whose inputs this code no longer builds.
CONTRACT_VERSION = 2

# The card-set network, `setmodel/model.py`. Every arch takes the same contract inputs.
MEAN = "mean"  # mean pooling per zone, the original
COUNT = "count"  # mean, mean times log(1 + count), and log(1 + count) per zone
ATTENTION = "attention"  # one self-attention block over all of a state's cards, then the count pooling
ARCHES = (MEAN, COUNT, ATTENTION)

CHECK_STATES = 300
CHECK_THRESHOLD = 1e-4


@dataclass(frozen=True)
class TrainConfig:
    generation: int = 0
    epochs: int = 10
    batch_size: int = 256
    learning_rate: float = 1e-3
    width: int = 64
    seed: int = 0
    device: str = "auto"
    ablation: str = FULL
    # Early stopping. `val_share` of the training games are held back from the weights and scored every `eval_every`
    # batches and at each epoch's end. 0 for `val_share` trains all `epochs` and keeps the last state.
    val_share: float = 0.1
    eval_every: int = 2000  # batches; 0 evaluates at each epoch's end only
    patience: int = 4  # evaluations without a better validation log loss before the run stops
    weight_decay: float = 0.0  # AdamW's decoupled decay; 0 is plain Adam
    arch: str = ATTENTION  # the card-set network, one of ARCHES
    dropout: float = 0.0  # dropout on the head's input and hidden layer while training; 0 is none
