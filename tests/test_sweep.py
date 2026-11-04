"""`python -m cardscript sweep`: parse a folder of scripts and report coverage."""

import json
import shutil

import pytest

import cardscript.sweep
from cardscript import Card, parse_card
from cardscript.__main__ import main
from cardscript.sweep import SweepReport
from cardscript.vocab import build_vocabulary

GOOD = ["lightning_bolt", "grizzly_bears", "grim_lavamancer", "fire_ice", "cryptic_command"]
BROKEN = "Name:Broken\nA:SP$ DealDamage | NumDmg 3\n"
BAD_TRIGGER = "Name:Bad Trigger\nT:DB$ Draw\n"


@pytest.fixture
def cards(tmp_path, fixture_path):
    """A card folder: five good scripts, two failing the same way, one failing
    another way, and a script in each excluded sub-folder."""
    root = tmp_path / "cardsfolder"
    for i, name in enumerate(GOOD):
        folder = root / "abcde"[i]
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_path(name), folder / f"{name}.txt")
    (root / "b" / "broken.txt").write_text(BROKEN, encoding="utf-8")
    (root / "c" / "broken_too.txt").write_text(BROKEN.replace("Broken", "Broken Too"), encoding="utf-8")
    (root / "d" / "bad_trigger.txt").write_text(BAD_TRIGGER, encoding="utf-8")
    for excluded in ("rebalanced", "upcoming"):
        (root / excluded).mkdir()
        shutil.copy(fixture_path("lightning_bolt"), root / excluded / "a-lightning_bolt.txt")
    return root


def report(capsys) -> str:
    return capsys.readouterr().out


def test_counts_scripts_parsed_and_percentage(cards, capsys):
    assert main(["sweep", str(cards)]) == 0
    out = report(capsys)
    assert "scripts   8\n" in out
    assert "parsed    5\n" in out
    assert "coverage  62.50%\n" in out


def test_groups_failures_by_cause_most_common_first(cards, capsys):
    main(["sweep", str(cards)])
    out = report(capsys)
    causes = out.split("failures by cause:\n")[1].splitlines()
    assert causes[0].split() == ["2", "segment", "without", "Key$", "(cardsfolder/b/broken.txt)"]
    assert causes[1].split() == ["1", "unknown", "ability", "API", "'DB'", "(cardsfolder/d/bad_trigger.txt)"]
    assert len(causes) == 2


def test_top_limits_the_causes_listed(cards, capsys):
    main(["sweep", str(cards), "--top", "1"])
    causes = report(capsys).split("failures by cause:\n")[1].splitlines()
    assert causes[0].split()[:2] == ["2", "segment"]
    assert causes[1:] == ["  ... 1 more, raise --top to list them"]


def test_skips_rebalanced_and_upcoming_by_default(cards, capsys):
    main(["sweep", str(cards)])
    assert "a-lightning_bolt" not in report(capsys)


def test_no_exclude_sweeps_every_sub_folder(cards, capsys):
    main(["sweep", str(cards), "--no-exclude"])
    assert "scripts   10\n" in report(capsys)


def test_exclude_replaces_the_default_list(cards, capsys):
    main(["sweep", str(cards), "--exclude", "upcoming", "--exclude", "b"])
    out = report(capsys)
    # a, c, d, e and rebalanced remain: 4 good + 1 broken + 1 bad trigger + 1 rebalanced.
    assert "scripts   7\n" in out
    assert "parsed    5\n" in out


def test_only_txt_files_count(cards, capsys):
    (cards / ".gitattributes").write_text("*.txt text eol=lf\n", encoding="utf-8")
    (cards / "mkzip.sh").write_text("zip\n", encoding="utf-8")
    main(["sweep", str(cards)])
    assert "scripts   8\n" in report(capsys)


def test_several_folders_sweep_as_one(cards, tmp_path, fixture_path, capsys):
    tokens = tmp_path / "tokenscripts"
    tokens.mkdir()
    shutil.copy(fixture_path("r_1_1_goblin"), tokens / "r_1_1_goblin.txt")
    main(["sweep", str(cards), str(tokens)])
    out = report(capsys)
    assert "scripts   9\n" in out
    assert "parsed    6\n" in out


