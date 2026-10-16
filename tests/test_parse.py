"""parse_card: script text in, plain-data card out."""

import json
from pathlib import Path

import pytest

from cardscript import Card, ParseError, parse_card


def test_lightning_bolt_is_one_face_with_one_spell_ability(script):
    card = parse_card(script("lightning_bolt"))
    assert len(card.faces) == 1
    face = card.faces[0]
    assert face.name == "Lightning Bolt"
    assert len(face.abilities) == 1
    bolt = face.abilities[0]
    assert bolt.kind == "spell"
    assert bolt.tokens == ["Effect=DealDamage", "ValidTgts=Any"]
    assert [(n.key, n.value) for n in bolt.numbers] == [("NumDmg", 3)]
    assert bolt.subabilities == []


def test_single_red_pip_is_generic_zero_and_one_red_pip(script):
    cost = parse_card(script("lightning_bolt")).faces[0].mana_cost
    assert cost.generic == 0
    assert cost.pips == {"R": 1}
    assert cost.special == []


def test_generic_and_colored_symbols_split(script):
    cost = parse_card(script("grizzly_bears")).faces[0].mana_cost
    assert (cost.generic, cost.pips) == (1, {"G": 1})


def test_types_line_splits_into_type_tokens(script):
    assert parse_card(script("lightning_bolt")).faces[0].types == ["Instant"]
    assert parse_card(script("grizzly_bears")).faces[0].types == ["Creature", "Bear"]


def test_pt_is_printed_power_and_toughness(script):
    bears = parse_card(script("grizzly_bears")).faces[0]
    assert (bears.power, bears.toughness) == (2, 2)


def test_a_card_without_pt_has_none(script):
    bolt = parse_card(script("lightning_bolt")).faces[0]
    assert (bolt.power, bolt.toughness) == (None, None)


def test_oracle_and_spell_description_leave_no_trace(script):
    face = parse_card(script("lightning_bolt")).faces[0]
    assert face.warnings == []
    assert not any("damage to any target" in t for t in face.abilities[0].tokens)


def test_parsed_card_round_trips_through_json(script):
    for name in ("lightning_bolt", "grizzly_bears"):
        card = parse_card(script(name))
        assert Card.from_json(card.to_json()) == card
        assert json.loads(card.to_json()) == card.to_dict()


def test_star_pt_is_a_var_marker_not_an_error():
    face = parse_card("Name:Tarmogoyf\nPT:*/1+*\n").faces[0]
    assert (face.power, face.toughness) == ("<var>", "<var>")


def test_unknown_line_key_is_a_warning_not_an_error(script):
    face = parse_card(script("lightning_bolt") + "Frobnicate:yes\n").faces[0]
    assert face.warnings == ["unknown key: Frobnicate"]
    assert len(face.abilities) == 1


def test_malformed_ability_segment_raises_parse_error_naming_the_line():
    line = "A:SP$ DealDamage | NumDmg 3"
    with pytest.raises(ParseError) as caught:
        parse_card(f"Name:Broken\n{line}\n")
    assert caught.value.line == line
    assert caught.value.cause


def test_goblin_token_script_parses_with_its_colors(script):
    face = parse_card(script("r_1_1_goblin")).faces[0]
    assert face.name == "Goblin Token"
    assert face.colors == ["red"]
    assert face.types == ["Creature", "Goblin"]
    assert (face.power, face.toughness) == (1, 1)
    assert face.warnings == []


def test_colors_line_splits_on_commas_in_file_order():
    assert parse_card("Name:T\nColors:green, blue\n").faces[0].colors == ["green", "blue"]


def test_a_card_without_a_colors_line_has_no_colors(script):
    assert parse_card(script("lightning_bolt")).faces[0].colors == []


def test_costs_keywords_and_colors_round_trip_through_json(script):
    for name in ("grim_lavamancer", "r_1_1_goblin", "silver_knight", "durkwood_baloth"):
        card = parse_card(script(name))
        assert Card.from_json(card.to_json()) == card


ALL_FIXTURES = sorted(p.stem for p in (Path(__file__).parent / "fixtures").glob("*.txt"))


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_parsing_twice_gives_identical_output(script, name):
    text = script(name)
    assert parse_card(text).to_json() == parse_card(text).to_json()


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_fixture_round_trips_through_json(script, name):
    card = parse_card(script(name))
    assert Card.from_json(card.to_json()) == card
