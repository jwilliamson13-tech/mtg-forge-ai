"""The fixture Forge's ONNX evaluator tests run on: a tiny bundle, one game file, and what setmodel scores it.

    runs\\venv\\Scripts\\python -m tests.forge_fixture OUT

writes `OUT/bundle/` (the five bundle files), `OUT/game.jsonl.gz` (a game record with every
input the contract has: hidden, split, token and revealed cards, the stack, combat,
attachments and counters) and `OUT/expected.tsv` (PyTorch's probability for each of its
snapshots, in the scores format of `setmodel.parity`). The fork commits the result under
`forge-gui-desktop/src/test/resources/onnx-fixture/`. The model is the smoke model, so its
numbers mean nothing, but Java has to reproduce them.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
from pathlib import Path

from setmodel.dataset import load_corpus
from setmodel.export import export_bundle, export_check
from setmodel.parity import corpus_scores, write_scores
from setmodel.train import TrainConfig, train
from tests.setmodel_corpus import BATCH, card, card_table, game, manifest, smoke_games, snapshot, vocabulary, write_parquet

FIXTURE_GAME = 50


def stack_item(name, controller, kind, *, id, face="Original", hidden=False, targets=()):
    return {"id": id, "name": None if hidden else name, "face": None if hidden else face, "face_down": hidden,
            "controller": controller, "kind": kind, "targets": list(targets)}


def revealed(name, owner, *, power=None, toughness=None):
    return card(name, owner, id=None, power=power, toughness=toughness)


def fixture_snapshots() -> list[dict]:
    """Six lines of one game, both sides, an empty board among them."""
    bears = card("Grizzly Bears", 1, id=11, power=3, toughness=3, tapped=True, counters={"P1P1": 1},
                 attacking_seat=2, blocked_by=[12])
    knight = card("Silver Knight", 2, id=12, power=2, toughness=2, blocking=[11], damage=1)
    angel = card("Platinum Angel", 1, id=13, power=4, toughness=4, summoning_sick=True,
                 counters={"M1M1": 1, "CHARGE": 2})
    morph = card(None, 2, id=14, power=2, toughness=2, hidden=True, counters={"LOYALTY": 3})
    token = card("Elemental Token", 1, id=15, power=3, toughness=3, attached_to_seat=2)
    goblin = card("Goblin Token", 2, id=16, power=1, toughness=1, attached_to=13)
    first = snapshot(1, life=17, turn=3, battlefield=[bears, knight, angel, morph, token, goblin],
                     hand=[card("Lightning Bolt", 1, id=21), card("Fire // Ice", 1, id=22),
                           card("Snapcaster Mage", 1, id=23)],
                     graveyard=[card("Fire", 1, id=24, face="LeftSplit")],
                     revealed=[revealed("Snapcaster Mage", 2, power=2, toughness=1), revealed("Lightning Bolt", 2)],
                     stack=[stack_item("Lightning Bolt", 2, "spell", id=31, targets=[{"card": None, "seat": 1}]),
                            stack_item("Snapcaster Mage", 1, "ability", id=32),
                            stack_item(None, 2, "spell", id=33, hidden=True)])
    first["zones"]["opp_graveyard"] = [card("Ice", 2, id=25, face="RightSplit")]
    first["zones"]["me_exile"] = [card("Grizzly Bears", 1, id=26)]
    first["zones"]["opp_exile"] = [card("Lightning Bolt", 2, id=27)]
    first["scoreboard"].update(opp_life=12, me_poison=1, opp_poison=4, me_library=33, opp_library=31, opp_hand=3)

    # the other side of the same moment: the morph is that side's own, so it has a name
    second = json.loads(json.dumps(first))
    second["side"] = 2
    second["scoreboard"].update(me_life=12, opp_life=17, me_poison=4, opp_poison=1, me_library=31, opp_library=33,
                                opp_hand=3, me_active=False, me_on_play=False)
    second["zones"]["battlefield"][3] = card("Snapcaster Mage", 2, id=14, power=2, toughness=2, face_down=True,
                                             counters={"LOYALTY": 3})
    second["zones"]["me_hand"] = [card("Silver Knight", 2, id=41)]
    second["zones"]["me_graveyard"], second["zones"]["opp_graveyard"] = (second["zones"]["opp_graveyard"],
                                                                         second["zones"]["me_graveyard"])
    second["zones"]["me_exile"], second["zones"]["opp_exile"] = (second["zones"]["opp_exile"],
                                                                 second["zones"]["me_exile"])
    second["zones"]["opp_revealed"] = []
    second["stack"][2] = stack_item("Lightning Bolt", 2, "spell", id=33, face="Original")

    empty = snapshot(1, turn=1)
    late = snapshot(2, life=5, turn=9, battlefield=[card("Grizzly Bears", 2, id=51, power=2, toughness=2)],
                    hand=[card("Fire // Ice", 2, id=52, face="EmptyRoom")])
    alone = snapshot(1, life=1, turn=9, battlefield=[card("Platinum Angel", 1, id=61, power=4, toughness=4)])
    return [first, second, empty, late, alone, snapshot(2, turn=10)]


def build(out: str | Path) -> Path:
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    work = out / "work"
    lines = game(FIXTURE_GAME, fixture_snapshots(), winner=2, final_turn=10)
    vocab, table = vocabulary(), card_table()
    corpus = load_corpus(write_parquet([*smoke_games(), lines], work), table, vocab, manifest())
    trained = train(corpus, vocab, TrainConfig(epochs=2, width=8, device="cpu"))
    model = trained.model.cpu().eval()
    export_bundle(model, vocab, corpus.caps, {"generation": 0, "batch_ids": [BATCH]}, table, corpus, out / "bundle")
    if not export_check(model, corpus, out / "bundle").passed:
        raise RuntimeError("the fixture bundle failed its own export check")

    (out / "game.jsonl.gz").write_bytes(gzip.compress("".join(json.dumps(line) + "\n" for line in lines).encode("utf-8")))
    game_id = f"{BATCH}-{lines[0]['seed']}-{FIXTURE_GAME}"
    scores = {key: p for key, p in corpus_scores(model, vocab, corpus).items() if key[0] == game_id}
    if len(scores) != len(lines) - 1:
        raise RuntimeError(f"the corpus kept {len(scores)} of the fixture's {len(lines) - 1} snapshots")
    write_scores(out / "expected.tsv", scores)
    shutil.rmtree(work)
    return out


if __name__ == "__main__":
    print(build(sys.argv[1]))
