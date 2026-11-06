"""The batch report: one summary table, then one row per worker."""

from __future__ import annotations

from simrunner.batch import BatchResult
from simrunner.output import DrawCause
from simrunner.worker import DEFAULT_SIM_MODE, Seat, WorkerResult

# HANDOFF.md: the nightly window is 23:00 to 07:00, and kill criterion 1 is about 500 usable games a night.
OVERNIGHT_HOURS = 8
KILL_CRITERION_GAMES = 500
TOP_SIGNATURES = 20


def _table(rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
                     for row in rows) + "\n"


def _seat(seat: Seat) -> str:
    return f"{seat.deck.stem} (AI {seat.ai_profile or 'default'}, sim {seat.sim_mode or DEFAULT_SIM_MODE})"


def _count(value: int | None) -> str:
    return "not reported" if value is None else str(value)


def _status(batch: BatchResult) -> str:
    if batch.complete:
        return "complete"
    return "incomplete, stopped at the hard stop" if batch.stopped else "incomplete"


def _tripwire_rows(batch: BatchResult) -> list[tuple[str, str]]:
    if not batch.tripwire_on:
        return [("tripwire", "off")]
    hits = batch.tripwire_hits
    if not hits:
        return [("tripwire", "no game reported, so the jar may be stale")]
    dirty = sum(count > 0 for count in hits)
    missing = batch.finished - len(hits)
    verdict = "yes" if batch.tripwire_clean else f"no, {dirty} of {batch.finished} games had hits"
    if missing:
        verdict += f", {missing} reported nothing"
    return [("tripwire hits per game", f"max {max(hits)}, mean {sum(hits) / len(hits):.2f}"), ("tripwire clean", verdict)]


def format_summary(batch: BatchResult) -> str:
    config = batch.config
    startups = batch.startup_times
    startup_mean = sum(startups) / len(startups) if startups else 0.0
    overnight = batch.steady_usable_per_hour * (OVERNIGHT_HOURS - startup_mean / 3600)
    window = []
    if config.window:
        window = [("window", f"{config.window.start} to {config.window.stop}"), ("status", _status(batch))]
    rows = [
        *window,
        ("seat 1", _seat(config.seats[0])),
        ("seat 2", _seat(config.seats[1])),
        ("batch seed", str(config.batch_seed)),
        ("workers", str(len(batch.workers))),
        ("clock", f"{config.clock_s} s"),
        ("search budget", f"{batch.search_budget} steps per decision" if batch.search_budget else "off"),
        ("games attempted", str(batch.attempted)),
        ("games finished", str(batch.finished)),
        ("games not finished", str(batch.not_finished)),
        ("wins seat 1", str(batch.wins(1))),
        ("wins seat 2", str(batch.wins(2))),
        ("draws", str(batch.draw_count)),
    ]
    rows += [(f"  by {cause.value}", str(batch.draws(cause))) for cause in DrawCause]
    if config.records:
        rows.append(("game records written", f"{batch.records_written} of {batch.finished} games"))
        rows += _tripwire_rows(batch)
        rows.append(("record digest", batch.record_digest or "no files"))
    rows += [
        ("wall time", f"{batch.wall_s:.1f} s"),
        ("games per hour", f"{batch.games_per_hour:.0f}"),
        ("usable games per hour", f"{batch.usable_per_hour:.0f}"),
        ("startup per worker", f"mean {startup_mean:.1f} s, max {max(startups, default=0.0):.1f} s"),
        ("steady-state games per hour", f"{batch.steady_games_per_hour:.0f}"),
        (f"usable games in {OVERNIGHT_HOURS} h, steady rate",
         f"{overnight:.0f} (kill criterion 1 needs about {KILL_CRITERION_GAMES})"),
        ("AI decision timeouts", str(sum(worker.ai_timeouts for worker in batch.workers))),
        ("AI deadline expiries", _count(batch.deadline_expiries)),
        ("AI search budget stops", _count(batch.budget_stops)),
        ("AI stops that played a line", _count(batch.stops_played)),
        ("AI stops that passed", _count(batch.stops_passed)),
        ("AI stops with a land candidate", _count(batch.stops_with_land)),
        ("AI land drops missed on stops", _count(batch.land_drops_missed)),
        ("stack traces on stderr", str(sum(worker.stderr_traces for worker in batch.workers))),
        ("result digest", batch.digest),
    ]
    return _table(rows)


def _worker_note(batch: BatchResult, worker: WorkerResult) -> str:
    notes = []
    if worker.timed_out:
        notes.append("killed at deadline")
    if worker.stopped:
        notes.append("killed at hard stop")
    if worker.left_behind:
        notes.append("left a process running that still holds its pipes, so check for java")
    if not worker.profile_used:
        notes.append("no log in its throwaway profile")
    if worker.ready_s is not None and not batch.played_requested_modes(worker):
        notes.append("Forge did not name the sim modes asked for, so the jar may be stale")
    if not batch.applied_search_budget(worker):
        notes.append("Forge did not report on the search budget, so the jar may be stale")
    if not batch.wrote_records(worker):
        notes.append("a game with no game record, so the jar may be stale")
    if not batch.reported_tripwire(worker):
        notes.append("a game with no tripwire count, so the jar may be stale")
    if worker.fatal:
        notes.append(worker.fatal)
    return "; ".join(notes)


def format_workers(batch: BatchResult) -> str:
    rows = [("worker", "seed", "games", "finished", "startup s", "last game s", "exit", "note")]
    for worker in batch.workers:
        rows.append((
            str(worker.plan.index), str(worker.plan.seed), str(worker.plan.games), str(len(worker.games)),
            "-" if worker.ready_s is None else f"{worker.ready_s:.1f}",
            "-" if worker.last_result_s is None else f"{worker.last_result_s:.1f}",
            str(worker.exit_code), _worker_note(batch, worker),
        ))
    return _table(rows)


def format_tripwire(batch: BatchResult) -> str:
    """The signatures to fix first. Every one is in the game records too."""
    signatures = batch.tripwire_signatures()
    if not signatures:
        return ""
    rows = [("hits", "games", "signature")]
    rows += [(str(hits), str(games), signature) for signature, hits, games in signatures[:TOP_SIGNATURES]]
    more = len(signatures) - TOP_SIGNATURES
    return "\n" + _table(rows) + (f"and {more} more\n" if more > 0 else "")


def format_report(batch: BatchResult) -> str:
    return format_summary(batch) + "\n" + format_workers(batch) + format_tripwire(batch)
