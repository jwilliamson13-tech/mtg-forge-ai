"""The card-set arches: each keeps the contract and the set properties, and the batch path matches the export path."""

import json

import numpy as np
import onnxruntime
import pytest
import torch

from setmodel.__main__ import main
from setmodel.config import ARCHES, ATTENTION, COUNT, MEAN
from setmodel.dataset import LIVE_FIELDS, SCOREBOARD_FIELDS, ZONE_IDS, load_corpus
from setmodel.export import MODEL, export_bundle, export_check
from setmodel.model import ModelConfig, SetModel, StateModel
from setmodel.review import ABLATION_JSON
from setmodel.train import ABLATIONS, NO_CARDS, TrainConfig, _batch, _logits, load_model, train
from tests.setmodel_corpus import (card_table, manifest, smoke_games, vocabulary, write_dump, write_manifest,
                                   write_parquet)

A, T, K = 4, 5, 6
NEW_ARCHES = (COUNT, ATTENTION)


def set_model(arch, width=16):
    torch.manual_seed(1)
    return SetModel(ModelConfig(token_count=20, key_count=K, width=width, arch=arch), torch.ones(K)).eval()


def state(cards, seed=2):
    generator = torch.Generator().manual_seed(seed)
    zone = torch.randint(0, 8, (cards,), generator=generator)
    return {"scoreboard": torch.rand(len(SCOREBOARD_FIELDS), generator=generator),
            "token_ids": torch.randint(0, 20, (cards, A, T), generator=generator),
            "token_mask": torch.ones(cards, A, T), "numbers": torch.rand(cards, A, K, generator=generator),
            "number_mask": torch.ones(cards, A, K), "live": torch.rand(cards, len(LIVE_FIELDS), generator=generator),
            "zone": zone}


def one(model, inputs):
    with torch.no_grad():
        return StateModel(model).eval()(**inputs)


def batched(model, states):
    """Score several states through the training path: cards flattened, `row` naming each card's state."""
    keys = ("token_ids", "token_mask", "numbers", "number_mask", "live", "zone")
    cat = {key: torch.cat([s[key] for s in states]) for key in keys}
    row = torch.cat([torch.full((len(s["zone"]),), n, dtype=torch.int64) for n, s in enumerate(states)])
    with torch.no_grad():
        text = model.card_text(cat["token_ids"], cat["token_mask"], cat["numbers"], cat["number_mask"])
        return torch.sigmoid(model.score(text, cat["live"], cat["zone"], row,
                                         torch.stack([s["scoreboard"] for s in states])))


def test_an_unknown_arch_is_refused():
    with pytest.raises(ValueError, match="unknown arch"):
        ModelConfig(token_count=20, key_count=K, arch="lstm")


def test_mean_keeps_the_original_weights_so_old_artifacts_load():
    names = set(set_model(MEAN).state_dict())
    assert not any(name.startswith("attention") for name in names)
    assert set_model(MEAN).head[0].in_features == 8 * 16 + len(SCOREBOARD_FIELDS)
    assert ModelConfig(token_count=20, key_count=K).arch == MEAN  # a model_config with no arch field


@pytest.mark.parametrize("arch", ARCHES)
def test_every_arch_gives_a_probability_for_empty_and_full_boards(arch):
    for cards in (0, 1, 30):
        p = one(set_model(arch), state(cards))
        assert p.shape == (1,) and 0 < p.item() < 1


@pytest.mark.parametrize("arch", ARCHES)
def test_permuting_the_cards_leaves_the_output_unchanged(arch):
    inputs = state(12)
    order = torch.randperm(12, generator=torch.Generator().manual_seed(5))
    permuted = {k: v[order] if k != "scoreboard" else v for k, v in inputs.items()}
    assert one(set_model(arch), permuted).item() == pytest.approx(one(set_model(arch), inputs).item(), abs=1e-6)


@pytest.mark.parametrize("arch", ARCHES)
def test_the_batch_path_gives_each_state_what_the_export_path_gives_it(arch):
    """Training pads states of different sizes together. A state's score must not depend on its batch mates."""
    model = set_model(arch)
    states = [state(n, seed=n) for n in (3, 0, 17, 1, 9)]
    together = batched(model, states)
    alone = torch.cat([one(model, s) for s in states])
    assert torch.allclose(together, alone, atol=1e-6)


def test_count_pooling_tells_two_copies_from_one_and_mean_does_not():
    copy = state(1)
    twice = {k: v if k == "scoreboard" else torch.cat([v, v]) for k, v in copy.items()}
    assert one(set_model(MEAN), twice).item() == pytest.approx(one(set_model(MEAN), copy).item(), abs=1e-6)
    for arch in NEW_ARCHES:
        assert abs(one(set_model(arch), twice).item() - one(set_model(arch), copy).item()) > 1e-5


def test_attention_lets_a_card_in_one_zone_change_how_another_zone_pools():
    """Under count pooling the hand's vector ignores the battlefield. Under attention it reads it."""
    model = set_model(ATTENTION)
    seen = {}
    model.head.register_forward_hook(lambda _, args, __: seen.update(head=args[0]))
    base = state(2)
    base["zone"] = torch.tensor([ZONE_IDS["me_hand"], ZONE_IDS["battlefield"]])
    one(model, base)
    first = seen["head"].clone()
    changed = {k: v.clone() for k, v in base.items()}
    changed["live"][1] += 1.0  # only the battlefield card changes
    one(model, changed)
    hand = slice(ZONE_IDS["me_hand"] * 33, ZONE_IDS["me_hand"] * 33 + 16)  # 2 * width + 1 per zone, mean first
    assert not torch.allclose(first[0, hand], seen["head"][0, hand])


