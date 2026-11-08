"""A stand-in for every command the generation loop runs, so its tests play no Forge and train nothing.

The loop is told to start Python as `python stub_loop.py`, so each step arrives here as
`-m MODULE COMMAND ARGS...`. The stub appends the call to the file STUB_LOOP_CALLS names, one
JSON line each, then writes what the real command leaves for the loop's gates to read, and
prints what the real command prints, at the same line formats.

STUB_LOOP holds a JSON object of knobs. Without it every step succeeds:

- `fail`: a step whose command exits 3 with a stack trace and writes nothing, as a crash does.
- `sleep`: {step: seconds} to sleep first, so a test can kill a step while it runs.
- `pool_incomplete`: the pool ends with a failed chunk and exits 1.
- `tripwire_games`: games with a tripwire hit (default 1), from the first game on.
- `hidden_named`: hiddencheck names a hidden card and exits 1.
- `rejected`: files convert rejects, which makes it exit 1.
- `dropped`: snapshots join drops for an unmatched card, of 1,000 (default 3).
- `margin`: points full beats no_cards by in the ablation (default 7.5).
- `export_fail`: the export check misses its threshold and exits 1.
- `ladder_incomplete`, `converge_unended`: those leave a batch or a matchup unfinished, and exit 1.
- `moved`: tracked-matchup slots whose rate moved (default none).
"""

import gzip
import json
import os
import sys
import time
from pathlib import Path

STEPS = {("simrunner", "pool"): "play", ("gamerecord", "hiddencheck"): "hiddencheck",
         ("gamerecord", "convert"): "convert", ("setmodel", "join"): "join", ("setmodel", "train"): "train",
         ("setmodel", "ablate"): "ablate", ("setmodel", "pairtest"): "pairtest", ("setmodel", "export"): "export",
         ("simrunner", "ladder"): "ladder", ("simrunner", "converge"): "converge"}
DECKS = ["burn-1", "elves-1", "held0", "held1"]
TRACKED = [["held0", "held1"], ["held0", "held2"], ["held3", "held4"], ["held3", "held5"]]


def option(args, flag, default=None, count=1):
    if flag not in args:
        return default
    at = args.index(flag)
    return args[at + 1] if count == 1 else args[at + 1:at + 1 + count]


def players(args, flag):
    """The values after `flag`, up to the next option."""
    at = args.index(flag) + 1
    out = []
    while at < len(args) and not args[at].startswith("--"):
        out.append(args[at])
        at += 1
    return out


def write_json(path, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields, indent=2), encoding="utf-8")


def game_file(folder, batch, seed, number, hits):
    signatures = ["forge.ai.ability.PlayAi.chooseSingleCard:173"] if hits else []
    metadata = {"schema_version": 1, "batch_id": batch, "seed": seed, "game_number": number,
                "seats": [], "winner": 1, "final_turn": 5, "end_reason": "win", "honest_play": True,
                "tripwire": {"enabled": True, "hits": hits, "signatures": signatures}}
    path = Path(folder) / f"{batch}_{seed}_{number}_burn-1_vs_elves-1.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps(metadata) + "\n{}\n")


