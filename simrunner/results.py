"""The results log: one JSON line per generation step, and a Markdown page rendered from all of them.

`results/log.jsonl` is append-only and committed. Each line is what the generation loop measured
for one generation: the data it trained on, the training metrics, the ablation table with kill
criterion 2, the pair test, the export check, the ladder ratings with their intervals and the
convergence verdicts. A step a gate stopped gets a line too, saying where and why, because a failed
gate is a result. `results/log.md` is rebuilt from the whole file after every append, so it never
holds anything the JSON doesn't.

No rate appears without its interval or its counts. See "Generation loop" in docs/forge-build.md.
"""

from __future__ import annotations

import json
from pathlib import Path

LOG_VERSION = 1
LOG_FILE = "log.jsonl"
PAGE_FILE = "log.md"


def read_entries(folder: Path) -> list[dict]:
    path = Path(folder) / LOG_FILE
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_entry(folder: Path, entry: dict) -> Path:
    """Add one line to the log and rebuild the page. The line is written whole, with one write."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=False, separators=(",", ":")) + "\n"
    with open(folder / LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
    page = folder / PAGE_FILE
    fresh = page.with_suffix(".tmp")
    fresh.write_text(render(read_entries(folder)), encoding="utf-8", newline="\n")
    fresh.replace(page)
    return folder / LOG_FILE


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{100 * value:.{digits}f}%"


def _count(value: int | None) -> str:
    return "n/a" if value is None else f"{value:,}"


def _n(count: int | None, word: str) -> str:
    """A count with its noun, `1 game` or `2 games`."""
    return f"{_count(count)} {word}{'' if count == 1 else 's'}"


def _share(part: int | None, whole: int | None) -> str:
    """`part of whole (x%)`: a share always with the counts behind it."""
    if part is None or not whole:
        return f"{_count(part)} of {_count(whole)}"
    return f"{part:,} of {whole:,} ({100 * part / whole:.2f}%)"


def _rate(interval: dict | None, status: str | None = None) -> str:
    """A rate with its interval and games, or why there is none. Never the rate alone."""
    if not interval:
        return f"no rate ({status})" if status else "no rate"
    return (f"{_pct(interval['rate'])} ({_pct(interval['low'])} to {_pct(interval['high'])}, "
            f"{interval['games']:,} games)")


def _table(header: tuple[str, ...], rows: list[tuple]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def _data(data: dict) -> list[str]:
    tripwire = data.get("tripwire") or {}
    timeouts = data.get("ai_timeouts") or {}
    traces = data.get("stack_traces") or {}
    records = data.get("records") or {}
    draws = ", ".join(f"{cause} {n:,}" for cause, n in (data.get("draws") or {}).items() if n) or "none"
    lines = [
        f"- Games: {_share(data.get('games_finished'), data.get('games_planned'))} finished, seed {data.get('seed')}, "
        f"{len(data.get('deck_ids') or []):,} decks, {data.get('chunks', 0):,} chunks "
        f"({', '.join(f'{n} {s}' for s, n in (data.get('chunk_statuses') or {}).items())}). Run folder `{data.get('run_dir')}`.",
        f"- Played by: {data.get('player')}, both seats.",
        f"- Draws: {draws}.",
        f"- Game records: {_count(records.get('files'))} files for {_count(data.get('games_finished'))} finished games, "
        f"{len(records.get('unreadable') or []):,} unreadable.",
        f"- Tripwire: {_share(tripwire.get('games_with_hits'), data.get('games_finished'))} games had hits "
        f"({_n(tripwire.get('hits'), 'hit')}), all left out of training."
        + (" The search's game copies are unchecked." if data.get("search_unchecked") else ""),
    ]
    lines += [f"  - `{s['signature']}`: {_n(s['games'], 'game')}" for s in tripwire.get("signatures") or []]
    lines.append(f"- Forge AI timeouts: {_count(timeouts.get('timeouts'))} in {_n(timeouts.get('games'), 'game')}"
                 + (", all left out of training." if timeouts.get("game_files") is not None else "."))
    lines += [f"  - `{name}`" for name in timeouts.get("game_files") or []]
    lines.append(f"- Stack traces on stderr: {_count(traces.get('total'))}, of which {_count(traces.get('ai_timeouts'))} "
                 f"AI timeouts and {_count(traces.get('other'))} other.")
    lines += [f"  - `{name}`: {n:,}" for name, n in (traces.get("by_exception") or {}).items()]
    hidden = data.get("hiddencheck")
    if hidden:
        lines.append(f"- Record ids: named {hidden.get('named by ids from other games')} hidden cards from other "
                     f"games and {hidden.get('named by ids from the other side')} from the other side, of "
                     f"{hidden.get('hidden cards')} hidden cards.")
    return lines


def _conversion(conversion: dict, join: dict | None) -> list[str]:
    dropped = ", ".join(f"{reason} {n:,}" for reason, n in (conversion.get("dropped") or {}).items()) or "none"
    lines = [f"- Converted: {_count(conversion.get('kept'))} games kept, dropped {dropped}, "
             f"{_count(conversion.get('rejected'))} rejected, {_count(conversion.get('snapshot_rows'))} snapshots."]
    if join:
        lines.append(f"- Join: {_share(join.get('snapshots_dropped'), join.get('snapshots'))} snapshots dropped for an "
                     f"unmatched card, {_n(join.get('unmatched_names'), 'unmatched name')}.")
        lines += [f"  - {u['rows']:,} rows: {u['name']}" for u in join.get("top_unmatched") or []]
    return lines


def _scores(scores: dict | None) -> str:
    if not scores or scores.get("accuracy") is None:
        return "n/a"
    return (f"{_pct(scores['accuracy'], 2)} over {_n(scores['games'], 'game')} ({_n(scores['rows'], 'row')}), "
            f"log loss {scores['log_loss']:.4f}")


def _training(training: dict) -> list[str]:
    return [
        f"- Generation {training.get('generation')}, trained on {_count(training.get('train_games'))} games "
        f"({_count(training.get('train_rows'))} rows), {training.get('epochs')} epochs, seed {training.get('seed')}, "
        f"on {training.get('device')} in {training.get('seconds', 0):,.0f} s.",
        f"- Held-out accuracy: {_scores(training.get('held_out'))}.",
        f"  - held-out games: {_scores(training.get('held_out_games'))}",
        f"  - held-out decks: {_scores(training.get('held_out_decks'))}",
    ]


def _ablation(ablation: dict) -> list[str]:
    def cell(value: float | None, digits: int) -> str:
        return "n/a" if value is None else f"{value:.{digits}f}"

    rows = [(r["run"], cell(r["accuracy"], 2), cell(r["log_loss"], 4), cell(r["vs_full"], 2))
            for r in ablation.get("rows") or []]
    kill = ablation.get("kill_criterion_2") or {}
    verdict = ("passed" if kill.get("passed") else "failed") if kill.get("margin_points") is not None else "no verdict"
    return [*_table(("Run", "Held-out accuracy (%)", "Held-out log loss", "Against full (points)"), rows), "",
            f"Kill criterion 2 {verdict}: full beats no_cards by {cell(kill.get('margin_points'), 2)} points, "
            f"the bar is {kill.get('bar_points')}. Accuracies are over the held-out games above."]


def _pair(pair: dict) -> list[str]:
    lifts = pair.get("lifts") or {}
    names = pair.get("names") or ["first", "second"]
    return [f"- {names[0]} lift {lifts.get('first', 0):+.4f}, {names[1]} lift {lifts.get('second', 0):+.4f}, "
            f"together {lifts.get('pair', 0):+.4f}: {pair.get('verdict')}."]


def _export(export: dict) -> list[str]:
    return [f"- Bundle `{export.get('bundle')}`, model digest `{export.get('model_digest')}`. Export check: "
            f"{export.get('states')} states, largest difference {export.get('max_difference')}, "
            f"threshold {export.get('threshold')}."]


def _rating(player: dict) -> str:
    if player.get("rating") is None:
        return "no rating"
    if player.get("low") is None:
        return f"{player['rating']:+.0f} (anchor)"
    return f"{player['rating']:+.0f} ({player['low']:+.0f} to {player['high']:+.0f})"


def _ladder(ladder: dict) -> list[str]:
    rows = [(p.get("rank") or "-", p["player"], _rating(p), f"{p.get('decisive_games', 0):,}")
            for p in ladder.get("players") or []]
    lines = [f"- Folder `{ladder.get('run_dir')}`, seed {ladder.get('seed')}, anchor {ladder.get('anchor')}, "
             f"{_pct(ladder.get('confidence'), 0)} profile-likelihood intervals, "
             f"{_n(ladder.get('games_finished'), 'game')}, {_count(ladder.get('unplayable_batches'))} of the batches "
             "Forge can't seat yet."]
    if ladder.get("ratings_problem"):
        lines.append(f"- No ratings: {ladder['ratings_problem']}")
    return [*lines, "", *_table(("Rank", "Player", "Elo (interval)", "Decisive games"), rows)]


def _convergence(convergence: dict) -> list[str]:
    before, after = convergence.get("before"), convergence.get("after")
    rows = []
    for m in convergence.get("matchups") or []:
        sides = [_rate((m.get(side) or {}).get("interval"), (m.get(side) or {}).get("status", "not played"))
                 for side in ("before", "after")]
        rows.append((m["slot"], m["deck_a"], m["deck_b"], *sides, m["verdict"]))
    counts = convergence.get("counts") or {}
    return [f"- {before} against {after}: {convergence.get('status')}, {counts.get('converged', 0)} converged, "
            f"{counts.get('moved', 0)} moved, {counts.get('no verdict', 0)} with no verdict. Seed "
            f"{convergence.get('seed')}, target half-width {convergence.get('width_points')} points, "
            f"{convergence.get('rule')}.", "",
            *_table(("Slot", "Deck A", "Deck B", f"{before} win rate", f"{after} win rate", "Verdict"), rows)]


def _steps(steps: list[dict]) -> list[str]:
    def peak(step: dict) -> str:
        return "-" if step.get("peak_mb") is None else f"{step['peak_mb']:,.0f}"

    rows = [(s["step"], s["status"], f"{s.get('seconds') or 0:,.0f}", peak(s), f"`{s.get('log') or '-'}`")
            for s in steps]
    return _table(("Step", "Status", "Seconds", "Peak MB", "Log"), rows)


SECTIONS = (
    ("data", "Data", _data),
    ("training", "Training", _training),
    ("ablation", "Ablations", _ablation),
    ("pair_test", "Pair test", _pair),
    ("export", "Export", _export),
    ("ladder", "Ladder", _ladder),
    ("convergence", "Convergence", _convergence),
)


def render_entry(entry: dict) -> str:
    title = f"## {entry['name']} from {entry['source']}, {entry['status']}"
    lines = [title, "", f"Finished {entry.get('finished_at')}, commit `{entry.get('commit')}`."]
    if entry["status"] != "complete":
        lines += ["", f"Stopped at `{entry.get('stopped_at')}`: {entry.get('why')}"]
    for key, heading, render_section in SECTIONS:
        if entry.get(key):
            lines += ["", f"### {heading}", ""]
            if key == "data":
                lines += render_section(entry[key])
                if entry.get("conversion"):
                    lines += _conversion(entry["conversion"], entry.get("join"))
            else:
                lines += render_section(entry[key])
    if entry.get("steps"):
        lines += ["", "### Steps", "", *_steps(entry["steps"])]
    return "\n".join(lines) + "\n"


def render(entries: list[dict]) -> str:
    head = ["# Results log", "",
            "Rendered from `log.jsonl`, one section per generation step, newest last. Don't edit by hand.", ""]
    return "\n".join(head) + "\n" + "\n".join(render_entry(entry) for entry in entries)
