"""Model generations: a name a seat can play, resolved to the export bundle the ONNX evaluator loads.

A generation is a folder under `models/`, holding what `python -m setmodel export --out models/NAME`
writes: `contract.json`, `model.onnx` and the rest of the bundle in docs/tensor-contract.md. The
folder's name is the generation's name, so `onnx:gen1` plays `models/gen1/`. The folders are
git-ignored, like everything else a run makes. A value with a path separator in it is taken as a
bundle folder instead, for a one-off bundle such as the dry run's.

See "Seat configurations" in docs/forge-build.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

ONNX = "onnx"
PREFIX = ONNX + ":"
DEFAULT_MODELS = Path(__file__).resolve().parents[1] / "models"
NAME = re.compile(r"[A-Za-z0-9_-]+")
NUMBERED = re.compile(r"gen(\d+)")  # a name that states its generation number
BUNDLE_FILES = ("contract.json", "model.onnx")
# Tensor contract versions the fork's ONNX evaluator loads, `OnnxBundle.CONTRACT_VERSIONS` in forge.view. Version 1
# bundles, such as gen0's, still load and run: the fork builds each version's scoreboard from the bundle's own
# field list. A jar from before version 2 loads only version 1 and refuses the rest itself.
CONTRACT_VERSIONS = (1, 2)


class GenerationError(ValueError):
    """A generation name that doesn't lead to a bundle."""


@dataclass(frozen=True)
class Generation:
    name: str  # what the seat asked for: a name under models/, or a bundle folder
    bundle: Path  # the bundle folder, absolute
    number: int | None  # `bundle.generation` in the bundle's contract.json
    model_digest: str  # the first 12 hex digits of model.onnx's SHA-256, so a results line names the weights

    @property
    def label(self) -> str:
        return PREFIX + self.name

    def fields(self) -> dict:
        return {"name": self.name, "bundle": str(self.bundle), "generation": self.number,
                "model_digest": self.model_digest}


def is_onnx(value: str) -> bool:
    return value.startswith(PREFIX)


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()[:12]


def bundle_folder(name: str, models: Path = DEFAULT_MODELS) -> Path:
    """Where a generation's bundle is. A bare name is a folder under `models`, anything else a path."""
    if not name:
        raise GenerationError("onnx: needs a generation name, such as onnx:gen1")
    if NAME.fullmatch(name):
        return Path(models) / name
    return Path(name)


def resolve(name: str, models: Path = DEFAULT_MODELS) -> Generation:
    """The bundle for a generation name, checked enough that a seat can't load nothing."""
    folder = bundle_folder(name, models)
    missing = [file for file in BUNDLE_FILES if not (folder / file).is_file()]
    if missing:
        known = sorted(path.parent.name for path in Path(models).glob("*/contract.json")) if NAME.fullmatch(name) else []
        hint = f"; generations under {models}: {', '.join(known)}" if known else ""
        raise GenerationError(f"no export bundle for {name}: {folder} has no {' or '.join(missing)}{hint}")
    try:
        contract = json.loads((folder / "contract.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GenerationError(f"can't read {folder / 'contract.json'}: {e}") from e
    version = contract.get("version") if isinstance(contract, dict) else None
    if type(version) is not int or version not in CONTRACT_VERSIONS:
        raise GenerationError(f"{name}: {folder / 'contract.json'} is tensor contract version {version!r}, and Forge "
                              f"loads versions {', '.join(map(str, CONTRACT_VERSIONS))}")
    number = (contract.get("bundle") or {}).get("generation")
    stated = NUMBERED.fullmatch(name)
    if stated and number is not None and int(stated[1]) != number:
        raise GenerationError(f"{name} holds a bundle of generation {number}: {folder}")
    return Generation(name, folder.resolve(), number, _digest(folder / "model.onnx"))
