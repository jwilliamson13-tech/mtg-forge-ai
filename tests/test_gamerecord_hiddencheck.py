"""The attacks on hidden cards work on Forge's own ids, and find nothing on record ids."""

import gzip
import json

import pytest

from gamerecord.__main__ import main
from gamerecord.hiddencheck import check_folder, check_games


def card(card_id, name, controller=1, face_down=True):
    return {"id": card_id, "name": name, "controller": controller, "tapped": False, "power": 2, "toughness": 2,
            "counters": {}, "damage": 0, "face_down": face_down}


def line(side, battlefield, turn=3, stack=()):
    zones = {zone: [] for zone in ("me_hand", "me_graveyard", "opp_graveyard", "me_exile", "opp_exile", "opp_revealed")}
    return {"side": side, "scoreboard": {"turn": turn}, "zones": {**zones, "battlefield": battlefield}, "stack": list(stack)}


def write_game(folder, number, snapshots):
    path = folder / f"b_1_{number}_a_vs_b.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as out:
        out.write(json.dumps({"seed": 1, "game_number": number}) + "\n")
        for snapshot in snapshots:
            out.write(json.dumps(snapshot) + "\n")
    return path


def test_forges_ids_name_a_hidden_card_from_another_game(tmp_path):
    write_game(tmp_path, 1, [line(2, [card(7, None)])])
    write_game(tmp_path, 2, [line(1, [card(7, "Grizzly Bears", face_down=False)])])

    check = check_folder(tmp_path)

    assert (check.games, check.hidden, check.by_other_games) == (2, 1, 1)
    assert not check.ids_named_nothing


def test_shared_ids_name_a_hidden_card_from_the_other_side(tmp_path):
    write_game(tmp_path, 1, [line(1, [card(7, "Grizzly Bears")]), line(2, [card(7, None)])])

    check = check_folder(tmp_path)

    assert (check.hidden, check.by_other_side, check.by_other_games) == (1, 1, 0)


def test_a_hidden_spell_on_the_stack_counts_too(tmp_path):
    item = {"id": 7, "name": None, "face": None, "face_down": True}
    write_game(tmp_path, 1, [line(1, [card(7, "Grizzly Bears")]), line(2, [], stack=[item])])

    assert check_folder(tmp_path).by_other_side == 1


def test_a_card_named_later_by_its_own_side_is_not_an_attack(tmp_path):
    write_game(tmp_path, 1, [line(2, [card(7, None)]), line(2, [card(7, "Grizzly Bears", face_down=False)], turn=4)])

    check = check_folder(tmp_path)

    assert (check.by_other_games, check.by_other_side) == (0, 0)


def test_ids_of_their_own_per_side_and_game_name_nothing(tmp_path):
    write_game(tmp_path, 1, [line(1, [card(101, "Grizzly Bears")]), line(2, [card(201, None)])])
    write_game(tmp_path, 2, [line(1, [card(102, "Grizzly Bears")]), line(2, [card(202, None)])])

    check = check_folder(tmp_path)

    assert check.hidden == 2
    assert check.ids_named_nothing


def test_public_state_is_counted_apart_and_needs_a_single_candidate(tmp_path):
    one = write_game(tmp_path, 1, [line(1, [card(101, "Grizzly Bears")]), line(2, [card(201, None)])])
    two = write_game(tmp_path, 2, [line(1, [card(102, "Grizzly Bears"), card(103, "Hill Giant")]), line(2, [card(202, None)])])

    assert check_games([one]).by_public_state == 1
    assert check_games([two]).by_public_state == 0


def test_the_command_exits_1_when_an_id_names_a_card(tmp_path, capsys):
    write_game(tmp_path, 1, [line(1, [card(7, "Grizzly Bears")]), line(2, [card(7, None)])])

    assert main(["hiddencheck", str(tmp_path)]) == 1
    assert "named by ids from the other side 1" in capsys.readouterr().out


def test_the_command_exits_0_when_no_id_does(tmp_path):
    write_game(tmp_path, 1, [line(1, [card(101, "Grizzly Bears")]), line(2, [card(201, None)])])

    assert main(["hiddencheck", str(tmp_path)]) == 0


def test_the_command_refuses_an_empty_folder(tmp_path):
    with pytest.raises(SystemExit):
        main(["hiddencheck", str(tmp_path)])
