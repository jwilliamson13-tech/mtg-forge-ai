"""The command line: `python -m cardscript <command> ...`."""

import subprocess
import sys
from pathlib import Path

from cardscript.__main__ import main


def test_show_prints_faces_abilities_tokens_and_numbers(fixture_path, capsys):
    assert main(["show", str(fixture_path("lightning_bolt"))]) == 0
    assert capsys.readouterr().out == (
        "Lightning Bolt\n"
        "  cost: generic=0 R=1\n"
        "  types: Instant\n"
        "  ability 1: spell\n"
        "    tokens: Effect=DealDamage ValidTgts=Any\n"
        "    numbers: NumDmg=3\n"
    )


def test_show_prints_generic_cost_and_pt(fixture_path, capsys):
    main(["show", str(fixture_path("grizzly_bears"))])
    out = capsys.readouterr().out
    assert "  cost: generic=1 G=1\n" in out
    assert "  pt: 2/2\n" in out


def test_show_prints_loyalty_and_defense(fixture_path, capsys):
    main(["show", str(fixture_path("teyo_the_shieldmage"))])
    assert "  loyalty: 5\n" in capsys.readouterr().out
    main(["show", str(fixture_path("invasion_of_dominaria_serra_faithkeeper"))])
    assert "  defense: 5\n" in capsys.readouterr().out


def test_show_reports_a_parse_error_on_one_line(tmp_path, capsys):
    broken = tmp_path / "broken.txt"
    broken.write_text("Name:Broken\nA:SP$ DealDamage | NumDmg 3\n", encoding="utf-8")
    assert main(["show", str(broken)]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "A:SP$ DealDamage | NumDmg 3" in err


def test_module_runs_as_a_script(fixture_path):
    result = subprocess.run(
        [sys.executable, "-m", "cardscript", "show", str(fixture_path("lightning_bolt"))],
        capture_output=True, text=True, encoding="utf-8",
        cwd=Path(__file__).parents[1],
    )
    assert result.returncode == 0
    assert result.stdout.startswith("Lightning Bolt\n")


def test_show_prints_colors_when_the_script_has_them(fixture_path, capsys):
    main(["show", str(fixture_path("r_1_1_goblin"))])
    assert "  colors: red\n" in capsys.readouterr().out
