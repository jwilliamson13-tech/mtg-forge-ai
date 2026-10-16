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
