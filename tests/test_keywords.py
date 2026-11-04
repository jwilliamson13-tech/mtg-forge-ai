"""K: lines: one keyword ability each, parameters as numbers or sub-tokens."""

import pytest

from cardscript import ParseError, parse_card


def keywords(script_text):
    return [a for a in parse_card(script_text).faces[0].abilities if a.kind == "keyword"]


def shape(ability):
    return ability.tokens, [(n.key, n.value) for n in ability.numbers]


def only_keyword(script_text):
    (kw,) = keywords(script_text)
    return shape(kw)


def test_bare_keywords_are_just_their_effect_token(script):
    assert only_keyword(script("snapcaster_mage")) == (["Effect=Flash"], [])
    assert only_keyword(script("platinum_angel")) == (["Effect=Flying"], [])
    assert only_keyword(script("empty_the_warrens")) == (["Effect=Storm"], [])


def test_keyword_names_keep_their_spaces(script):
    assert only_keyword(script("boros_recruit")) == (["Effect=First Strike"], [])


def test_keywords_keep_file_order(script):
    tokens = [k.tokens[0] for k in keywords(script("hexbane_tortoise"))]
    assert tokens == ["Effect=Ward", "Effect=Enlist"]


def test_ward_2_is_a_number_under_the_keyword_name(script):
    ward = keywords(script("hexbane_tortoise"))[0]
    assert shape(ward) == (["Effect=Ward"], [("Ward", 2)])
    assert ward.subabilities == []


def test_ward_with_a_non_mana_cost_parses_the_cost(script):
    ward = keywords(script("owlin_shieldmage"))[1]
    assert shape(ward) == (["Effect=Ward", "Ward=PayLife"], [("Ward.PayLife", 3)])


def test_protection_from_red_is_protection_with_a_red_sub_token(script):
    first_strike, protection = keywords(script("silver_knight"))
    assert shape(first_strike) == (["Effect=First Strike"], [])
    assert shape(protection) == (["Effect=Protection", "Protection=red"], [])


def test_protection_colon_form_matches_the_from_form():
    assert only_keyword("Name:T\nK:Protection:Artifact:artifacts\n") == (
        ["Effect=Protection", "Protection=Artifact"], [])


def test_mana_cost_parameter_splits_into_generic_number_and_pip_tokens(script):
    assert only_keyword(script("slipstream_eel")) == (
        ["Effect=Cycling", "Cycling=U"], [("Cycling", 1)])


def test_second_parameter_gets_a_positional_key_so_numbers_never_merge(script):
    assert only_keyword(script("durkwood_baloth")) == (
        ["Effect=Suspend", "Suspend.2=G"], [("Suspend", 5)])


def test_textual_parameter_is_a_sub_token_and_its_description_is_dropped(script):
    assert only_keyword(script("dizzying_gaze")) == (
        ["Effect=Enchant", "Enchant=Creature", "Enchant=YouCtrl"], [])


def test_numeric_second_parameter_is_a_positional_number():
    assert only_keyword("Name:T\nK:TypeCycling:Basic:2\n") == (
        ["Effect=TypeCycling", "TypeCycling=Basic"], [("TypeCycling.2", 2)])


def test_segment_parameter_parses_like_an_ability_segment():
    text = "Name:T\nK:Crew:1:ActivationLimit$ 1\n"
    assert only_keyword(text) == (["Effect=Crew"], [("Crew", 1), ("ActivationLimit", 1)])


def test_empty_parameters_are_skipped_but_keep_positions():
    text = "Name:T\nK:Equip:2:::ReduceCost$ Y:This ability costs {2} less to activate\n"
    assert only_keyword(text) == (["Effect=Equip", "ReduceCost=<var>"], [("Equip", 2)])


def test_keyword_lines_leave_no_warning(script):
    assert parse_card(script("hexbane_tortoise")).faces[0].warnings == []


def test_empty_keyword_line_raises_parse_error():
    with pytest.raises(ParseError) as caught:
        parse_card("Name:T\nK:\n")
    assert caught.value.line == "K:"


# --- SVars a keyword names by convention ---------------------------------------


def test_gift_inlines_its_gift_ability_svar(script):
    gift = parse_card(script("bilbos_gambit")).faces[0].abilities[0]
    assert gift.tokens == ["Effect=Gift"]
    (promise,) = gift.subabilities
    assert promise.kind == "sub"
    assert promise.tokens[:3] == ["Effect=Token", "TokenScript=c_a_treasure_sac", "TokenOwner=Promised"]


def test_tribute_inlines_its_not_tribute_trigger_body(script):
    tribute = parse_card(script("nessian_demolok")).faces[0].abilities[0]
    assert [(n.key, n.value) for n in tribute.numbers] == [("Tribute", 3)]
    (destroy,) = tribute.subabilities
    assert destroy.tokens[0] == "Effect=Destroy"


