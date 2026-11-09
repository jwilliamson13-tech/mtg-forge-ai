"""Converting a folder of game files to Parquet, from the outside: files in, tables and counts out.

Tables are read back through pyarrow as a training job would read them, so each test
sees the partition layout too.
"""

import pyarrow.dataset as ds
import pytest

from gamerecord import convert
from gamerecord.__main__ import main
from gamerecord.convert import convert_folder

BATCH = "20261105-230000-seed7"
GAME_ID = f"{BATCH}-187402938471-1"
EVERY_FIXTURE = ("win", "draw_clock", "draw_turn_cap", "exception", "wrong_version", "missing_field")


def read(root):
    return ds.dataset(root, format="parquet", partitioning="hive").to_table()


def rows(root):
    return sorted(read(root).to_pylist(), key=lambda row: row["snapshot_index"])


@pytest.fixture
def night(tmp_path, game_file):
    folder = tmp_path / "night"
    for name in EVERY_FIXTURE:
        game_file(name, folder)
    return folder


@pytest.fixture
def out(tmp_path):
    return tmp_path / "parquet"


def test_a_night_reports_kept_and_dropped_counts_by_end_reason(night, out):
    report = convert_folder(night, out)

    assert report.kept == {"win": 1}
    assert report.dropped == {"draw_clock": 1, "draw_turn_cap": 1, "exception": 1}


def test_a_file_that_fails_validation_costs_one_game_and_is_named(night, out):
    report = convert_folder(night, out)

    assert sorted(path.name for path, _ in report.rejected) == ["missing_field.jsonl.gz", "wrong_version.jsonl.gz"]
    assert "schema_version" in dict((path.name, why) for path, why in report.rejected)["wrong_version.jsonl.gz"]
    assert report.kept == {"win": 1}


def test_the_snapshot_table_has_one_row_per_kept_snapshot(night, out):
    report = convert_folder(night, out)

    table = rows(out / "snapshots")
    assert report.snapshot_rows == 4
    assert [row["snapshot_index"] for row in table] == [0, 1, 2, 3]
    assert {row["game_id"] for row in table} == {GAME_ID}
    assert {row["batch_id"] for row in table} == {BATCH}
    assert [row["side"] for row in table] == [1, 2, 1, 2]
    assert table[2]["me_life"] == 14
    assert table[2]["opp_life"] == 3
    assert table[2]["opp_poison"] == 1
    assert table[2]["opp_hand"] == 2
    assert table[3]["me_on_play"] is False
    assert table[2]["turn"] == 3


def test_the_label_comes_from_the_winner_and_the_side(night, out):
    convert_folder(night, out)
    assert [row["won"] for row in rows(out / "snapshots")] == [True, False, True, False]


def test_a_seat_2_win_flips_the_labels(tmp_path, game_lines, write_game, out):
    lines = game_lines("win")
    lines[0]["winner"] = 2
    write_game(lines, folder=tmp_path / "night")

    convert_folder(tmp_path / "night", out)

    assert [row["won"] for row in rows(out / "snapshots")] == [False, True, False, True]


def test_each_snapshot_row_carries_what_filters_and_splits_need(night, out):
    convert_folder(night, out)

    row = rows(out / "snapshots")[0]
    assert (row["seed"], row["game_number"], row["final_turn"]) == (187402938471, 1, 3)
    assert (row["seat1_deck"], row["seat2_deck"]) == ("Mono Red Burn", "Legacy Merfolk")
    assert (row["seat1_ai_profile"], row["seat2_ai_profile"]) == ("Default", "Reckless")
    assert (row["seat1_sim_mode"], row["seat2_sim_mode"]) == ("heuristic", "full")
    assert (row["seat1_search_budget"], row["seat2_search_budget"]) == (0, 100)


def test_the_card_table_has_one_row_per_card_per_snapshot(night, out):
    report = convert_folder(night, out)

    table = rows(out / "cards")
    assert report.card_rows == 29
    per_snapshot = [sum(row["snapshot_index"] == index for row in table) for index in range(4)]
    assert per_snapshot == [2, 2, 13, 12]
    assert {row["game_id"] for row in table} == {GAME_ID}


