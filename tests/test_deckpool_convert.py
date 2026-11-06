import pytest

from deckpool.convert import DeckError, NameSet, UnresolvedNames, convert, normalize, parse_decklist

NAMES = NameSet(["Lightning Bolt", "Mountain", "Fire", "Ice", "Rest in Peace", "Jötun Grunt",
                 "Akroma, Angel of Wrath", "Smash to Smithereens"])

LIST = """4 Lightning Bolt
20 Mountain
2 Fire // Ice

3 Rest in Peace
2 smash to smithereens
"""


def test_a_list_converts_to_forge_text():
    assert convert(LIST, NAMES, "burn-1") == (
        "[metadata]\nName=burn-1\n[Main]\n4 Lightning Bolt\n20 Mountain\n2 Fire // Ice\n"
        "[Sideboard]\n3 Rest in Peace\n2 Smash to Smithereens\n")


def test_a_sideboard_header_starts_the_sideboard():
    deck = parse_decklist("4 Lightning Bolt\nSideboard\n2 Mountain\n")
    assert deck.main == [(4, "Lightning Bolt")] and deck.side == [(2, "Mountain")]


def test_sb_prefix_lines_are_sideboard():
    deck = parse_decklist("4 Lightning Bolt\nSB: 2 Mountain\n")
    assert deck.side == [(2, "Mountain")]


def test_no_sideboard_means_no_section():
    assert "[Sideboard]" not in convert("4 Lightning Bolt\n", NAMES, "x")


def test_matching_ignores_case_accents_and_punctuation():
    text = convert("1 JOTUN GRUNT\n1 Akroma Angel of Wrath\n", NAMES, "x")
    assert "1 Jötun Grunt" in text and "1 Akroma, Angel of Wrath" in text
    assert normalize("Akroma’s Vengeance") == normalize("akromas vengeance")


def test_a_set_after_the_name_is_dropped():
    assert "1 Mountain\n" in convert("1 Mountain (M21) 275\n", NAMES, "x")


def test_a_repeated_card_merges():
    assert "5 Mountain\n" in convert("2 Mountain\n3 Mountain\n", NAMES, "x")


def test_unresolved_names_fail_and_are_all_named():
    with pytest.raises(UnresolvedNames) as e:
        convert("4 Lightning Bolt\n1 Black Lotos\n\n2 Fyre\n", NAMES, "x")
    assert e.value.names == ["Black Lotos", "Fyre"]
    assert "Black Lotos" in str(e.value) and "Fyre" in str(e.value)


def test_a_split_card_with_one_bad_half_fails():
    with pytest.raises(UnresolvedNames):
        convert("1 Fire // Frost\n", NAMES, "x")


def test_an_unreadable_line_or_empty_list_fails():
    with pytest.raises(DeckError):
        parse_decklist("Lightning Bolt\n")
    with pytest.raises(DeckError):
        parse_decklist("\n\n")


def test_only_known_split_cards_keep_both_names_and_a_double_faced_card_takes_its_front():
    names = NameSet(["Fire", "Ice", "Delver of Secrets", "Insectile Aberration"], split_cards=["Fire // Ice"])
    text = convert("1 Fire / Ice\n1 Delver of Secrets // Insectile Aberration\n", names, "x")
    assert "1 Fire // Ice\n" in text and "1 Delver of Secrets\n" in text
