"""What a finished pool run folder holds, read back before its games go to training.

The pool report counts games, draws and tripwire hits. Training needs more than counts: which
games the tripwire caught and on what signature, so they can be left out and the peek fixed;
how many games had a Forge AI decision time out; what the stack traces on stderr were; and
whether every finished game left a record. This reads `plan.json`, each chunk's `result.json`
and worker logs, and the metadata line of every game file, and returns it as one dict.

The 2026-11-07 gen-0 pool is the case it was written for: 10,000 games, 1 with a tripwire hit,
and 418 stack traces that were all `TimeoutException`s from the AI's eval thread in one chunk.
See "Generation loop" in docs/forge-build.md.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from gamerecord.record import GameRecordError, read_metadata
from simrunner.batch import STATUS_FILE
from simrunner.output import AI_TIMEOUT_LINE, TRACE_HEAD_LINE
from simrunner.pool import CHUNKS_FOLDER, PLAN_FILE, RESULT_FILE

GAMES_FOLDER = "games"
GAME_FILES = "*.jsonl.gz"
TIMEOUT_TRACE = "java.util.concurrent.TimeoutException"  # what the eval thread's timeout leaves on stderr
RESULT_LINE = re.compile(r"^Game Result: Game (\d+) ")
LISTED = 20  # games named one by one in the dict, past which only the count grows


class PoolCheckError(ValueError):
    """A run folder that isn't a pool run, or can't be read as one."""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PoolCheckError(f"can't read {path}: {e}") from e


def _lines(path: Path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line.rstrip("\r\n")


def game_timeouts(out_log: Path) -> dict[int, int]:
    """AI timeouts per finished game in one worker's stdout, by game number. A game's lines end at its result line."""
    per_game, count = {}, 0
    for line in _lines(out_log):
        if line.startswith(AI_TIMEOUT_LINE):
            count += 1
        elif match := RESULT_LINE.match(line):
            if count:
                per_game[int(match[1])] = count
            count = 0
    return per_game


def trace_heads(err_log: Path) -> Counter:
    """Stack traces on one worker's stderr, by the exception named on their first line."""
    heads = Counter()
    for line in _lines(err_log):
        if TRACE_HEAD_LINE.match(line):
            heads[line.split(":", 1)[0].strip()] += 1
    return heads


def check_pool(run_dir: Path) -> dict:
    """Everything the generation loop records about a pool run's data. Raises `PoolCheckError` on a non-pool folder."""
    run_dir = Path(run_dir)
    plan = _read_json(run_dir / PLAN_FILE)
    status = _read_json(run_dir / STATUS_FILE)
    chunks = plan.get("chunks") or []
    decks = sorted({deck for chunk in chunks for deck in (chunk["seat1"], chunk["seat2"])})

    statuses, notes = Counter(), []
    finished = wins = records = 0
    draws = Counter()
    timeouts, heads = [], Counter()
    for chunk in chunks:
        folder = run_dir / CHUNKS_FOLDER / f"chunk-{chunk['index']:04d}"
        result_path = folder / RESULT_FILE
        if not result_path.is_file():
            statuses["not started"] += 1
            continue
        result = _read_json(result_path)
        statuses[result["status"]] += 1
        finished += result.get("games_finished", 0)
        wins += result.get("wins", 0)
        records += result.get("records_written", 0)
        draws.update(result.get("draws") or {})
        if result.get("note"):
            notes.append({"chunk": folder.name, "status": result["status"], "note": result["note"]})
        for log in sorted(folder.glob("worker-*.out.log")):
            timeouts += [(folder.name, number, count) for number, count in game_timeouts(log).items()]
        for log in sorted(folder.glob("worker-*.err.log")):
            heads.update(trace_heads(log))

    end_reasons, unreadable, tripwire_games = Counter(), [], []
    tripwire_off = hits = 0
    files = sorted((run_dir / GAMES_FOLDER).glob(GAME_FILES))
    for path in files:
        try:
            metadata = read_metadata(path)
            end_reasons[metadata["end_reason"]] += 1
            tripwire = metadata["tripwire"]
            if not tripwire["enabled"]:
                tripwire_off += 1
            if tripwire["hits"]:
                hits += tripwire["hits"]
                tripwire_games.append({
                    "game_id": f"{metadata['batch_id']}-{metadata['seed']}-{metadata['game_number']}",
                    "file": path.name, "end_reason": metadata["end_reason"], "hits": tripwire["hits"],
                    "signatures": list(tripwire["signatures"])})
        except (GameRecordError, KeyError, TypeError) as e:
            unreadable.append({"file": path.name, "why": str(e)})

    signatures = Counter(signature for game in tripwire_games for signature in game["signatures"])
    games_with_timeouts = len(timeouts)
    timeout_traces = heads.get(TIMEOUT_TRACE, 0)
    return {
        "run_dir": str(run_dir),
        "status": status.get("status"),
        "seed": plan.get("seed"),
        "games_planned": plan.get("games"),
        "games_finished": finished,
        "wins": wins,
        "draws": dict(sorted(draws.items())),
        "chunks": len(chunks),
        "chunk_statuses": dict(sorted(statuses.items())),
        "chunk_notes": notes,
        "deck_ids": decks,
        "sim_modes": plan.get("sim_modes"),
        "evaluators": plan.get("evaluators"),
        "generations": plan.get("generations"),
        "honest": plan.get("honest"),
        "records": {"written": records, "files": len(files), "unreadable": unreadable,
                    "end_reasons": dict(sorted(end_reasons.items()))},
        "tripwire": {"enabled": plan.get("tripwire"), "games_without_it": tripwire_off, "hits": hits,
                     "games_with_hits": len(tripwire_games), "games": tripwire_games[:LISTED],
                     "signatures": [{"signature": s, "games": n} for s, n in signatures.most_common()]},
        "ai_timeouts": {"games": games_with_timeouts, "timeouts": sum(count for _, _, count in timeouts),
                        "worst": [{"chunk": chunk, "game_number": number, "timeouts": count}
                                  for chunk, number, count in sorted(timeouts, key=lambda t: -t[2])[:LISTED]]},
        "stack_traces": {"total": sum(heads.values()), "ai_timeouts": timeout_traces,
                         "other": sum(heads.values()) - timeout_traces,
                         "by_exception": dict(heads.most_common())},
    }