def test_a_filter_parameter_with_a_space_is_kept_not_dropped_as_text(script):
    strength, pumped = keywords(script("anavolver"))[1:]
    assert strength.tokens[-3:] == ["ETBReplacement.5=Card", "ETBReplacement.5=Self",
                                    "ETBReplacement.5=kicked 1"]
    assert pumped.tokens[-1] == "ETBReplacement.5=kicked 2"


def test_an_english_parameter_is_still_dropped(script):
    enchant = parse_card("Name:A\nK:Enchant:Creature.YouCtrl:creature you control\n").faces[0].abilities[0]
    assert enchant.tokens == ["Effect=Enchant", "Enchant=Creature", "Enchant=YouCtrl"]


# --- keywords granted by a segment (`AddKeyword$`, `KW$`, ...) ---------------


def granted(ability, key):
    """`key=` tokens and `key.` numbers of one ability."""
    return ([t for t in ability.tokens if t.split("=")[0].split(".")[0] == key],
            [(n.key, n.value) for n in ability.numbers if n.key.split(".")[0] == key])


def test_a_granted_keyword_keeps_its_number_under_a_keyed_name(script):
    static = parse_card(script("leather_armor")).faces[0].abilities[0]
    assert granted(static, "AddKeyword") == (["AddKeyword=Ward"], [("AddKeyword.Ward", 1)])


def test_a_granted_protection_from_reads_like_the_k_line(script):
    static = parse_card(script("absolute_law")).faces[0].abilities[0]
    assert granted(static, "AddKeyword") == (["AddKeyword=Protection", "AddKeyword.Protection=red"], [])


def test_a_comma_list_of_granted_keywords_parses_each(script):
    (trigger,) = parse_card(script("gabriel_angelfire")).faces[0].abilities
    (pump,) = trigger.subabilities
    assert granted(pump, "KWChoice") == (
        ["KWChoice=Flying", "KWChoice=First Strike", "KWChoice=Trample", "KWChoice=Rampage"],
        [("KWChoice.Rampage", 3)])


def test_an_ampersand_list_of_granted_keywords_parses_each():
    (pump,) = parse_card("Name:T\nA:SP$ Pump | KW$ Landwalk:Forest & Haste\n").faces[0].abilities
    assert granted(pump, "KW") == (["KW=Landwalk", "KW.Landwalk=Forest", "KW=Haste"], [])


def test_a_granted_keyword_drops_its_english_like_the_k_line():
    line = "A:SP$ Animate | Keywords$ Enchant:Creature.IsRemembered:creature put onto the battlefield"
    (animate,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    assert granted(animate, "Keywords") == (
        ["Keywords=Enchant", "Keywords.Enchant=Creature", "Keywords.Enchant=IsRemembered"], [])


def test_a_granted_sentence_keyword_stays_one_token(script):
    (effect,) = parse_card(script("magmatic_chasm")).faces[0].abilities
    (static,) = effect.subabilities
    assert granted(static, "AddHiddenKeyword") == (["AddHiddenKeyword=CARDNAME can't block."], [])


def test_a_granted_keyword_with_a_segment_parameter_parses_it():
    (pump,) = parse_card("Name:T\nA:SP$ Pump | KW$ Encore:X:XAlternative$ Number$ConvertedManaCost\n").faces[0].abilities
    assert pump.tokens == ["Effect=Pump", "KW=Encore", "KW.Encore=<var>", "XAlternative=<var>"]


def test_a_comma_inside_a_granted_keyword_parameter_does_not_split_it():
    line = ("A:AB$ Pump | ValidTgts$ Permanent.YouCtrl | "
            "KW$ Protection:Spell.Instant,Spell.Sorcery:instant spells and from sorcery spells")
    (pump,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    assert granted(pump, "KW") == (
        ["KW=Protection", "KW.Protection=Spell", "KW.Protection=Instant",
         "KW.Protection=Spell", "KW.Protection=Sorcery"], [])


def test_a_plain_comma_list_of_granted_keywords_still_splits():
    (animate,) = parse_card("Name:T\nA:SP$ Animate | Keywords$ Vigilance,Reach\n").faces[0].abilities
    assert granted(animate, "Keywords") == (["Keywords=Vigilance", "Keywords=Reach"], [])


def test_a_granted_cost_starting_with_the_cards_own_mana_cost_parses_as_a_cost():
    line = "S:Mode$ Continuous | Affected$ Card.YouOwn | AddKeyword$ Escape:CardManaCost ExileFromGrave<3/Card.Other/other>"
    (static,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    assert granted(static, "AddKeyword") == (
        ["AddKeyword=Escape", "AddKeyword.Escape=CardManaCost", "AddKeyword.Escape=ExileFromGrave",
         "AddKeyword.Escape.ExileFromGrave=Card", "AddKeyword.Escape.ExileFromGrave=Other"],
        [("AddKeyword.Escape.ExileFromGrave", 3)])
