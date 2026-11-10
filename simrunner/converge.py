"""The convergence check: the four tracked matchups under two AI configurations, and whether each one moved.

Each tracked matchup from the manifest runs as a matchup (`simrunner.matchup`) with the same player
in both seats, once for the player before and once for the player after, to the same target
width on the same seed. The verdict reads the difference, after minus before, with Newcombe's
hybrid score interval (method 10, the one `simrunner cardswap` uses). A matchup converged when
that interval contains 0: the stronger AI didn't move the number beyond what chance explains, so
the number is about the decks. It moved when the interval excludes 0. It has no verdict when
either side spent its budget or didn't finish.

This replaced a test that two Wilson intervals overlap. Overlap is lenient: two intervals can
overlap while the difference between them is clearly nonzero, so it let moves of up to about 10
points at 400 games a side pass as converged (see "Convergence" in docs/forge-build.md).

A matchup's folder is named for its player and its decks, so a later check reuses it. When the
generation loop goes from gen 1 against gen 0 to gen 2 against gen 1, gen 1's four matchups are
already played. A folder is reused only when its matchup ended (target met or budget spent) under
the same settings. One that failed or was cut off by the hard stop is set aside and played again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from deckpool import manifest as mf
from simrunner.batch import SetupError, check_setup, find_jar, write_json
from simrunner.cardswap import format_points
from simrunner.interval import Difference, Interval, newcombe
from simrunner.matchup import (BUDGET_SPENT, MET, ORDERS, MatchupConfig, MatchupResult, check_matchup,
                               format_matchup_report, format_rate, result_fields, round_config, round_sizes,
                               run_matchup)
from simrunner.players import Player
from simrunner.report import table
from simrunner.worker import STOCK_FIRST_PLAYER

RESULT_FILE = "convergence.json"
MATCHUP_FILE = "matchup.json"
CONVERGED = "converged"
MOVED = "moved"
NO_VERDICT = "no verdict"
INCOMPLETE = "incomplete"
ENDED = (MET, BUDGET_SPENT)  # a matchup that ended this way gives the same answer if played again
# The matchup.json fields that decide its games and its interval. A reused matchup has to match them.
SETTINGS = ("seed", "workers", "width_points", "confidence", "round_games", "max_games", "clock_s", "search_budget",
            "records", "honest", "turn_cap", "tripwire", "ai_steps", "first_player")
SIDE_SETTINGS = ("deck", "ai_profile", "sim_mode", "evaluator", "generation")


@dataclass(frozen=True)
class ConvergeConfig:
    matchup: MatchupConfig  # seats unused; run_dir is the convergence folder, batch_seed every matchup's seed
    before: Player
    after: Player
    manifest: Path

    @property
    def run_dir(self) -> Path:
        return self.matchup.batch.run_dir


@dataclass(frozen=True)
class Estimate:
    """One player's run of one tracked matchup, read from its matchup.json."""

    player: str
    folder: Path
    status: str
    interval: Interval | None  # only when the target was met
    a_wins: int
    b_wins: int
    draws: int
    games_finished: int
    more_games_needed: int | None
    reused: bool  # played before this check started

    @property
    def met(self) -> bool:
        return self.status == MET and self.interval is not None


@dataclass(frozen=True)
class Verdict:
    slot: int  # from 1, the manifest's slot
    deck_a: str
    deck_b: str
    before: Estimate | None  # None when it never ran, because the check stopped first
    after: Estimate | None

    @property
    def verdict(self) -> str:
        difference = self.difference
        if difference is None:
            return NO_VERDICT
        return MOVED if difference.excludes_zero else CONVERGED

    @property
    def difference(self) -> Difference | None:
        """After minus before, with Newcombe's interval. None without both rates."""
        if not (self.before and self.after and self.before.met and self.after.met):
            return None
        return newcombe(self.after.interval, self.before.interval)

    @property
    def why(self) -> str:
        if self.verdict == CONVERGED:
            return "the difference interval contains 0"
        if self.verdict == MOVED:
            return "the difference interval excludes 0"
        missing = [estimate for estimate in (self.before, self.after) if estimate and not estimate.met]
        if missing:
            return "; ".join(f"{estimate.player} {_short_status(estimate)}" for estimate in missing)
        return "not played, the check stopped first"


