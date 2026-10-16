"""Mana costs on the ManaCost: line, and Cost$ segments inside abilities."""

from cardscript import parse_card


def cost_of(script, name):
    c = parse_card(script(name)).faces[0].mana_cost
    return c.generic, c.pips, c.special


def numbers(ability):
    return [(n.key, n.value) for n in ability.numbers]


# --- the ManaCost: line -----------------------------------------------------


def test_generic_plus_three_blue_pips():
    face = parse_card("Name:Ancestral Vision Test\nManaCost:1 U U U\n").faces[0]
    assert (face.mana_cost.generic, face.mana_cost.pips, face.mana_cost.special) == (
        1, {"U": 3}, [])


def test_hybrid_symbols_are_special_pips_not_colored_pips(script):
    assert cost_of(script, "boros_recruit") == (0, {}, ["RW"])


def test_phyrexian_symbols_are_special_pips_kept_once_each(script):
    assert cost_of(script, "dismember") == (1, {}, ["BP", "BP"])


def test_x_is_a_special_pip_beside_its_colored_pip(script):
    assert cost_of(script, "blaze") == (0, {"R": 1}, ["X"])


def test_no_cost_is_an_empty_cost(script):
    assert cost_of(script, "r_1_1_goblin") == (0, {}, [])


def test_snow_and_colorless_symbols_are_special_pips():
    face = parse_card("Name:T\nManaCost:2 S C\n").faces[0]
    assert (face.mana_cost.generic, face.mana_cost.special) == (2, ["S", "C"])


# --- Cost$ inside an ability ------------------------------------------------


def test_lavamancer_keeps_its_two_2s_under_different_keys(script):
    face = parse_card(script("grim_lavamancer")).faces[0]
    (ability,) = face.abilities
    assert ability.kind == "activated"
    assert numbers(ability) == [("Cost.ExileFromGrave", 2), ("NumDmg", 2)]


def test_lavamancer_cost_parts_become_cost_tokens(script):
    (ability,) = parse_card(script("grim_lavamancer")).faces[0].abilities
    assert ability.tokens == [
        "Effect=DealDamage",
        "Cost=R",
        "Cost=T",
        "Cost=ExileFromGrave",
        "Cost.ExileFromGrave=Card",
        "ValidTgts=Any",
    ]


def test_generic_mana_in_a_cost_is_a_number_under_the_cost_key():
    line = "A:AB$ Draw | Cost$ 2 U T | NumCards$ 1"
    (ability,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    assert numbers(ability) == [("Cost", 2), ("NumCards", 1)]
    assert ability.tokens == ["Effect=Draw", "Cost=U", "Cost=T"]


def test_cost_part_description_text_is_dropped():
    line = "A:AB$ Draw | Cost$ Sac<1/Creature.Other/another creature> | NumCards$ 1"
    (ability,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    assert ability.tokens == ["Effect=Draw", "Cost=Sac", "Cost.Sac=Creature.Other"]
    assert numbers(ability) == [("Cost.Sac", 1), ("NumCards", 1)]


def test_amount_only_cost_part_is_a_keyed_number():
    line = "A:AB$ Draw | Cost$ PayLife<4> | NumCards$ 1"
    (ability,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    assert ability.tokens == ["Effect=Draw", "Cost=PayLife"]
    assert numbers(ability) == [("Cost.PayLife", 4), ("NumCards", 1)]


def test_other_cost_keys_parse_the_same_way():
    line = "A:SP$ Counter | TargetType$ Spell | UnlessCost$ Discard<1/Card>"
    (ability,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    assert "UnlessCost=Discard" in ability.tokens
    assert numbers(ability) == [("UnlessCost.Discard", 1)]
