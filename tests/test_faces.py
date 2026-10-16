"""Multi-face cards: ALTERNATE and SPECIALIZE separators."""

from cardscript import parse_card


def test_fire_ice_is_two_faces_with_their_own_names_costs_and_abilities(script):
    fire, ice = parse_card(script("fire_ice")).faces
    assert (fire.name, ice.name) == ("Fire", "Ice")
    assert (fire.mana_cost.generic, fire.mana_cost.pips) == (1, {"R": 1})
    assert (ice.mana_cost.generic, ice.mana_cost.pips) == (1, {"U": 1})
    assert fire.types == ice.types == ["Instant"]
    (damage,) = fire.abilities
    assert damage.tokens == ["Effect=DealDamage", "ValidTgts=Any"]
    assert [(n.key, n.value) for n in damage.numbers] == [
        ("NumDmg", 2), ("TargetMin", 1), ("TargetMax", 2), ("DividedAsYouChoose", 2)]
    (tap,) = ice.abilities
    assert tap.tokens == ["Effect=Tap", "ValidTgts=Permanent"]
    assert [s.tokens for s in tap.subabilities] == [["Effect=Draw"]]


def test_fire_ice_has_no_warnings(script):
    assert [f.warnings for f in parse_card(script("fire_ice")).faces] == [[], []]


def test_svars_are_scoped_to_their_face():
    text = (
        "Name:A\nA:SP$ Draw | SubAbility$ DBX\nSVar:DBX:DB$ Mill\n"
        "\nALTERNATE\n\n"
        "Name:B\nA:SP$ Draw | SubAbility$ DBX\nSVar:DBX:DB$ GainLife\n"
    )
    a, b = parse_card(text).faces
    assert a.abilities[0].subabilities[0].tokens == ["Effect=Mill"]
    assert b.abilities[0].subabilities[0].tokens == ["Effect=GainLife"]


def test_an_svar_from_the_other_face_does_not_resolve():
    text = "Name:A\nSVar:DBX:DB$ Mill\n\nALTERNATE\n\nName:B\nA:SP$ Draw | SubAbility$ DBX\n"
    _, b = parse_card(text).faces
    assert b.abilities[0].tokens == ["Effect=Draw", "SubAbility=DBX"]


def test_specialize_separators_give_one_face_each_in_file_order():
    text = "Name:Base\n"
    for color in ("WHITE", "BLUE", "BLACK", "RED", "GREEN"):
        text += f"\nSPECIALIZE:{color}\n\nName:Base {color.title()}\n"
    faces = parse_card(text).faces
    assert [f.name for f in faces] == [
        "Base", "Base White", "Base Blue", "Base Black", "Base Red", "Base Green"]
    assert all(f.warnings == [] for f in faces)


def test_alternate_mode_line_is_recognized():
    assert parse_card("Name:A\nAlternateMode:Split\n").faces[0].warnings == []
