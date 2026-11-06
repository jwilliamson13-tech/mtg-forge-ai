"""The Java hook's real output, so that the loader's rules are held against what Forge writes.

`tests/fixtures/games/real_game.jsonl.gz` is a file as the hook wrote it, byte for byte: game 3
of a worker in the 100-game Abhorrent Oculus deck vs Bloodtithe Harvester batch, with honest play
and the tripwire on. Every other fixture here is hand-written. This one has to be replaced when
the schema changes, by running a batch and copying a file in.
"""

import shutil
from pathlib import Path

import pytest

from gamerecord import game_digest, load_game, read_metadata
from gamerecord.convert import convert_folder
from gamerecord.hiddencheck import check_games

REAL_GAME = Path(__file__).parent / "fixtures" / "games" / "real_game.jsonl.gz"


@pytest.fixture(scope="module")
def game():
    return load_game(REAL_GAME)


def cards_in(snapshot):
    return [card for cards in snapshot["zones"].values() for card in cards]


def test_the_loader_takes_a_real_file_whole(game):
    assert game.metadata["end_reason"] == "win"
    assert game.metadata["winner"] in (1, 2)
    assert game.honest_play
    assert game.metadata["tripwire"] == {"enabled": True, "hits": 0, "signatures": []}
    assert {snapshot["side"] for snapshot in game.snapshots} == {1, 2}


def test_the_metadata_alone_says_what_the_loader_says(game):
    assert read_metadata(REAL_GAME) == game.metadata


def test_it_holds_what_the_hand_written_fixtures_only_imitate(game):
    cards = [card for snapshot in game.snapshots for card in cards_in(snapshot)]
    assert any(card["name"] is None and card["face_down"] for card in cards)
    assert any(snapshot["stack"] for snapshot in game.snapshots)
    assert any(snapshot["zones"]["opp_revealed"] for snapshot in game.snapshots)


def test_a_logged_card_has_no_id_and_every_other_card_has_one(game):
    for snapshot in game.snapshots:
        for zone, cards in snapshot["zones"].items():
            assert all((card["id"] is None) == (zone == "opp_revealed") for card in cards)


def test_the_two_sides_share_no_id(game):
    ids = {1: set(), 2: set()}
    for snapshot in game.snapshots:
        ids[snapshot["side"]] |= {row["id"] for row in [*cards_in(snapshot), *snapshot["stack"]]} - {None}
    assert not ids[1] & ids[2]


def test_a_card_that_moves_gets_a_new_id(game):
    for side in (1, 2):
        zones = {}
        for snapshot in game.snapshots:
            if snapshot["side"] == side:
                for zone, cards in snapshot["zones"].items():
                    zones.setdefault(zone, set()).update(card["id"] for card in cards)
        assert not zones["me_hand"] & zones["battlefield"]
        assert not zones["battlefield"] & zones["me_graveyard"]


def test_no_id_names_a_hidden_card(game):
    check = check_games([REAL_GAME])
    assert check.hidden > 0
    assert check.ids_named_nothing


def test_the_battlefield_is_in_id_order(game):
    for snapshot in game.snapshots:
        ids = [card["id"] for card in snapshot["zones"]["battlefield"]]
        assert ids == sorted(ids)


def test_a_real_file_converts(tmp_path):
    shutil.copy(REAL_GAME, tmp_path)
    report = convert_folder(tmp_path, tmp_path / "out")
    assert report.kept["win"] == 1
    assert report.rejected == []
    assert report.snapshot_rows > 0 and report.card_rows > 0 and report.stack_rows > 0


def test_a_real_file_has_a_digest():
    number, digest = game_digest(REAL_GAME)
    assert number == read_metadata(REAL_GAME)["game_number"]
    assert len(digest) == 64
