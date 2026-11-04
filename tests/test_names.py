"""Card names never reach a token: each becomes `<name>` under its key."""

from cardscript import parse_card


def walk(abilities):
    for ability in abilities:
        yield ability
        yield from walk(ability.subabilities)


def tokens(card_text, face=0):
    return [t for a in walk(parse_card(card_text).faces[face].abilities) for t in a.tokens]


def under(card_text, key, face=0):
    return [t for t in tokens(card_text, face) if t.split("=")[0] == key]


def tokens_of(line):
    (ability,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    return ability.tokens


# --- `named` filter pieces ----------------------------------------------------


def test_a_named_filter_piece_keeps_its_prefix_and_masks_the_name():
    assert tokens_of("A:SP$ ChangeZone | ChangeType$ Card.namedFestering Newt") == [
        "Effect=ChangeZone", "ChangeType=Card", "ChangeType=named<name>"]


def test_a_negated_named_piece_keeps_the_negation():
    assert tokens_of("A:SP$ Destroy | ValidTgts$ Creature.!namedClever Conjurer+YouCtrl") == [
        "Effect=Destroy", "ValidTgts=Creature", "ValidTgts=!named<name>", "ValidTgts=YouCtrl"]


def test_a_name_with_its_own_comma_is_masked_whole(script):
    # Grandeur: `Discard<1/Card.namedBaru, Fist of Krosa>`.
    assert under(script("baru_fist_of_krosa"), "Cost.Discard") == [
        "Cost.Discard=Card", "Cost.Discard=named<name>"]


def test_a_named_piece_in_a_trigger_condition_is_masked(script):
    assert under(script("graf_rats_chittering_host"), "IsPresent2") == [
        "IsPresent2=Creature", "IsPresent2=YouCtrl", "IsPresent2=YouOwn", "IsPresent2=named<name>"]


# --- keys whose value is a name -------------------------------------------------


def test_a_new_name_is_masked(script):
    assert under(script("absorbing_man"), "NewName") == ["NewName=<name>"]


def test_a_spellbook_masks_each_card_in_it(script):
    spellbook = under(script("adaptive_armorer"), "Spellbook")
    assert len(spellbook) == 15
    assert set(spellbook) == {"Spellbook=<name>"}


def test_meld_masks_the_result_and_both_halves(script):
    meld_tokens = under(script("graf_rats_chittering_host"), "Name") + \
        under(script("graf_rats_chittering_host"), "Primary") + \
        under(script("graf_rats_chittering_host"), "Secondary")
    assert meld_tokens == ["Name=<name>", "Primary=<name>", "Secondary=<name>"]


def test_secondary_outside_meld_is_a_plain_flag(script):
    assert "Secondary=True" in tokens(script("black_lotus_lounge"))


def test_a_defined_name_naming_a_card_is_masked(script):
    # Two triggers inline the same SVar, so the token appears twice.
    assert under(script("black_lotus_lounge"), "DefinedName") == ["DefinedName=<name>"] * 2


def test_a_defined_name_naming_a_reference_is_kept():
    assert tokens_of("A:SP$ CopyPermanent | DefinedName$ Targeted.!token") == [
        "Effect=CopyPermanent", "DefinedName=Targeted", "DefinedName=!token"]
    assert tokens_of("A:SP$ Effect | Name$ ChosenName") == ["Effect=Effect", "Name=ChosenName"]


def test_conjure_choices_are_names(script):
    assert under(script("izzet_polarizer"), "Choices") == ["Choices=<name>", "Choices=<name>"]


def test_a_names_list_is_masked_under_any_key(script):
    assert under(script("tibalt_the_chaotic"), "AnySupportedCard") == ["AnySupportedCard=<name>"] * 9
    assert tokens_of("A:AB$ Play | AnySupportedCard$ Instant") == ["Effect=Play", "AnySupportedCard=Instant"]


def test_a_dungeon_room_name_is_masked():
    assert tokens_of("A:SP$ Discard | RoomName$ Oubliette | NumCards$ 1") == ["Effect=Discard", "RoomName=<name>"]


def test_partner_with_masks_both_names(script):
    partner = parse_card(script("pir_imaginative_rascal")).faces[0].abilities[0]
    assert partner.tokens == ["Effect=Partner with", "Partner with=<name>", "Partner with.2=<name>"]


def test_the_faces_own_name_field_is_kept(script):
    assert parse_card(script("absorbing_man")).faces[0].name == "Absorbing Man"
