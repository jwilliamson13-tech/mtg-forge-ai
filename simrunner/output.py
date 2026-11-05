"""Turn what `forge sim -q` prints into game results.

The exit code says nothing: `Main` ends in `System.exit(0)` even when a deck fails
to load. Only a `Game Result:` line proves a game was played.

The `Game Result:` line can't be trusted to name a real winner either. `SimulateMatch`
ends a stopped game with `setGameOver(Draw)`, but `Player.onGameOver` then marks every
player who hasn't lost as a winner. So a game stopped by the clock or by an exception
prints `Ai(n)-... has won!` for an arbitrary seat. What gives it away is that both
`Game Outcome:` lines say "has won", and the clock prints its own line first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

WIN_LINE = re.compile(r"^Game Result: Game (\d+) ended in (\d+) ms\. Ai\((\d+)\)-.* has won!$")
DRAW_LINE = re.compile(r"^Game Result: Game (\d+) ended in a Draw! Took (\d+) ms\.$")
TURN_LINE = re.compile(r"^Game Outcome: Turn (\d+)$")
OUTCOME_WON_LINE = re.compile(r"^Game Outcome: Ai\(\d+\)-.* has won because all opponents have lost$")
HEADER_LINE = re.compile(r"^Ai\(1\)-.* vs Ai\(2\)-.* seed (\d+)$")
SIM_MODE_TAG = re.compile(r"\[sim=(\w+)\]")
AI_LIMITS_LINE = re.compile(r"^AI limits: (\d+) deadline expiries, (\d+) budget stops, (\d+) random draws"
                            r"(?:, (\d+) stops played, (\d+) stops passed, (\d+) stops with a land, "
                            r"(\d+) land drops missed)?$")
TRACE_HEAD_LINE = re.compile(r"^[\w.$]*(Exception|Error)\b")
CLOCK_LINE = "Stopping slow match as draw"
AI_TIMEOUT_LINE = "AI eval thread at timeout:"
FATAL_LINES = ("Could not load deck", "Unknown AI profile", "Unknown simulation mode")


class DrawCause(Enum):
    RULES = "rules"
    CLOCK = "clock"
    EXCEPTION = "exception"


@dataclass(frozen=True)
class AiLimits:
    """What Forge prints per game with `-sim` or `-budget`. A run without either prints nothing.

    The stop fields are None for a jar from before issue #51.
    """

    deadline_expiries: int
    budget_stops: int
    random_draws: int
    stops_played: int | None = None
    stops_passed: int | None = None
    stops_with_land: int | None = None
    land_drops_missed: int | None = None


@dataclass(frozen=True)
class GameResult:
    number: int
    duration_ms: int
    turn: int | None
    winner_seat: int | None = None
    draw_cause: DrawCause | None = None
    ai_limits: AiLimits | None = None

    @property
    def is_win(self) -> bool:
        return self.winner_seat is not None


def parse_games(lines: Iterable[str]) -> list[GameResult]:
    """Return one result per `Game Result:` line, with fake wins turned back into draws."""
    results = []
    turn = None
    clock_stopped = False
    outcome_winners = 0
    limits = None
    for line in lines:
        if line == CLOCK_LINE:
            clock_stopped = True
        elif match := AI_LIMITS_LINE.match(line):
            limits = AiLimits(*(None if group is None else int(group) for group in match.groups()))
        elif match := TURN_LINE.match(line):
            turn = int(match[1])
        elif OUTCOME_WON_LINE.match(line):
            outcome_winners += 1
        elif line.startswith("Game Result:"):
            if match := WIN_LINE.match(line):
                number, duration = int(match[1]), int(match[2])
                if clock_stopped:
                    results.append(GameResult(number, duration, turn, draw_cause=DrawCause.CLOCK, ai_limits=limits))
                elif outcome_winners > 1:
                    results.append(GameResult(number, duration, turn, draw_cause=DrawCause.EXCEPTION, ai_limits=limits))
                else:
                    results.append(GameResult(number, duration, turn, winner_seat=int(match[3]), ai_limits=limits))
            elif match := DRAW_LINE.match(line):
                cause = DrawCause.CLOCK if clock_stopped else DrawCause.RULES
                results.append(GameResult(int(match[1]), int(match[2]), turn, draw_cause=cause, ai_limits=limits))
            # A line that matches neither pattern was garbled, so that game counts as not finished.
            turn = None
            clock_stopped = False
            outcome_winners = 0
            limits = None
    return results


def find_fatal(lines: Iterable[str]) -> str | None:
    """Return the line where Forge gave up before playing, if it printed one."""
    for line in lines:
        if line.startswith(FATAL_LINES):
            return line
    return None


def printed_sim_modes(header: str) -> tuple[str, ...]:
    """Return the mode per seat from a match header. Empty when Forge ran without `-sim`."""
    return tuple(SIM_MODE_TAG.findall(header))


def count_stack_traces(lines: Iterable[str]) -> int:
    return sum(bool(TRACE_HEAD_LINE.match(line)) for line in lines)
