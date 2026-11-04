"""Reading game results out of `forge sim -q` output.

The text in these tests is what Forge printed at commit da66fb1. The clock case is
from a real run with `-c 1`. The exception case could not be forced without patching
Forge, so it follows the source: `simulateSingleMatch` ends a thrown game exactly as
it ends a clocked one, without the clock's line.
"""

from simrunner.output import (DrawCause, count_stack_traces, find_fatal, parse_games)

A = "Ai(1)-GAL_19_18_Burn Red Burn-sligh Generated Deck_27_18"
B = "Ai(2)-GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19"

WIN = f"""\
Game Outcome: Match Duration 0m : 02s
Game Outcome: Turn 7
Game Outcome: {A} has lost because life total reached 0
Game Outcome: {B} has won because all opponents have lost
Match Result: {A}: 0 {B}: 1

Game Result: Game 1 ended in 2679 ms. {B} has won!
"""

FORCED_END = f"""\
Game Outcome: Match Duration 0m : 00s
Game Outcome: Turn 4
Game Outcome: {A} has won because all opponents have lost
Game Outcome: {B} has won because all opponents have lost
Match Result: {A}: 0 {B}: 2

Game Result: Game 2 ended in 1013 ms. {B} has won!
"""

RULES_DRAW = f"""\
Game Outcome: Match Duration 0m : 03s
Game Outcome: Turn 9
Game Outcome: {A} has lost because life total reached 0
Game Outcome: {B} has lost because life total reached 0
Match Result: {A}: 0 {B}: 0

Game Result: Game 1 ended in a Draw! Took 3120 ms.
"""


def parse(text):
    return parse_games(text.splitlines())


def test_a_win_names_the_seat_turn_and_time():
    [game] = parse(WIN)
    assert (game.number, game.winner_seat, game.turn, game.duration_ms) == (1, 2, 7, 2679)
    assert game.is_win
    assert game.draw_cause is None


def test_the_winning_seat_comes_from_the_prefix_not_the_deck_name():
    [game] = parse(WIN.replace("Merfolk", "Ai(1)-Merfolk"))
    assert game.winner_seat == 2


def test_a_draw_forge_prints_as_a_draw_is_a_rules_draw():
    [game] = parse(RULES_DRAW)
    assert not game.is_win
    assert game.draw_cause is DrawCause.RULES


def test_a_clock_stop_is_a_draw_even_though_forge_prints_a_winner():
    [game] = parse("Stopping slow match as draw\n" + FORCED_END)
    assert not game.is_win
    assert game.winner_seat is None
    assert game.draw_cause is DrawCause.CLOCK


def test_both_players_winning_without_the_clock_line_is_an_exception():
    [game] = parse(FORCED_END)
    assert game.draw_cause is DrawCause.EXCEPTION
    assert not game.is_win


def test_a_clock_stop_does_not_leak_into_the_next_game():
    clocked, normal = parse("Stopping slow match as draw\n" + FORCED_END + WIN)
    assert clocked.draw_cause is DrawCause.CLOCK
    assert normal.winner_seat == 2


def test_startup_noise_and_the_match_header_are_ignored():
    text = "Error handling registered!\nSimulation mode\n" \
           f"{A} vs {B} - one game of Constructed seed 42\n" \
           "Mountain Did not have activator set in SpellAbilityRestriction.canPlay()\n" + WIN
    assert len(parse(text)) == 1


def test_no_game_result_line_means_no_game():
    assert parse("Simulation mode\nNo deck found in C:\\decks\\\n"
                 "Could not load deck - Nope.dck, match cannot start\n") == []


def test_a_garbled_result_line_is_not_a_game_and_resets_the_state():
    garbled = "Stopping slow match as draw\nGame Result: Game 2 ended in \tat forge.ai\n"
    [game] = parse(garbled + WIN)
    assert game.winner_seat == 2


def test_fatal_lines_are_found():
    assert find_fatal(["Simulation mode", "Could not load deck - x.dck, match cannot start"]) == \
        "Could not load deck - x.dck, match cannot start"
    assert find_fatal(["Unknown AI profile - Bogus. Available profiles: Default"]).startswith("Unknown AI")
    assert find_fatal(["Simulation mode"]) is None


def test_stack_traces_are_counted_by_their_first_line():
    lines = ["java.lang.InterruptedException", "\tat java.base/java.util.concurrent.FutureTask.get(FutureTask.java:203)",
             "java.lang.NullPointerException: boom", "The card A-Divide by Zero was not assigned to any set."]
    assert count_stack_traces(lines) == 2
