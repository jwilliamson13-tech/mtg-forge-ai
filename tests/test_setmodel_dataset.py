"""The join, from the outside: game files and a card dump in, training rows out.

The hidden information tests compare what the model would be fed, so they catch a join
to the other side's rows however it is written.
"""

import json

import numpy as np
import pytest

from gamerecord.convert import convert_folder
from gamerecord.record import END_REASONS
from setmodel import dataset
from setmodel.dataset import HELD_OUT_DECKS, LIVE, TRAIN, ZONE_IDS, DatasetError, load_corpus, load_manifest
from setmodel.features import Caps, encode, key_scales
from setmodel.train import TrainConfig, train
from tests.setmodel_corpus import (HELD_OUT_DECK, TRAIN_DECKS, card, card_table, game, manifest, smoke_games, snapshot,
                                   vocabulary, write_games, write_manifest, write_parquet)


def corpus_of(games, root, **options):
    return load_corpus(write_parquet(games, root), card_table(), vocabulary(), manifest(), **options)


def row_inputs(corpus, row):
    """Everything the model gets for a row, in a form two corpora can compare."""
    positions = range(corpus.card_offsets[row], corpus.card_offsets[row + 1])
    cards = []
    for position in positions:
        feature = corpus.card_feature[position]
        cards.append((int(corpus.card_zone[position]), corpus.card_live[position].tolist(),
                      corpus.token_ids[feature].tolist(), corpus.token_mask[feature].tolist(),
                      corpus.numbers[feature].tolist(), corpus.number_mask[feature].tolist()))
    return corpus.scoreboard[row].tolist(), float(corpus.labels[row]), sorted(cards)


def rows_of_side(corpus, side_rows):
    return [row_inputs(corpus, row) for row in side_rows]


def morph_game(other_side_name="Platinum Angel", other_side_life=20):
    """Side 1's line hides a face-down card, and side 2's line of the same moment names it."""
    hidden = card(None, 2, id=5, power=2, toughness=2, hidden=True, tapped=True)
    named = card(other_side_name, 2, id=9, power=2, toughness=2, tapped=True, face_down=True)
    bears = card("Grizzly Bears", 1, id=3, power=2, toughness=2)
    bolt_in_hand = card("Lightning Bolt", 2, id=4)
    return [snapshot(1, battlefield=[bears, hidden]),
            snapshot(2, life=other_side_life, battlefield=[bears, named], hand=[bolt_in_hand])]


def test_each_snapshot_is_one_row_with_no_flipped_copy(tmp_path):
    corpus = corpus_of([game(1, morph_game(), winner=2)], tmp_path)
    assert corpus.labels.tolist() == [0.0, 1.0]  # side 1 lost, side 2 won
    assert corpus.scoreboard[:, dataset.SCOREBOARD_FIELDS.index("me_active")].tolist() == [1, 0]
    assert np.diff(corpus.card_offsets).tolist() == [2, 3]


def test_row_weights_sum_to_one_per_game_whatever_its_length(tmp_path):
    games = [game(1, [snapshot(1), snapshot(2)]), game(2, [snapshot(1) for _ in range(7)])]
    corpus = corpus_of(games, tmp_path)
    assert np.bincount(corpus.game, weights=corpus.weights).tolist() == pytest.approx([1.0, 1.0])
    assert sorted(set(corpus.weights.tolist())) == pytest.approx([1 / 7, 1 / 2])


