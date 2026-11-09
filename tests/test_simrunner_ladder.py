"""The Elo ladder: players, the plan, the runs against `tests/stub_java.py`, and fits on outcomes with known strengths.

STUB_PATTERN's `sim=` and `eval=` subjects give an AI configuration a fixed win rate whatever deck
it plays, so these ladders know their counts in advance. The tests that need many players or
generations that can't meet in Forge yet play through a stand-in for `play_unit` instead.
"""

import json
import math
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from deckpool import manifest as mf
from simrunner.__main__ import main
from simrunner.batch import BatchConfig, SetupError
from simrunner.ladder import (FINISHED, PLAN_FILE, RESULT_FILE, LadderConfig, Unit, UnitResult, _unit_result,
                              format_ladder_report, plan_units, run_ladder, sample_deck_pairs, unit_seed)
from simrunner.matchup import Tally
from simrunner.players import PlayerError, parse_player
from simrunner.rating import expected_score
from simrunner.window import Window
from simrunner.worker import RecordPlan, Seat

STUB = Path(__file__).parent / "stub_java.py"
RATE = r"\d+\.\d% \(\d+\.\d% to \d+\.\d%\)"
CONFIDENCE_LABEL = r"\d+(\.\d+)?% confidence"
STEP = 400 * math.log10(3)  # the rating gap at which the stronger side wins 3 games in 4


def make_bundle(folder: Path, generation: int, weights: bytes) -> Path:
    folder.mkdir(parents=True)
    (folder / "contract.json").write_text(json.dumps({"version": 1, "bundle": {"generation": generation}}),
                                          encoding="utf-8")
    (folder / "model.onnx").write_bytes(weights)
    return folder


@pytest.fixture
def models(tmp_path):
    root = tmp_path / "models"
    make_bundle(root / "gen0", 0, b"gen0 weights")
    make_bundle(root / "gen1", 1, b"gen1 weights")
    return root


@pytest.fixture
def manifest(tmp_path):
    """Four train decks and six held-out decks, laid out like the real manifest."""
    root = tmp_path / "pool"
    (root / "decks" / "forge").mkdir(parents=True)
    decks = []
    for split, count in ((mf.TRAIN, 4), (mf.HELD_OUT, 6)):
        for number in range(count):
            deck = f"{split.replace('_', '')}{number}"
            (root / "decks" / "forge" / f"{deck}.dck").write_text(f"[metadata]\nName={deck}\n[Main]\n60 Island\n",
                                                                 encoding="utf-8")
            decks.append({"id": deck, "file": f"decks/forge/{deck}.dck", "source": "test", "archetype": deck,
                          "split": split})
    path = root / "decks" / "manifest.json"
    path.write_text(json.dumps({"version": 1, "decks": decks, "tracked_matchups": [None] * 4}), encoding="utf-8")
    return path


@pytest.fixture
def ladder(forge, manifest, models, tmp_path):
    def build(players=("heuristic", "full"), deck_pairs=1, pair_games=16, seed=11, workers=1, **overrides):
        # One batch at a time by default: the stub names its launch files by seed, and every pair of players shares seeds.
        settings = dict(seats=(Seat(Path()), Seat(Path())), games=0, batch_seed=seed, workers=workers,
                        forge_root=forge, run_dir=tmp_path / "ladder", clock_s=60, java=(sys.executable, str(STUB)),
                        records=RecordPlan())
        window = overrides.pop("window", None)
        return LadderConfig(BatchConfig(**settings, window=window),
                            tuple(parse_player(name, models) for name in players), manifest, deck_pairs, pair_games,
                            **overrides)

    return build


@pytest.fixture
def launches(tmp_path, monkeypatch):
    folder = tmp_path / "launches"
    folder.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(folder))

    def read():
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]

    return read


def pattern(monkeypatch, text):
    monkeypatch.setenv("STUB_PATTERN", text)


def assert_no_bare_percentage(report):
    rest = re.sub(CONFIDENCE_LABEL, "", re.sub(RATE, "", report))
    assert "%" not in rest, rest


def known_strengths(truth: dict[str, float], exact: dict[tuple[str, str], int] | None = None):
    """A stand-in for `play_unit`: the first player wins as the Elo curve says, drawn with the batch's seed."""

    def play(config, unit):
        if exact is not None:
            first_wins = exact[unit.players]
        else:
            rng = random.Random(f"{unit.seed}:{unit.folder}")
            p = expected_score(truth[unit.first.label], truth[unit.second.label])
            first_wins = sum(rng.random() < p for _ in range(unit.games))
        return _unit_result(unit, FINISHED, Tally(first_wins, unit.games - first_wins))

    return play


