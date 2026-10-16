# mtg-forge-ai

A learned board evaluator for Forge. `HANDOFF.md` has the design.

## Card-script parser

`cardscript` turns a Forge card script into plain data, described in `docs/card-structure.md`. `cardscript.vocab` builds the token and numeric-key tables the encoder looks up, described in `docs/vocabulary.md`. It needs Python 3.11 or newer and nothing outside the standard library.

Print one card's parsed structure:

```
python -m cardscript show tests/fixtures/lightning_bolt.txt
```

Run the tests (pytest is the only dependency):

```
pip install -r requirements.txt
python -m pytest
```

The tests feed real Forge scripts from `tests/fixtures/` to `parse_card` and check what comes back. Fixtures are byte-for-byte copies of Forge's files; `.gitattributes` keeps git from rewriting their line endings.