def pool(args, knobs):
    run_dir = Path(option(args, "--run-dir"))
    games, seed = int(option(args, "--games")), int(option(args, "--seed"))
    modes = option(args, "--sim-mode", ["heuristic", "heuristic"], 2)
    evaluators = option(args, "--evaluator", None, 2)
    generations = [{"name": value.removeprefix("onnx:"), "generation": 0} for value in evaluators] if evaluators else None
    half = games // 2
    chunks = [{"index": 0, "split": "train", "seat1": DECKS[0], "seat2": DECKS[1], "games": games - half, "seed": 11},
              {"index": 1, "split": "held_out", "seat1": DECKS[2], "seat2": DECKS[3], "games": half, "seed": 12}]
    plan = {"seed": seed, "games": games, "sim_modes": modes, "tripwire": True, "honest": True, "chunks": chunks}
    if generations:
        plan.update(evaluators=["onnx", "onnx"], generations=generations)
    held = run_dir / "plan.json"
    if held.is_file() and {k: v for k, v in json.loads(held.read_text()).items()} != plan:
        print("holds a run with a different plan", file=sys.stderr)
        return 2
    write_json(held, plan)
    games_folder = run_dir / "games"
    games_folder.mkdir(parents=True, exist_ok=True)
    tripwire_games = knobs.get("tripwire_games", 1)
    number = 0
    for chunk in chunks:
        folder = run_dir / "chunks" / f"chunk-{chunk['index']:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        failed = knobs.get("pool_incomplete") and chunk["index"] == 1
        out, err = [], []
        for game in range(1, chunk["games"] + 1):
            number += 1
            hits = 1 if number <= tripwire_games else 0
            if chunk["index"] == 0 and game == 2:  # the eval thread times out twice in this game
                out += ["AI eval thread at timeout:", "\tat forge.ai.AiController.canPlaySa(AiController.java:1024)"] * 2
                err += ["java.util.concurrent.TimeoutException", "\tat java.base/FutureTask.get(FutureTask.java:204)"] * 2
            out += [f"Tripwire: {hits} hits", f"Game Result: Game {game} ended in 100 ms. Ai(1)-x has won!", ""]
            if not failed:
                game_file(games_folder, run_dir.name, chunk["seed"], game, hits)
        (folder / "worker-00.out.log").write_text("\n".join(out) + "\n", encoding="utf-8")
        (folder / "worker-00.err.log").write_text("\n".join(err) + "\n", encoding="utf-8")
        result = {"index": chunk["index"], "status": "failed" if failed else "finished",
                  "games_finished": 0 if failed else chunk["games"], "wins": 0 if failed else chunk["games"],
                  "draws": {"rules": 0, "clock": 0, "turn_cap": 0, "exception": 0},
                  "tripwire_hits": [], "stack_traces": len(err) // 2, "records_written": 0 if failed else chunk["games"],
                  "note": "the JVM died" if failed else ""}
        write_json(folder / "result.json", result)
    finished = games - (half if knobs.get("pool_incomplete") else 0)
    write_json(run_dir / "batch.json", {"batch": run_dir.name, "status": "incomplete" if knobs.get("pool_incomplete")
                                        else "complete", "stopped_at_hard_stop": False, "games_attempted": games,
                                        "games_finished": finished, "chunks": 2,
                                        "chunks_failed": 1 if knobs.get("pool_incomplete") else 0, "wall_s": 1.0})
    print(f"games finished {finished}")
    return 1 if knobs.get("pool_incomplete") else 0


def hiddencheck(args, knobs):
    named = 1 if knobs.get("hidden_named") else 0
    print(f"games 10\nhidden cards 4\nnamed by ids from other games {named}\nnamed by ids from the other side 0\n"
          "matched by public state to the other side's row 4")
    return 1 if named else 0


def convert(args, knobs):
    out = Path(args[2])
    (out / "snapshots" / "batch_id=x").mkdir(parents=True, exist_ok=True)
    rejected = knobs.get("rejected", 0)
    tripwire = knobs.get("tripwire_games", 1)
    print(f"kept 9\n  win: 9\ndropped {tripwire}\n  tripwire: {tripwire}\n  tripwire game x-1-1: 1 hits\n"
          f"    forge.ai.ability.PlayAi.chooseSingleCard:173\nrejected {rejected}\nsnapshot rows 1000\n"
          "card rows 20000\nstack rows 100")
    return 1 if rejected else 0


def join(args, knobs):
    dropped = knobs.get("dropped", 3)
    print(f"games 9, 9 with rows left\nsnapshots 1000, {dropped} dropped for an unmatched card\n"
          f"card and stack rows 20000\nhidden cards 4\nunmatched names 1, {dropped} rows\n"
          f"  {dropped}  Summon: Fenrir (58)'s Effect (Original)\n"
          "names on several scripts, shared features only: 0, 0 rows")
    return 0


def scores(accuracy, games):
    return {"log_loss": 0.6, "accuracy": accuracy, "rows": games * 50, "games": games}


def train(args, knobs):
    out = Path(option(args, "--out"))
    generation = int(option(args, "--generation", 0))
    out.mkdir(parents=True, exist_ok=True)
    (out / "model.pt").write_bytes(b"weights")
    write_json(out / "metrics.json", {
        "generation": generation, "batch_ids": ["pool"], "ablation": "full", "train_rows": 400, "train_games": 8,
        "torch": "2.14.1", "device": "cpu", "held_out": scores(0.62, 2), "held_out_games": scores(0.6, 1),
        "held_out_decks": scores(0.64, 1), "epoch_loss": [0.69, 0.65], "seconds": 3.5,
        "config": {"generation": generation, "epochs": int(option(args, "--epochs", 10)),
                   "seed": int(option(args, "--seed", 0))}})
    print("device cpu")
    return 0


def ablate(args, knobs):
    out = Path(option(args, "--out"))
    margin = knobs.get("margin", 7.5)
    rows = [{"run": "full", "accuracy": 62.0, "log_loss": 0.6, "vs_full": 0.0},
            {"run": "no_cards", "accuracy": 62.0 - margin, "log_loss": 0.68, "vs_full": -margin},
            {"run": "no_graveyard_exile", "accuracy": 61.0, "log_loss": 0.61, "vs_full": -1.0},
            {"run": "no_live_state", "accuracy": 60.0, "log_loss": 0.62, "vs_full": -2.0},
            {"run": "no_numbers", "accuracy": 59.5, "log_loss": 0.63, "vs_full": -2.5}]
    outcome = "carries" if margin >= 5 else "does not carry"
    verdict = (f"Full model beats no cards by {margin:.2f} points. "
               f"The card encoding {outcome} information by the 5 point bar.")
    write_json(out / "ablation.json", {"rows": rows, "verdict": verdict})
    print(verdict)
    return 0