# Players


@pytest.mark.parametrize("name, label, mode, evaluator", [
    ("heuristic", "heuristic", "heuristic", None),
    ("full", "full", "full", None),
    ("full:forge", "full", "full", None),
    ("hybrid", "hybrid", "hybrid", None),
    ("full:random", "full:random", "full", "random"),
    ("hybrid:passthrough", "hybrid:passthrough", "hybrid", "passthrough"),
    ("onnx:gen1", "onnx:gen1", "full", "onnx"),
    ("full:onnx:gen1", "onnx:gen1", "full", "onnx"),
    ("hybrid:onnx:gen1", "hybrid:onnx:gen1", "hybrid", "onnx"),
])
def test_a_player_name_reads_as_seat_settings(name, label, mode, evaluator, models):
    player = parse_player(name, models)
    assert (player.label, player.sim_mode, player.evaluator) == (label, mode, evaluator)
    seat = player.seat(Path("x.dck"))
    assert (seat.sim_mode, seat.evaluator, seat.ai_profile) == (mode, evaluator, None)
    if evaluator == "onnx":
        assert seat.generation.number == 1 and player.fields()["generation"]["model_digest"]
    assert re.fullmatch(r"[A-Za-z0-9_-]+", player.slug)


@pytest.mark.parametrize("name, message", [
    ("deep", "not a player"),
    ("heuristic:random", "takes no evaluator"),
    ("full:bogus", "no evaluator 'bogus'"),
    ("onnx:gen7", "no export bundle for gen7"),
])
def test_a_name_that_is_not_a_player_is_refused(name, message, models):
    with pytest.raises(PlayerError, match=message):
        parse_player(name, models)


# The plan


def test_deck_pairs_come_from_the_split_with_no_deck_twice_and_stay_put_for_a_seed():
    splits = {f"h{n}": mf.HELD_OUT for n in range(8)} | {f"t{n}": mf.TRAIN for n in range(8)}
    pairs = sample_deck_pairs(splits, 4, 5)
    decks = [deck for pair in pairs for deck in pair]
    assert len(set(decks)) == 8 and all(deck.startswith("h") for deck in decks)
    assert sample_deck_pairs(splits, 4, 5) == pairs and sample_deck_pairs(splits, 4, 6) != pairs
    assert all(deck.startswith("t") for pair in sample_deck_pairs(splits, 2, 5, mf.TRAIN) for deck in pair)
    assert len(sample_deck_pairs(splits, 8, 5, "both")) == 8
    with pytest.raises(SetupError, match="5 deck pairs need 10 held_out decks, and the manifest has 8"):
        sample_deck_pairs(splits, 5, 5)


def test_every_pair_of_players_gets_each_deck_and_each_seat_on_shared_seeds(models):
    players = tuple(parse_player(name, models) for name in ("full", "heuristic", "onnx:gen0"))
    units = plan_units(players, [("d1", "d2"), ("d3", "d4")], 5, 9)
    assert len(units) == 3 * 2 * 4
    pairs = {unit.players for unit in units}
    assert pairs == {("full", "heuristic"), ("full", "onnx:gen0"), ("heuristic", "onnx:gen0")}
    for players_ in pairs:
        mine = [unit for unit in units if unit.players == players_]
        assert {(unit.pair_index, unit.first_deck, unit.first_seat) for unit in mine} == {
            (1, "d1", 1), (1, "d1", 2), (1, "d2", 1), (1, "d2", 2),
            (2, "d3", 1), (2, "d3", 2), (2, "d4", 1), (2, "d4", 2)}
    # Every pair of players and both seat orders share a seed for a deck pair and assignment.
    for unit in units:
        assert unit.seed == unit_seed(9, unit.pair_index, unit.assignment)
    assert len({unit.seed for unit in units}) == 4
    # Deck pair by deck pair, so a stop leaves every pair of players some games.
    assert [unit.pair_index for unit in units] == [1] * 12 + [2] * 12


# Runs through the stub


