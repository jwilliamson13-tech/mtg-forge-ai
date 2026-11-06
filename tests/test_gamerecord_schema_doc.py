"""The schema document and the validator must name the same fields.

The Java hook is written from the document and the loader enforces the code. If the
two drift apart, the first real batch fails, so this test fails first.
"""

import re
from pathlib import Path

import pytest

from gamerecord.convert import CARD_SCHEMA, SNAPSHOT_SCHEMA, STACK_SCHEMA
from gamerecord.record import (CARD, END_REASONS, FACES, METADATA, SCHEMA_VERSION, SCOREBOARD, SEAT, SIM_MODES,
                               SNAPSHOT, STACK_ITEM, STACK_KINDS, TARGET, TRIPWIRE, ZONES)

DOC = (Path(__file__).parent.parent / "docs" / "game-record.md").read_text(encoding="utf-8")
ROW = re.compile(r"^\| `([^`]+)` \| ([^|]+?) \|", re.MULTILINE)


def table_under(heading):
    """Return {name: type} from the first table after a heading, up to the next heading."""
    start = DOC.index(f"\n{heading}\n")
    end = re.compile(r"^#", re.MULTILINE).search(DOC, start + len(heading) + 2)
    section = DOC[start:end.start() if end else len(DOC)]
    return dict(ROW.findall(section))


@pytest.mark.parametrize("heading, shape", [
    ("## Metadata line", METADATA),
    ("### Seat", SEAT),
    ("### Tripwire summary", TRIPWIRE),
    ("## Snapshot lines", SNAPSHOT),
    ("### Scoreboard", SCOREBOARD),
    ("### Zones", ZONES),
    ("### Card", CARD),
    ("### Stack item", STACK_ITEM),
    ("### Target", TARGET),
], ids=lambda value: value if isinstance(value, str) else "")
def test_each_json_table_names_exactly_the_validated_fields_with_a_type(heading, shape):
    documented = table_under(heading)
    assert documented.keys() == shape.fields.keys()
    assert all(documented.values())


@pytest.mark.parametrize("heading, schema", [("### snapshots table", SNAPSHOT_SCHEMA), ("### cards table", CARD_SCHEMA),
                                             ("### stack table", STACK_SCHEMA)], ids=["snapshots", "cards", "stack"])
def test_each_parquet_table_names_exactly_the_written_columns_and_types(heading, schema):
    documented = table_under(heading)
    assert documented.pop("batch_id") == "string (partition)"
    assert documented == {column.name: str(column.type) for column in schema}


def test_the_document_states_the_version():
    assert f"The current `schema_version` is {SCHEMA_VERSION}." in DOC


@pytest.mark.parametrize("heading, values", [("### End reasons", END_REASONS), ("### Sim modes", SIM_MODES),
                                             ("### Faces", FACES), ("### Stack kinds", STACK_KINDS)],
                         ids=["end reasons", "sim modes", "faces", "stack kinds"])
def test_each_closed_list_matches_the_code(heading, values):
    assert list(table_under(heading)) == list(values)
