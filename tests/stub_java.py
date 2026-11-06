"""A stand-in for `java -jar forge.jar sim ...`, so sim-runner tests need no Java.

It prints what real `forge sim -q` prints, captured from Forge at commit da66fb1
plus the `-sim` flag, and exits 0 whatever happened, as Forge does. Like Forge, it reads
`forge.profile.properties` from its working directory, opens a log in the user
folder, and refuses a deck file that won't load.

STUB_SCENARIO picks what the games do. STUB_RECORD_DIR, when set, receives one JSON
file per run with the arguments and surroundings the stub saw.

With `-record` it writes one game file per game, named as Forge names them, except for
what Forge would skip: a game that threw, and a game the clock stopped before its first
priority pass. The `stale_jar` scenario ignores the flag, as a jar from before it would.
"""

import gzip
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

DRAW_RESULT = "\nGame Result: Game {n} ended in a Draw! Took 2100 ms.\n"
WON = "has won because all opponents have lost"
GAME_FIXTURE = Path(__file__).parent / "fixtures" / "games" / "win.jsonl"


def profile_values() -> dict:
    lines = Path("forge.profile.properties").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines)


def deck_name(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for line in text.splitlines():
        if line.startswith("Name="):
            return line.removeprefix("Name=")
    return None


def option(args: list[str], flag: str, count: int = 1) -> list[str]:
    at = args.index(flag)
    return args[at + 1:at + 1 + count]


def record(argv: list[str]) -> None:
    folder = os.environ.get("STUB_RECORD_DIR")
    if folder:
        seed = option(argv, "-s")[0]
        Path(folder, f"{seed}.json").write_text(json.dumps({
            "argv": argv,
            "cwd": os.getcwd(),
            "appdata": os.environ.get("APPDATA"),
            "localappdata": os.environ.get("LOCALAPPDATA"),
            "profile": profile_values(),
            "res_marker": Path("res/marker.txt").exists(),
            "status_at_launch": json.loads(Path("../batch.json").read_text(encoding="utf-8")),
        }), encoding="utf-8")


def outcome_lines(turn: int, first: str, second: str) -> None:
    print("Game Outcome: Match Duration 0m : 02s")
    print(f"Game Outcome: Turn {turn}")
    print(f"Game Outcome: {first}")
    print(f"Game Outcome: {second}")


def print_ai_limits(argv: list[str], scenario: str, seed: int, n: int) -> None:
    """Forge prints this per game with `-sim` or `-budget`, and a stale jar never does."""
    if "-sim" in argv or "-budget" in argv:
        if scenario not in ("stale_jar", "no_budget_jar"):
            expiries = 2 if scenario == "deadlines" else 0
            stops = 1 if scenario == "budget_stops" else 0
            line = f"AI limits: {expiries} deadline expiries, {stops} budget stops, {seed % 1000 + n} random draws"
            if scenario == "stops":
                line += ", 3 stops played, 1 stops passed, 2 stops with a land, 1 land drops missed"
            print(line)


def play_win(n: int, players: list[str], rng: random.Random) -> None:
    winner = rng.randrange(2)
    won = f"{players[winner]} {WON}"
    lost = f"{players[1 - winner]} has lost because life total reached 0"
    outcome_lines(rng.randint(4, 12), *((won, lost) if winner == 0 else (lost, won)))
    print(f"\nGame Result: Game {n} ended in 2679 ms. {players[winner]} has won!\n")


def play_forced_end(n: int, players: list[str]) -> None:
    """Both players show as winners, and Forge names one of them in the result line."""
    outcome_lines(6, f"{players[0]} {WON}", f"{players[1]} {WON}")
    print(f"\nGame Result: Game {n} ended in 1013 ms. {players[1]} has won!\n")


def play_rules_draw(n: int, players: list[str]) -> None:
    outcome_lines(9, f"{players[0]} has lost because life total reached 0",
                  f"{players[1]} has lost because life total reached 0")
    print(DRAW_RESULT.format(n=n))


def play_turn_cap(n: int, players: list[str]) -> None:
    print("Stopping game at the turn cap as draw")
    outcome_lines(51, f"{players[0]} has drawn", f"{players[1]} has drawn")
    print(DRAW_RESULT.format(n=n))


def write_game_file(argv: list[str], n: int, scenario: str) -> None:
    if "-record" not in argv or scenario in ("stale_jar", "clock", "exception", "no_game_files"):
        return
    folder = Path(option(argv, "-record")[0])
    name = f"{option(argv, '-batch')[0]}_{option(argv, '-s')[0]}_{n}_seat1_vs_seat2.jsonl.gz"
    with gzip.open(folder / name, "wb") as game:
        game.write(GAME_FIXTURE.read_bytes())


def main() -> int:
    argv = sys.argv[1:]
    scenario = os.environ.get("STUB_SCENARIO", "wins")
    profile = profile_values()
    if scenario != "no_log":
        Path(profile["userDir"]).mkdir(parents=True, exist_ok=True)
        Path(profile["userDir"], "forge.log").write_text("Error handling registered!\n", encoding="utf-8")
    record(argv)
    print("Error handling registered!", flush=True)
    print("Simulation mode", flush=True)

    deck_dir = Path(option(argv, "-D")[0])
    names = []
    for deck in option(argv, "-d", 2):
        name = deck_name(deck_dir / deck)
        if name is None:
            print(f"Could not load deck - {deck}, match cannot start")
            return 0
        names.append(name)
    if "-a" in argv and "Bogus" in option(argv, "-a", 2):
        print("Unknown AI profile - Bogus. Available profiles: Default, Reckless")
        return 0

    modes = None
    if "-sim" in argv and scenario != "stale_jar":
        modes = option(argv, "-sim", 2)
        if "Bogus" in modes:
            print("Unknown simulation mode - Bogus. Available modes: heuristic, hybrid, full")
            return 0

    games = int(option(argv, "-n")[0])
    seed = int(option(argv, "-s")[0])
    players = [f"Ai({seat})-{name}" for seat, name in enumerate(names, 1)]
    if modes:
        players = [f"{player} [sim={mode}]" for player, mode in zip(players, modes)]
    print(f"{players[0]} vs {players[1]} - {games} games of Constructed seed {seed}", flush=True)

    rng = random.Random(seed)
    for n in range(1, games + 1):
        print_ai_limits(argv, scenario, seed, n)
        if scenario == "hang":
            time.sleep(600)
        elif scenario == "hang_in_child":
            # What Oracle's `java` launcher on PATH does: the real JVM is a child process.
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
            Path(os.environ["STUB_RECORD_DIR"], f"child-{seed}.pid").write_text(str(child.pid), encoding="utf-8")
            child.wait()
        elif scenario == "launcher_dies":
            # The launcher goes, and the JVM it started keeps the pipes open.
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(8)"],
                             stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr)
            return 1
        elif scenario == "cut_off_after_one" and n == 2:
            # A hard stop lands mid-game, here partway through printing the result line.
            print("Game Outcome: Turn 3")
            print(f"Game Result: Game {n} ended in 26", end="", flush=True)
            time.sleep(600)
        elif scenario == "dies_after_one" and n == 2:
            print("java.lang.OutOfMemoryError: Java heap space", file=sys.stderr)
            return 1
        elif scenario == "clock":
            print("Stopping slow match as draw")
            play_forced_end(n, players)
        elif scenario == "exception":
            print("java.lang.NullPointerException: boom", file=sys.stderr)
            print("\tat forge.game.Game.fake(Game.java:1)", file=sys.stderr)
            play_forced_end(n, players)
        elif scenario == "rules_draw":
            play_rules_draw(n, players)
        elif scenario == "turn_cap":
            play_turn_cap(n, players)
        else:
            play_win(n, players, rng)
        write_game_file(argv, n, scenario)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
