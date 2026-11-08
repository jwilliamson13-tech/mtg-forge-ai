"""A card swap: deck A and deck A' (one card swapped) as two matchups against the same opponent.

The two arms share every setting and the seed, so round r of each arm plays the same worker seeds
with the same seats. The games start alike and part once the swapped card makes a difference. Each
arm stops on its own, when its interval meets the target or its budget runs out, as a matchup does.

The report gives both rates with their Wilson intervals and A' minus A with Newcombe's hybrid score
interval. That interval takes the arms as independent. Shared seeds make the arms agree more than
independent ones would, which makes the interval wider than it needs to be, not narrower.

A difference shows only when both arms met their target. The swapped arm runs only when the
original met its target, since without both rates there is no difference to report.
See "Card swaps" in docs/forge-build.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from deckpool.convert import normalize
from simrunner.batch import SetupError, check_setup, find_jar, write_json
from simrunner.interval import Difference, newcombe
from simrunner.matchup import (MET, ORDERS, MatchupConfig, MatchupResult, check_matchup, format_matchup_report,
                               format_rate, result_fields, round_config, round_sizes, run_matchup)
from simrunner.report import describe_seat, table

RESULT_FILE = "cardswap.json"
ARMS = ("original", "swapped")
SWAPPED_DECK = "swapped.dck"
SWAP = re.compile(r"\s*(?:(\d+)\s+)?(.+?)\s*=\s*(.+?)\s*")
CARD_LINE = re.compile(r"(\s*)(\d+)\s+([^|]+?)(\s*\|.*)?\s*$")


@dataclass(frozen=True)
class Swap:
    old: str
    new: str
    count: int | None = None  # None swaps every copy

    def __str__(self) -> str:
        copies = f"{self.count} " if self.count else ""
        return f"{copies}{self.old} for {self.new}"

    def fields(self) -> dict:
        return {"old": self.old, "new": self.new, "count": self.count}


def parse_swap(text: str) -> Swap:
    """`OLD=NEW` swaps every copy of OLD, `N OLD=NEW` swaps N of them."""
    match = SWAP.fullmatch(text)
    if not match or "=" in match[3]:
        raise ValueError(f"a swap is OLD=NEW or N OLD=NEW, not {text!r}")
    count = int(match[1]) if match[1] else None
    if count == 0:
        raise ValueError("a swap of 0 copies changes nothing")
    if normalize(match[2]) == normalize(match[3]):
        raise ValueError(f"{match[2]} for itself changes nothing")
    return Swap(match[2], match[3], count)


def swap_deck(text: str, swap: Swap) -> str:
    """The deck with the swap made in its main deck. The sideboard and the deck's name stay.

    The new card's lines go where the old card's first line was, so the rest of the file stays put.
    """
    lines: list[str | None] = list(text.splitlines())
    section = None
    found = []
    already = None  # a main-deck line of the new card, which takes the swapped copies
    for index, line in enumerate(lines):
        if line.startswith("["):
            section = line.strip().lower()
        elif section == "[main]" and (match := CARD_LINE.fullmatch(line)):
            if normalize(match[3]) == normalize(swap.old):
                found.append((index, int(match[2])))
            elif normalize(match[3]) == normalize(swap.new) and already is None:
                already = index
    held = sum(copies for _, copies in found)
    if not held:
        raise SetupError(f"the main deck has no {swap.old}")
    wanted = held if swap.count is None else swap.count
    if wanted > held:
        raise SetupError(f"the main deck has {held} {swap.old}, fewer than the {wanted} to swap")
    left = wanted
    for index, copies in found:
        taken = min(copies, left)
        left -= taken
        if copies > taken:
            match = CARD_LINE.fullmatch(lines[index])
            lines[index] = f"{match[1]}{copies - taken} {match[3]}{match[4] or ''}"
        else:
            lines[index] = None
    if already is not None:
        match = CARD_LINE.fullmatch(lines[already])
        lines[already] = f"{match[1]}{int(match[2]) + wanted} {match[3]}{match[4] or ''}"
    else:
        lines.insert(found[0][0], f"{wanted} {swap.new}")
    return "\n".join(line for line in lines if line is not None) + "\n"


@dataclass(frozen=True)
class CardSwapConfig:
    matchup: MatchupConfig  # deck A is the original; run_dir holds both arms
    swap: Swap

    @property
    def run_dir(self) -> Path:
        return self.matchup.batch.run_dir

    @property
    def swapped_deck(self) -> Path:
        return self.run_dir / SWAPPED_DECK


def arm_config(config: CardSwapConfig, arm: str) -> MatchupConfig:
    """One arm's matchup: its own folder and batch ids, the same seed and settings as the other."""
    base = config.matchup.batch
    seats = base.seats
    if arm == ARMS[1]:
        seats = (replace(seats[0], deck=config.swapped_deck), seats[1])
    batch = replace(base, seats=seats, run_dir=config.run_dir / arm, batch_name=f"{base.batch_id}-{arm}")
    return replace(config.matchup, batch=batch)


