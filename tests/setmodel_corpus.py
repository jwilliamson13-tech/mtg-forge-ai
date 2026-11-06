"""A tiny hand-built corpus: game files in Forge's format, a card dump from real scripts, a manifest.

Games go through `gamerecord convert` like real ones, so the tests read the same tables a
real run does. Each `snapshot` is one line of a game file, written from one side.
"""

import copy
import gzip
import json
from pathlib import Path

from cardscript import parse_card
from cardscript.vocab import build_vocabulary
from gamerecord.convert import convert_folder
from setmodel.features import CardTable

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPTS = ("lightning_bolt", "grizzly_bears", "silver_knight", "fire_ice", "r_1_1_goblin", "snapcaster_mage", "platinum_angel")
BATCH = "smoke"
TRAIN_DECKS = ("Red", "Green")
HELD_OUT_DECK = "Blue"


def ambiguous_token(power: int, keyword: str | None = None) -> str:
    keyword_line = f"K:{keyword}\n" if keyword else ""
    return f"Name:Elemental Token\nManaCost:no cost\nTypes:Creature Elemental\nPT:{power}/{power}\n{keyword_line}"


def dump_lines() -> list[dict]:
    lines = []
    for name in SCRIPTS:
        text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
        lines.append({"file": f"cardsfolder/{name}.txt", **parse_card(text).to_dict()})
    for n, text in enumerate((ambiguous_token(1, "Haste"), ambiguous_token(3, "Haste"), ambiguous_token(5))):
        lines.append({"file": f"tokenscripts/elemental_{n}.txt", **parse_card(text).to_dict()})
    return lines


def write_dump(path: Path) -> Path:
    path.write_text("".join(json.dumps(line) + "\n" for line in dump_lines()), encoding="utf-8")
    return path


def card_table() -> CardTable:
    return CardTable(dump_lines())


def vocabulary():
    return build_vocabulary(dump_lines())


def card(name, controller, *, face="Original", id=1, power=None, toughness=None, hidden=False, **fields):
    line = {"id": id, "name": None if hidden else name, "face": None if hidden else face, "power": power,
            "toughness": toughness, "tapped": False, "counters": {}, "summoning_sick": False, "attached_to": None,
            "attached_to_seat": None, "controller": controller, "face_down": hidden, "damage": 0, "attacking_seat": None,
            "attacking_card": None, "blocking": [], "blocked_by": []}
    return {**line, **fields}


def snapshot(side, *, life=20, turn=1, battlefield=(), hand=(), graveyard=(), revealed=(), stack=()):
    return {
        "side": side,
        "scoreboard": {"me_life": life, "opp_life": 20, "me_poison": 0, "opp_poison": 0, "me_library": 40,
                       "opp_library": 40, "opp_hand": 5, "turn": turn, "me_active": side == 1, "me_on_play": side == 1},
        "zones": {"battlefield": list(battlefield), "me_hand": list(hand), "me_graveyard": list(graveyard),
                  "opp_graveyard": [], "me_exile": [], "opp_exile": [], "opp_revealed": list(revealed)},
        "stack": list(stack),
    }


def game(number, snapshots, *, winner=1, decks=TRAIN_DECKS, final_turn=9, seed=7, profile="Default"):
    seats = [{"seat": seat, "deck": deck, "ai_profile": profile, "sim_mode": "heuristic", "search_budget": 0}
             for seat, deck in zip((1, 2), decks)]
    metadata = {"schema_version": 1, "batch_id": BATCH, "seed": seed, "game_number": number, "seats": seats,
                "winner": winner, "final_turn": final_turn, "end_reason": "win", "honest_play": True,
                "tripwire": {"enabled": True, "hits": 0, "signatures": []}}
    return [metadata, *snapshots]


def write_parquet(games: list[list[dict]], root: Path) -> Path:
    """Write these games as files, convert them, and return the Parquet folder."""
    folder = root / "games"
    folder.mkdir(parents=True, exist_ok=True)
    for n, lines in enumerate(games):
        text = "".join(json.dumps(line) + "\n" for line in lines)
        (folder / f"game_{n}.jsonl.gz").write_bytes(gzip.compress(text.encode("utf-8")))
    report = convert_folder(folder, root / "parquet")
    assert not report.rejected, report.rejected
    return root / "parquet"


def write_manifest(path: Path) -> Path:
    decks = [{"id": deck, "file": f"decks/forge/{deck}.dck", "source": "synthetic", "archetype": deck, "split": "train"}
             for deck in TRAIN_DECKS]
    decks.append({"id": HELD_OUT_DECK, "file": "decks/forge/Blue.dck", "source": "synthetic", "archetype": "Blue",
                  "split": "held_out"})
    path.write_text(json.dumps({"version": 1, "decks": decks, "tracked_matchups": [None] * 4}), encoding="utf-8")
    return path


def manifest() -> dict[str, str]:
    return {**dict.fromkeys(TRAIN_DECKS, "train"), HELD_OUT_DECK: "held_out"}


def smoke_games(count: int = 16) -> list[list[dict]]:
    """Games a model can learn: the winner has bears on the table and more life.

    Every third game has the held-out deck in a seat.
    """
    games = []
    for number in range(1, count + 1):
        winner = 1 if number % 2 else 2
        decks = (HELD_OUT_DECK, TRAIN_DECKS[1]) if number % 3 == 0 else TRAIN_DECKS
        snapshots = []
        for turn in range(1, 4):
            bears = [card("Grizzly Bears", winner, id=10 + n, power=2, toughness=2) for n in range(turn)]
            bolt = card("Lightning Bolt", 3 - winner, id=30)
            for side in (1, 2):
                snapshots.append(snapshot(side, life=20 if side == winner else 20 - 3 * turn, turn=turn,
                                          battlefield=copy.deepcopy(bears), graveyard=[bolt] if side == 3 - winner else []))
        games.append(game(number, snapshots, winner=winner, decks=decks))
    return games
