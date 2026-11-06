"""The nightly window: when a batch may start, and the hard stop that ends it."""

from __future__ import annotations

import time as clock
from dataclasses import dataclass
from datetime import datetime, time, timedelta

# Killing starts this long before the stop, so that the machine is idle at the stop and not just after it.
STOP_LEAD_S = 15.0
START_POLL_S = 30.0


@dataclass(frozen=True)
class Window:
    start: datetime
    stop: datetime
    lead_s: float = STOP_LEAD_S

    @property
    def kill_at(self) -> float:
        """The moment to start killing workers, as `time.time()` reads it."""
        return self.stop.timestamp() - self.lead_s

    @property
    def closed(self) -> bool:
        return clock.time() >= self.kill_at

    def wait_for_start(self) -> None:
        """Sleep in short steps against the wall clock, which one long sleep can drift from."""
        while (left := self.start.timestamp() - clock.time()) > 0:
            clock.sleep(min(left, START_POLL_S))


def parse_clock(text: str) -> time:
    """Read `HH:MM` or `HH:MM:SS`."""
    for layout in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, layout).time()
        except ValueError:
            pass
    raise ValueError(f"not a clock time: {text!r}")


def resolve_window(start: time, stop: time, now: datetime) -> Window:
    """Return the next window that hasn't ended. A stop at or before the start falls on the next day."""
    if start == stop:
        raise ValueError("the start and the stop can't be the same time")
    opens = datetime.combine(now.date(), start)
    closes = datetime.combine(now.date(), stop)
    if stop < start:
        if now < closes:
            opens -= timedelta(days=1)  # inside the window that opened last night
        else:
            closes += timedelta(days=1)
    elif now >= closes:
        opens += timedelta(days=1)
        closes += timedelta(days=1)
    return Window(opens, closes, STOP_LEAD_S)
