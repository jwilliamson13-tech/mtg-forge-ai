"""What the parser drops on purpose, and warnings versus errors."""

from pathlib import Path

import pytest

from cardscript import ParseError, parse_card

FIXTURES = sorted(p.stem for p in (Path(__file__).parent / "fixtures").glob("*.txt"))

# Keys that occur in the fixtures and must never reach a token.
HINT_AND_TEXT_KEYS = {
    "SpellDescription", "TriggerDescription", "Description", "TgtPrompt",
    "StackDescription", "AILogic", "IsCurse", "Oracle",
    "NeedsToPlayVar", "NonCombatPriority", "NonStackingAttachEffect",
}


def all_tokens(card):
    def walk(ability):
        yield from ability.tokens
        for sub in ability.subabilities:
            yield from walk(sub)

    for face in card.faces:
        for ability in face.abilities:
            yield from walk(ability)


def every_ability(card):
    def walk(ability):
        yield ability
        for sub in ability.subabilities:
            yield from walk(sub)

    for face in card.faces:
        for ability in face.abilities:
            yield from walk(ability)


@pytest.mark.parametrize("name", FIXTURES)
def test_no_fixture_token_carries_a_hint_or_text_key(script, name):
    card = parse_card(script(name))
    leaked = [t for t in all_tokens(card)
              if t.split("=", 1)[0].split(".", 1)[0] in HINT_AND_TEXT_KEYS]
    assert leaked == []


@pytest.mark.parametrize("name", FIXTURES)
def test_no_fixture_number_carries_a_hint_key(script, name):
    card = parse_card(script(name))
    assert [n.key for a in every_ability(card) for n in a.numbers
            if n.key in HINT_AND_TEXT_KEYS] == []


def test_ai_logic_inside_an_inlined_mode_is_dropped(script):
    (charm,) = parse_card(script("cryptic_command")).faces[0].abilities
    assert charm.subabilities[1].tokens == [
        "Effect=ChangeZone", "Origin=Battlefield", "Destination=Hand", "ValidTgts=Permanent"]


def test_is_curse_is_an_ai_hint_and_dropped(script):
    (pump,) = parse_card(script("dismember")).faces[0].abilities
    assert pump.tokens == ["Effect=Pump", "ValidTgts=Creature"]


def test_every_ai_segment_key_is_dropped():
    line = ("A:SP$ Draw | AITgts$ Creature | AIPreference$ X | UnlessAI$ Never | "
            "AiChoiceLogic$ Best | AINoRecursiveCheck$ True | AIMinTgts$ 2 | NumCards$ 1")
    (spell,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    assert spell.tokens == ["Effect=Draw"]
    assert [(n.key, n.value) for n in spell.numbers] == [("NumCards", 1)]


def test_prompt_and_title_text_keys_are_dropped():
    line = ("A:SP$ ChooseCard | Choices$ Creature | ChoiceTitle$ Choose a creature | "
            "SelectPrompt$ Pick one | ValidTgtsDesc$ creature you control | CostDesc$ | "
            "AdditionalDescription$ more words | OrString$ or")
    (spell,) = parse_card(f"Name:T\n{line}\n").faces[0].abilities
    assert spell.tokens == ["Effect=ChooseCard", "Choices=Creature"]


def test_deckbuilding_and_text_lines_are_dropped_without_a_warning():
    text = (
        "Name:T\nManaCost:R\nTypes:Instant\n"
        "DeckHas:Ability$Token\nDeckHints:Type$Goblin\nDeckNeeds:Color$Red\n"
        "AI:RemoveDeck:Random\nText:Some reminder text.\nDraft:Draft text\n"
        "DeckRule:Some rule\nMeldPair:Other Card\nOracle:Words.\n"
    )
    face = parse_card(text).faces[0]
    assert face.warnings == []
    assert face.abilities == []


def test_a_referenced_hint_svar_is_never_resolved():
    # AIPreference's body looks like an expression; naming it must not make <var>.
    text = "Name:T\nA:SP$ Draw | NumCards$ AIPreference\nSVar:AIPreference:SacCost$Creature\n"
    (spell,) = parse_card(text).faces[0].abilities
    assert spell.tokens == ["Effect=Draw", "NumCards=AIPreference"]


def test_comment_lines_are_skipped_even_with_a_colon():
    face = parse_card("Name:T\n# TODO: fix this\n# plain comment\n").faces[0]
    assert face.warnings == []


def test_invented_line_key_is_a_warning_and_parsing_goes_on():
    face = parse_card("Name:T\nWibble:3\n" + "A:SP$ Draw | NumCards$ 1\n").faces[0]
    assert face.warnings == ["unknown key: Wibble"]
    assert [a.tokens[0] for a in face.abilities] == ["Effect=Draw"]


def test_malformed_segment_error_names_the_line_and_a_groupable_cause():
    causes = set()
    for line in ("A:SP$ DealDamage | NumDmg 3", "T:Mode$ Attacks | Execute"):
        with pytest.raises(ParseError) as err:
            parse_card(f"Name:T\n{line}\n")
        assert err.value.line == line
        assert line in str(err.value)
        causes.add(err.value.cause)
    assert causes == {"segment without Key$"}


def test_malformed_line_in_the_second_face_still_raises():
    text = "Name:A\n\nALTERNATE\n\nName:B\nA:SP$ Draw | oops\n"
    with pytest.raises(ParseError) as err:
        parse_card(text)
    assert err.value.line == "A:SP$ Draw | oops"
