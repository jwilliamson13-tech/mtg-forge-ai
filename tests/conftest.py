"""Shared test fixtures.

Every file in tests/fixtures/ is a byte-for-byte copy of a Forge script from
forge-gui/res/cardsfolder or tokenscripts at Forge commit
64569999b8b99dd7176ab9d3e1e6008f3ab68590. `.gitattributes` keeps git from
rewriting their line endings.

`script` and `fixture_path` return functions rather than values, so a test can
load several fixtures by name. That is the usual pytest factory-fixture idiom.
"""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def script():
    """Return the verbatim text of a Forge script in tests/fixtures/."""

    def read(name: str) -> str:
        return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")

    return read


@pytest.fixture
def fixture_path():
    return lambda name: FIXTURES / f"{name}.txt"
