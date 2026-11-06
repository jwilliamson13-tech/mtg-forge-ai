"""The game-record loader, from the outside: a game file in, validated records out.

The fixtures in tests/fixtures/games/ are hand-written, because no Forge hook exists
yet. Every expected value here comes from reading those files or the spec.
"""

import gzip

import pytest

from gamerecord import ExceptionGameError, GameRecordError, load_game


def test_a_win_loads_with_its_metadata_and_every_snapshot(game_file):
    game = load_game(game_file("win"))

    assert game.metadata["end_reason"] == "win"
    assert game.metadata["winner"] == 1
    assert game.metadata["final_turn"] == 3
    assert game.metadata["tripwire"]["hits"] == 3
    assert [s["side"] for s in game.snapshots] == [1, 2, 1, 2]
    assert game.snapshots[2]["scoreboard"]["opp_life"] == 3
    assert game.snapshots[3]["zones"]["me_hand"][0]["name"] == "Force of Will"


def test_a_card_name_outside_ascii_survives(game_file):
    game = load_game(game_file("win"))
    assert game.snapshots[2]["zones"]["me_graveyard"][0]["name"] == "Lim-Dûl's Vault"


def test_a_file_from_another_schema_version_is_rejected(game_file):
    with pytest.raises(GameRecordError, match="schema_version"):
        load_game(game_file("wrong_version"))


def test_a_version_of_true_is_not_version_one(game_lines, write_game):
    lines = game_lines("win")
    lines[0]["schema_version"] = True
    with pytest.raises(GameRecordError, match="schema_version"):
        load_game(write_game(lines))


def test_the_version_is_reported_before_any_other_field(game_lines, write_game):
    """A newer file may differ anywhere. The version is the useful thing to say."""
    lines = game_lines("wrong_version")
    del lines[0]["seats"]
    with pytest.raises(GameRecordError, match="schema_version"):
        load_game(write_game(lines))


def test_a_file_missing_a_required_field_is_rejected(game_file):
    with pytest.raises(GameRecordError, match="final_turn"):
        load_game(game_file("missing_field"))


METADATA_FIELDS = ["schema_version", "batch_id", "seed", "game_number", "seats", "winner", "final_turn",
                   "end_reason", "tripwire"]
SEAT_FIELDS = ["seat", "deck", "ai_profile", "sim_mode", "search_budget"]
TRIPWIRE_FIELDS = ["enabled", "hits", "signatures"]
SNAPSHOT_FIELDS = ["side", "scoreboard", "zones", "stack"]
SCOREBOARD_FIELDS = ["me_life", "opp_life", "me_poison", "opp_poison", "me_library", "opp_library", "opp_hand",
                     "turn", "me_active", "me_on_play"]
ZONES = ["battlefield", "me_hand", "me_graveyard", "opp_graveyard", "me_exile", "opp_exile", "opp_revealed"]
CARD_FIELDS = ["id", "name", "face", "power", "toughness", "tapped", "counters", "summoning_sick", "attached_to",
               "attached_to_seat", "controller", "face_down", "damage", "attacking_seat", "attacking_card", "blocking",
               "blocked_by"]
STACK_FIELDS = ["id", "name", "face", "face_down", "controller", "kind", "targets"]
TARGET_FIELDS = ["card", "seat"]


def fields_in(names, reach):
    return [pytest.param(name, reach, id=f"{reach.__name__}-{name}") for name in names]


def metadata(lines):
    return lines[0]


def seat(lines):
    return lines[0]["seats"][1]


def tripwire(lines):
    return lines[0]["tripwire"]


def snapshot(lines):
    return lines[3]


def scoreboard(lines):
    return lines[3]["scoreboard"]


def zones(lines):
    return lines[3]["zones"]


def card(lines):
    return lines[3]["zones"]["battlefield"][0]


def stack_item(lines):
    return lines[3]["stack"][0]


def target(lines):
    return lines[3]["stack"][0]["targets"][0]


EVERY_FIELD = (fields_in(METADATA_FIELDS, metadata) + fields_in(SEAT_FIELDS, seat) + fields_in(TRIPWIRE_FIELDS, tripwire)
               + fields_in(SNAPSHOT_FIELDS, snapshot) + fields_in(SCOREBOARD_FIELDS, scoreboard)
               + fields_in(ZONES, zones) + fields_in(CARD_FIELDS, card)
               + fields_in(STACK_FIELDS, stack_item) + fields_in(TARGET_FIELDS, target))


