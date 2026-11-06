"""Reading game results out of `forge sim -q` output.

The text in these tests is what Forge printed at commit da66fb1. The clock case is
from a real run with `-c 1`. The exception case could not be forced without patching
Forge, so it follows the source: `simulateSingleMatch` ends a thrown game exactly as
it ends a clocked one, without the clock's line.
"""

from simrunner.output import (AiLimits, DrawCause, HEADER_LINE, count_stack_traces, find_fatal, parse_games,
                              printed_sim_modes)

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


def test_the_turn_cap_line_makes_a_draw_a_draw_by_turn_cap():
    capped, normal = parse("Stopping game at the turn cap as draw\n" + RULES_DRAW + WIN)
    assert capped.draw_cause is DrawCause.TURN_CAP
    assert normal.is_win


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


def test_a_win_with_sim_modes_in_the_names_still_names_the_seat():
    # The mode rides in the player name, as a real `-sim full heuristic` run prints it.
    text = WIN.replace(A, f"{A} [sim=full]").replace(B, f"{B} [sim=heuristic]")
    [game] = parse(text)
    assert game.winner_seat == 2
    assert game.turn == 7


def test_a_forced_end_with_sim_modes_in_the_names_is_still_a_draw():
    text = FORCED_END.replace(A, f"{A} [sim=full]").replace(B, f"{B} [sim=full]")
    [game] = parse(text)
    assert game.draw_cause is DrawCause.EXCEPTION


def test_the_match_header_gives_the_mode_per_seat():
    header = f"{A} [sim=full] vs {B} [sim=heuristic] - one game of Constructed seed 42"
    assert HEADER_LINE.match(header)
    assert printed_sim_modes(header) == ("full", "heuristic")


def test_the_mode_is_found_beside_an_ai_profile():
    header = f"{A} [sim=full] [Reckless] vs {B} [sim=hybrid] [Default] - 2 games of Constructed seed 42"
    assert printed_sim_modes(header) == ("full", "hybrid")


def test_a_header_without_the_flag_names_no_modes():
    assert printed_sim_modes(f"{A} vs {B} - one game of Constructed seed 42") == ()


def test_fatal_lines_are_found():
    assert find_fatal(["Simulation mode", "Could not load deck - x.dck, match cannot start"]) == \
        "Could not load deck - x.dck, match cannot start"
    assert find_fatal(["Unknown AI profile - Bogus. Available profiles: Default"]).startswith("Unknown AI")
    assert find_fatal(["Simulation mode"]) is None


def test_stack_traces_are_counted_by_their_first_line():
    lines = ["java.lang.InterruptedException", "\tat java.base/java.util.concurrent.FutureTask.get(FutureTask.java:203)",
             "java.lang.NullPointerException: boom", "The card A-Divide by Zero was not assigned to any set."]
    assert count_stack_traces(lines) == 2


AI_LIMITS = "AI limits: 3 deadline expiries, 1 budget stops, 5120 random draws\n"


def test_the_ai_limits_line_belongs_to_the_game_it_precedes():
    games = parse(AI_LIMITS + WIN + WIN)
    assert games[0].ai_limits == AiLimits(3, 1, 5120)
    assert games[1].ai_limits is None


def test_a_forced_end_keeps_its_ai_limits():
    assert parse(AI_LIMITS + FORCED_END)[0].ai_limits == AiLimits(3, 1, 5120)


def test_the_ai_limits_line_can_report_what_stops_did():
    line = ("AI limits: 0 deadline expiries, 5 budget stops, 5120 random draws, "
            "3 stops played, 2 stops passed, 4 stops with a land, 1 land drops missed\n")
    assert parse(line + WIN)[0].ai_limits == AiLimits(0, 5, 5120, 3, 2, 4, 1)


def test_a_jar_from_before_the_stop_counts_reports_none():
    assert parse(AI_LIMITS + WIN)[0].ai_limits.stops_played is None


def test_a_game_without_the_line_has_no_ai_limits():
    assert parse(WIN)[0].ai_limits is None


def test_tripwire_lines_belong_to_the_game_they_precede():
    games = parse_games([
        "Tripwire: 5 hits",
        "Tripwire hit: 4 forge.ai.ComputerUtil.hasAFogEffect:1582 < forge.ai.AiBlockController.assignBlockers:1152",
        "Tripwire hit: 1 no decider: forge.ai.ability.ClashAi.chooseSinglePlayer:75",
        "Game Outcome: Turn 7",
        "Game Result: Game 1 ended in 2679 ms. Ai(1)-Burn has won!",
        "Tripwire: 0 hits",
        "Game Outcome: Turn 3",
        "Game Result: Game 2 ended in 2679 ms. Ai(2)-Merfolk has won!",
    ])
    assert games[0].tripwire_hits == 5
    assert games[0].tripwire_signatures == (
        ("forge.ai.ComputerUtil.hasAFogEffect:1582 < forge.ai.AiBlockController.assignBlockers:1152", 4),
        ("no decider: forge.ai.ability.ClashAi.chooseSinglePlayer:75", 1))
    assert games[1].tripwire_hits == 0
    assert games[1].tripwire_signatures == ()


def test_a_stopped_game_keeps_its_tripwire_lines():
    games = parse_games([
        "Stopping slow match as draw",
        "Tripwire: 2 hits",
        "Tripwire hit: 2 forge.ai.ability.ClashAi.chooseSinglePlayer:75",
        "Game Result: Game 1 ended in 1013 ms. Ai(1)-Burn has won!",
    ])
    assert games[0].draw_cause == DrawCause.CLOCK
    assert games[0].tripwire_hits == 2


def test_a_game_without_tripwire_lines_reports_none():
    [game] = parse_games(["Game Result: Game 1 ended in 2679 ms. Ai(1)-Burn has won!"])
    assert game.tripwire_hits is None
    assert game.tripwire_signatures == ()