@pytest.mark.parametrize("reason", [reason for reason in END_REASONS if reason != "win"])
def test_a_game_with_no_rules_win_gives_the_corpus_no_rows_whatever_winner_it_claims(tmp_path, reason):
    """Without a winner both sides would read as losers, and Forge names an arbitrary winner on a stopped game."""
    games = [game(1, morph_game(), winner=2), game(2, morph_game(), winner=None, end_reason=reason),
             game(3, morph_game(), winner=1, end_reason=reason)]
    report = convert_folder(write_games(games, tmp_path), tmp_path / "parquet")
    corpus = load_corpus(tmp_path / "parquet", card_table(), vocabulary(), manifest())
    assert corpus.game_ids == ["smoke-7-1"]
    assert corpus.labels.tolist() == [0.0, 1.0]
    assert report.kept == {"win": 1}
    assert report.dropped[reason] == (2 if reason == "exception" else 1)  # the loader refuses any exception file first
    assert len(report.rejected) == (0 if reason == "exception" else 1)  # a stopped game can't name a winner


def test_changing_the_other_sides_snapshot_changes_nothing_on_this_sides_rows(tmp_path):
    """The rule from #31. A join on any key, public state included, shows up as a difference here."""
    before = corpus_of([game(1, morph_game())], tmp_path / "a")
    after = corpus_of([game(1, morph_game(other_side_name="Snapcaster Mage", other_side_life=3))], tmp_path / "b")
    assert rows_of_side(before, [0]) == rows_of_side(after, [0])
    assert before.weights[0] == after.weights[0]
    assert rows_of_side(before, [1]) != rows_of_side(after, [1])  # the mutation did reach the file


def test_a_hidden_card_stays_nameless_though_the_other_side_names_it(tmp_path):
    corpus = corpus_of([game(1, morph_game())], tmp_path)
    position = int(np.flatnonzero(corpus.card_live[:2, LIVE["hidden"]] == 1)[0])
    feature = corpus.card_feature[position]
    assert feature == 0
    assert not corpus.token_mask[feature].any() and not corpus.number_mask[feature].any()
    live = corpus.card_live[position]
    assert (live[LIVE["power"]], live[LIVE["tapped"]], live[LIVE["face_down"]], live[LIVE["mine"]]) == (2, 1, 1, 0)


def test_columns_a_side_shouldnt_use_change_nothing(tmp_path):
    """Ids, the game's end, the AI profile and the seat don't reach the model."""
    first = game(1, morph_game(), final_turn=9, profile="Default", seed=7)
    second = game(1, morph_game(), final_turn=40, profile="Reckless", seed=7)
    for line in second[1:]:
        for cards in line["zones"].values():
            for row in cards:
                row["id"] += 1000
    before, after = corpus_of([first], tmp_path / "a"), corpus_of([second], tmp_path / "b")
    assert rows_of_side(before, [0, 1]) == rows_of_side(after, [0, 1])


def test_the_two_seats_get_the_same_input_for_the_same_view(tmp_path):
    """`side` itself isn't an input: controller goes in as mine or not, so a seat number can't leak."""
    mine = lambda seat: card("Grizzly Bears", seat, power=2, toughness=2)  # noqa: E731
    corpus = corpus_of([game(1, [snapshot(1, battlefield=[mine(1)])], winner=1),
                        game(2, [snapshot(2, battlefield=[mine(2)])], winner=2)], tmp_path)
    assert corpus.card_live[0].tolist() == corpus.card_live[1].tolist()
    assert corpus.card_live[0][LIVE["mine"]] == 1


def test_a_snapshot_with_a_hidden_card_is_not_dropped_for_it(tmp_path):
    corpus = corpus_of([game(1, morph_game())], tmp_path)
    assert len(corpus.labels) == 2
    assert corpus.report.snapshots_dropped == 0 and not corpus.report.unmatched
    assert corpus.report.hidden_cards == 1


def test_a_hidden_card_can_count_as_unmatched_by_flipping_one_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset, "HIDDEN_COUNTS_AS_UNMATCHED", True)
    assert len(corpus_of([game(1, morph_game())], tmp_path).labels) == 1


