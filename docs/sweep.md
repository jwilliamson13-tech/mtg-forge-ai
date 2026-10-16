# Sweep

`python -m cardscript sweep` parses every script under one or more folders and prints how many parsed. It runs the same `parse_card` as everything else.

```
python -m cardscript sweep FOLDER [FOLDER ...] [--out FILE] [--min-coverage PCT] [--top N] [--exclude NAME ... | --no-exclude]
```

The checkpoint run covers Forge's cards and tokens in one go:

```
python -m cardscript sweep forge/forge-gui/res/cardsfolder forge/forge-gui/res/tokenscripts --out cards.jsonl --min-coverage 95
```

## What it reads

Every `.txt` file under each folder, at any depth, in sorted path order. Other files (`.gitattributes`, `mkzip.sh`) don't count. Sub-folders named `rebalanced` or `upcoming` are skipped at any depth: Alchemy rebalances and unreleased cards would skew the number. `--exclude NAME` (repeatable) replaces that list and `--no-exclude` empties it. The folder you name on the command line is never skipped, so `sweep cardsfolder/upcoming` still works.

Files are read as UTF-8, with or without a BOM. CRLF and LF both work.

## The report

```
scripts   34702
parsed    34701
coverage  99.99%
failures by cause:
  1  unknown ability API 'ST'  (cardsfolder/c/circling_vultures.txt)
```

(That is the first smoke run; the `ST$` failure is fixed. See The checkpoint run.)

Coverage is parsed over scripts, rounded down to two decimals, so `100.00%` means every script parsed. Causes are listed most common first, with the first file that failed that way. `--top N` sets how many causes to list (default 10). If more exist, the last line says how many were left out.

`--min-coverage 95` makes the command exit 1, with a line on stderr, when coverage is below 95. It compares the exact percentage, not the rounded one. Without the flag the exit code is 0 whatever the coverage. A folder that doesn't exist exits 2.

## Failures

A script that raises counts as seen and not parsed. Nothing is skipped and nothing stops the sweep.

- A `cardscript.ParseError` is grouped under its `cause` (see `card-structure.md`, "Errors and warnings").
- Any other exception is grouped under `unexpected <ExceptionType>`, such as `unexpected KeyError`. A script that raises one has found a parser bug. A file that isn't valid UTF-8 shows up as `unexpected UnicodeDecodeError`. The message is left out of the cause on purpose: `KeyError('Foo')` and `KeyError('Bar')` are probably the same bug and should count together.

From Python, `cardscript.sweep.sweep(folders)` returns a `SweepReport` whose `failures` list holds every failing file with its cause and detail (the offending line for a `ParseError`, the exception message otherwise).

## JSONL

`--out FILE` writes one JSON line per parsed card, in walk order, as UTF-8 with LF endings. Each line is the card's `to_dict()` (see `card-structure.md`) plus a `file` key holding the path from the swept folder's parent: `cardsfolder/l/lightning_bolt.txt` or `tokenscripts/r_1_1_goblin.txt`. `Card.from_dict` and `Card.from_json` ignore `file`.

Build the vocabulary from the dump without parsing again:

```python
import json
from cardscript.vocab import build_vocabulary

with open("cards.jsonl", encoding="utf-8") as f:
    vocab = build_vocabulary((json.loads(line) for line in f), min_count=5)
vocab.save("vocab.json")
```

## The checkpoint run

Run on 2026-10-16 over Forge commit `64569999b8b99dd7176ab9d3e1e6008f3ab68590` (`forge-gui/res/cardsfolder` and `forge-gui/res/tokenscripts`, default exclusions):

| | scripts | parsed | coverage |
|---|---|---|---|
| cardsfolder | 33,845 | 33,845 | 100.00% |
| tokenscripts | 857 | 857 | 100.00% |
| both | 34,702 | 34,702 | 100.00% |

The excluded sub-folders hold 230 `rebalanced` and 13 `upcoming` scripts. Every `A:`, `T:`, `S:`, `R:` and `K:` line (63,336) became exactly one top-level ability. Warnings land on 26 cards; see "Errors and warnings" in `card-structure.md`.

The outputs live in the main checkout's gitignored `data/` folder, which Spec 5 reads:

- `data/cards.jsonl`: the dump, 34,702 lines.
- `data/vocab.json`: `build_vocabulary(..., min_count=5)` over the dump: 3,494 tokens and 231 numeric keys, reserved entries included.

To rebuild them, from the repo root with Forge cloned beside it:

```
python -m cardscript sweep ../forge/forge-gui/res/cardsfolder ../forge/forge-gui/res/tokenscripts --out data/cards.jsonl --min-coverage 95
```

then the vocabulary snippet above with `min_count=5` and `vocab.save("data/vocab.json")`.