def test_a_card_row_keeps_its_zone_and_live_state(night, out):
    convert_folder(night, out)

    table = rows(out / "cards")
    adept = next(row for row in table if row["name"] == "Silvergill Adept" and row["snapshot_index"] == 2)
    assert adept["zone"] == "battlefield"
    assert (adept["id"], adept["face"], adept["controller"]) == (32, "Original", 2)
    assert (adept["power"], adept["toughness"], adept["tapped"]) == (4, 3, True)
    assert adept["counters"] == [("P1P1", 2)]
    assert (adept["summoning_sick"], adept["face_down"]) == (False, False)

    token = next(row for row in table if row["name"] == "Merfolk Token" and row["snapshot_index"] == 2)
    assert token["summoning_sick"] is True
    bonesplitter = next(row for row in table if row["name"] == "Bonesplitter" and row["snapshot_index"] == 2)
    assert (bonesplitter["attached_to"], bonesplitter["attached_to_seat"]) == (13, None)
    assert (bonesplitter["power"], bonesplitter["toughness"]) == (None, None)
    curse = next(row for row in table if row["name"] == "Curse of Stalked Prey" and row["snapshot_index"] == 2)
    assert (curse["attached_to"], curse["attached_to_seat"]) == (None, 2)


def test_a_card_row_keeps_combat_and_damage(night, out):
    convert_folder(night, out)

    table = rows(out / "cards")
    attacker = next(row for row in table if row["id"] == 13 and row["snapshot_index"] == 2 and row["zone"] == "battlefield")
    blocker = next(row for row in table if row["id"] == 32 and row["snapshot_index"] == 2 and row["zone"] == "battlefield")
    assert (attacker["attacking_seat"], attacker["attacking_card"], attacker["blocked_by"]) == (2, None, [32])
    assert (blocker["blocking"], blocker["damage"]) == ([13], 1)


def test_the_stack_table_has_one_row_per_item_with_its_position(night, out):
    report = convert_folder(night, out)

    table = sorted(read(out / "stack").to_pylist(), key=lambda row: (row["snapshot_index"], row["position"]))
    assert report.stack_rows == 4
    assert [(row["snapshot_index"], row["position"], row["kind"]) for row in table] == [
        (2, 0, "ability"), (2, 1, "spell"), (3, 0, "ability"), (3, 1, "spell")]
    assert table[0]["game_id"] == GAME_ID
    assert table[0]["name"] == "Grim Lavamancer"
    assert table[0]["targets"] == [{"card": None, "seat": 2}, {"card": 32, "seat": None}]
    assert (table[1]["name"], table[1]["face"], table[1]["face_down"]) == (None, None, True)
    assert (table[3]["name"], table[3]["controller"]) == ("Willbender", 2)


def test_every_zone_reaches_the_card_table_under_its_own_name(night, out):
    convert_folder(night, out)

    seen = {(row["zone"], row["name"]) for row in rows(out / "cards") if row["snapshot_index"] == 2}
    assert {("me_hand", "Lightning Bolt"), ("me_graveyard", "Lim-Dûl's Vault"), ("opp_graveyard", "Force Spike"),
            ("opp_exile", "Fire // Ice"), ("opp_revealed", "Force of Will")} <= seen
    swapped = {(row["zone"], row["name"]) for row in rows(out / "cards") if row["snapshot_index"] == 3}
    assert {("me_exile", "Fire // Ice"), ("me_graveyard", "Force Spike")} <= swapped


def test_a_hidden_card_stays_nameless_in_one_side_only(night, out):
    convert_folder(night, out)

    down = [row for row in rows(out / "cards") if row["id"] == 35]
    assert [(row["snapshot_index"], row["name"], row["face"]) for row in down] == [
        (2, None, None), (3, "Willbender", "Original")]
    assert all(row["face_down"] for row in down)