@dataclass
class CardSwapResult:
    config: CardSwapConfig
    arms: dict[str, MatchupResult]  # the swapped arm is missing when it didn't run

    @property
    def met(self) -> bool:
        return all(arm in self.arms and self.arms[arm].met for arm in ARMS)

    @property
    def difference(self) -> Difference | None:
        """Swapped minus original, over decisive games. None unless both arms met their target."""
        if not self.met:
            return None
        return newcombe(self.arms[ARMS[1]].interval, self.arms[ARMS[0]].interval)

    @property
    def status(self) -> str:
        if self.met:
            return MET
        if ARMS[1] not in self.arms:
            return f"original {self.arms[ARMS[0]].status}"
        return f"swapped {self.arms[ARMS[1]].status}"


def check_cardswap(config: CardSwapConfig) -> None:
    """Everything that can fail before a game, for both arms, so the second can't fail after the first ran."""
    if (config.run_dir / RESULT_FILE).exists():
        raise SetupError(f"the run folder already holds a card swap: {config.run_dir}")
    original = config.matchup.batch.seats[0].deck
    if not original.is_file():
        raise SetupError(f"not a file: {original}")
    swapped = swap_deck(original.read_text(encoding="utf-8"), config.swap)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    config.swapped_deck.write_text(swapped, encoding="utf-8", newline="\n")
    for arm in ARMS:
        matchup = arm_config(config, arm)
        check_matchup(matchup)
        check_setup(round_config(matchup, 1, ORDERS[0], round_sizes(matchup.round_games, matchup.max_games)[0]))
    find_jar(config.matchup.batch.forge_root)


def run_cardswap(config: CardSwapConfig) -> CardSwapResult:
    check_cardswap(config)
    result = CardSwapResult(config, {})
    for arm in ARMS:
        write_json(config.run_dir / RESULT_FILE, cardswap_fields(result))
        played = run_matchup(arm_config(config, arm))
        result.arms[arm] = played
        (played.config.batch.run_dir / "report.txt").write_text(format_matchup_report(played), encoding="utf-8",
                                                                newline="\n")
        if not played.met:
            break
    write_json(config.run_dir / RESULT_FILE, cardswap_fields(result))
    return result


def cardswap_fields(result: CardSwapResult) -> dict:
    difference = result.difference
    return {
        "status": result.status if result.arms else "running",
        "swap": result.config.swap.fields(),
        "swapped_deck": str(result.config.swapped_deck),
        **{arm: result_fields(result.arms[arm]) if arm in result.arms else None for arm in ARMS},
        "difference": difference and {"estimate": difference.estimate, "low": difference.low, "high": difference.high,
                                      "confidence": difference.confidence,
                                      "method": "Newcombe hybrid score, swapped minus original"},
    }


def format_points(difference: Difference) -> str:
    """The only way a difference gets printed: in points, with its interval."""
    return (f"{difference.estimate * 100:+.1f} points ({difference.low * 100:+.1f} to "
            f"{difference.high * 100:+.1f} points)")


def _arm_status(result: CardSwapResult, arm: str) -> str:
    if arm not in result.arms:
        return "not run, because the original arm did not meet its target"
    played = result.arms[arm]
    if played.met:
        return f"met, {format_rate(played.interval)} over {played.interval.games} decisive games"
    return f"{played.status}, no rate (see {played.config.batch.run_dir / 'report.txt'})"


def format_cardswap_report(result: CardSwapResult) -> str:
    config = result.config
    seats = config.matchup.batch.seats
    difference = result.difference
    rows = [
        ("status", "met, both arms met the target" if result.met else f"refused, {result.status}: no difference"),
        ("swap", str(config.swap)),
        ("deck A", describe_seat(seats[0])),
        ("deck A'", f"{config.swapped_deck}"),
        ("opponent", describe_seat(seats[1])),
        ("original", _arm_status(result, ARMS[0])),
        ("swapped", _arm_status(result, ARMS[1])),
    ]
    if difference:
        verdict = "excludes 0" if difference.excludes_zero else "includes 0"
        rows += [
            ("difference, A' minus A", format_points(difference)),
            ("  the interval", verdict),
        ]
    rows += [
        ("rate interval", f"Wilson score, {config.matchup.confidence * 100:g}% confidence, over decisive games"),
        ("difference interval", "Newcombe hybrid score (method 10), from the two Wilson intervals"),
        ("target per arm", f"a half-width of {config.matchup.width:.1f} points"),
        ("seed", f"{config.matchup.batch.batch_seed}, the same for both arms"),
        ("games attempted", ", ".join(f"{arm} {result.arms[arm].attempted}" for arm in ARMS if arm in result.arms)),
    ]
    return table(rows)
