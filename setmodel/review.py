"""The review step: the ablation table for kill criterion 2, and the pair test.

Both report what they measure and leave the decision to whoever reads the file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import torch

from cardscript.vocab import Vocabulary
from setmodel.dataset import LIVE, LIVE_FIELDS, SCOREBOARD_FIELDS, ZONE_IDS, Corpus
from setmodel.features import CardTable, Caps, densify, encode
from setmodel.model import SetModel, StateModel
from setmodel.train import ABLATIONS, FULL, NO_CARDS, TrainConfig, run

KILL_MARGIN_POINTS = 5.0
ABLATION_JSON = "ablation.json"
ABLATION_TABLE = "ablation.md"


def _points(accuracy: float | None) -> float | None:
    return None if accuracy is None else 100 * accuracy


def ablation_rows(results: dict[str, dict]) -> list[dict]:
    """One row per run, the full model first. `vs_full` is in points of held-out accuracy."""
    full = _points(results[FULL]["held_out"]["accuracy"])
    rows = []
    for name, metrics in results.items():
        accuracy = _points(metrics["held_out"]["accuracy"])
        rows.append({"run": name, "accuracy": accuracy, "log_loss": metrics["held_out"]["log_loss"],
                     "vs_full": None if accuracy is None or full is None else accuracy - full})
    return rows


def kill_verdict(rows: list[dict]) -> str:
    gap = next(row["vs_full"] for row in rows if row["run"] == NO_CARDS)
    if gap is None:
        return "No verdict: nothing was held out."
    margin = round(-gap, 6)  # 5.00 points must pass, and float noise can land it at 4.999999
    outcome = "carries" if margin >= KILL_MARGIN_POINTS else "does not carry"
    return f"Full model beats no cards by {margin:.2f} points. The card encoding {outcome} information by the {KILL_MARGIN_POINTS:g} point bar."


def format_table(rows: list[dict], verdict: str) -> str:
    def cell(value: float | None, digits: int) -> str:
        return "n/a" if value is None else f"{value:.{digits}f}"

    lines = ["| Run | Held-out accuracy (%) | Held-out log loss | Against full (points) |", "|---|---|---|---|"]
    lines += [f"| {r['run']} | {cell(r['accuracy'], 2)} | {cell(r['log_loss'], 4)} | {cell(r['vs_full'], 2)} |" for r in rows]
    return "\n".join(lines) + f"\n\n{verdict}\n"


def run_ablations(corpus: Corpus, vocab: Vocabulary, config: TrainConfig, out: str | Path) -> str:
    """Train the full model and the four ablations, same seed and settings, and write the table."""
    out = Path(out)
    results = {name: run(corpus, vocab, replace(config, ablation=name), out / name) for name in (FULL, *ABLATIONS)}
    rows = ablation_rows(results)
    verdict = kill_verdict(rows)
    table = format_table(rows, verdict)
    (out / ABLATION_TABLE).write_text(table, encoding="utf-8")
    (out / ABLATION_JSON).write_text(json.dumps({"rows": rows, "verdict": verdict}, indent=1) + "\n", encoding="utf-8")
    return table


@dataclass(frozen=True)
class PairResult:
    names: tuple[str, str]
    empty: float
    first: float
    second: float
    both: float

    @property
    def first_lift(self) -> float:
        return self.first - self.empty

    @property
    def second_lift(self) -> float:
        return self.second - self.empty

    @property
    def pair_lift(self) -> float:
        return self.both - self.empty

    @property
    def sees_interaction(self) -> bool:
        return self.pair_lift > self.first_lift + self.second_lift

    def format(self) -> str:
        verdict = ("the pair's lift exceeds the sum of the single lifts: the model sees the interaction" if self.sees_interaction
                   else "the pair's lift does not exceed the sum of the single lifts: consider attention over the battlefield")
        return (f"empty board      {self.empty:.4f}\n"
                f"{self.names[0]} alone  {self.first:.4f}\n"
                f"{self.names[1]} alone  {self.second:.4f}\n"
                f"both             {self.both:.4f}\n"
                f"lifts            {self.first_lift:+.4f}, {self.second_lift:+.4f}, pair {self.pair_lift:+.4f}\n"
                f"verdict          {verdict}\n")


def _card_inputs(cards: list[dict], table: CardTable, vocab: Vocabulary, caps: Caps, key_count: int) -> list[torch.Tensor]:
    encoded, live = [], np.zeros((len(cards), len(LIVE_FIELDS)), dtype=np.float32)
    for n, card in enumerate(cards):
        resolved = table.resolve(card["name"], card.get("face", "Original"))
        if resolved is None:
            raise ValueError(f"{card['name']!r} isn't in the card dump")
        encoded.append(encode(resolved.rows, vocab, caps))
        live[n, LIVE["mine"]] = 1
        for name, value in card.get("live", {}).items():
            live[n, LIVE[name]] = value
    arrays = densify(encoded, caps, key_count)
    return [torch.as_tensor(array) for array in (*arrays, live)]


def pair_test(model: SetModel, vocab: Vocabulary, caps: Caps, table: CardTable, fixture: dict) -> PairResult:
    """Score the fixture's board with both cards, each alone, and neither, all on the battlefield.

    The fixture holds `cards`, two objects with a `name` and optional `face` and `live`
    fields, and `scoreboard`, which sets any field of a default even game.
    """
    wrapper = StateModel(model).eval()
    first, second = fixture["cards"]
    defaults = {"me_life": 20, "opp_life": 20, "me_library": 50, "opp_library": 50, "opp_hand": 5, "turn": 5, "me_active": 1}
    scoreboard = {**defaults, **fixture.get("scoreboard", {})}
    board = torch.tensor([float(scoreboard.get(name, 0)) for name in SCOREBOARD_FIELDS])

    def score(cards: list[dict]) -> float:
        inputs = _card_inputs(cards, table, vocab, caps, model.config.key_count)
        zone = torch.full((len(cards),), ZONE_IDS["battlefield"], dtype=torch.int64)
        with torch.no_grad():
            return wrapper(board, *inputs, zone).item()

    return PairResult((first["name"], second["name"]), score([]), score([first]), score([second]), score([first, second]))