def pairtest(args, knobs):
    print("empty board      0.5000\nPlatinum Angel alone  0.5600\nLightning Greaves alone  0.5100\n"
          "both             0.5900\nlifts            +0.0600, +0.0100, pair +0.0900\n"
          "verdict          the pair's lift exceeds the sum of the single lifts: the model sees the interaction")
    return 0


def export(args, knobs):
    model, out = Path(args[1]), Path(option(args, "--out"))
    generation = json.loads((model / "metrics.json").read_text())["generation"]
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "contract.json", {"version": 1, "bundle": {"generation": generation}})
    (out / "model.onnx").write_bytes(f"onnx {generation} {time.time()}".encode())
    difference = 0.5 if knobs.get("export_fail") else 1.2e-07
    print(f"wrote {out}: 10 card entries\nexport check: 300 states, max difference {difference:.3g}, threshold 0.0001")
    return 1 if knobs.get("export_fail") else 0


def ladder(args, knobs):
    labels = players(args, "--players")
    run_dir = Path(option(args, "--run-dir"))
    entries = [{"label": label, "rank": rank, "rating": 0.0 if label == "heuristic" else 40.0 * rank,
                "low": None if label == "heuristic" else 40.0 * rank - 30, "high": None if label == "heuristic"
                else 40.0 * rank + 30, "decisive_games": 240, "wins": 120, "losses": 120, "draws": 0}
               for rank, label in enumerate(labels, 1)]
    incomplete = knobs.get("ladder_incomplete")
    write_json(run_dir / "ladder.json", {
        "status": "incomplete" if incomplete else "complete", "seed": int(option(args, "--seed")), "split": "held_out",
        "deck_pairs": [["held0", "held1"]], "pair_games": 40, "confidence": 0.95,
        "anchor": {"player": "heuristic", "rating": 0.0}, "method": "Bradley-Terry", "ratings_problem": None,
        "players": entries, "pairs": [], "games_finished": 240 * len(labels),
        "batches": {"failed": ["b1"] if incomplete else [], "stopped": [], "not_started": [],
                    "unplayable": [{"batch": "gen-vs-gen", "why": "one bundle per JVM"}]},
        "tripwire_hits": 0, "tripwire_clean": True})
    return 1 if incomplete else 0


def estimate(label, wins, status="met"):
    interval = {"rate": wins / 400, "low": wins / 400 - 0.049, "high": wins / 400 + 0.049, "games": 400}
    return {"player": label, "status": status, "interval": interval if status == "met" else None,
            "a_wins": wins, "b_wins": 400 - wins, "draws": 0, "games_finished": 400, "more_games_needed": None,
            "reused": False}


def converge(args, knobs):
    before, after = args[1], args[2]
    run_dir = Path(option(args, "--run-dir"))
    moved = knobs.get("moved", [])
    rows = []
    for slot, (a, b) in enumerate(TRACKED, 1):
        after_status = "stopped" if knobs.get("converge_unended") and slot == 4 else "met"
        rows.append({"slot": slot, "deck_a": a, "deck_b": b,
                     "verdict": "moved" if slot in moved else "converged", "why": "",
                     "before": estimate(before, 200), "after": estimate(after, 250 if slot in moved else 210,
                                                                        after_status), "difference": None})
    counts = {"converged": 4 - len(moved), "moved": len(moved), "no verdict": 0}
    write_json(run_dir / "convergence.json", {
        "status": "moved" if moved else "converged", "before": {"label": before}, "after": {"label": after},
        "rule": "converged when the two Wilson intervals overlap", "seed": int(option(args, "--seed")),
        "width_points": 5.0, "confidence": 0.95, "max_games": 4000, "counts": counts, "matchups": rows})
    return 1 if knobs.get("converge_unended") else 0


def main(argv):
    assert argv[0] == "-m", argv
    module, command, args = argv[1], argv[2], argv[2:]
    step = STEPS[(module, command)]
    knobs = json.loads(os.environ.get("STUB_LOOP", "{}"))
    if calls := os.environ.get("STUB_LOOP_CALLS"):
        with open(calls, "a", encoding="utf-8") as f:
            f.write(json.dumps({"step": step, "argv": argv}) + "\n")
    time.sleep(knobs.get("sleep", {}).get(step, 0))
    if knobs.get("fail") == step:
        print(f"Traceback (most recent call last):\nRuntimeError: {step} fell over", file=sys.stderr)
        return 3
    return globals()[step if step != "play" else "pool"](args, knobs)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
