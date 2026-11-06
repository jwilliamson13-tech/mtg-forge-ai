import argparse
import json

import pytest

from deckpool import manifest as mf
from deckpool.__main__ import add_deck
from deckpool.convert import DeckError, NameSet, UnresolvedNames
from simrunner.__main__ import _deck_path

NAMES = NameSet(["Lightning Bolt", "Mountain", "Rest in Peace"])
LIST = "4 Lightning Bolt\n56 Mountain\n"


def deck(i, archetype, split="train"):
    return mf.Deck(i, f"decks/forge/{i}.dck", "https://example.test/" + i, archetype, split)


def test_every_list_of_an_archetype_shares_a_split():
    m = mf.Manifest()
    m.add_deck(deck("burn-1", "Burn"))
    with pytest.raises(mf.ManifestError, match="share one split"):
        m.add_deck(deck("burn-2", "Burn", "held_out"))
    m.add_deck(deck("burn-2", "Burn"))
    assert len(m.decks) == 2


def test_two_spellings_of_an_archetype_are_one_archetype():
    m = mf.Manifest([deck("dt-1", "Death & Taxes")])
    for i, spelling in enumerate(["death & taxes", "Death and Taxes", "Death & Taxes "]):
        with pytest.raises(mf.ManifestError, match="share one split"):
            m.add_deck(deck(f"dt-{i + 2}", spelling, "held_out"))


def test_a_hand_edit_that_splits_an_archetype_is_refused_on_load(tmp_path):
    path = tmp_path / "manifest.json"
    bad = {"version": 1, "tracked_matchups": [None] * 4,
           "decks": [deck("a-1", "A").to_dict(), deck("a-2", "A", "held_out").to_dict()]}
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(mf.ManifestError, match="both splits"):
        mf.load(path)


def test_duplicate_ids_and_bad_splits_are_refused():
    m = mf.Manifest([deck("a-1", "A")])
    with pytest.raises(mf.ManifestError):
        m.add_deck(deck("a-1", "B"))
    with pytest.raises(mf.ManifestError):
        m.add_deck(deck("b-1", "B", "test"))


@pytest.mark.parametrize("bad_id", ["../../evil", "a/b", "a\b", "..", "", "a.b"])
def test_a_deck_id_that_is_not_a_plain_name_is_refused(bad_id):
    with pytest.raises(mf.ManifestError, match="bad deck id"):
        mf.Manifest().add_deck(deck(bad_id, "A"))


def test_tracked_slots_default_empty_and_take_only_held_out_pairs(tmp_path):
    m = mf.Manifest([deck("a-1", "A", "held_out"), deck("b-1", "B", "held_out"), deck("c-1", "C")])
    assert m.tracked_matchups == [None] * 4
    m.set_tracked(0, ["a-1", "b-1"])
    with pytest.raises(mf.ManifestError, match="held-out"):
        m.set_tracked(1, ["a-1", "c-1"])
    mf.save(m, tmp_path / "m.json")
    assert mf.load(tmp_path / "m.json").tracked_matchups == [["a-1", "b-1"], None, None, None]


def test_held_out_choice_repeats_and_holds_out_whole_archetypes():
    names = [f"Arch {i}" for i in range(40)] * 3
    a, b = mf.choose_held_out(names, 7), mf.choose_held_out(reversed(names), 7)
    assert a == b and len(a) == 10 == len(set(a))
    assert a != mf.choose_held_out(names, 8)
    with pytest.raises(mf.ManifestError):
        mf.choose_held_out(["A", "B"], 1)


def test_add_deck_round_trips(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text(LIST + "\n2 Rest in Peace\n", encoding="utf-8")
    added = add_deck(src, "mine-1", "Mine", "held_out", "user", NAMES, root=tmp_path)
    m = mf.load(tmp_path / "decks" / "manifest.json")
    assert m.get("mine-1") == added
    assert (tmp_path / added.file).read_text(encoding="utf-8").endswith("[Sideboard]\n2 Rest in Peace\n")
    assert (tmp_path / "decks" / "raw" / "mine-1.txt").read_text(encoding="utf-8").startswith("4 Lightning Bolt")
    assert mf.deck_path(m, "mine-1", tmp_path) == tmp_path / added.file


def test_add_deck_with_a_bad_name_or_split_writes_nothing(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text(LIST + "1 Black Lotos\n", encoding="utf-8")
    with pytest.raises(UnresolvedNames):
        add_deck(src, "x-1", "X", "train", "user", NAMES, root=tmp_path)
    assert not (tmp_path / "decks").exists()
    src.write_text(LIST, encoding="utf-8")
    add_deck(src, "x-1", "X", "train", "user", NAMES, root=tmp_path)
    with pytest.raises(mf.ManifestError):
        add_deck(src, "x-2", "X", "held_out", "user", NAMES, root=tmp_path)
    assert not (tmp_path / "decks" / "forge" / "x-2.dck").exists()


def test_add_deck_refuses_a_main_under_sixty(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("4 Lightning Bolt\n55 Mountain\n", encoding="utf-8")
    with pytest.raises(DeckError, match="59 cards"):
        add_deck(src, "x-1", "X", "train", "user", NAMES, root=tmp_path)


def test_the_sim_runner_takes_a_deck_id_or_a_path(tmp_path, monkeypatch):
    src = tmp_path / "in.txt"
    src.write_text(LIST, encoding="utf-8")
    add_deck(src, "x-1", "X", "train", "user", NAMES, root=tmp_path)
    parser = argparse.ArgumentParser()
    manifest = tmp_path / "decks" / "manifest.json"
    assert _deck_path("x-1", manifest, parser) == tmp_path / "decks" / "forge" / "x-1.dck"
    assert _deck_path(str(src), manifest, parser) == src
    with pytest.raises(SystemExit):
        _deck_path("nope", manifest, parser)
    monkeypatch.chdir(tmp_path / "decks")
    assert _deck_path("x-1", "manifest.json", parser).samefile(tmp_path / "decks" / "forge" / "x-1.dck")


def test_a_pull_becomes_a_manifest_that_holds_out_ten_whole_archetypes():
    record = {"decks": [{"id": f"a{n}-{i}", "archetype": f"A{n}", "source": f"https://x/{n}{i}"}
                        for n in range(14) for i in range(1, 4)]}
    manifest = mf.build_from_pull(record, seed=3, left_out=["a0-2"])
    assert len(manifest.decks) == 41 and manifest.tracked_matchups == [None] * 4
    held = {d.archetype for d in manifest.decks if d.split == "held_out"}
    assert len(held) == 10 and held == set(mf.choose_held_out({f"A{n}" for n in range(14)}, 3))
    assert all(d.split == "held_out" for d in manifest.decks if d.archetype in held)
    with pytest.raises(mf.ManifestError, match="a9-9"):
        mf.build_from_pull(record, seed=3, left_out=["a9-9"])