def _short_status(estimate: Estimate) -> str:
    if estimate.status == BUDGET_SPENT and estimate.more_games_needed is not None:
        return f"spent its budget, about {estimate.more_games_needed} more games needed"
    return estimate.status


def overall(verdicts: list[Verdict]) -> str:
    """Moved if any matchup moved, whatever else is missing. Converged only when all four converged."""
    labels = [verdict.verdict for verdict in verdicts]
    if MOVED in labels:
        return MOVED
    if NO_VERDICT in labels:
        return INCOMPLETE
    return CONVERGED


def tracked_matchups(manifest: mf.Manifest) -> list[tuple[str, str]]:
    """The four tracked matchups, deck A first. Refuses while any slot is empty."""
    empty = [str(slot) for slot, pair in enumerate(manifest.tracked_matchups, 1) if pair is None]
    if empty:
        raise SetupError(f"the manifest's tracked-matchup slots {', '.join(empty)} of {len(manifest.tracked_matchups)} "
                         "are empty; convergence runs on all four, so fill them first")
    return [tuple(pair) for pair in manifest.tracked_matchups]


def matchup_folder(config: ConvergeConfig, player: Player, pair: tuple[str, str]) -> Path:
    return config.run_dir / player.slug / f"{pair[0]}--{pair[1]}"


def matchup_config(config: ConvergeConfig, player: Player, pair: tuple[str, str], decks: dict[str, Path]) -> MatchupConfig:
    base = config.matchup.batch
    folder = matchup_folder(config, player, pair)
    batch = replace(base, seats=(player.seat(decks[pair[0]]), player.seat(decks[pair[1]])), run_dir=folder,
                    batch_name=f"{config.run_dir.name}-{player.slug}-{pair[0]}-{pair[1]}",
                    records=replace(base.records, folder=None))
    return replace(config.matchup, batch=batch)


def _settings(fields: dict) -> dict:
    return {**{key: fields.get(key) for key in SETTINGS},
            **{f"{side}.{key}": (fields.get(side) or {}).get(key) for side in ("side_a", "side_b") for key in SIDE_SETTINGS}}


def _estimate(player: Player, folder: Path, fields: dict, reused: bool) -> Estimate:
    interval = fields.get("interval")
    return Estimate(player.label, folder, fields["status"],
                    interval and Interval(interval["rate"], interval["low"], interval["high"], interval["games"],
                                          fields["confidence"]),
                    fields["a_wins"], fields["b_wins"], sum(fields["draws"].values()), fields["games_finished"],
                    fields.get("more_games_needed"), reused)


def _set_aside(folder: Path) -> Path:
    """Move an unfinished matchup out of the way, keeping it to look at."""
    number = 1
    while (aside := folder.with_name(f"{folder.name}-unfinished-{number}")).exists():
        number += 1
    folder.rename(aside)
    return aside


def _plan(config: ConvergeConfig, pairs: list[tuple[str, str]], decks: dict[str, Path]):
    """For each player and pair: a reused estimate, or the matchup to play. Refuses before anything moves."""
    reused, to_play, unfinished = {}, [], []
    for player in (config.before, config.after):
        for pair in pairs:
            matchup = matchup_config(config, player, pair, decks)
            path = matchup.batch.run_dir / MATCHUP_FILE
            if path.is_file():
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("status") in ENDED:
                    expected = _settings(result_fields(MatchupResult(matchup, [])))
                    held = _settings(saved)
                    differs = sorted(key for key in expected if held[key] != expected[key])
                    if differs:
                        raise SetupError(f"{matchup.batch.run_dir} holds {player.label}'s matchup under other settings, "
                                         f"in: {', '.join(differs)}. Use another --run-dir, or move that folder")
                    reused[(player.label, pair)] = _estimate(player, matchup.batch.run_dir, saved, True)
                    continue
                unfinished.append(matchup.batch.run_dir)
            elif matchup.batch.run_dir.exists() and any(matchup.batch.run_dir.iterdir()):
                unfinished.append(matchup.batch.run_dir)
            to_play.append((player, pair, matchup))
    return reused, to_play, unfinished