@pytest.mark.parametrize("name, reach", EVERY_FIELD)
def test_every_field_is_required(game_lines, write_game, name, reach):
    lines = game_lines("win")
    del reach(lines)[name]
    with pytest.raises(GameRecordError, match=name):
        load_game(write_game(lines))


@pytest.mark.parametrize("reach", [metadata, seat, tripwire, snapshot, scoreboard, zones, card])
def test_an_unknown_field_is_drift_and_is_rejected(game_lines, write_game, reach):
    lines = game_lines("win")
    reach(lines)["surprise"] = 1
    with pytest.raises(GameRecordError, match="surprise"):
        load_game(write_game(lines))


def test_an_exception_end_reason_is_rejected_with_its_own_error(game_file):
    with pytest.raises(ExceptionGameError, match="exception"):
        load_game(game_file("exception"))


def test_an_exception_file_with_no_snapshots_is_still_called_an_exception(game_lines, write_game):
    with pytest.raises(ExceptionGameError):
        load_game(write_game(game_lines("exception")[:1]))


@pytest.mark.parametrize("name, reason, final_turn", [("draw_clock", "draw_clock", 1),
                                                      ("draw_turn_cap", "draw_turn_cap", 51)])
def test_a_stopped_game_is_a_draw_with_no_winner(game_file, name, reason, final_turn):
    game = load_game(game_file(name))
    assert game.metadata["end_reason"] == reason
    assert game.metadata["winner"] is None
    assert game.metadata["final_turn"] == final_turn


@pytest.mark.parametrize("reason", ["draw_clock", "draw_turn_cap", "draw_rules"])
def test_a_draw_naming_a_winner_is_rejected(game_lines, write_game, reason):
    """Forge names a false winner on a stopped game. That must not reach training."""
    lines = game_lines("draw_clock")
    lines[0]["end_reason"] = reason
    lines[0]["winner"] = 2
    with pytest.raises(GameRecordError, match="winner"):
        load_game(write_game(lines))


def test_a_rules_draw_loads(game_lines, write_game):
    lines = game_lines("draw_clock")
    lines[0]["end_reason"] = "draw_rules"
    assert load_game(write_game(lines)).metadata["end_reason"] == "draw_rules"


def test_a_win_with_no_winner_is_rejected(game_lines, write_game):
    lines = game_lines("win")
    lines[0]["winner"] = None
    with pytest.raises(GameRecordError, match="winner"):
        load_game(write_game(lines))


def test_an_unknown_end_reason_is_rejected(game_lines, write_game):
    lines = game_lines("win")
    lines[0]["end_reason"] = "conceded"
    with pytest.raises(GameRecordError, match="end_reason"):
        load_game(write_game(lines))


def test_each_side_sees_its_own_hand_and_a_hidden_card_has_no_name(game_file):
    game = load_game(game_file("win"))
    from_seat_1, from_seat_2 = game.snapshots[2], game.snapshots[3]

    hidden = from_seat_1["zones"]["battlefield"][7]
    assert (hidden["name"], hidden["face"], hidden["face_down"]) == (None, None, True)
    assert from_seat_2["zones"]["battlefield"][7]["name"] == "Willbender"
    assert from_seat_1["zones"]["me_hand"][0]["name"] == "Lightning Bolt"
    assert from_seat_2["zones"]["me_hand"][0]["name"] == "Force of Will"


def test_a_nameless_card_must_be_face_down(game_lines, write_game):
    lines = game_lines("win")
    lines[3]["zones"]["battlefield"][0].update(name=None, face=None)
    with pytest.raises(GameRecordError, match="face_down"):
        load_game(write_game(lines))


@pytest.mark.parametrize("change", [{"name": None}, {"face": None}])
def test_name_and_face_are_both_there_or_both_null(game_lines, write_game, change):
    lines = game_lines("win")
    lines[3]["zones"]["battlefield"][1].update(face_down=True, **change)
    with pytest.raises(GameRecordError, match="name"):
        load_game(write_game(lines))


@pytest.mark.parametrize("change", [{"power": None}, {"toughness": None}])
def test_power_and_toughness_are_both_there_or_both_null(game_lines, write_game, change):
    lines = game_lines("win")
    lines[3]["zones"]["battlefield"][1].update(change)
    with pytest.raises(GameRecordError, match="power"):
        load_game(write_game(lines))


def test_an_attachment_to_a_card_points_at_a_card_on_the_battlefield(game_lines, write_game):
    lines = game_lines("win")
    lines[3]["zones"]["battlefield"][2]["attached_to"] = 999
    with pytest.raises(GameRecordError, match="attached_to"):
        load_game(write_game(lines))


