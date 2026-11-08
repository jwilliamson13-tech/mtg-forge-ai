"""Card swaps: the difference interval against published values, the deck edit, and runs against the stub.

STUB_PATTERN's `has=CARD` subject lets the swapped deck play to its own pattern, so these runs
know both arms' counts in advance.
"""

import json
import re
import sys
from pathlib import Path

import pytest

from simrunner.__main__ import main
from simrunner.batch import BatchConfig, SetupError
from simrunner.cardswap import (CardSwapConfig, Swap, format_cardswap_report, format_points, parse_swap, run_cardswap,
                                swap_deck)
from simrunner.interval import newcombe, wilson
from simrunner.matchup import BUDGET_SPENT, MatchupConfig
from simrunner.output import HEADER_LINE
from simrunner.worker import RecordPlan, Seat

STUB = Path(__file__).parent / "stub_java.py"
RATE = r"\d+\.\d% \(\d+\.\d% to \d+\.\d%\)"
CONFIDENCE_LABEL = r"\d+(\.\d+)?% confidence"
BURN = "[metadata]\nName=Burn\n[Main]\n4 Lightning Bolt\n56 Mountain\n[Sideboard]\n2 Lightning Bolt\n"


# Newcombe, "Interval estimation for the difference between independent proportions: comparison of
# eleven methods", Statistics in Medicine 17 (1998) 873-890, Table II, method 10. Its examples are
# the first proportion minus the second.
@pytest.mark.parametrize("first, second, low, high", [
    ((56, 70), (48, 80), 0.0524, 0.3339),
    ((9, 10), (3, 10), 0.1705, 0.8090),
    ((5, 56), (0, 29), -0.0381, 0.1926),
    ((0, 10), (0, 20), -0.1611, 0.2775),
    ((0, 10), (0, 10), -0.2775, 0.2775),
    ((10, 10), (0, 20), 0.6791, 1.0),
    ((10, 10), (0, 10), 0.6075, 1.0),
    # Fagerland, Lydersen and Laake, "Recommended confidence intervals for two independent binomial
    # proportions", Statistical Methods in Medical Research 24 (2015) 224-254: 0.019 to 0.34.
    ((7, 34), (1, 34), 0.0189, 0.3404),
])
def test_the_difference_interval_matches_published_values(first, second, low, high):
    difference = newcombe(wilson(*first), wilson(*second))
    assert difference.estimate == pytest.approx(first[0] / first[1] - second[0] / second[1])
    assert difference.low == pytest.approx(low, abs=5e-5)
    assert difference.high == pytest.approx(high, abs=5e-5)


def test_the_difference_interval_flips_with_its_sides():
    one, two = wilson(56, 70), wilson(48, 80)
    forward, back = newcombe(one, two), newcombe(two, one)
    assert (back.low, back.high) == pytest.approx((-forward.high, -forward.low))
    assert forward.excludes_zero and not newcombe(wilson(5, 56), wilson(0, 29)).excludes_zero


def test_a_difference_prints_in_points_with_its_interval():
    assert format_points(newcombe(wilson(56, 70), wilson(48, 80))) == "+20.0 points (+5.2 to +33.4 points)"


@pytest.mark.parametrize("text, swap", [
    ("Lightning Bolt=Chain Lightning", Swap("Lightning Bolt", "Chain Lightning")),
    ("2 Lightning Bolt = Chain Lightning", Swap("Lightning Bolt", "Chain Lightning", 2)),
    ("Fire // Ice=Lightning Bolt", Swap("Fire // Ice", "Lightning Bolt")),
])
def test_a_swap_reads_old_new_and_an_optional_count(text, swap):
    assert parse_swap(text) == swap


@pytest.mark.parametrize("text", ["Lightning Bolt", "=Chain Lightning", "0 Lightning Bolt=Chain Lightning",
                                  "Lightning Bolt=lightning bolt", "A=B=C"])
def test_a_swap_that_changes_nothing_or_cant_be_read_is_refused(text):
    with pytest.raises(ValueError):
        parse_swap(text)


def test_every_copy_swaps_in_the_main_deck_only():
    assert swap_deck(BURN, Swap("Lightning Bolt", "Chain Lightning")) == (
        "[metadata]\nName=Burn\n[Main]\n4 Chain Lightning\n56 Mountain\n[Sideboard]\n2 Lightning Bolt\n")


def test_some_copies_swap_across_lines_and_keep_a_set_code():
    deck = "[Main]\n1 Lightning Bolt|M10\n2 lightning bolt\n57 Mountain\n"
    assert swap_deck(deck, Swap("Lightning Bolt", "Chain Lightning", 2)) == (
        "[Main]\n2 Chain Lightning\n1 lightning bolt\n57 Mountain\n")


