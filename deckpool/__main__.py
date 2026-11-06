"""Command line: `python -m deckpool add-deck LIST.txt --id ID --archetype NAME --split train|held_out`.

Converts the plain-text list, checks its names, writes the original text and the
Forge file into `decks/`, and adds the deck to the manifest.

`python -m deckpool pull` is the one-off MTGTop8 Legacy pull; it refuses a second run without `--again`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deckpool import manifest as mf
from deckpool.convert import DeckError, convert
from deckpool.names import load_name_set

DEFAULT_CARDS = mf.DEFAULT_ROOT / "data" / "cards.jsonl"


def add_deck(list_path: Path, deck_id: str, archetype: str, split: str, source: str,
             names, root: Path = mf.DEFAULT_ROOT, manifest_path: Path | None = None) -> mf.Deck:
    """Validate and place one deck. Writes nothing if the conversion or the manifest rules fail."""
    manifest_path = manifest_path or root / "decks" / "manifest.json"
    manifest = mf.load(manifest_path) if manifest_path.is_file() else mf.Manifest()
    text = Path(list_path).read_text(encoding="utf-8")
    forge_text = convert(text, names, deck_id)
    deck = mf.Deck(deck_id, f"decks/forge/{deck_id}.dck", source, archetype, split)
    manifest.add_deck(deck)
    raw, forge = root / "decks" / "raw" / f"{deck_id}.txt", root / deck.file
    raw.parent.mkdir(parents=True, exist_ok=True)
    forge.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(text, encoding="utf-8", newline="\n")
    forge.write_text(forge_text, encoding="utf-8", newline="\n")
    mf.save(manifest, manifest_path)
    return deck


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m deckpool")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-deck", help="validate a plain-text list and add it to the pool")
    add.add_argument("list", help="plain-text decklist, count and name per line")
    add.add_argument("--id", required=True)
    add.add_argument("--archetype", required=True)
    add.add_argument("--split", required=True, choices=mf.SPLITS)
    add.add_argument("--source", default="user")
    add.add_argument("--cards", default=str(DEFAULT_CARDS), help="sweep output with the known card names")
    pull = commands.add_parser("pull", help="one-off pull of the Legacy pool from mtgtop8.com")
    pull.add_argument("--again", action="store_true", help="run even though decks/pull.json says the pool was pulled")
    pull.add_argument("--cache", default=str(mf.DEFAULT_ROOT / "runs" / "deckpool-cache"),
                      help="where fetched pages are saved, so a resume never refetches")
    pull.add_argument("--cards", default=str(DEFAULT_CARDS), help="sweep output with the known card names")
    args = parser.parse_args(argv)
    if not Path(args.cards).is_file():
        parser.error(f"no card names at {args.cards}; run the sweep first")
    if args.command == "pull":
        from deckpool import pull as pl
        try:
            record = pl.run_pull(mf.DEFAULT_ROOT, Path(args.cache), load_name_set(Path(args.cards)), again=args.again)
        except (pl.AlreadyPulled, pl.PullStopped) as e:
            print(f"pull stopped: {e}", file=sys.stderr)
            return 1
        print(f"pulled {len(record['decks'])} decks in {record['requests']} requests")
        return 0
    try:
        deck = add_deck(Path(args.list), args.id, args.archetype, args.split, args.source,
                        load_name_set(Path(args.cards)))
    except (DeckError, mf.ManifestError) as e:
        print(f"not added: {e}", file=sys.stderr)
        return 1
    print(f"added {deck.id} ({deck.archetype}, {deck.split}) as {deck.file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
