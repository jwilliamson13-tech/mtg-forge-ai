"""Command line: `python -m gamerecord convert FOLDER OUT` and `python -m gamerecord hiddencheck FOLDER`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gamerecord.convert import convert_folder, format_report, game_files
from gamerecord.hiddencheck import check_folder, format_check


def _convert(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    if not game_files(folder):
        args.parser.error(f"no game files under {folder}")
    report = convert_folder(folder, Path(args.out))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    sys.stdout.write(format_report(report))
    return 1 if report.rejected else 0


def _hiddencheck(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    if not game_files(folder):
        args.parser.error(f"no game files under {folder}")
    check = check_folder(folder)
    sys.stdout.write(format_check(check))
    return 0 if check.ids_named_nothing else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m gamerecord")
    commands = parser.add_subparsers(dest="command", required=True)

    convert = commands.add_parser("convert", help="convert a folder of game files to Parquet, keeping wins only")
    convert.add_argument("folder", help="folder of .jsonl.gz game files, searched with its subfolders")
    convert.add_argument("out", help="folder for the snapshots, cards and stack tables")
    convert.set_defaults(handler=_convert, parser=convert)

    hidden = commands.add_parser("hiddencheck", help="try to name a batch's hidden cards from its ids, exit 1 if any are")
    hidden.add_argument("folder", help="folder of .jsonl.gz game files, searched with its subfolders")
    hidden.set_defaults(handler=_hiddencheck, parser=hidden)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
