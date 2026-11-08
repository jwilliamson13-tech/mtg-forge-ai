# mtg-forge-ai

A learned board evaluator for Forge. `HANDOFF.md` has the design.

## Card-script parser

`cardscript` turns a Forge card script into plain data, described in `docs/card-structure.md`. `cardscript.vocab` builds the token and numeric-key tables the encoder looks up, described in `docs/vocabulary.md`. It needs Python 3.11 or newer and nothing outside the standard library.

Print one card's parsed structure:

```
python -m cardscript show tests/fixtures/lightning_bolt.txt
```

Parse Forge's card and token folders, print coverage and the top failure causes, and dump every parsed card to JSONL (`docs/sweep.md` has the details):

```
python -m cardscript sweep path/to/cardsfolder path/to/tokenscripts --out cards.jsonl --min-coverage 95
```

## Sim-runner

`simrunner` runs batches of headless `forge sim` games in parallel, each worker in its own throwaway Forge profile, and reports games finished, draws by cause, wins per seat and games per hour. `docs/forge-build.md` has the build, the command and the measured rates.

```
python -m simrunner batch deck1.dck deck2.dck --games 50 --seed 1
```

`python -m simrunner matchup` plays two decks in seat-swapped rounds until the first deck's win rate has a 95% Wilson interval no wider than 5 points either side, or refuses when its game budget runs out. "Matchups" in `docs/forge-build.md` has the details.

Each seat plays stock heuristic, stock full sim, or a model generation: `--sim-mode full --evaluator onnx:gen1` plays the export bundle in `models/gen1/`. `python -m simrunner cardswap DECK OPPONENT --swap OLD=NEW` runs deck A and deck A with the swap made as two matchups on the same seed, and reports both rates and the difference with its interval. "Seat configurations" and "Card swaps" in `docs/forge-build.md` have the details.

`python -m simrunner ladder --players heuristic full onnx:gen0 --seed S` rates AI configurations against each other over a fixed sample of held-out deck pairs, each player on each deck in each seat. It fits Elo ratings with profile-likelihood intervals, and a later run with a new generation plays only that generation's games. "Elo ladder" in `docs/forge-build.md` has the details.

`python -m simrunner converge onnx:gen0 onnx:gen1 --seed S` runs the four tracked matchups under both generations to the same width and gives each a verdict: converged when the two intervals overlap, moved when they don't. "Convergence" in `docs/forge-build.md` has the details.

`python -m simrunner generation --from gen0 --to gen1 --games N --seed S --start 23:00 --stop 07:00` runs one generation step end to end: a pool played by gen0 in both seats, the data checks, conversion, training, ablations, pair test, export, ladder and convergence check, then one entry in `results/log.jsonl`. A step that finished is skipped on the next start, so the same command continues after a crash or the hard stop. "Generation loop" in `docs/forge-build.md` has the steps, the gates and the commands for #44.

## Deck pool

`deckpool` holds the real Legacy lists in `decks/` and splits them into training and held-out decks. `docs/deck-pool.md` has the commands and the near-copy rule. `simrunner` takes a deck id from `decks/manifest.json` in place of a file.

```
python -m deckpool check
python -m simrunner batch hogaak-1 elves-1 --games 50 --seed 1
```

## Game records

`docs/game-record.md` is the contract between Forge and Python: one gzip'd JSONL file per game. `gamerecord.load_game` reads a file and validates it against that contract. This command converts a folder of them to Parquet, keeps the wins, and reports what it kept and dropped by end reason:

```
python -m gamerecord convert path/to/games path/to/parquet
```

## Training

`setmodel` joins game records to parsed cards, trains the two-level set model, runs the ablations and pair test, and exports the bundle Forge loads. `docs/training.md` has the commands and the PyTorch pin for the 1080 Ti, and `docs/tensor-contract.md` is the contract with Forge.

```
python -m setmodel train path/to/parquet data/cards.jsonl decks/manifest.json --out runs/gen0 --generation 0
```

## Tests

`cardscript`, `simrunner` and the `gamerecord` loader need nothing outside the standard library. The Parquet converter needs `pyarrow`, and `setmodel` needs PyTorch and the ONNX packages. `requirements-dev.txt` holds those and the test runner, with the pinned CUDA 12.6 PyTorch. The test suite needs all of them, so install the file in a venv and run the tests with that venv's Python. A Python without `onnx` stops at collection on `tests/test_setmodel_export.py` and runs nothing.

```
python -m venv venv
venv\Scripts\python -m pip install -r requirements-dev.txt
venv\Scripts\python -m pytest
```

The tests feed real Forge scripts from `tests/fixtures/` to `parse_card` and check what comes back. Those `.txt` fixtures are byte-for-byte copies of Forge's files at commit `64569999b8b99dd7176ab9d3e1e6008f3ab68590`; `.gitattributes` keeps git from rewriting their line endings. `tests/fixtures/games/` holds hand-written game records as plain JSONL, and the tests gzip them.