def test_unmatched_names_are_counted_and_only_their_snapshots_drop(tmp_path):
    ghost = card("No Such Card", 1)
    corpus = corpus_of([game(1, [snapshot(1, hand=[ghost, ghost]), snapshot(2), snapshot(1)])], tmp_path)
    assert corpus.report.unmatched == {("No Such Card", "Original"): 2}
    assert corpus.report.snapshots_dropped == 1
    assert len(corpus.labels) == 2
    assert corpus.weights.tolist() == [0.5, 0.5]  # the game still weighs 1


def test_a_stack_item_joins_like_a_card_and_says_what_it_is(tmp_path):
    item = {"id": 1, "name": "Lightning Bolt", "face": "Original", "face_down": False, "controller": 2, "kind": "spell",
            "targets": []}
    ability = {**item, "name": None, "face": None, "face_down": True, "kind": "ability"}
    corpus = corpus_of([game(1, [snapshot(1, stack=[item, ability])])], tmp_path)
    assert corpus.card_zone.tolist() == [ZONE_IDS["stack"]] * 2
    assert corpus.card_live[:, LIVE["stack_ability"]].tolist() == [0, 1]
    assert corpus.card_live[:, LIVE["hidden"]].tolist() == [0, 1]
    assert corpus.card_feature[1] == 0


def test_live_state_reads_counters_combat_and_attachment(tmp_path):
    row = card("Grizzly Bears", 1, power=3, toughness=3, counters={"P1P1": 1, "OIL": 2}, damage=1, attacking_seat=2,
               blocked_by=[9], attached_to_seat=2)
    blocker = card("Grizzly Bears", 2, id=9, power=2, toughness=2, blocking=[1])
    corpus = corpus_of([game(1, [snapshot(1, battlefield=[row, blocker])])], tmp_path)
    live = {name: corpus.card_live[0][index] for name, index in LIVE.items()}
    assert (live["power"], live["counters_p1p1"], live["counters_other"], live["damage"]) == (3, 1, 2, 1)
    assert (live["attacking"], live["blocked"], live["attached"], live["blocking"]) == (1, 1, 1, 0)
    assert corpus.card_live[1][LIVE["blocking"]] == 1


def test_no_held_out_decks_game_reaches_training_from_either_seat(tmp_path):
    games = [game(1, [snapshot(1)], decks=TRAIN_DECKS), game(2, [snapshot(1)], decks=(HELD_OUT_DECK, "Red")),
             game(3, [snapshot(2)], decks=("Green", HELD_OUT_DECK))]
    corpus = corpus_of(games, tmp_path, game_share=0.0)
    train_games = {corpus.game_ids[g] for g in corpus.game[corpus.rows(TRAIN)].tolist()}
    assert train_games == {"smoke-7-1"}
    assert {corpus.game_ids[g] for g in corpus.game[corpus.rows(HELD_OUT_DECKS)].tolist()} == {"smoke-7-2", "smoke-7-3"}


def test_the_game_split_keeps_every_row_of_a_game_together(tmp_path):
    corpus = corpus_of(smoke_games(40), tmp_path, game_share=0.3)
    assert len(corpus.rows(dataset.HELD_OUT_GAMES)) > 0
    for code in set(corpus.game.tolist()):
        assert len(set(corpus.split[corpus.game == code].tolist())) == 1


def test_a_deck_outside_the_manifest_is_refused(tmp_path):
    with pytest.raises(DatasetError, match="Mystery"):
        corpus_of([game(1, [snapshot(1)], decks=("Red", "Mystery"))], tmp_path)


def test_the_manifest_reader_takes_spec_4s_format(tmp_path):
    assert load_manifest(write_manifest(tmp_path / "manifest.json")) == manifest()
    (tmp_path / "bad.json").write_text(json.dumps({"version": 2, "decks": []}), encoding="utf-8")
    with pytest.raises(DatasetError, match="version"):
        load_manifest(tmp_path / "bad.json")


