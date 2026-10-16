"""T:/S:/R: lines, SVar inlining one level deep, and <var> for runtime values."""

import pytest

from cardscript import ParseError, parse_card


def abilities(script_text):
    return parse_card(script_text).faces[0].abilities


def numbers(ability):
    return [(n.key, n.value) for n in ability.numbers]


# --- T:, S:, R: lines -------------------------------------------------------


def test_snapcaster_is_a_keyword_plus_a_trigger(script):
    kinds = [a.kind for a in abilities(script("snapcaster_mage"))]
    assert kinds == ["keyword", "trigger"]


def test_trigger_mode_is_its_effect_token(script):
    trigger = abilities(script("snapcaster_mage"))[1]
    assert trigger.tokens[0] == "Effect=ChangesZone"
    assert "Destination=Battlefield" in trigger.tokens


def test_platinum_angel_is_flying_plus_two_replacements(script):
    flying, loss, win = abilities(script("platinum_angel"))
    assert flying.tokens == ["Effect=Flying"]
    assert (loss.kind, win.kind) == ("replacement", "replacement")
    assert loss.tokens[0] == "Effect=GameLoss"
    assert win.tokens[0] == "Effect=GameWin"
    assert "Layer=CantHappen" in win.tokens


def test_static_line_is_a_static_ability(script):
    (static,) = abilities(script("deaths_shadow"))
    assert static.kind == "static"
    assert static.tokens[0] == "Effect=Continuous"


def test_trigger_line_must_start_with_mode():
    with pytest.raises(ParseError) as err:
        parse_card("Name:T\nT:Event$ GameLoss | Execute$ Foo\n")
    assert err.value.cause == "unknown ability API 'Event'"


def test_svar_lines_are_recognized_not_warned(script):
    for name in ("snapcaster_mage", "thassas_oracle", "deaths_shadow", "cryptic_command"):
        assert parse_card(script(name)).faces[0].warnings == []


def test_svar_lines_alone_add_no_abilities():
    assert abilities("Name:T\nSVar:DBDraw:DB$ Draw | NumCards$ 1\nSVar:PlayMain1:TRUE\n") == []


# --- Inlining ---------------------------------------------------------------


def test_snapcaster_execute_target_is_inlined_as_a_pump_sub_ability(script):
    trigger = abilities(script("snapcaster_mage"))[1]
    (pump,) = trigger.subabilities
    assert pump.kind == "sub"
    assert pump.tokens[0] == "Effect=Pump"
    assert "KW=Flashback" in pump.tokens
    assert not any(t.startswith("Execute=") for t in trigger.tokens)


def test_svar_defined_after_its_use_still_inlines():
    text = "Name:T\nA:SP$ Draw | NumCards$ 1 | SubAbility$ DBGain\nSVar:DBGain:DB$ GainLife | LifeAmount$ 3\n"
    (spell,) = abilities(text)
    (gain,) = spell.subabilities
    assert gain.tokens == ["Effect=GainLife"]
    assert numbers(gain) == [("LifeAmount", 3)]


def test_thassas_oracle_inlines_one_level_and_refs_the_next(script):
    (trigger,) = abilities(script("thassas_oracle"))
    (dig,) = trigger.subabilities
    assert dig.tokens[0] == "Effect=Dig"
    assert "SubAbility=<ref>" in dig.tokens
    assert dig.subabilities == []
    assert numbers(dig) == [("ChangeNum", 1), ("LibraryPosition", 0)]


def test_cryptic_command_is_four_inlined_modes_and_charm_num_2(script):
    (charm,) = abilities(script("cryptic_command"))
    assert charm.kind == "spell"
    assert charm.tokens[0] == "Effect=Charm"
    assert numbers(charm) == [("CharmNum", 2)]
    assert [s.tokens[0] for s in charm.subabilities] == [
        "Effect=Counter", "Effect=ChangeZone", "Effect=TapAll", "Effect=Draw"]
    assert numbers(charm.subabilities[3]) == [("NumCards", 1)]
    assert not any(t.startswith("Choices=") for t in charm.tokens)


def test_a_comma_list_inside_a_sub_ability_becomes_one_ref_each():
    text = (
        "Name:T\nT:Mode$ Attacks | Execute$ TrigCharm\n"
        "SVar:TrigCharm:DB$ Charm | Choices$ DBA,DBB\n"
        "SVar:DBA:DB$ Draw\nSVar:DBB:DB$ Mill\n"
    )
    (charm,) = abilities(text)[0].subabilities
    assert charm.tokens == ["Effect=Charm", "Choices=<ref>", "Choices=<ref>"]


