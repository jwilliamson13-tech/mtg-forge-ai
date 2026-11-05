"""Shared test fixtures.

Every .txt file in tests/fixtures/ is a byte-for-byte copy of a Forge script from
forge-gui/res/cardsfolder or tokenscripts at Forge commit
64569999b8b99dd7176ab9d3e1e6008f3ab68590. `.gitattributes` keeps git from
rewriting their line endings.

tests/fixtures/games/ holds hand-written game records as plain JSONL, so a diff can
show them. `game_file` gzips one into a test's temp folder, the form Forge writes.

`script` and `fixture_path` return functions rather than values, so a test can
load several fixtures by name. That is the usual pytest factory-fixture idiom.
"""

import gzip
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GAMES = FIXTURES / "games"


@pytest.fixture
def script():
    """Return the verbatim text of a Forge script in tests/fixtures/."""

    def read(name: str) -> str:
        return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")

    return read


@pytest.fixture
def fixture_path():
    return lambda name: FIXTURES / f"{name}.txt"


@pytest.fixture
def game_file(tmp_path):
    """Gzip a game fixture, byte for byte, into a folder and return its path."""

    def write(name: str, folder: Path = tmp_path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.jsonl.gz"
        path.write_bytes(gzip.compress((GAMES / f"{name}.jsonl").read_bytes()))
        return path

    return write


@pytest.fixture
def game_lines():
    """Return a game fixture parsed line by line: metadata first, then snapshots."""

    def read(name: str) -> list[dict]:
        text = (GAMES / f"{name}.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    return read


@pytest.fixture
def write_game(tmp_path):
    """Gzip parsed lines into a game file, so a test can change a fixture first."""

    def write(lines: list[dict], name: str = "game", folder: Path = tmp_path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.jsonl.gz"
        text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
        path.write_bytes(gzip.compress(text.encode("utf-8")))
        return path

    return write
