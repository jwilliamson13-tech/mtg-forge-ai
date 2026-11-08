"""Check Forge's ONNX evaluator against setmodel, snapshot by snapshot.

Forge's side, `forge.view.OnnxScoreRecords` in the fork, reads game files, builds the
contract's inputs from each snapshot line in Java and runs the bundle under ONNX Runtime
for Java. It writes one line per snapshot to a scores file. This side reads the same games
from Parquet through `load_corpus` and scores them with the PyTorch model. `export` already
checks ONNX against PyTorch on inputs Python built, so a difference here is in how Java
builds the inputs.

The scores file is tab-separated, with a header line: `game_id`, `snapshot_index`,
`probability`. `game_id` is the corpus's, `<batch_id>-<seed>-<game_number>`, and
`snapshot_index` is the snapshot's position in its game file, from 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from setmodel.config import CHECK_THRESHOLD, TrainConfig
from setmodel.dataset import Corpus
from setmodel.model import SetModel
from setmodel.train import Trained, probabilities

SCORE_COLUMNS = ("game_id", "snapshot_index", "probability")


def read_scores(path: str | Path) -> dict[tuple[str, int], float]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or tuple(lines[0].split("\t")) != SCORE_COLUMNS:
        raise ValueError(f"{path}: the header must be {chr(9).join(SCORE_COLUMNS)}")
    scores = {}
    for line in lines[1:]:
        game_id, index, probability = line.split("\t")
        scores[(game_id, int(index))] = float(probability)
    return scores


def write_scores(path: str | Path, scores: dict[tuple[str, int], float]) -> None:
    rows = ["\t".join(SCORE_COLUMNS)]
    rows += [f"{game_id}\t{index}\t{probability!r}" for (game_id, index), probability in sorted(scores.items())]
    Path(path).write_text("\n".join(rows) + "\n", encoding="utf-8")


def corpus_scores(model: SetModel, vocab, corpus: Corpus, rows: np.ndarray | None = None) -> dict[tuple[str, int], float]:
    """PyTorch's probability for these rows, or every row, by game id and snapshot index."""
    rows = np.arange(len(corpus.labels)) if rows is None else rows
    p = probabilities(Trained(model.cpu().eval(), vocab, TrainConfig(device="cpu"), "cpu"), corpus, rows)
    return {(corpus.game_ids[corpus.game[row]], int(corpus.snapshot_index[row])): float(value)
            for row, value in zip(rows.tolist(), p.tolist())}


@dataclass(frozen=True)
class ParityResult:
    scored: int  # lines in the scores file
    matched: int  # of those, rows the corpus has
    max_difference: float
    mean_difference: float
    threshold: float

    @property
    def not_in_corpus(self) -> int:
        """Snapshots training dropped, for an unmatched card, or games it never kept, such as draws."""
        return self.scored - self.matched

    @property
    def passed(self) -> bool:
        return self.matched > 0 and self.max_difference <= self.threshold

    def format(self) -> str:
        return (f"scored {self.scored}, matched {self.matched}, not in the corpus {self.not_in_corpus}\n"
                f"largest difference {self.max_difference:.3g}, mean {self.mean_difference:.3g}, "
                f"threshold {self.threshold:.3g}: {'pass' if self.passed else 'FAIL'}\n")


def parity(model: SetModel, vocab, corpus: Corpus, scores: dict[tuple[str, int], float], *,
           threshold: float = CHECK_THRESHOLD) -> ParityResult:
    """Compare Java's scores with PyTorch's on every snapshot the corpus also has."""
    row_of = {(corpus.game_ids[game], int(index)): row
              for row, (game, index) in enumerate(zip(corpus.game.tolist(), corpus.snapshot_index.tolist()))}
    keys = [key for key in scores if key in row_of]
    if not keys:
        return ParityResult(len(scores), 0, float("nan"), float("nan"), threshold)
    expected = corpus_scores(model, vocab, corpus, np.array([row_of[key] for key in keys], dtype=np.int64))
    differences = np.abs(np.array([scores[key] - expected[key] for key in keys], dtype=np.float64))
    return ParityResult(len(scores), len(keys), float(differences.max()), float(differences.mean()), threshold)
