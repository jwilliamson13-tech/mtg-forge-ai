"""Pool batches: the plan against the real manifest, the runs against `tests/stub_java.py`."""

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from deckpool import manifest as mf
from setmodel.dataset import HELD_OUT_DECKS, HELD_OUT_GAMES, TRAIN, game_split, load_manifest
from simrunner.__main__ import main
from simrunner.batch import plan_workers
from simrunner.pool import plan_pool

STUB = Path(__file__).parent / "stub_java.py"
SPLITS = {deck.id: deck.split for deck in mf.load().decks}
FULL_RUN = plan_pool(SPLITS, 10000, 20261107)


def deck_games(plan, *seats):
    games = Counter()
    for chunk in plan:
        for seat in seats:
            games[getattr(chunk, seat)] += chunk.games
    return games


def test_every_deck_in_a_split_gets_about_the_same_games():
    games = deck_games(FULL_RUN, "seat1", "seat2")
    assert set(games) == set(SPLITS)
    for split in (mf.TRAIN, mf.HELD_OUT):
        counts = [games[deck] for deck in SPLITS if SPLITS[deck] == split]
        assert max(counts) - min(counts) <= 2


def test_every_deck_plays_both_seats_about_equally():
    first, second = deck_games(FULL_RUN, "seat1"), deck_games(FULL_RUN, "seat2")
    assert max(abs(first[deck] - second[deck]) for deck in SPLITS) <= 2


def test_a_plan_keeps_each_pair_inside_one_split_with_no_mirrors():
    assert sum(chunk.games for chunk in FULL_RUN) == 10000
    assert sum(chunk.games for chunk in FULL_RUN if chunk.split == mf.HELD_OUT) == 1500
    for chunk in FULL_RUN:
        assert chunk.seat1 != chunk.seat2
        assert SPLITS[chunk.seat1] == SPLITS[chunk.seat2] == chunk.split


def test_the_same_seed_gives_the_same_plan():
    assert plan_pool(SPLITS, 10000, 20261107) == FULL_RUN
    assert plan_pool(SPLITS, 10000, 20261108) != FULL_RUN


def test_a_real_plans_held_out_games_cant_reach_training():
    manifest = load_manifest(mf.DEFAULT_PATH)
    landed = {mf.TRAIN: Counter(), mf.HELD_OUT: Counter()}
    for chunk in FULL_RUN:
        worker = plan_workers(chunk.games, chunk.seed, 1)[0]
        for number in range(1, chunk.games + 1):
            game_id = f"pool-{worker.seed}-{number}"
            landed[chunk.split][game_split(game_id, (chunk.seat1, chunk.seat2), manifest, 0.1)] += 1
    assert set(landed[mf.HELD_OUT]) == {HELD_OUT_DECKS}
    assert set(landed[mf.TRAIN]) == {TRAIN, HELD_OUT_GAMES}


@pytest.fixture
def pool_manifest(tmp_path):
    """Six train decks and four held-out decks in a manifest laid out like the real one."""
    root = tmp_path / "pool"
    (root / "decks" / "forge").mkdir(parents=True)
    decks = []
    for split, count in ((mf.TRAIN, 6), (mf.HELD_OUT, 4)):
        for number in range(count):
            deck = f"{split}-{number}"
            (root / "decks" / "forge" / f"{deck}.dck").write_text(f"[metadata]\nName={deck}\n[Main]\n60 Island\n", encoding="utf-8")
            decks.append({"id": deck, "file": f"decks/forge/{deck}.dck", "source": "test", "archetype": deck, "split": split})
    path = root / "decks" / "manifest.json"
    path.write_text(json.dumps({"version": 1, "decks": decks, "tracked_matchups": [None] * 4}), encoding="utf-8")
    return path


@pytest.fixture
def launches(tmp_path, monkeypatch):
    """Return a function that reads the argv of each stub run so far, keyed by seed."""
    folder = tmp_path / "launches"
    folder.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(folder))
    return lambda: {path.stem: json.loads(path.read_text(encoding="utf-8"))["argv"] for path in folder.glob("*.json")}


