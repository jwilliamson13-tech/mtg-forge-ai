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

Run the tests. The package itself needs nothing outside the standard library; `requirements-dev.txt` holds the test runner:

```
pip install -r requirements-dev.txt
python -m pytest
```

The tests feed real Forge scripts from `tests/fixtures/` to `parse_card` and check what comes back. Fixtures are byte-for-byte copies of Forge's files at commit `64569999b8b99dd7176ab9d3e1e6008f3ab68590`; `.gitattributes` keeps git from rewriting their line endings.
