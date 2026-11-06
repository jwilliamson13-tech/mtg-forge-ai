"""Command line: `python -m setmodel join|train|export|ablate|pairtest`. `docs/training.md` has each one."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("parquet", help="folder `gamerecord convert` wrote")
    parser.add_argument("cards", help="parsed card dump, from `cardscript sweep --out`")
    parser.add_argument("manifest", help="deck manifest, decks/manifest.json")


def _vocab_cutoff(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--min-count", type=int, default=5, help="vocabulary cutoff, as in docs/vocabulary.md")


def _training(parser: argparse.ArgumentParser) -> None:
    _inputs(parser)
    _vocab_cutoff(parser)
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
    from setmodel.dataset import load_inputs

    corpus, _ = load_inputs(args.parquet, args.cards, args.manifest, args.min_count)
    _print(corpus.report.format())
    _print(f"truncation {corpus.truncation()}\n")
    return 0


def _train(args: argparse.Namespace) -> int:
    from setmodel.dataset import load_inputs
    from setmodel.train import METRICS_FILE, run

    corpus, vocab = load_inputs(args.parquet, args.cards, args.manifest, args.min_count)
    _print(corpus.report.format())
    metrics = run(corpus, vocab, _config(args), args.out)
    _print(f"device {metrics['device']}\nheld out {metrics['held_out']}\nwrote {Path(args.out) / METRICS_FILE}\n")
    return 0


def _export(args: argparse.Namespace) -> int:
    from setmodel.dataset import load_corpus, load_manifest
    from setmodel.export import export_bundle, export_check
    from setmodel.features import CardTable
    from setmodel.train import load_model

    model, vocab, caps, metadata = load_model(args.model)
    table = CardTable.load(args.cards)
    corpus = load_corpus(args.parquet, table, vocab, load_manifest(args.manifest), caps=caps)
    contract = export_bundle(model, vocab, caps, metadata, table, corpus, args.out)
    check = export_check(model, corpus, args.out, states=args.states, threshold=args.threshold)
    _print(f"wrote {args.out}: {contract['bundle']['card_entries']} card entries\n"
           f"truncation {contract['bundle']['truncation_rates']}\n"
           f"export check: {check.states} states, max difference {check.max_difference:.3g}, threshold {check.threshold:.3g}\n")
    return 0 if check.passed else 1


def _ablate(args: argparse.Namespace) -> int:
    from setmodel.dataset import load_inputs
    from setmodel.review import run_ablations

    corpus, vocab = load_inputs(args.parquet, args.cards, args.manifest, args.min_count)
    _print(run_ablations(corpus, vocab, _config(args), args.out))
    return 0


def _pairtest(args: argparse.Namespace) -> int:
    from setmodel.features import CardTable
    from setmodel.review import pair_test
    from setmodel.train import load_model

    model, vocab, caps, _ = load_model(args.model)
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    _print(pair_test(model, vocab, caps, CardTable.load(args.cards), fixture).format())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m setmodel")
    commands = parser.add_subparsers(dest="command", required=True)

    join = commands.add_parser("join", help="join the tables to the card dump and report unmatched names, without training")
    _inputs(join)
    _vocab_cutoff(join)
    join.set_defaults(handler=_join)

    train = commands.add_parser("train", help="train a model and write its artifact and metrics report")
    _training(train)
    train.set_defaults(handler=_train)

    export = commands.add_parser("export", help="write the ONNX bundle and check it against PyTorch, exit 1 if it fails")
    export.add_argument("model", help="folder `train` wrote")
    _inputs(export)
    export.add_argument("--out", required=True, help="folder for the bundle")
    export.add_argument("--states", type=int, default=300, help="held-out states to compare")
    export.add_argument("--threshold", type=float, default=1e-4, help="largest difference allowed")
    export.set_defaults(handler=_export)

    ablate = commands.add_parser("ablate", help="retrain with each input group removed and write the table")
    _training(ablate)
    ablate.set_defaults(handler=_ablate)

    pair = commands.add_parser("pairtest", help="score a card pair together and alone, and print the verdict")
    pair.add_argument("model", help="folder `train` wrote")
    pair.add_argument("cards", help="parsed card dump")
    pair.add_argument("--fixture", default=str(Path(__file__).parent / "pair_fixture.json"), help="the cards and scoreboard to test")
    pair.set_defaults(handler=_pairtest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
