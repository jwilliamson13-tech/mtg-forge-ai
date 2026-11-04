"""Filter values split on `.`, `+`, `,` and ` & ` into sub-tokens under their key."""

from cardscript import parse_card


def tokens_of(line):
    (ability,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    return ability.tokens


def test_opp_ctrl_tapped_creature_is_three_sub_tokens_under_its_key():
    assert tokens_of("A:SP$ TapAll | ValidCards$ Creature.OppCtrl+tapped") == [
        "Effect=TapAll", "ValidCards=Creature", "ValidCards=OppCtrl", "ValidCards=tapped"]


def test_comma_alternatives_split_too(script):
    (trigger,) = [a for a in parse_card(script("snapcaster_mage")).faces[0].abilities
                  if a.kind == "trigger"]
    (pump,) = trigger.subabilities
    assert pump.tokens[1:5] == [
        "ValidTgts=Instant", "ValidTgts=YouCtrl", "ValidTgts=Sorcery", "ValidTgts=YouCtrl"]


def test_cryptic_command_tap_mode_splits_its_filter(script):
    (charm,) = parse_card(script("cryptic_command")).faces[0].abilities
    assert charm.subabilities[2].tokens == [
        "Effect=TapAll", "ValidCards=Creature", "ValidCards=OppCtrl"]


def test_negation_and_comparisons_stay_inside_their_piece():
    assert tokens_of("A:SP$ Destroy | ValidTgts$ Creature.YouCtrl+!token+cmcLE4") == [
        "Effect=Destroy", "ValidTgts=Creature", "ValidTgts=YouCtrl",
        "ValidTgts=!token", "ValidTgts=cmcLE4"]


def test_a_piece_keeps_its_inner_spaces():
    assert tokens_of("A:SP$ Destroy | ValidTgts$ Permanent.Other+hasAbility Activated") == [
        "Effect=Destroy", "ValidTgts=Permanent", "ValidTgts=Other", "ValidTgts=hasAbility Activated"]


def test_an_ampersand_list_splits_like_a_comma_list(script):
    (trigger,) = [a for a in parse_card(script("absorbing_man")).faces[0].abilities if a.kind == "trigger"]
    (clone,) = trigger.subabilities
    assert [t for t in clone.tokens if t.startswith("AddTypes=")] == [
        "AddTypes=Legendary", "AddTypes=Creature", "AddTypes=Human", "AddTypes=Villain"]


def test_ampersand_and_filter_separators_combine():
    assert tokens_of("A:SP$ Sacrifice | SacValid$ Creature.Artifact & Creature.nonArtifact") == [
        "Effect=Sacrifice", "SacValid=Creature", "SacValid=Artifact", "SacValid=Creature", "SacValid=nonArtifact"]


def test_an_ampersand_without_spaces_is_not_a_separator():
    assert tokens_of("A:SP$ Pump | ValidTgts$ R&D") == ["Effect=Pump", "ValidTgts=R&D"]


def test_a_value_with_no_separator_is_one_token():
    assert tokens_of("A:SP$ DealDamage | ValidTgts$ Any") == ["Effect=DealDamage", "ValidTgts=Any"]


def test_empty_pieces_are_skipped():
    assert tokens_of("A:SP$ Pump | ValidTgts$ Creature.,Land") == [
        "Effect=Pump", "ValidTgts=Creature", "ValidTgts=Land"]


def test_keyword_filter_parameter_splits():
    (enchant,) = parse_card("Name:T\nK:Enchant:Creature.YouCtrl:creature you control\n").faces[0].abilities
    assert enchant.tokens == ["Effect=Enchant", "Enchant=Creature", "Enchant=YouCtrl"]


def test_cost_part_type_splits_like_any_filter():
    (ability,) = parse_card("Name:T\nA:AB$ Draw | Cost$ Sac<1/Creature.Other/another creature>\n").faces[0].abilities
    assert ability.tokens == ["Effect=Draw", "Cost=Sac", "Cost.Sac=Creature", "Cost.Sac=Other"]
    assert [(n.key, n.value) for n in ability.numbers] == [("Cost.Sac", 1)]


def test_runtime_values_are_var_not_split():
    assert tokens_of("A:SP$ GainLife | LifeAmount$ SVar$Y/Minus.Z") == ["Effect=GainLife", "LifeAmount=<var>"]
