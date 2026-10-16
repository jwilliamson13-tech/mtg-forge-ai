"""Command line: `python -m cardscript <command> ...`.

Each command is one subparser plus one handler: `show` pretty-prints one
card, `sweep` reports coverage over folders of cards.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

from cardscript.parser import ParseError, parse_card
from cardscript.show import format_card
from cardscript.sweep import DEFAULT_EXCLUDE, sweep


def _show(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8")
    try:
        card = parse_card(text)
    except ParseError as e:
        print(f"{args.file}: {e.cause}: {e.line}", file=sys.stderr)
        return 1
    sys.stdout.write(format_card(card))
    return 0


def _sweep(args: argparse.Namespace) -> int:
    for folder in args.folders:
        if not Path(folder).is_dir():
            args.parser.error(f"not a folder: {folder}")
    exclude = [] if args.no_exclude else (args.exclude or DEFAULT_EXCLUDE)
    with contextlib.ExitStack() as stack:
        on_card = None
        if args.out:
            out = stack.enter_context(open(args.out, "w", encoding="utf-8", newline="\n"))

            def on_card(label, card):
                out.write(json.dumps({"file": label, **card.to_dict()}, ensure_ascii=False) + "\n")

        report = sweep(args.folders, exclude, on_card)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    sys.stdout.write(report.format(top=args.top))
    if args.min_coverage is not None and report.coverage < args.min_coverage:
        print(f"{report.coverage_text} is below --min-coverage {args.min_coverage:g}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cardscript")
    commands = parser.add_subparsers(dest="command", required=True)

    show = commands.add_parser("show", help="pretty-print one card script's parsed structure")
    show.add_argument("file", help="path to a Forge card script (.txt)")
    show.set_defaults(handler=_show)

    sw = commands.add_parser(
        "sweep",
        help="parse every script under some folders and report coverage",
        description="Parse every .txt script under the folders and print scripts seen, "
        "parsed, coverage and the most common failure causes.",
    )
    sw.add_argument("folders", nargs="+", metavar="folder",
                    help="folders to walk, such as cardsfolder and tokenscripts")
    sw.add_argument("--out", metavar="FILE", help="write one JSON line per parsed card")
    sw.add_argument("--min-coverage", type=float, metavar="PCT",
                    help="exit 1 if coverage is below PCT percent")
    sw.add_argument("--top", type=int, default=10, metavar="N",
                    help="failure causes to list (default 10)")
    sw.add_argument("--exclude", action="append", metavar="NAME",
                    help="skip sub-folders with this name; repeatable, replaces the default "
                    f"({', '.join(DEFAULT_EXCLUDE)})")
    sw.add_argument("--no-exclude", action="store_true", help="skip no sub-folders")
    sw.set_defaults(handler=_sweep, parser=sw)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