def test_each_batch_gets_its_own_partition_and_they_read_as_one(tmp_path, game_lines, write_game, out):
    """Batch two has no attachment or counter at all. A column inferred from its rows
    alone would come out with no type, and the two files would not read together."""
    folder = tmp_path / "night"
    write_game(game_lines("win"), "one", folder)
    plain = game_lines("win")[:3]
    plain[0].update(batch_id="batch-two", final_turn=1)
    write_game(plain, "two", folder)

    convert_folder(folder, out)

    assert sorted(path.name for path in (out / "snapshots").iterdir()) == [f"batch_id={BATCH}", "batch_id=batch-two"]
    assert sorted(path.name for path in (out / "cards").iterdir()) == [f"batch_id={BATCH}", "batch_id=batch-two"]
    assert read(out / "snapshots").num_rows == 6
    assert read(out / "cards").num_rows == 33


def test_converting_a_folder_twice_replaces_rather_than_doubles(night, out):
    convert_folder(night, out)
    convert_folder(night, out)

    assert read(out / "snapshots").num_rows == 4
    assert read(out / "cards").num_rows == 29
    assert [path.name for path in (out / "cards" / f"batch_id={BATCH}").iterdir()] == ["part-0.parquet"]


def test_a_crash_partway_leaves_the_last_good_conversion_alone(tmp_path, game_lines, write_game, out, monkeypatch):
    """The good run holds two games and the crash stops after one, so the row count
    shows which file survived."""
    folder = tmp_path / "night"
    second = game_lines("win")
    second[0]["game_number"] = 9
    write_game(game_lines("win"), "first", folder)
    write_game(second, "second", folder)
    convert_folder(folder, out)

    real_load = convert.load_game

    def crash_on_second(path):
        if path.name == "second.jsonl.gz":
            raise RuntimeError("disk full")
        return real_load(path)

    monkeypatch.setattr(convert, "load_game", crash_on_second)
    with pytest.raises(RuntimeError):
        convert_folder(folder, out)

    assert read(out / "snapshots").num_rows == 8
    assert read(out / "cards").num_rows == 58
    partition = out / "cards" / f"batch_id={BATCH}"
    assert [path.name for path in partition.iterdir()] == ["part-0.parquet"]


def test_a_folder_of_draws_writes_no_tables_and_still_reports(tmp_path, game_file, out):
    folder = tmp_path / "night"
    game_file("draw_clock", folder)

    report = convert_folder(folder, out)

    assert report.kept == {}
    assert report.dropped == {"draw_clock": 1}
    assert report.snapshot_rows == 0
    assert not (out / "snapshots").exists()


def test_game_files_in_subfolders_are_found(tmp_path, game_file, out):
    game_file("win", tmp_path / "night" / "worker-0")
    assert convert_folder(tmp_path / "night", out).kept == {"win": 1}


def test_a_file_that_is_not_a_game_file_is_ignored(tmp_path, game_file, out):
    folder = tmp_path / "night"
    game_file("win", folder)
    (folder / "game.jsonl.gz.tmp").write_bytes(b"half written")
    (folder / "report.txt").write_text("not a game", encoding="utf-8")

    report = convert_folder(folder, out)

    assert report.kept == {"win": 1}
    assert report.rejected == []


def test_the_command_prints_the_counts(night, out, capsys):
    code = main(["convert", str(night), str(out)])

    printed = capsys.readouterr().out
    assert code == 1  # two files were rejected
    assert "kept 1" in printed
    assert "win: 1" in printed
    assert "dropped 3" in printed
    assert "draw_clock: 1" in printed
    assert "draw_turn_cap: 1" in printed
    assert "exception: 1" in printed
    assert "rejected 2" in printed
    assert "wrong_version.jsonl.gz" in printed
    assert "snapshot rows 4" in printed
    assert "card rows 29" in printed
    assert "stack rows 4" in printed