def pool_args(manifest, forge, run_dir, seed="5"):
    now = datetime.now()
    return ["pool", "--manifest", str(manifest), "--games", "24", "--seed", seed, "--chunk-games", "4", "--workers", "2",
            "--clock", "60", "--start", (now - timedelta(minutes=1)).strftime("%H:%M:%S"),
            "--stop", (now + timedelta(minutes=5)).strftime("%H:%M:%S"), "--forge", str(forge), "--run-dir", str(run_dir),
            "--java", sys.executable, str(STUB)]


def break_a_deck(manifest):
    """Leave the first chunk's seat 1 unable to load. Return its id, its file, and the file's good text."""
    deck = plan_pool({d.id: d.split for d in mf.load(manifest).decks}, 24, 5, 4)[0].seat1
    path = manifest.parent / "forge" / f"{deck}.dck"
    text = path.read_text(encoding="utf-8")
    path.write_text("[Main]\n60 Island\n", encoding="utf-8")
    return deck, path, text


def test_a_failed_chunk_shows_in_the_report_and_the_run_goes_on(pool_manifest, forge, tmp_path, capsys, launches):
    run_dir = tmp_path / "run"
    break_a_deck(pool_manifest)
    assert main(pool_args(pool_manifest, forge, run_dir)) == 1

    report = capsys.readouterr().out
    assert "chunk-0000  failed" in report and "Could not load deck" in report
    status = json.loads((run_dir / "batch.json").read_text(encoding="utf-8"))
    assert status["status"] == "incomplete" and status["chunks_failed"] >= 1
    assert status["chunks_finished"] + status["chunks_failed"] == status["chunks"]
    assert status["games_finished"] == len(list((run_dir / "games").glob("*.jsonl.gz"))) > 0

    argv = next(iter(launches().values()))
    assert argv[argv.index("-sim") + 1:argv.index("-sim") + 3] == ["heuristic", "heuristic"]
    assert argv[argv.index("-honest") + 1] == "on"
    assert argv[argv.index("-batch") + 1] == "run"
    assert "-record" in argv and "-tripwire" not in argv


def test_a_restart_plays_only_what_did_not_finish(pool_manifest, forge, tmp_path, capsys, launches):
    run_dir = tmp_path / "run"
    deck, path, text = break_a_deck(pool_manifest)
    main(pool_args(pool_manifest, forge, run_dir))
    before = launches()
    status = json.loads((run_dir / "batch.json").read_text(encoding="utf-8"))
    capsys.readouterr()

    for seed in before:
        (tmp_path / "launches" / f"{seed}.json").unlink()
    path.write_text(text, encoding="utf-8")
    assert main(pool_args(pool_manifest, forge, run_dir)) == 0

    report = capsys.readouterr().out
    assert len(launches()) == status["chunks_failed"]
    assert set(launches()) <= set(before)
    assert re.search(r"tripwire clean\s+yes", report)
    assert re.search(rf"skipped on this start\s+{status['chunks_finished']}", report)
    done = json.loads((run_dir / "batch.json").read_text(encoding="utf-8"))
    assert done["status"] == "complete" and done["games_finished"] == 24
    assert len(list((run_dir / "games").glob("*.jsonl.gz"))) == 24

    with pytest.raises(SystemExit):
        main(pool_args(pool_manifest, forge, run_dir, seed="6"))
    assert "different plan" in capsys.readouterr().err


def test_a_plan_names_evaluators_only_when_one_is_set(pool_manifest, forge, tmp_path, launches):
    # A plan written before evaluators existed has no such key, and its restart has to match it.
    plain = tmp_path / "plain"
    assert main(pool_args(pool_manifest, forge, plain)) == 0
    assert "evaluators" not in json.loads((plain / "plan.json").read_text(encoding="utf-8"))
    assert all("-eval" not in argv for argv in launches().values())

    searching = tmp_path / "searching"
    args = pool_args(pool_manifest, forge, searching) + ["--sim-mode", "full", "heuristic", "--evaluator", "random", "forge"]
    assert main(args) == 0
    assert json.loads((searching / "plan.json").read_text(encoding="utf-8"))["evaluators"] == ["random", "forge"]