@dataclass
class ConvergeResult:
    config: ConvergeConfig
    verdicts: list[Verdict]
    set_aside: list[Path]

    @property
    def status(self) -> str:
        return overall(self.verdicts)

    @property
    def counts(self) -> dict[str, int]:
        labels = [verdict.verdict for verdict in self.verdicts]
        return {label: labels.count(label) for label in (CONVERGED, MOVED, NO_VERDICT)}


def check_converge(config: ConvergeConfig) -> None:
    if config.before.label == config.after.label:
        raise SetupError(f"convergence compares two players, and both are {config.before.label}")
    if config.matchup.batch.records is None:
        raise SetupError("convergence matchups need records, for honest play and the result check")


def run_converge(config: ConvergeConfig) -> ConvergeResult:
    """Play what the folder doesn't hold yet, the player before first, then give each matchup its verdict."""
    check_converge(config)
    try:
        manifest = mf.load(config.manifest)
        pairs = tracked_matchups(manifest)
        decks = {deck: mf.deck_path(manifest, deck, mf.root_of(config.manifest)) for pair in pairs for deck in pair}
    except (OSError, mf.ManifestError) as e:
        raise SetupError(str(e))
    reused, to_play, unfinished = _plan(config, pairs, decks)
    for _, _, matchup in to_play:
        if matchup.batch.run_dir in unfinished:
            # Its folder is set aside below, once every check has passed, so check the settings alone.
            check_matchup(replace(matchup, batch=replace(matchup.batch, run_dir=config.run_dir / "not-a-folder")))
        else:
            check_matchup(matchup)
        check_setup(round_config(matchup, 1, ORDERS[0], round_sizes(matchup.round_games, matchup.max_games)[0]))
    if to_play:
        find_jar(config.matchup.batch.forge_root)
    set_aside = [_set_aside(folder) for folder in unfinished]
    estimates = dict(reused)
    result = ConvergeResult(config, [], set_aside)

    def verdicts() -> list[Verdict]:
        return [Verdict(slot, *pair, estimates.get((config.before.label, pair)), estimates.get((config.after.label, pair)))
                for slot, pair in enumerate(pairs, 1)]

    result.verdicts = verdicts()
    config.run_dir.mkdir(parents=True, exist_ok=True)
    write_json(config.run_dir / RESULT_FILE, converge_fields(result))
    for player, pair, matchup in to_play:
        played = run_matchup(matchup)
        (matchup.batch.run_dir / "report.txt").write_text(format_matchup_report(played), encoding="utf-8", newline="\n")
        estimates[(player.label, pair)] = _estimate(player, matchup.batch.run_dir, result_fields(played), False)
        result.verdicts = verdicts()
        write_json(config.run_dir / RESULT_FILE, converge_fields(result))
        window = matchup.batch.window
        if window and window.closed:
            break  # the rest would refuse at once; the next start plays them
    return result


def _interval_fields(interval: Interval | None) -> dict | None:
    return interval and {"rate": interval.rate, "low": interval.low, "high": interval.high, "games": interval.games}


def _estimate_fields(estimate: Estimate | None) -> dict | None:
    if estimate is None:
        return None
    return {"player": estimate.player, "status": estimate.status, "folder": str(estimate.folder),
            "reused": estimate.reused, "interval": _interval_fields(estimate.interval), "a_wins": estimate.a_wins,
            "b_wins": estimate.b_wins, "draws": estimate.draws, "games_finished": estimate.games_finished,
            "more_games_needed": estimate.more_games_needed}