def test_ab_and_trigger_bodies_inline_with_their_own_kind():
    text = (
        "Name:T\nA:SP$ Effect | Triggers$ TrigA | StaticAbilities$ StA | Execute$ AbA\n"
        "SVar:TrigA:Mode$ Phase | Phase$ Upkeep\n"
        "SVar:StA:Mode$ Continuous | Affected$ You\n"
        "SVar:AbA:AB$ Draw | Cost$ T\n"
    )
    (spell,) = abilities(text)
    assert [(s.kind, s.tokens[0]) for s in spell.subabilities] == [
        ("trigger", "Effect=Phase"), ("static", "Effect=Continuous"), ("activated", "Effect=Draw")]


def test_replacement_body_inlines_as_a_replacement():
    text = "Name:T\nA:SP$ Effect | ReplacementEffects$ ReDraw\nSVar:ReDraw:Event$ Draw | ActiveZones$ Command\n"
    (spell,) = abilities(text)
    assert [s.kind for s in spell.subabilities] == ["replacement"]


def test_a_name_with_no_svar_stays_a_plain_token():
    (spell,) = abilities("Name:T\nA:SP$ Draw | SubAbility$ DBMissing\n")
    assert spell.tokens == ["Effect=Draw", "SubAbility=DBMissing"]
    assert spell.subabilities == []


def test_a_self_referencing_svar_stops_at_one_level():
    text = "Name:T\nA:SP$ Draw | SubAbility$ DBLoop\nSVar:DBLoop:DB$ Mill | SubAbility$ DBLoop\n"
    (spell,) = abilities(text)
    (loop,) = spell.subabilities
    assert loop.tokens == ["Effect=Mill", "SubAbility=<ref>"]


def test_keyword_parameter_naming_svars_inlines_them():
    text = "Name:T\nK:Chapter:2:DBA,DBB\nSVar:DBA:DB$ Draw\nSVar:DBB:DB$ Mill\n"
    (chapter,) = abilities(text)
    assert chapter.tokens == ["Effect=Chapter"]
    assert numbers(chapter) == [("Chapter", 2)]
    assert [s.tokens[0] for s in chapter.subabilities] == ["Effect=Draw", "Effect=Mill"]


def test_a_bad_inlined_body_raises_naming_the_svar_line():
    text = "Name:T\nA:SP$ Draw | SubAbility$ DBBad\nSVar:DBBad:DB$ Mill | nonsense\n"
    with pytest.raises(ParseError) as err:
        parse_card(text)
    assert err.value.line == "SVar:DBBad:DB$ Mill | nonsense"


# --- <var> ------------------------------------------------------------------


def test_deaths_shadow_minus_x_is_var_with_a_sign_token_and_no_number(script):
    (static,) = abilities(script("deaths_shadow"))
    assert static.tokens == [
        "Effect=Continuous", "AffectedDefined=Self",
        "AddPower=<var>", "AddPower.sign=-",
        "AddToughness=<var>", "AddToughness.sign=-",
    ]
    assert static.numbers == []


def test_count_svar_inside_an_inlined_body_is_var(script):
    (trigger,) = abilities(script("thassas_oracle"))
    assert "DigNum=<var>" in trigger.subabilities[0].tokens


def test_bare_x_y_z_are_var_without_an_svar():
    (spell,) = abilities("Name:T\nA:SP$ DealDamage | NumDmg$ X | NumAtt$ +Y | NumDef$ Z\n")
    assert spell.tokens == [
        "Effect=DealDamage", "NumDmg=<var>", "NumAtt=<var>", "NumAtt.sign=+", "NumDef=<var>"]
    assert spell.numbers == []


def test_any_runtime_svar_body_is_var():
    text = (
        "Name:T\nA:SP$ GainLife | LifeAmount$ Gained | NumCards$ Half\n"
        "SVar:Gained:TriggerCount$LifeAmount\nSVar:Half:SVar$Gained/HalfUp\n"
    )
    (spell,) = abilities(text)
    assert spell.tokens == ["Effect=GainLife", "LifeAmount=<var>", "NumCards=<var>"]


def test_cost_amount_naming_a_count_svar_is_var_but_cost_x_stays_mana():
    text = "Name:T\nA:AB$ Draw | Cost$ X Sac<Y/Creature> | NumCards$ X\nSVar:Y:Count$Foo\n"
    (ab,) = abilities(text)
    assert ab.tokens == ["Effect=Draw", "Cost=X", "Cost=Sac", "Cost.Sac=<var>",
                         "Cost.Sac=Creature", "NumCards=<var>"]


