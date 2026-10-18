"""Identity fields beyond PT: printed loyalty and defense."""

from cardscript import Card, parse_card
from cardscript.vocab import face_numbers, face_tokens


def test_loyalty_line_is_printed_loyalty(script):
    teyo = parse_card(script("teyo_the_shieldmage")).faces[0]
    assert teyo.loyalty == 5
    assert teyo.defense is None
    assert teyo.warnings == []


def test_loyalty_x_is_a_var(script):
    nissa = parse_card(script("nissa_steward_of_elements")).faces[0]
    assert nissa.loyalty == "<var>"
    assert nissa.warnings == []


def test_defense_line_is_printed_defense_on_the_battle_face_only(script):
    battle, angel = parse_card(script("invasion_of_dominaria_serra_faithkeeper")).faces
    assert (battle.defense, battle.loyalty) == (5, None)
    assert (angel.defense, angel.power, angel.toughness) == (None, 4, 4)
    assert battle.warnings == angel.warnings == []


def test_a_card_without_loyalty_or_defense_has_none(script):
    bolt = parse_card(script("lightning_bolt")).faces[0]
    assert (bolt.loyalty, bolt.defense) == (None, None)


def test_loyalty_and_defense_round_trip_through_json(script):
    for name in ("teyo_the_shieldmage", "nissa_steward_of_elements",
                 "invasion_of_dominaria_serra_faithkeeper"):
        card = parse_card(script(name))
        assert Card.from_json(card.to_json()) == card


def test_loyalty_and_defense_are_numbers_or_var_tokens_in_the_vocabulary(script):
    teyo = parse_card(script("teyo_the_shieldmage")).faces[0]
    assert ("loyalty", 5) in [(n.key, n.value) for n in face_numbers(teyo)]
    nissa = parse_card(script("nissa_steward_of_elements")).faces[0]
    assert "loyalty=<var>" in face_tokens(nissa)
    battle = parse_card(script("invasion_of_dominaria_serra_faithkeeper")).faces[0]
    assert ("defense", 5) in [(n.key, n.value) for n in face_numbers(battle)]
