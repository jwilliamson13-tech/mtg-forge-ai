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

`cardscript`, `simrunner` and the `gamerecord` loader need nothing outside the standard library. The Parquet converter needs `pyarrow`, and `setmodel` needs PyTorch and the ONNX packages. `requirements-dev.txt` holds those and the test runner, with the pinned CUDA 12.6 PyTorch:

```
pip install -r requirements-dev.txt
python -m pytest
```

The tests feed real Forge scripts from `tests/fixtures/` to `parse_card` and check what comes back. Those `.txt` fixtures are byte-for-byte copies of Forge's files at commit `64569999b8b99dd7176ab9d3e1e6008f3ab68590`; `.gitattributes` keeps git from rewriting their line endings. `tests/fixtures/games/` holds hand-written game records as plain JSONL, and the tests gzip them.
