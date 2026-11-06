"""Working out a nightly window from two clock times. Pure dates, so no stub is needed."""

from datetime import datetime, time

import pytest

from simrunner.window import parse_clock, resolve_window

ELEVEN_PM = time(23, 0)
SEVEN_AM = time(7, 0)


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute)


def test_an_evening_window_called_in_the_afternoon_runs_tonight():
    window = resolve_window(ELEVEN_PM, SEVEN_AM, at(5, 15))
    assert (window.start, window.stop) == (at(5, 23), at(6, 7))


def test_called_inside_the_window_before_midnight_it_stops_tomorrow():
    window = resolve_window(ELEVEN_PM, SEVEN_AM, at(5, 23, 30))
    assert (window.start, window.stop) == (at(5, 23), at(6, 7))


def test_called_inside_the_window_after_midnight_it_stops_this_morning():
    window = resolve_window(ELEVEN_PM, SEVEN_AM, at(6, 2))
    assert (window.start, window.stop) == (at(5, 23), at(6, 7))


def test_called_at_the_stop_it_waits_for_the_next_window():
    window = resolve_window(ELEVEN_PM, SEVEN_AM, at(6, 7))
    assert (window.start, window.stop) == (at(6, 23), at(7, 7))


def test_a_daytime_window_called_before_it_runs_today():
    window = resolve_window(time(13), time(14), at(5, 12))
    assert (window.start, window.stop) == (at(5, 13), at(5, 14))


def test_a_daytime_window_called_inside_it_runs_today():
    window = resolve_window(time(13), time(14), at(5, 13, 30))
    assert (window.start, window.stop) == (at(5, 13), at(5, 14))


def test_a_daytime_window_called_after_it_runs_tomorrow():
    window = resolve_window(time(13), time(14), at(5, 15))
    assert (window.start, window.stop) == (at(6, 13), at(6, 14))


def test_a_window_of_no_length_is_refused():
    with pytest.raises(ValueError, match="can't be the same"):
        resolve_window(SEVEN_AM, SEVEN_AM, at(5, 15))


def test_the_kill_starts_ahead_of_the_stop():
    window = resolve_window(ELEVEN_PM, SEVEN_AM, at(5, 15))
    assert window.kill_at == at(6, 7).timestamp() - window.lead_s
    assert window.lead_s > 0


@pytest.mark.parametrize("text, expected", [
    ("23:00", time(23, 0)),
    ("07:05", time(7, 5)),
    ("07:05:30", time(7, 5, 30)),
])
def test_clock_times_parse(text, expected):
    assert parse_clock(text) == expected


@pytest.mark.parametrize("text", ["7pm", "25:00", "", "23"])
def test_a_bad_clock_time_is_refused(text):
    with pytest.raises(ValueError):
        parse_clock(text)
