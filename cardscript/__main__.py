"""Command line: `python -m cardscript <command> ...`.

Each command is one subparser plus one handler. `show` is the first; the
coverage `sweep` slots in beside it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cardscript.parser import ParseError, parse_card
from cardscript.show import format_card


def _show(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8")
    try:
        card = parse_card(text)
    except ParseError as e:
        print(f"{args.file}: {e.cause}: {e.line}", file=sys.stderr)
        return 1
    sys.stdout.write(format_card(card))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cardscript")
    commands = parser.add_subparsers(dest="command", required=True)

    show = commands.add_parser("show", help="pretty-print one card script's parsed structure")
    show.add_argument("file", help="path to a Forge card script (.txt)")
    show.set_defaults(handler=_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
