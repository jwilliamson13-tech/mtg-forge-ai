"""Players: an AI configuration named in one word, so a ladder or a convergence check can list several.

A player is what sits in a seat apart from the deck: a simulation mode and what its search scores
states with. It plays whichever deck it is handed.

| Player | Seat settings |
|---|---|
| `heuristic` | `--sim-mode heuristic`, stock |
| `full`, `hybrid` | that mode with Forge's evaluator |
| `full:random`, `hybrid:passthrough` | that mode with a #23 test evaluator |
| `onnx:gen1` | `full` with the ONNX evaluator on generation `gen1` |
| `hybrid:onnx:gen1` | the same in `hybrid` |

`full:forge` reads as `full`. A player's label is its shortest spelling, and it names the player in
every report and results file. A generation's model digest travels with it, so a later run can tell
when the weights behind a name have changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from simrunner.generations import DEFAULT_MODELS, ONNX, PREFIX, Generation, GenerationError, is_onnx, resolve
from simrunner.worker import DEFAULT_EVALUATOR, DEFAULT_SIM_MODE, EVALUATORS, SIM_MODES, Seat

UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


class PlayerError(ValueError):
    """A player name that doesn't describe a seat the runner can set up."""


@dataclass(frozen=True)
class Player:
    sim_mode: str
    evaluator: str | None = None  # None is Forge's own, with `-eval` left off
    generation: Generation | None = None  # for the `onnx` evaluator

    @property
    def label(self) -> str:
        if self.evaluator == ONNX:
            onnx = self.generation.label
            return onnx if self.sim_mode == "full" else f"{self.sim_mode}:{onnx}"
        return f"{self.sim_mode}:{self.evaluator}" if self.evaluator else self.sim_mode

    @property
    def slug(self) -> str:
        """The label in letters, digits, `_` and `-`, for folder names and batch ids."""
        return UNSAFE.sub("-", self.label).strip("-")

    def seat(self, deck: Path) -> Seat:
        return Seat(deck, None, self.sim_mode, self.evaluator, self.generation)

    def fields(self) -> dict:
        return {"label": self.label, "sim_mode": self.sim_mode, "evaluator": self.evaluator,
                "generation": self.generation.fields() if self.generation else None}

    def __str__(self) -> str:
        return self.label


def parse_player(text: str, models: Path = DEFAULT_MODELS) -> Player:
    """Read a player name. An `onnx:` generation is resolved to its bundle, so a missing one fails here."""
    text = text.strip()
    mode, _, evaluator = text.partition(":")
    if is_onnx(text):
        mode, evaluator = "full", text
    if mode not in SIM_MODES:
        raise PlayerError(f"not a player: {text!r} (a player is heuristic, full, hybrid, MODE:EVALUATOR or "
                          f"{PREFIX}GENERATION)")
    if not evaluator or evaluator == DEFAULT_EVALUATOR:
        return Player(mode)
    if mode == DEFAULT_SIM_MODE:
        raise PlayerError(f"{text}: a heuristic seat never scores a state, so it takes no evaluator")
    if is_onnx(evaluator):
        try:
            return Player(mode, ONNX, resolve(evaluator.removeprefix(PREFIX), Path(models)))
        except GenerationError as e:
            raise PlayerError(str(e)) from e
    if evaluator not in EVALUATORS:
        raise PlayerError(f"{text}: no evaluator {evaluator!r} (choose from {', '.join(EVALUATORS)} or "
                          f"{PREFIX}GENERATION)")
    return Player(mode, evaluator)
