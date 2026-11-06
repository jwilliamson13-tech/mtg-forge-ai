"""Command line: `python -m deckpool add-deck LIST.txt --id ID --archetype NAME --split train|held_out`.

Converts the plain-text list, checks its names, writes the original text and the
Forge file into `decks/`, and adds the deck to the manifest.

`python -m deckpool pull` is the one-off MTGTop8 Legacy pull; it refuses a second run without `--again`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from deckpool import manifest as mf
from deckpool.convert import MIN_MAIN_CARDS, DeckError, parse_decklist, to_forge
from deckpool.names import DEFAULT_CARDSFOLDER, load_name_set

DEFAULT_CARDS = mf.DEFAULT_ROOT / "data" / "cards.jsonl"


def add_deck(list_path: Path, deck_id: str, archetype: str, split: str, source: str,
             names, root: Path = mf.DEFAULT_ROOT, manifest_path: Path | None = None) -> mf.Deck:
    """Validate and place one deck. Writes nothing if the conversion or the manifest rules fail."""
    manifest_path = manifest_path or root / "decks" / "manifest.json"
    manifest = mf.load(manifest_path) if manifest_path.is_file() else mf.Manifest()
    text = Path(list_path).read_text(encoding="utf-8")
    parsed = parse_decklist(text)
    if parsed.main_cards < MIN_MAIN_CARDS:
        raise DeckError(f"main deck has {parsed.main_cards} cards; it needs at least {MIN_MAIN_CARDS}")
    forge_text = to_forge(parsed, names, deck_id)
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
    add.add_argument("--cardsfolder", default=str(DEFAULT_CARDSFOLDER), help="Forge cardsfolder, to tell split cards from double-faced ones")
    pull = commands.add_parser("pull", help="one-off pull of the Legacy pool from mtgtop8.com")
    pull.add_argument("--again", action="store_true", help="run even though decks/pull.json says the pool was pulled")
    pull.add_argument("--cache", default=str(mf.DEFAULT_ROOT / "runs" / "deckpool-cache"),
                      help="where fetched pages are saved, so a resume never refetches")
    pull.add_argument("--cards", default=str(DEFAULT_CARDS), help="sweep output with the known card names")
    pull.add_argument("--cardsfolder", default=str(DEFAULT_CARDSFOLDER), help="Forge cardsfolder, to tell split cards from double-faced ones")
    build = commands.add_parser("build-manifest", help="write decks/manifest.json from decks/pull.json")
    build.add_argument("--seed", type=int, required=True, help="seed for choosing the held-out archetypes")
    build.add_argument("--leave-out", nargs="*", default=[], help="deck ids that can't play, kept out of the manifest")
    args = parser.parse_args(argv)
    if args.command == "build-manifest":
        record = json.loads((mf.DEFAULT_ROOT / "decks" / "pull.json").read_text(encoding="utf-8"))
        if mf.DEFAULT_PATH.is_file():
            parser.error(f"{mf.DEFAULT_PATH} exists; the split is assigned once, so edit it by hand to change it")
        manifest = mf.build_from_pull(record, args.seed, args.leave_out)
        mf.save(manifest, mf.DEFAULT_PATH)
        held = sorted({d.archetype for d in manifest.decks if d.split == "held_out"})
        print(f"{len(manifest.decks)} decks; held out: {'; '.join(held)}")
        return 0
    if not Path(args.cards).is_file():
        parser.error(f"no card names at {args.cards}; run the sweep first")
    if args.command == "pull":
        from deckpool import pull as pl
        try:
            names = load_name_set(Path(args.cards), Path(args.cardsfolder))
            record = pl.run_pull(mf.DEFAULT_ROOT, Path(args.cache), names, again=args.again)
        except (pl.AlreadyPulled, pl.PullStopped) as e:
            print(f"pull stopped: {e}", file=sys.stderr)
            return 1
        print(f"pulled {len(record['decks'])} decks in {record['requests']} requests")
        return 0
    try:
        deck = add_deck(Path(args.list), args.id, args.archetype, args.split, args.source,
                        load_name_set(Path(args.cards), Path(args.cardsfolder)))
    except (DeckError, mf.ManifestError) as e:
        print(f"not added: {e}", file=sys.stderr)
        return 1
    print(f"added {deck.id} ({deck.archetype}, {deck.split}) as {deck.file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