def test_ai_hint_svar_bodies_never_surface():
    text = "Name:T\nA:SP$ Draw | NumCards$ 1\nSVar:AIPreference:SacCost$Creature\nSVar:PlayMain1:TRUE\n"
    (spell,) = abilities(text)
    assert spell.tokens == ["Effect=Draw"]


# --- Only reference keys resolve -------------------------------------------


def test_a_value_that_merely_matches_an_svar_name_is_not_inlined():
    text = (
        "Name:T\nA:SP$ ChangeZone | Origin$ Battlefield | Destination$ Exile | SubAbility$ Exile\n"
        "SVar:Exile:DB$ ChangeZone | Defined$ Self\n"
    )
    (spell,) = abilities(text)
    assert spell.tokens == ["Effect=ChangeZone", "Origin=Battlefield", "Destination=Exile"]
    assert len(spell.subabilities) == 1


def test_choices_that_are_not_all_svars_stay_a_value():
    text = "Name:T\nA:SP$ ChooseCard | Choices$ DBA,Creature\nSVar:DBA:DB$ Draw\n"
    (spell,) = abilities(text)
    assert spell.tokens == ["Effect=ChooseCard", "Choices=DBA", "Choices=Creature"]
    assert spell.subabilities == []


def test_ampersand_lists_inline_each_and_static_keys_give_statics():
    text = (
        "Name:T\nA:SP$ Animate | AddStaticAbility$ StA & StB\n"
        "SVar:StA:Mode$ CantBlock | ValidCard$ Card.Self\n"
        "SVar:StB:Mode$ Continuous | Affected$ You\n"
    )
    (spell,) = abilities(text)
    assert [(s.kind, s.tokens[0]) for s in spell.subabilities] == [
        ("static", "Effect=CantBlock"), ("static", "Effect=Continuous")]


def test_keyword_parameter_naming_a_non_ability_svar_stays_a_token():
    text = "Name:T\nK:Enchant:Creature\nSVar:Creature:DB$ Draw\n"
    (enchant,) = abilities(text)
    assert enchant.tokens == ["Effect=Enchant", "Enchant=Creature"]
    assert enchant.subabilities == []


def test_an_inline_expression_value_is_var():
    (spell,) = abilities("Name:T\nA:SP$ DealDamage | NumDmg$ TriggerCount$Amount | NumAtt$ +Count$ChosenNumber\n")
    assert spell.tokens == ["Effect=DealDamage", "NumDmg=<var>", "NumAtt=<var>", "NumAtt.sign=+"]


# --- ST$: abilities that don't use the stack ----------------------------------


def test_st_line_is_an_activated_ability(script):
    vultures = abilities(script("circling_vultures"))
    st = vultures[-1]
    assert [a.kind for a in vultures] == ["keyword", "trigger", "activated"]
    assert st.tokens[:2] == ["Effect=Discard", "Mode=Defined"]
    assert ("Cost", 0) in numbers(st)


def test_st_svar_body_is_an_ability_reference(script):
    (trigger,) = abilities(script("glass_asp"))
    (effect,) = trigger.subabilities
    assert "Abilities=<ref>" in effect.tokens
    assert "Abilities=PayUp" not in effect.tokens


# --- ResultSubAbilities$: die results ------------------------------------------


def test_each_die_result_is_a_sub_ability_carrying_its_range(script):
    (roll,) = abilities(script("berserkers_frenzy"))
    assert not any(t.startswith("ResultSubAbilities=") for t in roll.tokens)
    must_block, choose_block = roll.subabilities
    assert must_block.tokens[0] == "Effect=ChooseCard"
    assert numbers(must_block)[:2] == [("ResultSubAbilities.min", 1), ("ResultSubAbilities.max", 14)]
    assert choose_block.tokens[0] == "Effect=Effect"
    assert numbers(choose_block)[:2] == [("ResultSubAbilities.min", 15), ("ResultSubAbilities.max", 20)]


def test_else_die_result_is_a_token_on_its_sub_ability(script):
    (roll,) = abilities(script("diviners_portent"))
    draw, scry = roll.subabilities
    assert numbers(draw)[:2] == [("ResultSubAbilities.min", 1), ("ResultSubAbilities.max", 14)]
    assert scry.tokens[:2] == ["Effect=Scry", "ResultSubAbilities=Else"]


def test_die_results_past_the_inline_depth_are_refs(script):
    trigger = abilities(script("arden_angel"))[1]
    (roll,) = trigger.subabilities
    assert roll.tokens == ["Effect=RollDice", "ResultSubAbilities=<ref>"]
    assert numbers(roll) == [("Sides", 4)]
