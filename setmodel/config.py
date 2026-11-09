"""Run settings and their defaults.

The command line reads these, and `join` has to run without torch, so this module imports none.
"""

from __future__ import annotations

from dataclasses import dataclass

FULL = "full"  # the run with no ablation

# The tensor contract this code trains and exports, docs/tensor-contract.md. A model artifact records it, and
# `train.load_model` refuses one trained under another version, whose inputs this code no longer builds.
CONTRACT_VERSION = 2

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
