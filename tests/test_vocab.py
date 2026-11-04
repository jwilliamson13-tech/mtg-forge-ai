"""The vocabulary builder: token counts, cutoff, reserved markers, save/load."""

import json

import pytest

from cardscript import parse_card
from cardscript.vocab import (
    REF,
    UNK,
    VAR,
    Vocabulary,
    build_vocabulary,
    face_numbers,
    face_tokens,
)

ALL = [
    "lightning_bolt",
    "grim_lavamancer",
    "snapcaster_mage",
    "cryptic_command",
    "deaths_shadow",
    "platinum_angel",
    "thassas_oracle",
    "empty_the_warrens",
    "r_1_1_goblin",
    "dismember",
]


@pytest.fixture
def cards(script):
    return lambda *names: [parse_card(script(n)) for n in names]


# --- Reserved markers --------------------------------------------------------


@pytest.mark.parametrize(
    "corpus",
    [[], ["lightning_bolt"], ["thassas_oracle", "deaths_shadow"], ALL],
)
def test_reserved_markers_sit_at_fixed_indices_whatever_the_corpus(cards, corpus):
    vocab = build_vocabulary(cards(*corpus))
    assert vocab.tokens[:3] == [UNK, VAR, REF]
    assert (vocab.index(UNK), vocab.index(VAR), vocab.index(REF)) == (0, 1, 2)


def test_reserved_markers_survive_a_cutoff_nothing_passes(cards):
    vocab = build_vocabulary(cards(*ALL), min_count=10_000)
    assert vocab.tokens == [UNK, VAR, REF]


def test_reserved_markers_appear_once(cards):
    vocab = build_vocabulary(cards(*ALL))
    for marker in (UNK, VAR, REF):
        assert vocab.tokens.count(marker) == 1


# --- Cutoff ------------------------------------------------------------------


def test_token_seen_often_enough_gets_its_own_index(cards):
    # Lightning Bolt and Grim Lavamancer both deal damage; Bolt and Dismember are instants.
    vocab = build_vocabulary(cards("lightning_bolt", "grim_lavamancer", "dismember"), min_count=2)
    assert vocab.index("Effect=DealDamage") > 2
    assert vocab.index("type=Instant") > 2


def test_token_below_the_cutoff_maps_to_unk(cards):
    # Grim Lavamancer is the only Human and the only card with a tap cost.
    vocab = build_vocabulary(cards("lightning_bolt", "grim_lavamancer", "dismember"), min_count=2)
    for token in ("type=Human", "Cost=T"):
        assert token not in vocab.tokens
        assert vocab.index(token) == vocab.index(UNK) == 0


def test_a_token_never_seen_maps_to_unk(cards):
    vocab = build_vocabulary(cards(*ALL))
    assert vocab.index("Effect=SomethingForgeAddsNextYear") == 0


def test_cutoff_counts_cards_not_repeats_within_a_card(cards):
    # Dismember has the special pip BP twice, but it is one card.
    vocab = build_vocabulary(cards("dismember", "lightning_bolt"), min_count=2)
    assert "pip=BP" not in vocab.tokens


def test_min_count_one_keeps_every_token(cards):
    vocab = build_vocabulary(cards("lightning_bolt"), min_count=1)
    assert vocab.tokens == [UNK, VAR, REF, "Effect=DealDamage", "ValidTgts=Any", "kind=spell", "type=Instant"]


def test_more_frequent_tokens_come_first_ties_alphabetical(cards):
    vocab = build_vocabulary(cards("lightning_bolt", "grim_lavamancer"))
    body = vocab.tokens[3:]
    # In both cards: Effect=DealDamage, ValidTgts=Any.
    assert body[:2] == ["Effect=DealDamage", "ValidTgts=Any"]
    assert body[2:] == sorted(body[2:])


def test_same_corpus_same_vocabulary(cards):
    assert build_vocabulary(cards(*ALL), min_count=2) == build_vocabulary(cards(*ALL), min_count=2)


def test_min_count_below_one_is_rejected(cards):
    with pytest.raises(ValueError):
        build_vocabulary(cards("lightning_bolt"), min_count=0)


# --- <var> and <ref> tokens ---------------------------------------------------


def test_var_and_ref_tokens_above_the_cutoff_keep_their_own_index(cards):
    vocab = build_vocabulary(cards("deaths_shadow", "mayaels_aria"))
    assert vocab.index("AddPower=<var>") > 2
    assert vocab.index("SubAbility=<ref>") > 2


def test_rare_var_token_falls_back_to_the_var_marker_not_unk(cards):
    vocab = build_vocabulary(cards("deaths_shadow", "lightning_bolt"), min_count=2)
    assert "AddPower=<var>" not in vocab.tokens
    assert vocab.index("AddPower=<var>") == vocab.index(VAR) == 1


def test_rare_ref_token_falls_back_to_the_ref_marker_not_unk(cards):
    vocab = build_vocabulary(cards("mayaels_aria", "lightning_bolt"), min_count=2)
    assert "SubAbility=<ref>" not in vocab.tokens
    assert vocab.index("SubAbility=<ref>") == vocab.index(REF) == 2


