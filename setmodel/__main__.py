"""Command line: `python -m setmodel join|train|export|ablate|pairtest`. `docs/training.md` has each one."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("parquet", help="folder `gamerecord convert` wrote")
    parser.add_argument("cards", help="parsed card dump, from `cardscript sweep --out`")
    parser.add_argument("manifest", help="deck manifest, decks/manifest.json")
    parser.add_argument("--min-count", type=int, default=5, help="vocabulary cutoff, as in docs/vocabulary.md")


def _training(parser: argparse.ArgumentParser) -> None:
    _inputs(parser)
    parser.add_argument("--out", required=True, help="folder for the artifact and report")
    parser.add_argument("--generation", type=int, default=0, help="this model's generation number")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="auto", help="auto, cpu or cuda")


def _config(args: argparse.Namespace):
    from setmodel.train import TrainConfig

    return TrainConfig(generation=args.generation, epochs=args.epochs, batch_size=args.batch_size,
                       learning_rate=args.learning_rate, width=args.width, seed=args.seed, device=args.device)


def _print(text: str) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    sys.stdout.write(text)


def _join(args: argparse.Namespace) -> int:
    import json

    from cardscript.vocab import build_vocabulary
    from setmodel.dataset import load_corpus, load_manifest
    from setmodel.features import CardTable

    with open(args.cards, encoding="utf-8") as lines:
        vocab = build_vocabulary((json.loads(line) for line in lines), min_count=args.min_count)
    corpus = load_corpus(args.parquet, CardTable.load(args.cards), vocab, load_manifest(args.manifest))
    _print(corpus.report.format())
    _print(f"truncation {corpus.truncation()}\n")
    return 0


def _train(args: argparse.Namespace) -> int:
    from setmodel.train import METRICS_FILE, load_inputs, run

    corpus, vocab, _ = load_inputs(args.parquet, args.cards, args.manifest, args.min_count)
    _print(corpus.report.format())
    metrics = run(corpus, vocab, _config(args), args.out)
    _print(f"device {metrics['device']}\nheld out {metrics['held_out']}\nwrote {Path(args.out) / METRICS_FILE}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m setmodel")
    commands = parser.add_subparsers(dest="command", required=True)

    join = commands.add_parser("join", help="join the tables to the card dump and report unmatched names, without training")
    _inputs(join)
    join.set_defaults(handler=_join)

    train = commands.add_parser("train", help="train a model and write its artifact and metrics report")
    _training(train)
    train.set_defaults(handler=_train)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