def test_a_card_attaches_to_a_card_or_a_seat_but_not_both(game_lines, write_game):
    lines = game_lines("win")
    lines[3]["zones"]["battlefield"][2]["attached_to_seat"] = 2
    with pytest.raises(GameRecordError, match="attached_to"):
        load_game(write_game(lines))


def test_a_curse_attached_to_a_player_loads(game_file):
    curse = load_game(game_file("win")).snapshots[2]["zones"]["battlefield"][3]
    assert (curse["attached_to"], curse["attached_to_seat"]) == (None, 2)


@pytest.mark.parametrize("reach, name, value", [
    (card, "tapped", 1),
    (card, "power", 1.5),
    (card, "power", "1"),
    (card, "counters", {"P1P1": 0}),
    (card, "counters", {"P1P1": True}),
    (card, "counters", []),
    (card, "face", "FaceDown"),
    (card, "controller", 3),
    (scoreboard, "me_active", 0),
    (scoreboard, "me_life", 20.0),
    (scoreboard, "me_life", 2**31),
    (scoreboard, "opp_hand", -1),
    (scoreboard, "turn", 0),
    (snapshot, "side", 0),
    (snapshot, "side", True),
    (zones, "battlefield", {}),
    (metadata, "seed", -1),
    (metadata, "seed", 2**63),
    (metadata, "game_number", 0),
    (metadata, "batch_id", "../escape"),
    (metadata, "batch_id", ""),
    (metadata, "winner", 3),
    (seat, "sim_mode", "search"),
    (seat, "deck", ""),
    (tripwire, "hits", -1),
])
def test_a_wrong_value_is_rejected_and_named(game_lines, write_game, reach, name, value):
    lines = game_lines("win")
    reach(lines)[name] = value
    with pytest.raises(GameRecordError, match=name):
        load_game(write_game(lines))


def test_the_stack_loads_top_first_with_a_spell_hidden_from_one_side(game_file):
    game = load_game(game_file("win"))

    seat_1, seat_2 = game.snapshots[2]["stack"], game.snapshots[3]["stack"]
    assert [item["kind"] for item in seat_1] == ["ability", "spell"]
    assert seat_1[0]["targets"] == [{"card": None, "seat": 2}, {"card": 32, "seat": None}]
    assert seat_1[1]["name"] is None
    assert seat_2[1]["name"] == "Willbender"


def test_combat_and_damage_load_on_the_card(game_file):
    game = load_game(game_file("win"))

    battlefield = {card["id"]: card for card in game.snapshots[2]["zones"]["battlefield"]}
    assert (battlefield[13]["attacking_seat"], battlefield[13]["blocked_by"]) == (2, [32])
    assert (battlefield[32]["blocking"], battlefield[32]["damage"]) == ([13], 1)


def stack_top(lines):
    return lines[3]["stack"][1]


@pytest.mark.parametrize("change", [{"name": "Willbender"}, {"face": "Original"}])
def test_a_stack_item_has_name_and_face_both_or_neither(game_lines, write_game, change):
    lines = game_lines("win")
    stack_top(lines).update(change)
    with pytest.raises(GameRecordError, match="name and face"):
        load_game(write_game(lines))


def test_a_nameless_stack_item_must_be_face_down(game_lines, write_game):
    lines = game_lines("win")
    stack_top(lines)["face_down"] = False
    with pytest.raises(GameRecordError, match="face_down"):
        load_game(write_game(lines))


def test_a_stack_item_is_a_spell_or_an_ability(game_lines, write_game):
    lines = game_lines("win")
    stack_top(lines)["kind"] = "trigger"
    with pytest.raises(GameRecordError, match="kind"):
        load_game(write_game(lines))


@pytest.mark.parametrize("value", [{"card": None, "seat": None}, {"card": 32, "seat": 2}])
def test_a_target_is_a_card_or_a_seat(game_lines, write_game, value):
    lines = game_lines("win")
    stack_item(lines)["targets"][0] = value
    with pytest.raises(GameRecordError, match="target"):
        load_game(write_game(lines))


def attacker(lines):
    return next(c for c in lines[3]["zones"]["battlefield"] if c["id"] == 13)


def test_a_card_attacks_a_seat_or_a_card_but_not_both(game_lines, write_game):
    lines = game_lines("win")
    attacker(lines)["attacking_card"] = 32
    with pytest.raises(GameRecordError, match="attacking"):
        load_game(write_game(lines))


@pytest.mark.parametrize("change", [{"attacking_seat": None, "attacking_card": 999}, {"blocking": [999]},
                                    {"blocked_by": [999]}], ids=["attacking_card", "blocking", "blocked_by"])