def test_a_ladder_rates_the_stronger_configuration_and_writes_its_files(ladder, monkeypatch, launches, tmp_path):
    pattern(monkeypatch, "sim=full:WWWL")  # full sim wins 3 of 4, whatever deck and seat
    result = run_ladder(ladder())
    assert result.complete and len(result.finished_units) == 4
    rated = result.fit
    assert rated.of("full").rating == pytest.approx(STEP) and rated.of("heuristic").rating == 0
    assert [rating.player for rating in rated.ratings] == ["full", "heuristic"]
    pair = result.pairs[0]
    assert (pair.first, pair.second, pair.tally.a_wins, pair.tally.b_wins) == ("full", "heuristic", 12, 4)

    # Each player on each deck, in each seat.
    seen = set()
    for launch in launches():
        argv = launch["argv"]
        decks = argv[argv.index("-d") + 1:argv.index("-d") + 3]
        modes = argv[argv.index("-sim") + 1:argv.index("-sim") + 3]
        seen.add((Path(launch["cwd"]).parent.name, tuple(modes), tuple(decks)))
    assert {(folder, modes) for folder, modes, _ in seen} == {
        ("pair-01-x1-ab", ("full", "heuristic")), ("pair-01-x1-ba", ("heuristic", "full")),
        ("pair-01-x2-ab", ("full", "heuristic")), ("pair-01-x2-ba", ("heuristic", "full"))}

    run_dir = tmp_path / "ladder"
    plan = json.loads((run_dir / PLAN_FILE).read_text(encoding="utf-8"))
    assert plan["seed"] == 11 and plan["pair_games"] == 16 and len(plan["deck_pairs"]) == 1
    assert set(plan["players"]) == {"heuristic", "full"}
    saved = json.loads((run_dir / RESULT_FILE).read_text(encoding="utf-8"))
    assert saved["status"] == "complete" and saved["anchor"] == {"player": "heuristic", "rating": 0.0}
    assert [player["label"] for player in saved["players"]] == ["full", "heuristic"]
    full = saved["players"][0]
    assert full["rank"] == 1 and full["rating"] == pytest.approx(STEP) and full["low"] < STEP < full["high"]
    assert (full["decisive_games"], full["wins"], full["losses"]) == (16, 12, 4)
    assert saved["pairs"][0]["interval"]["rate"] == 0.75 and saved["pairs"][0]["batches_finished"] == 4
    assert saved["batches"]["finished"] == 4 and saved["games_finished"] == 16
    assert len(list((run_dir / "games").glob("*.jsonl.gz"))) == 16
    for folder in ("pair-01-x1-ab", "pair-01-x1-ba", "pair-01-x2-ab", "pair-01-x2-ba"):
        unit = json.loads((run_dir / "batches" / "full--heuristic" / folder / "result.json").read_text(encoding="utf-8"))
        assert unit["status"] == FINISHED and (unit["first_wins"], unit["second_wins"]) == (3, 1)


def test_the_report_gives_ratings_with_intervals_and_game_counts_and_no_bare_percentage(ladder, monkeypatch):
    pattern(monkeypatch, "sim=full:WWWL")
    report = format_ladder_report(run_ladder(ladder()))
    assert re.search(r"1\s+full\s+\+191 \(\+\d+ to \+\d+\)\s+16\s+12\s+4\s+0", report)
    assert re.search(r"2\s+heuristic\s+0 \(the anchor\)\s+16\s+4\s+12\s+0", report)
    assert re.search(r"full\s+heuristic\s+75\.0% \(\d+\.\d% to \d+\.\d%\)\s+16\s+0\s+4 of 4", report)
    assert re.search(r"status\s+complete", report)
    assert_no_bare_percentage(report)


def test_a_new_generation_plays_only_its_own_batches(ladder, monkeypatch, launches):
    pattern(monkeypatch, "seat1:WL")
    run_ladder(ladder(("heuristic", "full")))
    first = len(launches())
    assert first == 4
    result = run_ladder(ladder(("heuristic", "full", "onnx:gen0")))
    assert len(launches()) == first + 8  # gen0 against each of the two, four batches each
    assert result.skipped == 4 and result.complete and len(result.finished_units) == 12
    assert {rating.player for rating in result.fit.ratings} == {"heuristic", "full", "onnx:gen0"}
    # Naming fewer players rates only them, from the batches already played.
    again = run_ladder(ladder(("heuristic", "onnx:gen0")))
    assert len(launches()) == first + 8 and again.skipped == 4
    assert {rating.player for rating in again.fit.ratings} == {"heuristic", "onnx:gen0"}


