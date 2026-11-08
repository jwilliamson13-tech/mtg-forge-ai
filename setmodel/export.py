"""Write what Forge loads: the ONNX model, the card-feature table, the vocabulary, the scales and the contract.

`docs/tensor-contract.md` describes the bundle for the Java side. `build_contract` is the
one source of it, and `docs/tensor-contract-v1.json` is its committed copy.

The dynamo exporter is the one to use. The older TorchScript exporter also runs, but its
scatter-add drifted by 0.002 at 300 cards on the same model, so it would fail the check.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnx
import onnxruntime
import torch

from setmodel.config import CHECK_STATES, CHECK_THRESHOLD
from setmodel.dataset import HELD_OUT_DECKS, HELD_OUT_GAMES, LIVE_FIELDS, SCOREBOARD_FIELDS, ZONE_NAMES, Corpus
from setmodel.features import BACK, EMPTY_CARD, EMPTY_ROOM, FRONT, CardTable, Caps, Encoded, densify, encode
from setmodel.model import LIVE_SCALE, SCOREBOARD_SCALE, SetModel, StateModel

CONTRACT_VERSION = 1
MODEL = "model.onnx"
CARD_TABLE = "card_features.jsonl.gz"
VOCAB = "vocab.json"
SCALES = "scales.json"
CONTRACT = "contract.json"

CHECK_SEED = 0  # which held-out states the check samples, the same ones each run
TRACE_CARDS = 5  # any size above 1 will do: the card axis is exported as dynamic

INPUTS = (
    ("scoreboard", "float32", ["S"], "Raw values, in `scoreboard_fields` order. Booleans are 0 or 1."),
    ("token_ids", "int64", ["N", "A", "T"], "Vocabulary ids from the card table. 0 where the mask is 0."),
    ("token_mask", "float32", ["N", "A", "T"], "1 where a token is present."),
    ("numbers", "float32", ["N", "A", "K"], "Raw values from the card table, unscaled. The model holds the scales."),
    ("number_mask", "float32", ["N", "A", "K"], "1 where a key is present."),
    ("live", "float32", ["N", "L"], "Raw live state, in `live_fields` order."),
    ("zone", "int64", ["N"], "Index into `zones`."),
)
OUTPUT = {"name": "win_probability", "dtype": "float32", "shape": [1], "meaning": "P(this side wins)."}


def build_contract(caps: Caps, key_count: int) -> dict:
    """Tensor contract version 1. Shape letters are named in `sizes`. N, the card count, is the one dynamic axis."""
    return {
        "version": CONTRACT_VERSION,
        "sizes": {"N": "dynamic, 0 or more", "A": caps.abilities, "T": caps.tokens, "K": key_count,
                  "L": len(LIVE_FIELDS), "S": len(SCOREBOARD_FIELDS)},
        "inputs": [{"name": name, "dtype": dtype, "shape": shape, "meaning": meaning}
                   for name, dtype, shape, meaning in INPUTS],
        "output": OUTPUT,
        "scoreboard_fields": list(SCOREBOARD_FIELDS),
        "live_fields": list(LIVE_FIELDS),
        "zones": list(ZONE_NAMES),
        "truncation": {"abilities": "Row 0 stays. Past A rows, the oldest other rows are dropped.",
                       "tokens": "Past T tokens in a row, the oldest are dropped.",
                       "numbers": "Two numbers under one key in a row are added."},
        "card_table": {"file": CARD_TABLE,
                       "slots": {"Original": FRONT, "EmptyRoom": EMPTY_ROOM, "any other face": BACK},
                       "lookup": "(name, slot), else the name's entry under any slot, else all masks 0.",
                       "hidden": "A hidden card has no lookup: all masks 0 and `hidden` 1 in `live`."},
    }


def write_card_table(table: CardTable, model_vocab, caps: Caps, path: Path) -> int:
    """One JSON line per name and slot. Row lengths stand in for the masks. Returns the entry count."""
    count = 0
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as lines:
        for name, slot, resolved in table.entries():
            card = encode(resolved.rows, model_vocab, caps)
            lines.write(json.dumps({"name": name, "slot": slot, "tokens": card.tokens, "numbers": card.numbers},
                                   ensure_ascii=False) + "\n")
            count += 1
    return count


def read_card_table(path: Path) -> dict[tuple[str, str], Encoded]:
    """The table as Forge reads it, with no help from the dump."""
    entries = {}
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            entry = json.loads(line)
            numbers = tuple(tuple((key, value) for key, value in row) for row in entry["numbers"])
            entries[(entry["name"], entry["slot"])] = Encoded(tuple(map(tuple, entry["tokens"])), numbers)
    return entries


def export_onnx(model: SetModel, caps: Caps, path: Path) -> None:
    cards = TRACE_CARDS
    key_count = model.config.key_count
    sample = (torch.zeros(len(SCOREBOARD_FIELDS)), torch.zeros(cards, caps.abilities, caps.tokens, dtype=torch.int64),
              torch.ones(cards, caps.abilities, caps.tokens), torch.zeros(cards, caps.abilities, key_count),
              torch.zeros(cards, caps.abilities, key_count), torch.zeros(cards, len(LIVE_FIELDS)),
              torch.zeros(cards, dtype=torch.int64))
    axis = torch.export.Dim("N", min=0)
    names = [name for name, *_ in INPUTS]
    shapes = {name: {} if name == "scoreboard" else {0: axis} for name in names}
    torch.onnx.export(StateModel(model).eval(), sample, str(path), input_names=names, output_names=[OUTPUT["name"]],
                      dynamic_shapes=shapes, dynamo=True, external_data=False, verbose=False)


def export_bundle(model: SetModel, vocab, caps: Caps, metadata: dict, table: CardTable, corpus: Corpus,
                  out: str | Path) -> dict:
    """Write the bundle to `out` and return its contract. `corpus` supplies the truncation rates."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    export_onnx(model, caps, out / MODEL)
    entries = write_card_table(table, vocab, caps, out / CARD_TABLE)
    vocab.save(out / VOCAB)
    scales = {"numeric_keys": dict(zip(vocab.numeric_keys, model.key_scale.tolist())),
              "scoreboard": SCOREBOARD_SCALE, "live": LIVE_SCALE}
    (out / SCALES).write_text(json.dumps(scales, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    contract = {**build_contract(caps, model.config.key_count),
                "bundle": {"generation": metadata["generation"], "batch_ids": metadata["batch_ids"],
                           "card_entries": entries,
                           "opset": onnx.load(out / MODEL, load_external_data=False).opset_import[0].version,
                           "onnxruntime": onnxruntime.__version__, "torch": str(torch.__version__),
                           "truncation_rates": corpus.truncation()}}
    (out / CONTRACT).write_text(json.dumps(contract, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return contract


@dataclass(frozen=True)
class CheckResult:
    states: int
    max_difference: float
    threshold: float

    @property
    def passed(self) -> bool:
        return self.max_difference <= self.threshold


def _lookup(entries: dict[tuple[str, str], Encoded], names: dict[str, list[Encoded]],
            key: tuple[str, str] | None) -> Encoded:
    """The contract's lookup rule."""
    if key is None:
        return EMPTY_CARD
    found = entries.get(key)
    return found if found is not None else names.get(key[0], [EMPTY_CARD])[0]


def export_check(model: SetModel, corpus: Corpus, bundle: str | Path, *, states: int = CHECK_STATES,
                 threshold: float = CHECK_THRESHOLD) -> CheckResult:
    """Score held-out states in both runtimes and compare.

    The ONNX side builds its inputs the way Forge will, from the bundle's card table, and
    the PyTorch side from the corpus, so a bad table fails here too.
    """
    bundle = Path(bundle)
    rows = corpus.rows(HELD_OUT_GAMES, HELD_OUT_DECKS)
    if not len(rows):
        raise ValueError("no held-out rows to check the export on")
    rows = np.random.default_rng(CHECK_SEED).permutation(rows)[:states]
    entries = read_card_table(bundle / CARD_TABLE)
    names: dict[str, list[Encoded]] = {}
    for (name, _), encoded in entries.items():
        names.setdefault(name, []).append(encoded)
    session = onnxruntime.InferenceSession(bundle / MODEL, providers=["CPUExecutionProvider"])
    wrapper = StateModel(model).eval()
    worst = 0.0
    for row in rows.tolist():
        cards = np.arange(corpus.card_offsets[row], corpus.card_offsets[row + 1])
        features = corpus.card_feature[cards]
        live, zone = corpus.live(cards), corpus.card_zone[cards].astype(np.int64)
        shared = {"scoreboard": corpus.scoreboard[row], "live": live, "zone": zone}
        from_table = densify([_lookup(entries, names, corpus.feature_keys[f]) for f in features.tolist()],
                             corpus.caps, model.config.key_count)
        onnx_inputs = {**shared, **dict(zip(("token_ids", "token_mask", "numbers", "number_mask"), from_table))}
        got = session.run(None, onnx_inputs)[0]
        torch_inputs = [torch.as_tensor(array) for array in (
            corpus.scoreboard[row], corpus.token_ids[features], corpus.token_mask[features], corpus.numbers[features],
            corpus.number_mask[features], live, zone)]
        with torch.no_grad():
            expected = wrapper(*torch_inputs).numpy()
        worst = max(worst, float(np.abs(got - expected).max()))
    return CheckResult(len(rows), worst, threshold)