def test_a_token_name_on_several_scripts_keeps_only_what_they_share():
    resolved = card_table().resolve("Elemental Token", "Original")
    assert resolved.ambiguous
    tokens, numbers = resolved.rows[0]
    assert tokens == ("type=Creature", "type=Elemental")
    assert numbers == (("generic", 0),)  # three different printed sizes, so no printed size
    assert len(resolved.rows) == 1  # two of the three have Haste, so nothing says this one does


def test_a_whole_split_card_joins_on_its_two_halves():
    table = card_table()
    whole, fire, ice = (table.resolve(name, "Original") for name in ("Fire // Ice", "Fire", "Ice"))
    assert whole.rows == fire.rows + ice.rows
    assert table.resolve("Fire // Ice", "EmptyRoom").rows == fire.rows[:1] + ice.rows[:1]
    assert table.resolve("Fire // Nothing", "Original") is None


def test_caps_drop_the_oldest_abilities_and_tokens_and_keep_the_identity_row():
    caps = Caps(abilities=3, tokens=2)
    vocab = vocabulary()
    rows = tuple(((f"t{n}", "type=Creature", "type=Instant"), (("generic", 1), ("generic", 2))) for n in range(5))
    encoded = encode(rows, vocab, caps)
    assert encoded.rows_before_cap == 5 and encoded.rows_over_token_cap == 3 and encoded.rows_with_merged_keys == 3
    assert len(encoded.tokens) == 3
    assert encoded.tokens[0] == (vocab.index("type=Creature"), vocab.index("type=Instant"))
    assert encoded.numbers[0] == ((vocab.key_index("generic"), 3.0),)


def test_truncation_rates_count_the_training_rows(tmp_path):
    corpus = corpus_of(smoke_games(6), tmp_path, game_share=0.0, caps=Caps(abilities=1, tokens=1))
    rates = corpus.truncation()
    assert rates["cards"] > 0 and rates["abilities"] > 0 and rates["tokens"] > 0 and rates["live_state"] == 0


def test_held_out_games_do_not_change_the_key_scales_a_model_trains_with(tmp_path):
    bears = card("Grizzly Bears", 1, power=2, toughness=2)
    angel = card("Platinum Angel", 2, id=2, power=4, toughness=4)

    def corpus_with(held_out_card, root):
        games = [game(1, [snapshot(1, battlefield=[bears])], decks=TRAIN_DECKS),
                 game(2, [snapshot(1, battlefield=[held_out_card])], decks=(HELD_OUT_DECK, "Red"))]
        return corpus_of(games, root, game_share=0.0)

    def trained_scales(corpus):
        return train(corpus, vocabulary(), TrainConfig(epochs=0, width=8, device="cpu")).model.key_scale.tolist()

    plain, with_angel = corpus_with(bears, tmp_path / "a"), corpus_with(angel, tmp_path / "b")
    assert trained_scales(plain) == trained_scales(with_angel)
    # The held-out card would move the scale if it were counted, so the line above can fail.
    every_card = key_scales(with_angel.numbers, with_angel.number_mask).tolist()
    assert trained_scales(with_angel) != every_card


def test_a_key_scale_resists_an_outlier():
    numbers = np.array([[1.0], [2.0], [2.0], [3.0], [1000.0]], dtype=np.float32).reshape(5, 1, 1)
    assert key_scales(numbers, np.ones_like(numbers)).tolist() == [1.0]
    assert key_scales(np.full((3, 1, 1), 4.0), np.ones((3, 1, 1))).tolist() == [4.0]  # no spread: its size
    assert key_scales(np.zeros((2, 1, 1)), np.zeros((2, 1, 1))).tolist() == [1.0]  # never seen


def test_snapshots_and_cards_stay_together_when_a_table_is_read_out_of_order(tmp_path):
    games = [game(number, [snapshot(1, life=number, battlefield=[card("Grizzly Bears", 1, power=2, toughness=2)] * number)])
             for number in (3, 1, 2)]
    corpus = corpus_of(games, tmp_path)
    assert corpus.scoreboard[:, 0].tolist() == np.diff(corpus.card_offsets).tolist()