def test_other_settings_in_a_ladder_folder_are_refused(ladder, monkeypatch):
    pattern(monkeypatch, "seat1:WL")
    run_ladder(ladder())
    with pytest.raises(SetupError, match="other settings, in: pair_games"):
        run_ladder(ladder(pair_games=8))
    with pytest.raises(SetupError, match="other settings, in: seed"):
        run_ladder(ladder(seed=12))
    with pytest.raises(SetupError, match="over 1 deck pairs, not 2"):
        run_ladder(ladder(deck_pairs=2))


def test_a_ladder_folder_refuses_a_change_of_first_player_rule_either_way(ladder, monkeypatch, tmp_path):
    from dataclasses import replace
    pattern(monkeypatch, "seat1:WL")
    stock = ladder()
    flipped = replace(stock, base=replace(stock.base, first_player="random"))
    run_ladder(stock)
    assert "first_player" not in json.loads((stock.run_dir / PLAN_FILE).read_text(encoding="utf-8"))
    with pytest.raises(SetupError, match="other settings, in: first_player"):
        run_ladder(flipped)
    other = replace(flipped, base=replace(flipped.base, run_dir=tmp_path / "flipped"))
    run_ladder(other)
    assert json.loads((other.run_dir / PLAN_FILE).read_text(encoding="utf-8"))["first_player"] == "random"
    with pytest.raises(SetupError, match="other settings, in: first_player"):
        run_ladder(replace(stock, base=replace(stock.base, run_dir=tmp_path / "flipped")))


def test_a_generation_whose_weights_changed_under_its_name_is_refused(ladder, monkeypatch, models):
    pattern(monkeypatch, "seat1:WL")
    run_ladder(ladder(("heuristic", "onnx:gen0")))
    (models / "gen0" / "model.onnx").write_bytes(b"retrained")
    with pytest.raises(SetupError, match="onnx:gen0 isn't the player this ladder knows by that name"):
        run_ladder(ladder(("heuristic", "onnx:gen0")))


def test_once_forge_seats_two_generations_the_ladder_plays_them(ladder, monkeypatch):
    # What lifting the refusal in `onnx_problem` does, with a stand-in for Forge.
    monkeypatch.setattr("simrunner.ladder.onnx_problem", lambda seats: None)
    monkeypatch.setattr("simrunner.ladder.check_setup", lambda config: None)
    # Per batch of 20: gen0 beats heuristic 15 to 5, gen1 beats gen0 15 to 5, and gen1 beats heuristic 18 to 2.
    exact = {("heuristic", "onnx:gen0"): 5, ("onnx:gen0", "onnx:gen1"): 5, ("heuristic", "onnx:gen1"): 2}
    result = run_ladder(ladder(("heuristic", "onnx:gen0", "onnx:gen1"), pair_games=80), known_strengths({}, exact))
    assert not result.unplayable and result.complete and len(result.finished_units) == 12
    rated = result.fit
    assert [rating.player for rating in rated.ratings] == ["onnx:gen1", "onnx:gen0", "heuristic"]
    assert rated.of("onnx:gen0").rating == pytest.approx(STEP)
    assert rated.of("onnx:gen1").rating == pytest.approx(2 * STEP)


def test_known_strengths_come_back_from_a_ladder(ladder, forge):
    truth = {"heuristic": 0, "full": 90, "full:random": -150, "hybrid": 40}
    result = run_ladder(ladder(tuple(truth), deck_pairs=3, pair_games=200, workers=4), known_strengths(truth))
    rated = result.fit
    assert [rating.player for rating in rated.ratings] == ["full", "hybrid", "heuristic", "full:random"]
    for player, rating in truth.items():
        if player != "heuristic":
            assert rated.of(player).low < rating < rated.of(player).high
    assert all(result.player_tally(player).decisive == 3 * 600 for player in truth)


def test_a_chain_of_three_to_one_results_spaces_the_ladder_evenly(ladder, forge):
    # heuristic beats full:random 3 to 1, full beats heuristic 3 to 1, and full beats full:random 9 to 1.
    exact = {("full", "heuristic"): 15, ("full:random", "heuristic"): 5, ("full", "full:random"): 18}
    result = run_ladder(ladder(("heuristic", "full", "full:random"), pair_games=80), known_strengths({}, exact))
    rated = result.fit
    assert rated.of("full").rating == pytest.approx(STEP)
    assert rated.of("full:random").rating == pytest.approx(-STEP)
    assert [rating.player for rating in rated.ratings] == ["full", "heuristic", "full:random"]


