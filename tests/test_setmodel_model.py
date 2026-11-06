"""The set model: what must hold whatever the weights are, then a short training run on CPU."""

import pytest
import torch

from setmodel.dataset import LIVE_FIELDS, SCOREBOARD_FIELDS, ZONE_IDS, load_corpus
from setmodel.model import ModelConfig, SetModel, StateModel, weighted_loss
from setmodel.train import TrainConfig, train
from tests.setmodel_corpus import card_table, manifest, smoke_games, vocabulary, write_parquet

A, T, K = 4, 5, 6


def model():
    torch.manual_seed(1)
    return StateModel(SetModel(ModelConfig(token_count=20, key_count=K, width=16), torch.ones(K))).eval()


def state(cards: int):
    generator = torch.Generator().manual_seed(2)
    return {"scoreboard": torch.rand(len(SCOREBOARD_FIELDS), generator=generator),
            "token_ids": torch.randint(0, 20, (cards, A, T), generator=generator),
            "token_mask": torch.ones(cards, A, T), "numbers": torch.rand(cards, A, K, generator=generator),
            "number_mask": torch.ones(cards, A, K), "live": torch.rand(cards, len(LIVE_FIELDS), generator=generator),
            "zone": torch.zeros(cards, dtype=torch.int64)}


def run(inputs):
    with torch.no_grad():
        return model()(**inputs)


def test_permuting_cards_within_a_zone_leaves_the_output_unchanged():
    inputs = state(6)
    inputs["zone"][3:] = ZONE_IDS["me_hand"]
    order = torch.tensor([2, 0, 1, 5, 3, 4])
    permuted = {k: v[order] if k != "scoreboard" else v for k, v in inputs.items()}
    assert run(permuted).item() == pytest.approx(run(inputs).item(), abs=1e-6)


def test_moving_a_card_to_another_zone_changes_the_output():
    inputs = state(2)
    moved = {**inputs, "zone": torch.tensor([ZONE_IDS["battlefield"], ZONE_IDS["me_hand"]])}
    assert run(moved).item() != run(inputs).item()


def test_empty_zones_and_a_board_with_no_cards_give_a_probability():
    for cards in (0, 1):
        p = run(state(cards))
        assert p.shape == (1,) and 0 < p.item() < 1


def test_padding_tokens_and_abilities_change_nothing():
    inputs = state(1)
    padded = {k: v.clone() for k, v in inputs.items()}
    inputs["token_mask"][0, 2:] = 0
    inputs["number_mask"][0, 2:] = 0
    padded["token_mask"], padded["number_mask"] = inputs["token_mask"], inputs["number_mask"]
    padded["token_ids"][0, 2:] = 7
    padded["numbers"][0, 2:] = 99
    assert run(padded).item() == pytest.approx(run(inputs).item(), abs=1e-6)


def test_a_keyed_number_scales_its_key_by_value_over_the_constant():
    inner = SetModel(ModelConfig(token_count=20, key_count=K, width=16), torch.full((K,), 2.0))
    ids, tokens = torch.zeros(1, 1, 1, dtype=torch.int64), torch.zeros(1, 1, 1)
    numbers, mask = torch.zeros(1, 1, K), torch.zeros(1, 1, K)
    numbers[0, 0, 3], mask[0, 0, 3] = 6.0, 1.0
    seen = {}
    inner.ability.register_forward_hook(lambda _, args, __: seen.update(sum=args[0]))
    inner.card_text(ids, tokens, numbers, mask)
    assert torch.allclose(seen["sum"][0, 0], 3.0 * inner.keys.weight[3])


def test_every_game_weighs_the_same_in_the_loss():
    logits = torch.zeros(4)
    labels = torch.tensor([1.0, 1.0, 1.0, 0.0])
    weights = torch.tensor([1 / 3, 1 / 3, 1 / 3, 1.0])  # a three-row game, then a one-row game
    scale = weights.mean().item() * len(labels)
    loss = weighted_loss(logits, labels, weights, scale)
    assert loss.item() == pytest.approx(torch.log(torch.tensor(2.0)).item())
    flipped = torch.tensor([1.0, 1.0, 1.0, 1.0])
    one_game_right = weighted_loss(torch.tensor([5.0, 5.0, 5.0, 0.0]), flipped, weights, scale)
    other_game_right = weighted_loss(torch.tensor([0.0, 0.0, 0.0, 5.0]), flipped, weights, scale)
    assert one_game_right.item() == pytest.approx(other_game_right.item())


def test_the_loss_scale_does_not_depend_on_how_many_rows_a_batch_holds():
    logits = torch.zeros(4, requires_grad=True)
    labels, weights = torch.ones(4), torch.full((4,), 0.5)
    weighted_loss(logits, labels, weights, 8.0).backward()
    full_batch = logits.grad[0].item()
    tail = torch.zeros(1, requires_grad=True)
    weighted_loss(tail, labels[:1], weights[:1], 8.0).backward()
    assert tail.grad[0].item() == pytest.approx(full_batch)


def test_smoke_training_on_cpu_lowers_the_loss(tmp_path):
    corpus = load_corpus(write_parquet(smoke_games(), tmp_path), card_table(), vocabulary(), manifest())
    trained = train(corpus, vocabulary(), TrainConfig(epochs=8, batch_size=16, width=16, device="cpu"))
    assert trained.epoch_losses[-1] < trained.epoch_losses[0]