def test_padding_tokens_and_abilities_change_nothing_under_attention():
    inputs = state(3)
    inputs["token_mask"][:, 2:] = 0
    inputs["number_mask"][:, 2:] = 0
    padded = {k: v.clone() for k, v in inputs.items()}
    padded["token_ids"][:, 2:] = 7
    padded["numbers"][:, 2:] = 99
    model = set_model(ATTENTION)
    assert one(model, padded).item() == pytest.approx(one(model, inputs).item(), abs=1e-6)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("arch")
    return load_corpus(write_parquet(smoke_games(), root), card_table(), vocabulary(), manifest()), root


@pytest.mark.parametrize("arch", NEW_ARCHES)
def test_training_lowers_the_loss_and_the_training_batches_match_single_states(corpus, arch):
    corpus, _ = corpus
    trained = train(corpus, vocabulary(), TrainConfig(epochs=8, batch_size=16, width=16, device="cpu", arch=arch))
    assert trained.epoch_losses[-1] < trained.epoch_losses[0]
    model = trained.model.cpu().eval()
    rows = np.arange(len(corpus.labels))
    with torch.no_grad():
        together = torch.sigmoid(_logits(model, _batch(corpus, rows, "cpu"))).numpy()
    wrapper = StateModel(model).eval()
    for row in rows.tolist():
        cards = np.arange(corpus.card_offsets[row], corpus.card_offsets[row + 1])
        features = corpus.card_feature[cards]
        inputs = [torch.as_tensor(a) for a in (
            corpus.scoreboard[row], corpus.token_ids[features], corpus.token_mask[features], corpus.numbers[features],
            corpus.number_mask[features], corpus.live(cards), corpus.card_zone[cards].astype(np.int64))]
        with torch.no_grad():
            assert wrapper(*inputs).item() == pytest.approx(float(together[row]), abs=1e-5)


@pytest.mark.parametrize("arch", NEW_ARCHES)
def test_every_ablation_trains_under_the_new_arches(corpus, arch):
    corpus, _ = corpus
    for ablation in ABLATIONS:
        trained = train(corpus, vocabulary(), TrainConfig(epochs=1, batch_size=16, width=16, device="cpu",
                                                          arch=arch, ablation=ablation))
        assert np.isfinite(trained.epoch_losses).all()


@pytest.mark.parametrize("arch", NEW_ARCHES)
def test_the_export_check_passes_and_the_card_axis_stays_dynamic(corpus, arch, tmp_path):
    corpus, _ = corpus
    vocab, table = vocabulary(), card_table()
    model = train(corpus, vocab, TrainConfig(epochs=2, width=16, device="cpu", arch=arch)).model.cpu().eval()
    contract = export_bundle(model, vocab, corpus.caps, {"generation": 1, "batch_ids": ["smoke"]}, table, corpus,
                             tmp_path)
    check = export_check(model, corpus, tmp_path)
    assert check.passed and check.max_difference < 1e-5
    session = onnxruntime.InferenceSession(tmp_path / MODEL, providers=["CPUExecutionProvider"])
    sizes = contract["sizes"]
    assert [item.name for item in session.get_inputs()] == [item["name"] for item in contract["inputs"]]
    for cards in (0, 1, 60):
        inputs = {k: v.numpy() for k, v in state(cards).items()}
        inputs = {"scoreboard": inputs["scoreboard"],
                  "token_ids": np.zeros((cards, sizes["A"], sizes["T"]), dtype=np.int64),
                  "token_mask": np.zeros((cards, sizes["A"], sizes["T"]), dtype=np.float32),
                  "numbers": np.zeros((cards, sizes["A"], sizes["K"]), dtype=np.float32),
                  "number_mask": np.zeros((cards, sizes["A"], sizes["K"]), dtype=np.float32),
                  "live": inputs["live"], "zone": inputs["zone"]}
        p = session.run(None, inputs)[0]
        torch_inputs = [torch.as_tensor(inputs[name]) for name in
                        ("scoreboard", "token_ids", "token_mask", "numbers", "number_mask", "live", "zone")]
        with torch.no_grad():
            expected = StateModel(model).eval()(*torch_inputs).numpy()
        assert p.shape == (1,) and 0 < p[0] < 1 and abs(p[0] - expected[0]) < 1e-5


def test_the_command_line_trains_saves_and_reloads_an_arch(tmp_path):
    parquet = write_parquet(smoke_games(), tmp_path)
    cards, deck_manifest = write_dump(tmp_path / "cards.jsonl"), write_manifest(tmp_path / "manifest.json")
    out = tmp_path / "model"
    assert main(["train", str(parquet), str(cards), str(deck_manifest), "--out", str(out), "--epochs", "1",
                 "--width", "8", "--min-count", "1", "--device", "cpu", "--arch", ATTENTION]) == 0
    model, *_ = load_model(out)
    assert model.config.arch == ATTENTION and hasattr(model, "attention")
    assert json.loads((out / "metrics.json").read_text(encoding="utf-8"))["config"]["arch"] == ATTENTION
    ablation = tmp_path / "ablation"
    assert main(["ablate", str(parquet), str(cards), str(deck_manifest), "--out", str(ablation), "--epochs", "1",
                 "--width", "8", "--min-count", "1", "--device", "cpu", "--arch", COUNT]) == 0
    rows = json.loads((ablation / ABLATION_JSON).read_text(encoding="utf-8"))["rows"]
    assert [row["run"] for row in rows] == ["full", *ABLATIONS] and NO_CARDS in [row["run"] for row in rows]
    for row in rows:
        config = json.loads((ablation / row["run"] / "metrics.json").read_text(encoding="utf-8"))["config"]
        assert config["arch"] == COUNT