def test_the_sign_token_beside_a_var_is_an_ordinary_token(cards):
    vocab = build_vocabulary(cards("deaths_shadow", "lightning_bolt"), min_count=2)
    assert vocab.index("AddPower.sign=-") == 0


def test_star_power_is_a_var_token_on_the_face():
    face = parse_card("Name:Tarmogoyf\nTypes:Creature Lhurgoyf\nPT:*/1+*\n").faces[0]
    assert "power=<var>" in face_tokens(face)
    assert "toughness=<var>" in face_tokens(face)
    assert [n.key for n in face_numbers(face)] == ["generic"]


# --- Which tokens count -------------------------------------------------------


def test_face_tokens_cover_kinds_types_colors_special_pips_and_abilities(cards):
    (goblin,) = cards("r_1_1_goblin")
    tokens = face_tokens(goblin.faces[0])
    assert {"type=Creature", "type=Goblin", "color=red"} <= set(tokens)
    (dismember,) = cards("dismember")
    assert face_tokens(dismember.faces[0]).count("pip=BP") == 2


def test_sub_ability_tokens_count(cards):
    vocab = build_vocabulary(cards("snapcaster_mage"))
    # Effect=Pump lives only in the inlined TrigFlashback body.
    assert "Effect=Pump" in vocab.tokens
    assert "kind=sub" in vocab.tokens


def test_keyword_sub_tokens_count(cards):
    vocab = build_vocabulary(cards("snapcaster_mage", "empty_the_warrens"))
    assert {"Effect=Flash", "Effect=Storm", "kind=keyword"} <= set(vocab.tokens)


def test_names_and_warnings_never_become_tokens():
    card = parse_card("Name:Lightning Bolt\nManaCost:R\nTypes:Instant\nFrobnicate:yes\n")
    tokens = face_tokens(card.faces[0])
    assert not any("Lightning" in t or "Frobnicate" in t for t in tokens)


# --- Numeric keys -------------------------------------------------------------


def test_numeric_keys_come_back_alongside_the_tokens(cards):
    vocab = build_vocabulary(cards("lightning_bolt", "grim_lavamancer"))
    assert {"NumDmg", "Cost.ExileFromGrave", "generic", "pip.R", "power", "toughness"} <= set(
        vocab.numeric_keys[1:]
    )
    assert not any("=" in k for k in vocab.numeric_keys)


def test_numeric_key_table_reserves_unk_at_zero_and_uses_the_cutoff(cards):
    vocab = build_vocabulary(cards("lightning_bolt", "grim_lavamancer", "cryptic_command"), min_count=2)
    assert vocab.numeric_keys[0] == UNK
    assert vocab.key_index("NumDmg") > 0
    assert "CharmNum" not in vocab.numeric_keys
    assert vocab.key_index("CharmNum") == 0


def test_sub_ability_numbers_count(cards):
    vocab = build_vocabulary(cards("cryptic_command"))
    # NumCards comes from the inlined DBDraw mode.
    assert "NumCards" in vocab.numeric_keys


def test_numeric_keys_and_tokens_are_separate_tables(cards):
    vocab = build_vocabulary(cards("lightning_bolt"))
    assert "NumDmg" not in vocab.tokens
    assert "Effect=DealDamage" not in vocab.numeric_keys


def test_empty_corpus_gives_only_the_reserved_entries():
    vocab = build_vocabulary([])
    assert vocab.tokens == [UNK, VAR, REF]
    assert vocab.numeric_keys == [UNK]


# --- Input forms --------------------------------------------------------------


def test_json_dicts_give_the_same_vocabulary_as_cards(cards):
    parsed = cards(*ALL)
    from_dicts = build_vocabulary([json.loads(c.to_json()) for c in parsed], min_count=2)
    assert from_dicts == build_vocabulary(parsed, min_count=2)


def test_any_iterable_works_including_a_generator(cards):
    parsed = cards(*ALL)
    assert build_vocabulary(c for c in parsed) == build_vocabulary(parsed)


# --- Save and load -------------------------------------------------------------


def test_vocabulary_saves_and_loads_back(cards, tmp_path):
    vocab = build_vocabulary(cards(*ALL), min_count=2)
    path = tmp_path / "vocab.json"
    vocab.save(path)
    loaded = Vocabulary.load(path)
    assert loaded == vocab
    assert loaded.index("Effect=DealDamage") == vocab.index("Effect=DealDamage")
    assert loaded.index("AddPower=<var>") == vocab.index("AddPower=<var>")


def test_saved_file_is_plain_json(cards, tmp_path):
    vocab = build_vocabulary(cards("lightning_bolt"))
    path = tmp_path / "vocab.json"
    vocab.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tokens"][:3] == [UNK, VAR, REF]
    assert data["numeric_keys"][0] == UNK
    assert data["min_count"] == 1


def test_loading_a_file_without_the_reserved_prefix_fails(tmp_path):
    path = tmp_path / "vocab.json"
    path.write_text(json.dumps({"min_count": 1, "tokens": ["Effect=X"], "numeric_keys": [UNK]}))
    with pytest.raises(ValueError):
        Vocabulary.load(path)