def test_swapped_copies_join_a_line_of_the_new_card():
    deck = "[Main]\n4 Lightning Bolt\n2 Chain Lightning\n54 Mountain\n"
    assert swap_deck(deck, Swap("Lightning Bolt", "Chain Lightning", 1)) == (
        "[Main]\n3 Lightning Bolt\n3 Chain Lightning\n54 Mountain\n")


def test_a_swap_the_deck_cant_make_is_refused():
    with pytest.raises(SetupError, match="the main deck has no Brainstorm"):
        swap_deck(BURN, Swap("Brainstorm", "Ponder"))
    with pytest.raises(SetupError, match="the main deck has 4 Lightning Bolt, fewer than the 5 to swap"):
        swap_deck(BURN, Swap("Lightning Bolt", "Chain Lightning", 5))


@pytest.fixture
def decks(tmp_path):
    folder = tmp_path / "decks"
    folder.mkdir()
    one = folder / "burn.dck"
    two = folder / "merfolk.dck"
    one.write_text(BURN, encoding="utf-8")
    two.write_text("[metadata]\nName=Merfolk\n[Main]\n60 Island\n", encoding="utf-8")
    return one, two


@pytest.fixture
def cardswap(forge, decks, tmp_path):
    def build(swap=Swap("Lightning Bolt", "Chain Lightning"), width=10.0, max_games=1000, workers=1, **overrides):
        settings = dict(
            seats=(Seat(decks[0], None, "heuristic"), Seat(decks[1], None, "heuristic")),
            games=0, batch_seed=11, workers=workers, forge_root=forge, run_dir=tmp_path / "swap", clock_s=60,
            java=(sys.executable, str(STUB)), records=RecordPlan(),
        )
        return CardSwapConfig(MatchupConfig(BatchConfig(**{**settings, **overrides}), width, 100, max_games), swap)

    return build


def pattern(monkeypatch, text):
    monkeypatch.setenv("STUB_PATTERN", text)


def assert_no_bare_percentage(report):
    rest = re.sub(CONFIDENCE_LABEL, "", re.sub(RATE, "", report))
    assert "%" not in rest, rest


def test_the_report_gives_both_rates_and_the_difference_with_its_interval(cardswap, monkeypatch, tmp_path):
    pattern(monkeypatch, "has=Chain Lightning:WWWWL;Burn:WL")  # the swap takes Burn from 50% to 80%
    result = run_cardswap(cardswap())
    assert result.met
    original, swapped = result.arms["original"], result.arms["swapped"]
    assert original.interval == wilson(50, 100) and swapped.interval == wilson(80, 100)
    assert result.difference == newcombe(wilson(80, 100), wilson(50, 100))
    report = format_cardswap_report(result)
    assert re.search(r"status\s+met, both arms met the target", report)
    assert re.search(r"original\s+met, 50\.0% \(40\.4% to 59\.6%\) over 100 decisive games", report)
    assert re.search(r"swapped\s+met, 80\.0% \(71\.1% to 86\.7%\) over 100 decisive games", report)
    assert re.search(r"difference, A' minus A\s+\+30\.0 points \(\+16\.9 to \+41\.7 points\)", report)
    assert re.search(r"the interval\s+excludes 0", report)
    assert "Newcombe hybrid score" in report
    assert_no_bare_percentage(report)
    saved = json.loads((tmp_path / "swap" / "cardswap.json").read_text(encoding="utf-8"))
    assert saved["status"] == "met" and saved["swap"] == {"old": "Lightning Bolt", "new": "Chain Lightning", "count": None}
    assert saved["difference"]["estimate"] == pytest.approx(0.3)
    assert saved["original"]["interval"]["rate"] == 0.5 and saved["swapped"]["interval"]["rate"] == 0.8


def test_both_arms_play_the_same_seeds_and_seats_with_only_the_card_changed(cardswap, monkeypatch, tmp_path):
    pattern(monkeypatch, "has=Chain Lightning:WWWWL;Burn:WL")
    launches = tmp_path / "launches"
    launches.mkdir()
    monkeypatch.setenv("STUB_RECORD_DIR", str(launches))
    run_cardswap(cardswap(workers=2, seats=(Seat(tmp_path / "decks" / "burn.dck", "Reckless", "full", "random"),
                                            Seat(tmp_path / "decks" / "merfolk.dck", None, "heuristic"))))
    seen = {}
    for path in launches.glob("*.json"):
        launch = json.loads(path.read_text(encoding="utf-8"))
        argv = launch["argv"]
        arm, order = Path(launch["cwd"]).parents[2].name, Path(launch["cwd"]).parent.name
        decks = Path(launch["cwd"]) / "decks"
        seen.setdefault((arm, order), []).append((
            argv[argv.index("-s") + 1], argv[argv.index("-a") + 1:argv.index("-a") + 3],
            argv[argv.index("-eval") + 1:argv.index("-eval") + 3], argv[argv.index("-batch") + 1]))
    assert sorted(seen) == [(arm, order) for arm in ("original", "swapped") for order in ("round-001-ab", "round-001-ba")]
    for order in ("round-001-ab", "round-001-ba"):
        original, swapped = sorted(seen[("original", order)]), sorted(seen[("swapped", order)])
        assert [row[:3] for row in original] == [row[:3] for row in swapped]
        assert {row[3] for row in original} != {row[3] for row in swapped}
    assert ["Reckless", "Default"] in [row[1] for row in seen[("swapped", "round-001-ab")]]
    assert ["forge", "random"] in [row[2] for row in seen[("swapped", "round-001-ba")]]
    written = (tmp_path / "swap" / "swapped.dck").read_text(encoding="utf-8")
    assert "4 Chain Lightning\n" in written and "[Sideboard]\n2 Lightning Bolt\n" in written