def test_a_player_nobody_beat_gets_no_ratings_and_the_command_exits_1(forge, manifest, tmp_path, monkeypatch, capsys):
    pattern(monkeypatch, "sim=full:W")
    args = ["ladder", "--players", "heuristic", "full", "--manifest", str(manifest), "--seed", "3", "--deck-pairs", "1",
            "--pair-games", "8", "--workers", "2", "--clock", "60", "--forge", str(forge),
            "--run-dir", str(tmp_path / "ladder"), "--java", sys.executable, str(STUB)]
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "no ratings: no finite ratings: nobody else ever beat full" in out
    assert re.search(r"full\s+heuristic\s+100\.0% \(", out)
    assert_no_bare_percentage(out)


def test_the_command_rates_and_exits_0(forge, manifest, tmp_path, monkeypatch, capsys):
    pattern(monkeypatch, "eval=random:WLLL;sim=full:WWWL")
    args = ["ladder", "--players", "heuristic", "full", "full:random", "--manifest", str(manifest), "--seed", "3",
            "--deck-pairs", "2", "--pair-games", "16", "--workers", "3", "--clock", "60", "--forge", str(forge),
            "--run-dir", str(tmp_path / "ladder"), "--java", sys.executable, str(STUB)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "ladder" / "report.txt").read_text(encoding="utf-8") == out
    ranks = re.findall(r"^(\d)\s+(\S+)\s+", out, re.M)
    assert ranks == [("1", "full"), ("2", "heuristic"), ("3", "full:random")]
    assert re.search(r"full\s+full:random\s+75\.0% \(", out) and re.search(r"full:random\s+heuristic\s+25\.0% \(", out)
    assert_no_bare_percentage(out)


def test_a_failed_batch_shows_and_a_rerun_plays_only_it(ladder, monkeypatch, launches, manifest):
    pattern(monkeypatch, "seat1:WL")
    config = ladder()
    deck = sample_deck_pairs({d.id: d.split for d in mf.load(manifest).decks}, 1, 11)[0][0]
    path = manifest.parent / "forge" / f"{deck}.dck"
    text = path.read_text(encoding="utf-8")
    path.write_text("[Main]\n60 Island\n", encoding="utf-8")  # won't load
    result = run_ladder(config)
    assert not result.complete and len(result.failed) == 4
    report = format_ladder_report(result)
    assert "Could not load deck" in report and "incomplete, 4 batches not finished" in report
    path.write_text(text, encoding="utf-8")
    before = len(launches())
    again = run_ladder(config)
    assert again.complete and len(launches()) == before + 4


def test_the_hard_stop_leaves_batches_for_the_next_start(ladder, monkeypatch):
    pattern(monkeypatch, "seat1:WL")
    now = datetime.now()
    result = run_ladder(ladder(window=Window(now - timedelta(minutes=1), now + timedelta(seconds=1))))
    assert not result.complete and not result.finished_units
    assert "incomplete" in format_ladder_report(result)


@pytest.mark.parametrize("settings, message", [
    (dict(players=("heuristic",)), "at least two players"),
    (dict(players=("full", "full:forge")), "named twice: full"),
    (dict(pair_games=10), "multiple of 4"),
    (dict(deck_pairs=0), "at least one deck pair"),
    (dict(deck_pairs=4), "4 deck pairs need 8 held_out decks, and the manifest has 6"),
    (dict(players=("full", "hybrid")), "the anchor heuristic isn't one of the players"),
])
def test_bad_settings_fail_before_anything_launches(ladder, settings, message, tmp_path):
    if settings.get("players") == ("full", "hybrid"):
        settings = {**settings, "anchor": "heuristic"}
    with pytest.raises(SetupError, match=message):
        run_ladder(ladder(**settings))
    assert not (tmp_path / "ladder" / "batches").exists()


def test_the_anchor_defaults_to_heuristic_or_the_first_player(ladder):
    assert ladder(("full", "heuristic")).anchor_label == "heuristic"
    assert ladder(("full", "hybrid")).anchor_label == "full"
    assert ladder(("full", "hybrid"), anchor="hybrid").anchor_label == "hybrid"