def test_out_writes_one_json_line_per_parsed_card(cards, tmp_path, capsys):
    out = tmp_path / "cards.jsonl"
    main(["sweep", str(cards), "--out", str(out)])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    records = [json.loads(line) for line in lines]
    assert [r["file"] for r in records] == [
        "cardsfolder/a/lightning_bolt.txt",
        "cardsfolder/b/grizzly_bears.txt",
        "cardsfolder/c/grim_lavamancer.txt",
        "cardsfolder/d/fire_ice.txt",
        "cardsfolder/e/cryptic_command.txt",
    ]
    assert Card.from_json(lines[3]).faces[1].name == "Ice"
    assert build_vocabulary(records).index("Effect=DealDamage") > 2


def test_out_matches_parse_card(cards, tmp_path, script, capsys):
    out = tmp_path / "cards.jsonl"
    main(["sweep", str(cards), "--out", str(out)])
    first = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert Card.from_dict(first) == parse_card(script("lightning_bolt"))


def test_min_coverage_exits_non_zero_below_the_threshold(cards, capsys):
    assert main(["sweep", str(cards), "--min-coverage", "95"]) == 1
    assert "62.50% is below --min-coverage 95" in capsys.readouterr().err


def test_min_coverage_exits_zero_at_or_above_the_threshold(cards, capsys):
    assert main(["sweep", str(cards), "--min-coverage", "62.5"]) == 0


def test_reads_utf8_with_bom_and_crlf(tmp_path, script, capsys):
    root = tmp_path / "cards"
    root.mkdir()
    text = "﻿" + script("lightning_bolt").replace("\n", "\r\n")
    (root / "bolt.txt").write_bytes(text.encode("utf-8"))
    out = tmp_path / "cards.jsonl"
    main(["sweep", str(root), "--out", str(out)])
    assert "parsed    1\n" in report(capsys)
    card = Card.from_json(out.read_text(encoding="utf-8"))
    assert card.faces[0].name == "Lightning Bolt"
    assert card.faces[0].warnings == []


def test_any_other_exception_is_a_failure_grouped_by_its_type(tmp_path, fixture_path, monkeypatch, capsys):
    root = tmp_path / "cards"
    root.mkdir()
    shutil.copy(fixture_path("lightning_bolt"), root / "bolt.txt")
    (root / "not_utf8.txt").write_bytes(b"Name:Caf\xe9\n")
    for name in ("x", "y"):
        (root / f"{name}.txt").write_text(f"Name:{name}\n", encoding="utf-8")

    # No real script raises anything but ParseError, so a stand-in parse_card
    # is the only way to reach the sweep's catch-all branch.
    real = cardscript.sweep.parse_card

    def flaky(text):
        if text.startswith("Name:x") or text.startswith("Name:y"):
            raise KeyError("boom")
        return real(text)

    monkeypatch.setattr(cardscript.sweep, "parse_card", flaky)
    assert main(["sweep", str(root)]) == 0
    out = report(capsys)
    assert "scripts   4\n" in out
    assert "parsed    1\n" in out
    causes = out.split("failures by cause:\n")[1].splitlines()
    assert causes[0].split() == ["2", "unexpected", "KeyError", "(cards/x.txt)"]
    assert causes[1].split() == ["1", "unexpected", "UnicodeDecodeError", "(cards/not_utf8.txt)"]


def test_a_missing_folder_is_an_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit:
        main(["sweep", str(tmp_path / "nope")])
    assert exit.value.code == 2
    assert "nope" in capsys.readouterr().err


def test_an_empty_folder_reports_zero_coverage(tmp_path, capsys):
    assert main(["sweep", str(tmp_path), "--min-coverage", "95"]) == 1
    out = report(capsys)
    assert "scripts   0\n" in out
    assert "coverage  0.00%\n" in out


def test_coverage_rounds_down_so_100_means_every_script_parsed():
    assert "coverage  99.99%\n" in SweepReport(seen=34702, parsed=34701).format()
    assert "coverage  100.00%\n" in SweepReport(seen=3, parsed=3).format()
    assert "coverage  33.33%\n" in SweepReport(seen=3, parsed=1).format()