def test_the_command_succeeds_when_nothing_is_rejected(tmp_path, game_file, out, capsys):
    folder = tmp_path / "night"
    game_file("win", folder)
    game_file("draw_clock", folder)

    assert main(["convert", str(folder), str(out)]) == 0


def test_the_command_refuses_a_folder_with_no_game_files(tmp_path, out, capsys):
    """A mistyped path must not look like a quiet night."""
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit) as stopped:
        main(["convert", str(tmp_path / "empty"), str(out)])
    assert stopped.value.code == 2
    assert "no game files" in capsys.readouterr().err


def test_tripwire_games_stay_in_by_default(tmp_path, game_file, out):
    folder = tmp_path / "night"
    game_file("win", folder)  # its tripwire caught 3 hits

    assert convert_folder(folder, out).kept == {"win": 1}


def test_dropping_tripwire_games_names_each_with_its_signatures(night, out):
    report = convert_folder(night, out, drop_tripwire_hits=True)

    assert report.kept == {}
    assert report.dropped == {"tripwire": 1, "draw_clock": 1, "draw_turn_cap": 1, "exception": 1}
    assert report.tripwire_games == [(GAME_ID, 3, ["forge.ai.ComputerUtil.hasAFogEffect:2971",
                                                   "forge.ai.ability.ClashAi.checkApi:72"])]
    assert not (out / "snapshots").exists()


def test_the_command_drops_tripwire_games_on_request(tmp_path, game_file, out, capsys):
    folder = tmp_path / "night"
    game_file("win", folder)
    game_file("draw_clock", folder)

    assert main(["convert", str(folder), str(out), "--drop-tripwire-hits"]) == 0
    printed = capsys.readouterr().out
    assert "kept 0" in printed
    assert "tripwire: 1" in printed
    assert f"tripwire game {GAME_ID}: 3 hits" in printed
    assert "    forge.ai.ability.ClashAi.checkApi:72" in printed


def test_listed_files_are_left_out_and_named(tmp_path, game_file, out):
    folder = tmp_path / "night"
    kept = game_file("win", folder)
    other = game_file("draw_clock", folder)

    report = convert_folder(folder, out, drop_files={kept.name})

    assert report.kept == {} and report.dropped == {"listed": 1, "draw_clock": 1}
    assert report.listed_games == [kept.name] and other.name not in report.listed_games


def test_a_listed_file_that_is_not_there_changes_nothing(tmp_path, game_file, out):
    folder = tmp_path / "night"
    game_file("win", folder)

    report = convert_folder(folder, out, drop_files={"no-such-game.jsonl.gz"})

    assert report.kept == {"win": 1} and report.dropped == {} and report.listed_games == []


def test_a_tripwire_game_that_is_also_listed_counts_as_a_tripwire_game(night, out):
    names = {path.name for path in night.glob("*.jsonl.gz")}

    report = convert_folder(night, out, drop_tripwire_hits=True, drop_files=names)

    assert report.dropped["tripwire"] == 1 and report.dropped["listed"] == 2  # the two draws; the exception game and the rejected files are not counted
    assert len(report.tripwire_games) == 1


def test_the_command_takes_a_drop_list_and_names_each_file(tmp_path, game_file, out, capsys):
    folder = tmp_path / "night"
    path = game_file("win", folder)
    game_file("draw_clock", folder)
    listing = tmp_path / "drop.txt"
    listing.write_text(f"{path.name}\n\n", encoding="utf-8")

    assert main(["convert", str(folder), str(out), "--drop-list", str(listing)]) == 0
    printed = capsys.readouterr().out
    assert "kept 0" in printed and "  listed: 1" in printed
    assert f"  listed game {path.name}" in printed


def test_a_drop_list_that_cannot_be_read_is_an_error(tmp_path, game_file, out):
    folder = tmp_path / "night"
    game_file("win", folder)

    with pytest.raises(SystemExit):
        main(["convert", str(folder), str(out), "--drop-list", str(tmp_path / "missing.txt")])