def converge_fields(result: ConvergeResult) -> dict:
    """`convergence.json`: both estimates and the verdict per tracked matchup, and the overall verdict."""
    config = result.config
    matchup = config.matchup
    batch = matchup.batch
    rows = []
    for verdict in result.verdicts:
        difference = verdict.difference
        rows.append({
            "slot": verdict.slot, "deck_a": verdict.deck_a, "deck_b": verdict.deck_b, "verdict": verdict.verdict,
            "why": verdict.why, "before": _estimate_fields(verdict.before), "after": _estimate_fields(verdict.after),
            "difference": difference and {"estimate": difference.estimate, "low": difference.low,
                                          "high": difference.high, "excludes_zero": difference.excludes_zero,
                                          "method": "Newcombe hybrid score, after minus before"},
        })
    return {
        "status": result.status,
        "before": config.before.fields(),
        "after": config.after.fields(),
        "rule": "converged when the Newcombe interval for after minus before contains 0",
        "manifest": str(config.manifest),
        "seed": batch.batch_seed,
        "workers": batch.workers,
        "width_points": matchup.width,
        "confidence": matchup.confidence,
        "round_games": matchup.round_games,
        "max_games": matchup.max_games,
        "clock_s": batch.clock_s,
        "search_budget": batch.search_budget,
        "turn_cap": batch.records.turn_cap,
        "honest": batch.records.honest,
        "tripwire": batch.tripwire,
        **({"ai_steps": batch.ai_steps} if batch.ai_steps else {}),
        **({"first_player": batch.first_player} if batch.first_player != STOCK_FIRST_PLAYER else {}),
        "counts": result.counts,
        "matchups": rows,
        "set_aside": [str(folder) for folder in result.set_aside],
    }


def _estimate_text(estimate: Estimate | None) -> str:
    if estimate is None:
        return "not played"
    if estimate.met:
        return format_rate(estimate.interval)
    return f"no rate, {_short_status(estimate)}"


def format_converge_report(result: ConvergeResult) -> str:
    config = result.config
    counts = result.counts
    status = {CONVERGED: "converged, all four matchups", MOVED: "moved", INCOMPLETE: "incomplete"}[result.status]
    rows = [
        ("status", f"{status}: {counts[CONVERGED]} converged, {counts[MOVED]} moved, {counts[NO_VERDICT]} with no verdict"),
        ("before", config.before.label),
        ("after", config.after.label),
        ("rule", f"converged when the {config.matchup.confidence * 100:g}% confidence Newcombe interval for after minus before contains 0"),
        ("target", f"a half-width of {config.matchup.width:.1f} points for each, both seats one player"),
        ("seed", f"{config.matchup.batch.batch_seed}, the same for both players"),
        ("difference", "after minus before, Newcombe hybrid score interval (method 10), the verdict's test"),
    ]
    if result.set_aside:
        rows.append(("set aside", ", ".join(folder.name for folder in result.set_aside)))
    text = table(rows)
    lines = [("slot", "deck A", "deck B", config.before.label, config.after.label, "after minus before", "verdict")]
    for verdict in result.verdicts:
        difference = verdict.difference
        lines.append((str(verdict.slot), verdict.deck_a, verdict.deck_b, _estimate_text(verdict.before),
                      _estimate_text(verdict.after), format_points(difference) if difference else "-", verdict.verdict))
    text += "\n" + table(lines)
    lines = [("slot", "why", f"{config.before.label} games", f"{config.after.label} games")]
    for verdict in result.verdicts:
        lines.append((str(verdict.slot), verdict.why,
                      *(f"{estimate.games_finished}{', reused' if estimate.reused else ''}" if estimate else "-"
                        for estimate in (verdict.before, verdict.after))))
    return text + "\n" + table(lines)