def test_combat_points_at_cards_on_the_battlefield(game_lines, write_game, change):
    lines = game_lines("win")
    attacker(lines).update(change)
    with pytest.raises(GameRecordError, match="battlefield"):
        load_game(write_game(lines))


def test_marked_damage_cant_be_negative(game_lines, write_game):
    lines = game_lines("win")
    attacker(lines)["damage"] = -1
    with pytest.raises(GameRecordError, match="damage"):
        load_game(write_game(lines))


def test_a_negative_life_total_and_power_load(game_lines, write_game):
    """Platinum Angel keeps a player alive below zero. Forge allows negative power."""
    lines = game_lines("win")
    lines[3]["scoreboard"]["me_life"] = -7
    lines[3]["zones"]["battlefield"][1]["power"] = -2
    assert load_game(write_game(lines)).snapshots[2]["scoreboard"]["me_life"] == -7


def test_a_seed_above_the_32_bit_range_loads(game_file):
    assert load_game(game_file("win")).metadata["seed"] == 187402938471


def test_the_seats_must_be_seat_1_then_seat_2(game_lines, write_game):
    lines = game_lines("win")
    lines[0]["seats"].reverse()
    with pytest.raises(GameRecordError, match="seats"):
        load_game(write_game(lines))


def test_exactly_two_seats_are_required(game_lines, write_game):
    lines = game_lines("win")
    lines[0]["seats"].pop()
    with pytest.raises(GameRecordError, match="seats"):
        load_game(write_game(lines))


def test_a_snapshot_after_the_final_turn_is_rejected(game_lines, write_game):
    lines = game_lines("win")
    lines[0]["final_turn"] = 2
    with pytest.raises(GameRecordError, match="turn"):
        load_game(write_game(lines))


def test_a_game_with_no_snapshots_is_rejected(game_lines, write_game):
    with pytest.raises(GameRecordError, match="no snapshots"):
        load_game(write_game(game_lines("win")[:1]))


@pytest.mark.parametrize("change", [
    {"enabled": False},
    {"hits": 1},
    {"hits": 0},
    {"signatures": []},
    {"signatures": ["same", "same"]},
])
def test_a_tripwire_summary_that_contradicts_itself_is_rejected(game_lines, write_game, change):
    """An off tripwire reporting hits, or hits with no signature, can't be believed."""
    lines = game_lines("win")
    lines[0]["tripwire"].update(change)
    with pytest.raises(GameRecordError, match="tripwire"):
        load_game(write_game(lines))


def test_a_tripwire_that_was_off_is_not_a_clean_zero(game_file):
    summary = load_game(game_file("draw_clock")).metadata["tripwire"]
    assert summary == {"enabled": False, "hits": 0, "signatures": []}


def test_the_game_id_tells_two_games_from_one_worker_apart(game_file):
    """All games in a JVM share its seed, so the seed alone can't name a game."""
    first = load_game(game_file("win"))
    second = load_game(game_file("draw_clock"))
    assert first.metadata["seed"] == second.metadata["seed"]
    assert first.game_id == "20261105-230000-seed7-187402938471-1"
    assert second.game_id == "20261105-230000-seed7-187402938471-2"


def test_a_truncated_file_is_rejected(game_file):
    """A worker killed at the nightly stop can leave one of these."""
    path = game_file("win")
    path.write_bytes(path.read_bytes()[:-20])
    with pytest.raises(GameRecordError):
        load_game(path)


@pytest.mark.parametrize("content", [
    b"this is not gzip",
    b"",
    gzip.compress(b""),
    gzip.compress(b"{not json\n"),
    gzip.compress(b"[1, 2]\n"),
    gzip.compress(b"\xff\xfe{}\n"),
])
def test_a_file_that_is_not_a_game_is_rejected(tmp_path, content):
    path = tmp_path / "bad.jsonl.gz"
    path.write_bytes(content)
    with pytest.raises(GameRecordError):
        load_game(path)


def test_a_bad_snapshot_line_is_reported_by_line_and_field(game_lines, write_game):
    lines = game_lines("win")
    lines[2]["zones"]["me_hand"][0]["tapped"] = "no"
    with pytest.raises(GameRecordError, match=r"line 3\.zones\.me_hand\[0\]\.tapped"):
        load_game(write_game(lines))


def test_a_missing_file_is_a_game_record_error(tmp_path):
    with pytest.raises(GameRecordError):
        load_game(tmp_path / "gone.jsonl.gz")