def test_an_original_arm_that_misses_its_target_leaves_the_swap_unplayed(cardswap, monkeypatch, tmp_path):
    pattern(monkeypatch, "has=Chain Lightning:WWWWL;Burn:WL")
    result = run_cardswap(cardswap(width=5.0, max_games=200))
    assert not result.met and result.difference is None
    assert result.arms["original"].status == BUDGET_SPENT and "swapped" not in result.arms
    report = format_cardswap_report(result)
    assert re.search(r"status\s+refused, original budget spent: no difference", report)
    assert re.search(r"swapped\s+not run, because the original arm did not meet its target", report)
    assert "difference, A' minus A" not in report and not re.search(RATE, report)
    assert_no_bare_percentage(report)
    assert not (tmp_path / "swap" / "swapped").exists()
    saved = json.loads((tmp_path / "swap" / "cardswap.json").read_text(encoding="utf-8"))
    assert saved["difference"] is None and saved["swapped"] is None


def test_a_swapped_arm_that_misses_its_target_gives_no_difference(cardswap, monkeypatch):
    # 50% meets 10 points in 100 games. The swapped arm draws 3 games in 5, so 100 give 40 decisive.
    pattern(monkeypatch, "has=Chain Lightning:WLDDD;Burn:WL")
    result = run_cardswap(cardswap(max_games=100))
    assert result.arms["original"].met and not result.arms["swapped"].met
    assert result.difference is None
    assert re.search(r"refused, swapped budget spent", format_cardswap_report(result))


def test_a_swap_the_deck_cant_make_fails_before_anything_launches(cardswap, tmp_path):
    with pytest.raises(SetupError, match="no Brainstorm"):
        run_cardswap(cardswap(swap=Swap("Brainstorm", "Ponder")))
    assert not (tmp_path / "swap" / "original").exists()


def test_a_run_folder_with_a_card_swap_in_it_is_refused(cardswap, monkeypatch):
    pattern(monkeypatch, "has=Chain Lightning:WWWWL;Burn:WL")
    run_cardswap(cardswap())
    with pytest.raises(SetupError, match="already holds a card swap"):
        run_cardswap(cardswap())


def cli_args(decks, forge, run_dir, *more):
    return ["cardswap", str(decks[0]), str(decks[1]), "--swap", "Lightning Bolt=chain lightning", "--seed", "3",
            "--workers", "1", "--clock", "60", "--width", "10", "--forge", str(forge), "--run-dir", str(run_dir),
            "--java", sys.executable, str(STUB), *more]


def test_the_command_checks_the_new_card_and_exits_by_whether_both_arms_met(decks, forge, tmp_path, monkeypatch,
                                                                            capsys):
    pattern(monkeypatch, "has=Chain Lightning:WWWWL;Burn:WL")
    cards = tmp_path / "cards.jsonl"
    cards.write_text("".join(json.dumps({"faces": [{"name": name}], "file": "x"}) + "\n"
                             for name in ("Lightning Bolt", "Chain Lightning", "Mountain")), encoding="utf-8")
    assert main(cli_args(decks, forge, tmp_path / "met", "--cards", str(cards))) == 0
    out = capsys.readouterr().out
    assert re.search(r"difference, A' minus A\s+\+30\.0 points", out)
    assert (tmp_path / "met" / "report.txt").read_text(encoding="utf-8") == out
    # The card list spells it as Forge does.
    assert "4 Chain Lightning\n" in (tmp_path / "met" / "swapped.dck").read_text(encoding="utf-8")

    args = cli_args(decks, forge, tmp_path / "unknown", "--cards", str(cards))
    args[args.index("--swap") + 1] = "Lightning Bolt=Chain Lightnin"
    with pytest.raises(SystemExit) as stop:
        main(args)
    assert stop.value.code == 2 and "not a card Forge knows: Chain Lightnin" in capsys.readouterr().err

    assert main(cli_args(decks, forge, tmp_path / "short", "--cards", str(tmp_path / "none.jsonl"),
                         "--width", "5", "--max-games", "100")) == 1
    err = capsys.readouterr().err
    assert "is unchecked" in err and "no difference (original budget spent)" in err
