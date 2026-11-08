# Forge build

How to get from a fresh Windows machine to one headless `forge sim` game between two shipped `GAL_` decks. Last run 2026-11-04 on Windows 11 with 12 logical cores.

The fork is https://github.com/jwilliamson13-tech/forge. All work goes on its `mtg-forge-ai` branch, and nothing is ever pushed to Card-Forge. Forge sits beside this repo at `..\forge`, which on the build machine is `C:\Users\Josh\Desktop\Programming Projects\forge`. That is the path the rebuild command in `sweep.md` expects.

## What was built

| | |
|---|---|
| Java | Oracle JDK 21.0.2 at `C:\Program Files\Java\jdk-21` |
| Maven | 3.10.0, from scoop |
| Forge version | 2.0.16-SNAPSHOT |
| Upstream commit | `da66fb1ac4be7957461477bc8bd26628a439c747` (2026-11-04) |
| Fork branch | `mtg-forge-ai`, that upstream commit plus the fork's own patches. `git log da66fb1..mtg-forge-ai` lists them. They add `-sim` (issue #17), `-budget` (issue #50) and the search changes of issue #51 |
| Jar | `forge-gui-desktop\target\forge-gui-desktop-2.0.16-SNAPSHOT-jar-with-dependencies.jar` |

Forge's pom requires Java 17 or newer and Maven 3.8.1 or newer. It compiles to release 17, so Java 21 works.

## Steps

Run these in PowerShell, starting in the folder that holds this repo.

### 1. Install the tools

You need a real JDK, 17 or newer. Check with `javac -version`. The build machine already had one, so installing a JDK is untested here.

```powershell
scoop install maven
```

Scoop adds Maven to the user PATH instead of making a shim, so open a new terminal before going on. Then point Maven at the JDK for this session:

```powershell
$env:JAVA_HOME = "C:\Program Files\Java\jdk-21"
mvn -version
```

### 2. Clone the fork

```powershell
git clone -c core.longpaths=true -c core.autocrlf=false --branch mtg-forge-ai https://github.com/jwilliamson13-tech/forge.git forge
cd forge
git remote add upstream https://github.com/Card-Forge/forge.git
git remote set-url --push upstream DISABLED
```

The first clone took about 2.5 minutes and uses 1.1 GB for `.git`. `core.autocrlf=false` keeps files byte-for-byte as upstream has them, because this machine's system git config sets it to true. `core.longpaths=true` is insurance: the longest tracked path is about 170 characters here, so it only matters for a deeper clone folder. The disabled push URL makes a push to upstream fail.

To rebuild the upstream commit in the table without the fork's patches, run `git checkout da66fb1ac4be7957461477bc8bd26628a439c747`. That build has no `-sim` flag, so a sim-runner batch with `--sim-mode` fails against it.

### 3. Build

```powershell
mvn -B -DskipTests -pl forge-gui-desktop -am package
```

`-pl forge-gui-desktop -am` builds the desktop module and the five it needs (parent, core, game, ai, gui). It leaves out the mobile, Android and installer modules, which `forge sim` doesn't use. The cold build took 3:24 with an empty Maven cache, and left 205 MB in `~\.m2\repository`. `-DskipTests` skips Forge's test suite, so the suite has not been run on this machine.

### 4. Give the sim its own profile

```powershell
cd forge-gui
"userDir=target/sim-profile/user/", "cacheDir=target/sim-profile/cache/" | Set-Content -Encoding ascii forge.profile.properties
```

Without this file Forge reads and writes the normal user profile in `%APPDATA%\Forge`, so desktop preferences would leak into sim runs and a sim would write to that folder. On the build machine that folder is also synced by Syncthing. The file is gitignored by Forge. `mvn clean` deletes the profile folder, and Forge recreates it on the next run.

### 5. Run one game

Stay in `forge-gui`:

```powershell
$jar = "..\forge-gui-desktop\target\forge-gui-desktop-2.0.16-SNAPSHOT-jar-with-dependencies.jar"
$decks = (Resolve-Path "res\geneticaidecks").Path
java -Xmx4096m "-Dfile.encoding=UTF-8" "-Dsentry.enabled=false" -jar $jar sim -D $decks -d "GAL_19_18_Burn Red Burn-sligh Generated Deck_27_18.dck" "GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19.dck" -n 1 -s 42 -c 600 -q
```

It ends with:

```
Ai(1)-GAL_19_18_Burn Red Burn-sligh Generated Deck_27_18 vs Ai(2)-GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19 - one game of Constructed seed 42
Mountain Did not have activator set in SpellAbilityRestriction.canPlay()
Game Outcome: Match Duration 0m : 02s
Game Outcome: Turn 7
Game Outcome: Ai(1)-GAL_19_18_Burn Red Burn-sligh Generated Deck_27_18 has lost because life total reached 0
Game Outcome: Ai(2)-GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19 has won because all opponents have lost
Match Result: Ai(1)-GAL_19_18_Burn Red Burn-sligh Generated Deck_27_18: 0 Ai(2)-GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19: 1 

Game Result: Game 1 ended in 2679 ms. Ai(2)-GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19 has won!
```

The whole command took 19.7 seconds. The game itself took 2.7 of those, and the rest is JVM start and card loading. Game time ranged from 2.7 to 4.6 seconds across repeats. These are single games on an idle machine, not a throughput figure.

Steps 2 to 5, run exactly as written here in a new clone with a warm Maven cache, took 4.4 minutes and played the same game: Merfolk on turn 7.

## The flags

Read from `SimulateMatch.java` at the commit above, plus the fork's `-sim`:

- `-D DIR` is the deck directory. The file names passed to `-d` resolve under it. Use an absolute path, as Forge's own help says.
- `-d A.dck B.dck` takes the deck file names, which must end in a three-character extension to count as files. The `GAL_` names contain spaces, so quote them.
- `-n` is the game count, default 1.
- `-s` is the RNG seed. Two runs with seed 42 produced the same game log, line for line, once the startup banner's timestamps and the timing lines were removed.
- `-a` is an AI profile per seat, in deck order, such as `-a Default Reckless`. An unknown name prints the valid ones and stops.
- `-sim` is the simulation mode per seat, in deck order, such as `-sim full heuristic`. The modes are `heuristic`, `hybrid` and `full`, the same three as the desktop lobby's menu. Seats left out stay heuristic. With the flag, each player name ends in `[sim=mode]`, so every result line names the mode. Without it, the output is what stock Forge prints. An unknown mode prints the valid ones and stops, and so does `-sim` with `-t`.
- `-budget` is the number of search steps an AI decision may take, such as `-budget 100`. A step is one game copied for the search. With it, the wall-clock limit on AI decisions stops mattering, so a run replays from its seed on a busy machine too. What a decision that runs out does is under "Stopped decisions" below. Without the flag the limit stays at 5 seconds. Only `forge sim` sets a budget.
- `-record DIR` writes one game record per game to that folder, as `docs/game-record.md` describes. It needs `-batch`, `-s` and exactly two decks, and it can't go with `-t`. Anything missing prints why and stops. Without it Forge writes no records, keeps no reveal log and has no turn cap.
- `-batch ID` is the batch id for those records: letters, digits, `_` and `-`.
- `-turncap N` stops a recorded game as a draw once turn N is over, default 50. A capped game prints `Stopping game at the turn cap as draw` before its result lines.
- `-tripwire on|off` is on by default for every `forge sim` run, with or without `-record`, and off for any game outside `forge sim`. It reports in `Tripwire` lines before each result, and in the game record if there is one. It can't go with `-t`, and a tournament has none. See "Tripwire" below.
- `-honest on|off` keeps the AI to what a person in its seat could know, and off is the default, so a plain `forge sim` is the stock AI with its peeks. It works without `-record`, but only `-record` keeps a reveal log, so the AI then goes by public cards alone. It can't go with `-t`. See `docs/peek-fixes.md`.
- `-eval` is the evaluator per seat, in deck order, such as `-eval random forge`: what that seat's search scores game states with. The names are `forge`, `random` and `passthrough`, and `forge`, Forge's own evaluator, is the default for a seat left out. Any other name needs the seat to be `hybrid` or `full`, because a heuristic seat never scores a state, and Forge refuses it otherwise. With the flag, each player name ends in `[eval=name]` after its `[sim=mode]`. An unknown name prints the valid ones and stops, and so does `-eval` with `-t`. See "Evaluators" below.
- `-prune on|off` decides what the copies a search plays on hold of the other players' hidden cards. On, a copy holds placeholders, plus what the searching seat was shown. It is on by default whenever `-sim` gives a seat `hybrid` or `full`, and off otherwise, where no seat searches. It can't go with `-t`. See "Pruned copies" below.
- `-c` is the clock in seconds before the game is stopped, default 120.
- `-q` prints only the result lines, though Forge's startup messages still appear.
- `-mc on|max|off` turns on Flat Monte Carlo. It is new since `HANDOFF.md` was written and is off by default in a sim.

With `-sim` or `-budget`, each game prints one more line before its `Game Result:` line. Without them and with `-tripwire off`, the output is what stock Forge prints.

```
AI limits: 0 deadline expiries, 2 budget stops, 5120 random draws, 2 stops played, 0 stops passed, 1 stops with a land, 0 land drops missed
```

Deadline expiries and budget stops count the decisions the clock or the budget cut short in that game. Random draws is every draw from `MyRandom` in that game, including the ones the search makes, so two games that played differently almost never share it. The last four numbers say what those stopped decisions did, and only count when a seat is `hybrid` or `full`. A stop played a line or passed. A stop with a land had a land among its candidates. A land drop missed is a turn that ended with no land played after such a stop.

In tournament mode (`-t`), `-D` works differently: it loads every `.dck` in the folder, and names passed to `-d` no longer resolve under it.

## Things that will trip you up

- **Run from `forge-gui`.** The jar's version string doesn't contain "git", so Forge looks for `res\` and `forge.profile.properties` in the working directory. From anywhere else it can't find either one, and it falls back to the real user profile. This comes from reading `GuiDesktop.getAssetsDir()`. I didn't run it that way, to keep the real profile clean.
- **Quote `-D` properties in Windows PowerShell.** Version 5.1 splits an unquoted `-Dfile.encoding=UTF-8` at the dot, and Java fails with `Could not find or load main class .encoding=UTF-8`.
- **Keep `-Dsentry.enabled=false`.** `Main` starts Sentry with a DSN on the Forge team's server. Reports are off by default through the `USE_SENTRY` preference, and with this property Sentry doesn't start at all. Batches of bot games shouldn't reach that server.
- **The exit code doesn't mean the game worked.** `Main` always ends with `System.exit(0)`, even after `Could not load deck`. Only a `Game Result:` line proves a game was played.
- **A stopped game prints a winner.** A game past the clock prints `Stopping slow match as draw`, then `Game Result: ... has won!` for an arbitrary seat. `SimulateMatch` calls `setGameOver(Draw)`, but `Player.onGameOver` marks everyone who hasn't lost as a winner, so both `Game Outcome:` lines say "has won" and `isDraw()` is false. The running score in `Match Result:` counts these fake wins too. I saw this on a real run with `-c 1`. By the source, a game that throws ends the same way, without the clock's line. The sim-runner counts both as draws.
- **Forge skips flags it doesn't know.** A jar built before `-sim` plays a heuristic game with `-sim full` and doesn't complain. So the `[sim=mode]` tag in the output is the proof that a mode took effect, not the flag on the command line. The same goes for `-budget`, where the proof is the `AI limits:` line, and for `-record`, where the proof is the files.
- **Some noise is normal.** Startup prints about 20 `was not assigned to any set` lines. `Did not have activator set` is a `println` in `SpellAbilityRestriction.java`, where Forge fills in the missing activator and carries on. In full sim, `Simulation: SA not found!` on stderr comes from `GameSimulator`: the search couldn't find that ability on its copy of the game, scored it as a failed simulation and moved on. It isn't a crash. On Merfolk it is Merfolk Sovereign's `{T}` ability, which is expected. See "Stopped decisions" below.

## Running batches

`simrunner` plays two decks against each other across parallel `forge sim` JVMs and reports throughput. It needs Python 3.11 or newer and nothing outside the standard library.

```powershell
$decks = "..\forge\forge-gui\res\geneticaidecks"
python -m simrunner batch "$decks\GAL_19_18_Burn Red Burn-sligh Generated Deck_27_18.dck" "$decks\GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19.dck" --games 50 --seed 1 --workers 10 --clock 600
```

Run it from this repo. `--workers` defaults to logical cores minus 2. `--ai Default Reckless` sets the AI profile per seat, in deck order. `--sim-mode full heuristic` sets the simulation mode the same way, and leaving it out leaves `-sim` off the command. `--evaluator random forge` sets `-eval` per seat, in deck order, and leaving it out leaves the flag off. The runner refuses anything but `forge` on a heuristic seat before it launches. `--search-budget` sets `-budget`. It defaults to 100 when a seat is in `hybrid` or `full` and to off otherwise, and `--search-budget 0` turns it off. `--clock` defaults to 2400 seconds, the wall clock in the spec, and `--heap` to 2048m. `--forge` defaults to `..\forge`. The report prints and goes to `runs\<time>-seed<seed>\report.txt`, beside one stdout and one stderr log per worker. `runs\` is gitignored. The command exits 1 if any game didn't finish, if a worker left no log in its throwaway profile, if a worker left a process holding its pipes, if a batch with `--sim-mode` or `--evaluator` doesn't get those modes or evaluators back in Forge's match header, if a batch with a search budget gets no `AI limits:` lines, if a batch with records is missing a file for a game that should have one, or if a batch that asked for honest play has a game file that doesn't say it got it. Those last four mean a stale jar, and the worker table says so. It also exits 1 if a game file and a result line disagree.

`--records` has Forge write a game record per game, into the run folder's `games\`. `--records-dir` picks another folder, and also turns records on. `--turn-cap` sets `-turncap`, needs records, and leaves Forge's 50 in place if left out. `--no-tripwire` passes `-tripwire off`, with or without records. `--no-prune` passes `-prune off`. Without it the runner leaves the flag out, so Forge prunes a searching seat's copies. With records the runner also passes `-honest on`, because records are training data, and `--no-honest` leaves it out. The run folder's name is the batch id, so with records on it has to be letters, digits, `_` and `-`. See "Game records" and "Tripwire" below.

### What the report counts

- **Seat 1** and **seat 2** give the deck, AI profile and simulation mode, and the evaluator when `--evaluator` named one.
- **Attempted** is what you asked for. **Finished** is every game with a `Game Result:` line. Games not finished is the difference, and covers a deck that won't load, an unknown AI profile and a JVM that died.
- **Wins** come from the `Ai(n)-` prefix, so they are per seat, and seat n holds deck n for the whole batch.
- **Draws by rules** are games Forge printed as a draw. In the runs below that meant both players lost.
- **Draws by clock** are games with the `Stopping slow match as draw` line. **Draws by exception** are games where both `Game Outcome:` lines say "has won" without that line. Both print a fake winner, and neither counts as a win. **Draws by turn cap** are games with the `Stopping game at the turn cap as draw` line.
- **Usable** means decisive, because draws get dropped before training.
- **Startup** runs from launching the JVM to Forge printing its match header. **Steady-state games per hour** is each worker's games divided by the time from that header to its last result, summed over workers.
- **Search budget** is the steps per decision, or off.
- **AI stops that played a line**, **AI stops that passed**, **AI stops with a land candidate** and **AI land drops missed on stops** add up the last four numbers of the `AI limits:` lines. They say `not reported` for a jar from before issue #51.
- **AI decision timeouts** counts the `AI eval thread at timeout:` lines, one narrow kind of expiry that the heuristic AI prints. **AI deadline expiries** counts every decision the clock cut short, and includes those. **AI search budget stops** counts the decisions the budget cut short. The last two come from the `AI limits:` lines and say `not reported` for a run without `-sim` or `-budget`.
- **Game records written** counts this batch's files against the games finished, when records are on. A game that threw has no file, and neither does one the clock stopped before its first priority pass.
- **Records against results** reads every finished game against its file, when records are on: winner and end reason. It leaves turns out: `GameLogFormatter` prints `Game Outcome: Turn` as the turn number halved and rounded up, so 20 in a file prints as 10, and Karn's restart would throw that off. It takes the metadata line of each file and leaves the snapshots alone, so a night of files costs seconds. `match` is games that agree. `no file by design` is clock and exception games with no file, the two kinds allowed to have none. `mismatches` is everything else: a file that disagrees, a game that should have a file and doesn't, an exception game with one, a file that can't be read, and a file with no result line. That last one isn't counted after a hard stop, which can land between the two. Any mismatch fails the batch, and the report lists the first 20.
- **Tripwire hits per game** gives the max and mean over the games that reported. **Tripwire clean** is yes only when every finished game printed a count and every count is 0. A game that printed nothing means the jar has no tripwire. It fails the batch, and a jar like that never reads as a clean zero. With a `hybrid` or `full` seat a zero says yes only when every game also printed its `Tripwire copies:` line, because a copy has no watch and only that check covers it. A jar from before #25 doesn't print it, and the row then says `the real game is clean, search copies unchecked, so the jar may be from before #25`. **Search copies checked** adds up those lines. With `--no-tripwire` the row says `off`.
- **The signature table** after the worker table lists the top 20 signatures by hits across the batch, with the games each one showed up in.
- **Result digest** is a hash of every game's seat, turn, draw cause and, when Forge prints it, random draws. It only says the runs ended alike. Winner and turn alone aren't enough: the two runs that opened issue #50 agreed on all ten winners. Random draws catch most of the rest, but an AI that chooses differently without drawing leaves them unchanged.
- **Record digest** is the replay check, when records are on. It hashes every snapshot of every game file, plus the metadata less `batch_id`, `honest_play` and `tripwire`, which can differ between two runs of the same games. The same digest means the same games card by card. Record ids come from the seed, so they replay too. Digests in this file from before it existed are result digests.

The output can't separate everything:

- A game that threw after one player had already lost looks like a normal win on stdout. With records on, it shows up as a mismatch, because it has no file. Without them only `stack traces on stderr` hints at it, and that count is per worker, not per game, because stdout and stderr have to stay on separate pipes. On one pipe a stopped game's stack trace lands in the middle of the `Game Result:` line.
- The exception signature comes from the source. I could not force a throw without patching Forge, so it is only tested against the stub. The clock signature comes from real runs with `--clock 1`.
- The report's win and draw counts still come from stdout, so a batch without records has nothing to check them against. With records, a disagreement fails the batch rather than correcting the count.

### Nightly window

`nightly` runs a batch inside a time window and kills whatever is still playing at the end of it. The machine is in use in the mornings, so the stop is hard.

```powershell
python -m simrunner nightly "$decks\GAL_19_18_Burn Red Burn-sligh Generated Deck_27_18.dck" "$decks\GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19.dck" --games 100000 --seed 1 --start 23:00 --stop 07:00 --clock 600
```

It takes every `batch` option, plus these:

- `--start` and `--stop` are local clock times, `HH:MM` or `HH:MM:SS`. A stop at or before the start falls on the next day, so the line above means 23:00 tonight to 07:00 tomorrow. Called inside the window, it starts at once and stops at the end of that window. Called after it, it waits for the next one.
- `--reserve` is the number of logical cores to leave free, 2 by default. The workers are the rest, and never fewer than 1. `--workers` sets the count outright, and the two can't be combined.

Nothing schedules the command. Leave it waiting in a terminal, or start it from Task Scheduler. It checks the decks, the jar and the run folder when you call it, not at 23:00, so a bad path fails while you can still fix it.

Set `--games` higher than the window can play, or the workers finish early and the rest of the night goes unused. The window ends the batch, and `--games` only caps it. Each worker's share goes to Forge as `-n`, which is a Java `int`.

**The stop.** Killing starts 15 seconds before `--stop` (`STOP_LEAD_S`), so the machine is idle at the stop and not just after it. Workers watch the wall clock in half-second waits, so a clock change on the night doesn't move the stop. A worker dies by `taskkill /F /T`, which takes the launcher and the JVM together. That kill gives Forge no chance to print, so a game cut off by the stop has no `Game Result:` line and counts as not finished. It can't turn into a fake win, and neither can a result line cut off halfway, because a partial line matches neither pattern.

**Incomplete.** A batch the stop cut off is marked incomplete in `batch.json` and in the report, and the command exits 1. With `--games` set high that is the normal end of a night, so read exit 1 from `nightly` alongside the status row. The games it finished are real games. They count, and the report's rates use them. What the batch has lost is replay as a whole: the stop lands at a different game on each run, so its digest won't match a rerun. A rerun with the same seed, games and workers still plays the same first games per worker, as under "Seeds" below.

A window that closes before the batch can launch, say because the machine slept through it, launches nothing and is marked incomplete the same way.

What the stop doesn't cover:

- The stop lives in the runner. If the runner itself dies, nothing kills the JVMs, and they play on into the morning. `batch.json` stays at `running`, which is the sign to go looking.
- `taskkill /T` follows parent links. If a launcher died on its own and left its JVM alive, the tree kill can't find that JVM. The runner doesn't hang on it. It gives up on the pipes after 10 seconds, notes `left a process running` on that worker and fails `clean`, but the process stays up. I haven't seen a launcher die alone. The test for this is a stub.
- A machine asleep at the stop kills its workers when it wakes, and not before.
- Each JVM keeps one `Match` for its whole run, and `Match` keeps every game's outcome, so heap use may grow through a night. No run has gone longer than the numbers below show.

### Pool batches

`pool` is `nightly` over the whole deck pool. It draws deck pairs from the manifest and plays them in chunks, one pair per JVM. This is the command that makes gen-0 training data.

```powershell
python -m simrunner pool --games 10000 --seed 20261107 --workers 4 --start 23:00 --stop 05:00 --run-dir runs/gen0-pool
```

It takes every `nightly` option except the two decks, and `--start` and `--stop` work the same way. Two defaults differ: both seats are `heuristic`, and records are on. The tripwire and honest play are on as usual, and `--no-tripwire` and `--no-honest` turn them off. `--manifest` picks another manifest, and its deck files resolve as they do for `batch`.

Always pass `--run-dir`. The default name has the time in it, so a restart couldn't find the folder. The folder name is the batch id of every game in the run, which makes the whole run one batch in Parquet.

**The plan.** `--held-out-share`, 0.15 by default, is the share of games played between held-out decks. The rest are train deck against train deck. A train deck never meets a held-out deck, and no deck meets itself. `setmodel` sends any game with a held-out deck to `held_out_decks`, so these games can't reach training. It still keeps back 10% of the train deck games as `held_out_games`, by a hash of the game id.

Each split seats its decks around a random ring, and each deck plays the next one. A whole ring gives every deck one chunk in each seat, so games and seats balance together. The chunk count moves to a whole number of rings, which is why `--chunk-games` is approximate. On the 2026-11-06 pool, the command above plans 195 chunks of 51 or 52 games. Each train deck gets 204 or 205 games, each held-out deck 103 or 104, and no deck's two seats differ by more than 1 game.

Train and held-out decks don't get the same number of games. There are 29 held-out decks of 112, and 15% of the games, so a held-out deck plays half as many. A share of 0.26 would even them out.

The balance needs at least `--chunk-games` times the deck count in each split. Below that, not every deck can have a chunk in each seat. The 40-game proof below reached 10 of the 112 decks.

The two splits interleave in the playing order, so a stop leaves games in both. The same seed and manifest give the same plan. A chunk's seed comes from its place in the plan, so the worker count doesn't change the games.

**Stopping and restarting.** The stop works as in `nightly`: it kills what is playing, keeps every finished game, marks `batch.json` incomplete, and the command exits 1. Run the same command again to continue. It reads `plan.json` from the run folder and refuses to go on if the seed, games, manifest split or any other setting that changes the games differs. Workers and the window can change.

A restart skips each chunk with a `finished` result. It plays every other chunk from its first game, with the same seed, so a replayed game overwrites its own file and nothing counts twice. A chunk the stop cut off costs at most one chunk of replay per worker. Ctrl-C starts nothing new and still writes the report.

**Failed chunks.** A chunk fails when `batch` would call it not clean, such as a deck that won't load or a dead JVM. The run goes on, the report lists the chunk and why, the command exits 1, and a restart tries the chunk again. A bad `--forge` path or a missing deck file fails at the start, before anything launches.

**The report.** `report.txt` covers every start of the run folder. Games per deck counts a deck that played nothing as 0, and has rows for all decks and for each split. Train and held-out deck games go by the deck's split in the manifest, so the train figure still includes the games `setmodel` keeps back. Tripwire clean means the same as for `batch`: every finished game printed a count, and every count is 0. Wall time and games per hour add up all the starts, and include the replays.

```
runs\<batch>\
    plan.json                 settings and every chunk, written once
    batch.json                as for a batch, plus chunk counts and wall_s
    report.txt                the one report
    games\                    every chunk's game records
    chunks\chunk-NNNN\        one plain batch folder, plus result.json
```

Checked on 2026-11-06 with 2 workers: 40 games, `--chunk-games 8`, 5 chunks, one of them held out. A stop at 100 seconds left 2 chunks finished, 2 cut off and 1 not started. The restart skipped the 2, left their logs untouched and played the other 3. All 40 games were wins, every tripwire count was 0, and there were no stack traces. `gamerecord convert`, `setmodel join` and a 1-epoch `setmodel train` all ran on the output. The Parquet held 40 game ids with no mixed pair: 29 `train`, 5 `held_out_games` and 6 `held_out_decks`.

What that run doesn't cover:

- No chunk failed on real Forge. The failure path has only run against the stub.
- The stop cut both chunks off before their first game, so a stop that keeps part of a chunk has only run against the stub.
- Ctrl-C has only run as a simulated interrupt in the runner. I expect the JVMs in the same console to take it too, and haven't seen that.
- 4 workers and 50-game chunks are untested.

### Matchups

`matchup` answers "how does deck A do against deck B". It plays the two decks in rounds until deck A's win rate has a narrow enough interval, then prints the rate with that interval. If the game budget runs out first, it prints no rate. It says how many more games the current estimate needs instead.

```powershell
python -m simrunner matchup hogaak-1 ur-tempo-1 --seed 1 --run-dir runs/hogaak-vs-ur-tempo
```

It takes `batch`'s options except `--games`, plus three of its own. `--evaluator` is how one deck's search runs a model while the other runs stock. `--width` is the target, 5 points by default. `--round-games` is the games per round, 100 by default, and has to be even. `--max-games` is the budget, 1000 by default. As with `pool`, both seats default to `heuristic` and records are on. Records are on so that the AI plays honestly, because the runner only passes `-honest on` with records, and so that each game's file is checked against its result line. They also bring Forge's turn cap of 50. `--ai`, `--sim-mode` and `--evaluator` take deck A's setting first, and each setting moves with its deck when the seats swap.

**Width means half-width.** The target is how far the interval reaches on each side of the rate, averaged over the two sides because Wilson is lopsided away from 50%. That's the only reading under which HANDOFF.md's figures work out: a half-width of 5 points takes 381 decisive games at even odds, and 2 points takes 2,398. The full width of the interval is twice the target.

**The interval** is the Wilson score interval at 95% confidence, over decisive games. `simrunner.interval` has it, and the tests check it against Newcombe's published values (Statistics in Medicine 17, 1998, Table I).

**Draws leave the rate.** Deck A's win rate is its wins over decisive games, and the report counts the draws beside it, by cause. A win rate is a share of games someone won, and a Wilson interval needs every game to be a win or a loss. Counting a draw as half a win would break that. Clock and exception draws aren't results of the matchup at all, and this is how training already treats every draw. A deck that stalls into the turn cap gets no credit for it, and the draw count in the report is where that shows.

**A round** is two batches with the same seed and the same game count: deck A in seat 1, then deck A in seat 2. With the same seed and worker count, both batches get the same worker seeds, so the two halves draw from the same random streams. Seats can't alternate game by game, because one JVM keeps its seats for its whole run. Each round's seed is a hash of `--seed` and the round number. The last round shrinks to what the budget has left, rounded down to an even count.

**The stopping rule.** After each round, the runner puts an interval on every decisive game so far. At or under the target, the run stops and reports. If the budget can't fit another round, it refuses. The games still needed come from the current rate: the fewest decisive games whose interval at that rate meets the target, less those already played, scaled up so that draws keep the share of games they have had so far.

**A failed batch ends the run.** A batch that isn't clean, by the same test `batch` uses, stops the matchup with no rate, and the report names the round, the seat order and why. So does the hard stop, when a caller of `run_matchup` gives the batch a window. The command has no `--start` and `--stop` yet. A dirty tripwire doesn't stop the run. The report shows it as `batch` does.

The report never prints a bare percentage. A rate always comes with its interval, and the only other percentage is the confidence level. When the target is met, it also gives deck A's rate in each seat, which is where a seat edge would show. The command exits 0 when the target was met and 1 otherwise.

```
runs\<matchup>\
    matchup.json              settings, counts per round and seat order, seeds, digests, and the interval when met
    report.txt                the report
    games\                    every batch's game records
    rounds\round-NNN-ab\      one plain batch folder, deck A in seat 1
    rounds\round-NNN-ba\      the same seed, deck A in seat 2
```

A batch's id is the run folder's name plus `-rNNN-ab` or `-rNNN-ba`, so the two orders of a round don't share game ids. A run folder that already holds a matchup is refused, and a matchup can't continue after a stop.

### Seat configurations

Each seat plays one of three AI configurations. `batch`, `nightly`, `pool`, `matchup` and `cardswap` all take them the same way, deck A's seat first where seats swap:

| Configuration | Options for that seat |
|---|---|
| Stock heuristic | `--sim-mode heuristic`, the default |
| Stock full sim, Forge's evaluator | `--sim-mode full` |
| A model generation | `--sim-mode full --evaluator onnx:gen1` |

`hybrid` works in place of `full` for the last two. The other evaluator values, `random` and `passthrough`, are the #23 test evaluators in "Evaluators".

**A generation is a folder under `models/`.** `onnx:gen1` plays the bundle in `models/gen1/`, which is what `python -m setmodel export ... --out models/gen1` writes. `models/` is git-ignored. `--models DIR` points at another folder of generations. A value with a path separator in it is a bundle folder, so `onnx:runs\dryrun\bundle` plays the dry run's bundle without moving it. The runner checks the folder for `contract.json` and `model.onnx`, reads `bundle.generation` from the contract, and refuses `gen1` if the contract says another number. It also hashes `model.onnx`. The batch report, `matchup.json` and a pool's plan carry the generation's name, folder, number and that digest, so a result names the weights it came from.

**How the runner tells Forge.** A generation seat becomes `-eval onnx:<folder>`, with the bundle's full path, and the other seat gets its own `-eval` value, `forge` if it names none. In a matchup the value follows the deck from seat to seat. Forge's match header names such a seat `[eval=onnx]`, without the path, and its stderr line `ONNX evaluator: seat N searches with <folder>` for each seat it put on a model is the proof that it took that bundle. A batch whose workers don't report the seats and folders asked for isn't clean. A jar from #23 alone refuses `onnx:` as an unknown evaluator before any game, and a jar older than `-eval` ignores the flag, prints no seat line, and fails the batch. The report's ONNX worker rows give each JVM's call count and time per call, one summary per bundle the JVM loaded, joined by `;`. `--java` has to name the patched JDK while the installed one crashes ONNX Runtime.

On 2026-11-08 a 2-game batch, boros-aggro-1 against dimir-tempo-1, seed 4001, 1 worker, `--forge ..\forge-wt-35 --sim-mode full heuristic --evaluator onnx:<dry-run bundle> forge --records` with the patched JDK as `--java`, ran clean at below-normal priority. Both games finished, both to seat 2, with records matching results and a tripwire count of 0. Forge reported seat 1 on the bundle, and the evaluator made 2,993 calls at 3.2 ms each. That run came before `-eval onnx:DIR`, when the runner still passed the bundle as `-D` properties to the #35 jar. It checks the wiring and says nothing about the model, which is the throwaway dry run.

Any two seat configurations can meet. Forge loads each bundle folder once per JVM and keeps it, so two generations at one table each play their own model, and a generation can sit across from `random` or `passthrough`. The fork's `t-eval-onnx` branch has this. Before it, the #35 jar loaded one bundle per JVM from `-D` properties, and the runner refused both pairings. `evaluator_wiring` in `simrunner/worker.py` is the one place that knows how a seat's evaluator reaches Forge.

On 2026-11-08 two generations met at one table on fork `t-eval-onnx` (`e7d70f3`): boros-aggro-1 against dimir-tempo-1, `--sim-mode full full --evaluator onnx:runs\dryrun\bundle onnx:runs\dryrun\bundle-gen1`, seed 4101, 6 games on 2 workers, budget 100, records on, the patched JDK, below-normal priority. `bundle-gen1` is a copy of the dry-run bundle with `bundle.generation` set to 1, so the two seats load two sessions of the same weights. That tests the wiring, not a ladder step. All 6 games finished, 5 to seat 1 and 1 to seat 2, with no exceptions, 6 records matching results and a tripwire count of 0. Each JVM loaded both bundles, once each, reported seat 1 on `bundle` and seat 2 on `bundle-gen1`, and wrote one summary per bundle: 10,748 and 6,053 calls in worker 0, 18,680 and 17,393 in worker 1, at 2.9 to 3.5 ms per call.

Seat 2's model scored 315 and 272 cards without features, all of one name, `hidden`. That is #25's placeholder for a card the searching seat can't know. A search copy holds the opponent's unknown hand and library cards as placeholders, and when the copy moves one into a public zone, the snapshot names it, and the card table has no such card. The contract's fallback scores it as a card with no text, which is what the seat knows of it. Boros as seat 1 never showed it.

### Card swaps

`cardswap` answers "how much does this card change deck A's win rate against B". It runs two matchups: deck A as written, then deck A' with one swap made, against the same opponent, with the same seed and settings. It reports both rates with their intervals, and A' minus A with an interval of its own.

```powershell
python -m simrunner cardswap burn-1 merfolk-1 --swap "Lightning Bolt=Chain Lightning" --seed 1 --run-dir runs/burn-chain-lightning
```

`--swap OLD=NEW` swaps every copy of OLD in the main deck for NEW, and `--swap "1 OLD=NEW"` swaps that many. The sideboard and the deck's name stay as they were. When the main deck already holds NEW, the swapped copies join that line. The swap is refused if the main deck holds no OLD, or fewer copies than asked. With `data/cards.jsonl` at hand, NEW has to be a card the parser knows, and takes Forge's spelling. Without it, the runner says NEW is unchecked and goes on. `--cards` names another card list. Every other option is `matchup`'s. `--ai`, `--sim-mode` and `--evaluator` give deck A's setting first, and both arms use it.

**Same seeds.** Each arm is a full matchup with the same seed, so round r of each arm plays the same worker seeds in the same seat orders. The games start alike and part once the swapped card matters.

**Each arm stops on its own**, at the target width or at its budget, as a matchup does. The swapped arm runs only if the original met its target, because without both rates there's no difference to give. The report shows a difference only when both arms met theirs.

**The difference interval** is Newcombe's hybrid score interval, method 10 of Newcombe, "Interval estimation for the difference between independent proportions: comparison of eleven methods" (Statistics in Medicine 17, 1998, 873-890). Each side of it combines the two Wilson intervals' reaches on that side in quadrature. The tests check it against seven of the paper's Table II examples, and against Fagerland, Lydersen and Laake (2015): 7/34 minus 1/34 gives 0.019 to 0.340. The method takes the two arms as independent. Shared seeds make the arms agree more often than independent samples would, which makes the interval wider than it has to be, never narrower.

**Plan for the width.** Two arms at a half-width of 5 points each give a difference with a half-width of about 7 points, 5 times the square root of 2. A card worth 3 points needs arms at about 2 points each, about 2,400 decisive games per arm at even odds.

The report prints each rate as a percentage with its interval, and the difference as signed points with its interval, and says whether that interval includes 0. It never prints a bare percentage. The command exits 0 when both arms met their target and 1 otherwise.

```
runs\<card swap>\
    cardswap.json             the swap, both arms' matchup.json fields, and the difference when both met
    report.txt                the report
    swapped.dck               deck A', as the runner wrote it
    original\                 deck A's matchup folder, batch ids <run>-original-rNNN-ab and -ba
    swapped\                  deck A' matchup folder, batch ids <run>-swapped-rNNN-ab and -ba
```

### Elo ladder

`ladder` gives each AI configuration one strength number. It plays every pair of players over a fixed sample of deck pairs from the manifest, then fits Elo ratings to the decisive games.

```powershell
python -m simrunner ladder --players heuristic full onnx:gen0 --seed 41
```

**A player** is an AI configuration in one word. `heuristic`, `full` and `hybrid` are the stock modes with Forge's evaluator. `full:random` and `hybrid:passthrough` put a #23 test evaluator on a searching seat. `onnx:gen0` is full sim with generation `gen0` from `models/`, and `hybrid:onnx:gen0` the same in hybrid. `simrunner/players.py` reads the names. A player has no AI profile of its own and plays Forge's `Default`.

**What gets played.** For each pair of players and each deck pair there are four batches: the first player on the pair's first deck, then on its second, each with the first player in seat 1 and then in seat 2. Neither the decks nor the seats can favor a player. `--pair-games` is the games per pair of players per deck pair, 40 by default, so each batch plays 10. `--deck-pairs` is 6 by default, so a pair of players meets over 240 games. Each batch is one JVM, and `--workers` of them run at once.

**Which decks.** `--split held_out`, the default, draws the deck pairs from the held-out decks, so no deck in the ladder is one a generation trained on. That is the case the harness is for, an unseen list. `--split train` and `--split both` are there for comparison. The seed draws the pairs, and no deck is in two of them. Every pair of players gets the same seed for the same deck pair and deck assignment, so all of them face the same shuffles. The two seat orders of an assignment share a seed, as a matchup round's two halves do.

**The ratings** are a Bradley-Terry fit, by maximum likelihood, on the Elo curve: a player rated 100 points above another is expected to win 64% of their games, 200 points 76%. Draws leave the fit, as they leave a matchup's rate, and the report counts them beside it. One player, the anchor, is held at `--anchor-rating`, 0 by default, so every other rating reads as points above or below it. `--anchor` picks it and defaults to `heuristic` when it plays, otherwise the first player.

**The interval** on each rating is the profile-likelihood interval at 95%. It holds every rating at which the best fit of the other players loses no more than 1.92 of log-likelihood against the overall best, half the chi-squared cut-off of 3.84. With two players that is the likelihood-ratio interval for a binomial share, which the tests check against a separate bisection. It is lopsided where the data are lopsided. Each interval is a difference from the anchor, so the anchor has none. The tests also check coverage. Over 300 simulated three-player ladders of 60 games a pair, the 95% intervals held the true rating 573 times in 600, 95.5%.

**No finite ratings.** If some group of players never lost a game to the rest, the fit has no maximum and the ratings would grow without limit. The ladder then gives no ratings at all, says which group, and still prints every head-to-head rate. So does a player with no decisive games.

**Plan for the precision.** At even odds, 240 decisive games between two players pin their gap to about 45 Elo points either side. Ratings linked through several opponents come out tighter.

**Adding a generation.** The ladder folder keeps every batch. A later run with a new player plays only the new player's batches against the others, then refits over everything among the players named. A batch that finished is never replayed. One that failed or was cut off by the hard stop plays again on the next run. Naming fewer players rates only them, from the games already played. The first run writes `plan.json`, with the seed, split, deck pairs, games per pair, clock, search budget, turn cap, honest play and tripwire setting, and a later run with any of those changed is refused. `plan.json` also keeps each player's settings, and a generation's model digest with them. A run that names `onnx:gen0` after `models/gen0/` was re-exported with other weights is refused, because the old games were played by another model.

**Two generations at one table** can't play yet, because Forge loads one ONNX bundle per JVM (see "Seat configurations"). The ladder plans those batches anyway, lists them as not played with the reason, and rates from the rest. Every generation still meets `heuristic` and `full`, which connects the fit. Once `evaluator_wiring` takes `-eval onnx:DIR`, the next run plays the missing batches and nothing else.

`--start` and `--stop` give the ladder a window with a hard stop, as `nightly` does. Without them it starts now. Records are always on, for honest play and the check of each game file against its result line, and they go to the ladder folder's `games\`. The command exits 0 when every batch Forge can seat has finished and the fit has ratings, and 1 otherwise.

```
runs\ladder\                        the default ladder folder
    plan.json                       the settings that decide the games, and every player the ladder has seen
    ladder.json                     the ratings with their intervals, each pair's record, and the batches left
    report.txt                      the report
    games\                          every batch's game records
    batches\<first>--<second>\pair-NN-x1-ab\   one plain batch folder and its result.json
```

`x1` has the first player, in label order, on the deck pair's first deck, and `x2` on its second. `ab` has the first player in seat 1, `ba` in seat 2. In `ladder.json`, `players` lists each player with its settings, rank, `rating`, `low`, `high`, decisive games, wins, losses and draws, best first. `pairs` gives each pair of players' record with its Wilson interval, and `ratings_problem` says why there are no ratings when there are none.

The report never prints a bare percentage. Each head-to-head rate carries its Wilson interval, and each rating its profile interval or the word anchor.

#41's second criterion, that the rungs reproduce on the real sim-runner, needs a real run. A 4-game smoke run on 2026-11-08, heuristic against full on one held-out deck pair (initiative-stompy-1 and oops-all-spells-3), 1 worker at below-normal priority, finished all four batches clean in 176 seconds with a tripwire count of 0. initiative-stompy-1 won all four games, whichever AI piloted it, so the split was 2 to 2. That checks the wiring and says nothing about strength. The heuristic against full rung should agree with what #26 measures, once #26 has run.

### Convergence

`converge` is the trust gate from HANDOFF.md. It runs the manifest's four tracked matchups under two players, the one before and the one after, and says for each whether the win rate moved.

```powershell
python -m simrunner converge onnx:gen0 onnx:gen1 --seed 42
```

Players are named as for `ladder`. Each tracked matchup is a `matchup` with that player in both seats, deck A first as the manifest lists it, to the target width (`--width`, 5 points by default). Both players use the same seed, round size and budget, so round r of each starts from the same shuffles. The manifest has to have all four slots filled, and the command refuses before any game otherwise.

**The verdict** follows HANDOFF.md and Spec 6. A matchup converged when the two Wilson intervals overlap, and moved when they don't. Touching counts as overlap. A matchup where either player spent its budget or was cut off has no verdict, and the report says which player and how many more games it needs. Overall, the check moved if any matchup moved, converged only if all four converged, and is incomplete otherwise.

**The difference** is there to read alongside. The report gives after minus before with Newcombe's hybrid score interval, as `cardswap` does. Overlap is the looser test of the two: 200 wins in 400 against 230 in 400 overlap, so the matchup converged, while the difference's interval, +0.6 to +14.3 points, leaves out 0. Two intervals of 5 points each overlap until the rates are about 10 points apart, and the difference flags a move from about 7. The verdict stays the overlap because that's the rule as written. Switching it to the difference is a one-line change in `Verdict.verdict`.

**Reuse.** A matchup's folder is named for its player and decks, `runs\convergence\<player>\<deck A>--<deck B>\`. The generation loop checks gen 1 against gen 0, then gen 2 against gen 1, and the second check reuses gen 1's four matchups. A folder is reused only when its matchup ended, target met or budget spent, under the same settings: seed, workers, width, confidence, round size, budget, clock, search budget, records, honest play, turn cap, tripwire, and each side's deck, mode, evaluator and generation, with the model digest. Workers matter because they split the seeds. A folder with other settings is refused, with the settings that differ. A matchup that failed or was cut off by the hard stop is moved aside to `<deck A>--<deck B>-unfinished-N` and played again.

`--start` and `--stop` give a window, as for `ladder`. After a matchup the hard stop cut off, the check plays no more, and the next start picks up from there. The command exits 0 when all four matchups have a verdict, converged or moved, and 1 otherwise. `convergence.json` is where a script reads which.

```
runs\convergence\                   the default folder
    convergence.json                both players, the settings, and per slot both estimates, the verdict and the difference
    report.txt                      the report
    <player>\<deck A>--<deck B>\    one plain matchup folder per player and tracked matchup
```

In `convergence.json`, `status` is `converged`, `moved` or `incomplete`. Each entry of `matchups` has the slot, both deck ids, `verdict` (`converged`, `moved` or `no verdict`), `why`, and `before` and `after` with the status, interval, counts and whether the folder was reused. A rate appears only when its target was met, and never without its interval.

### Batch output folder

Every batch, nightly or not, writes this layout. `<batch>` is the folder name, which `--run-dir` sets and which defaults to `<time>-seed<seed>`. It doubles as the batch id.

```
runs\<batch>\
    batch.json            where the batch stands
    report.txt            the report, once the batch has ended
    worker-NN.out.log     Forge's stdout for worker NN, written as it arrives
    worker-NN.err.log     Forge's stderr for worker NN
    games\                game records, with --records
    worker-NN\            throwaway profile, deleted when the batch ends
```

`batch.json` holds:

| Field | Meaning |
|---|---|
| `batch` | The folder name |
| `status` | `running`, `complete` or `incomplete` |
| `stopped_at_hard_stop` | True when the stop killed a worker or came before launch |
| `window_start`, `window_stop` | The window as local times, or null for a plain batch |
| `games_attempted`, `games_finished`, `games_not_finished` | The report's counts |

Only `complete` means every game was played. The file says `running` from launch until the batch ends, so a runner that dies can't leave a batch looking finished: `running` with no runner alive means it died. `incomplete` also covers a worker lost to a crash, with `stopped_at_hard_stop` false. The file doesn't exist while a nightly batch waits for its window. It is replaced in one step, so a reader never sees half of it.

`complete` doesn't mean clean. A stale jar can play every game in the wrong mode, and the report and the exit code are what say so.

`games\` holds the game records, one gzip'd JSONL file per game. The runner always creates it, and only `--records` fills it.

- Records go here and not under `worker-NN\`, because the runner deletes that folder when the batch ends. All workers share this one folder, and the seed and game number in the file name keep their files apart.
- The hard stop can land while a file is being written, and `taskkill /F` gives Java no chance to finish. The hook writes to a temporary name in `games\` and renames when the file is closed, so a kill leaves a `.tmp` file at worst, and the loader never sees a truncated `.jsonl.gz`.

### Seeds

Each worker's `-s` is a SHA-256 hash of the batch seed and worker index, cut to the 48 bits `java.util.Random` keeps. Plain `seed + index` would give neighboring seeds, and those give correlated first draws.

Forge calls `MyRandom.setRandom(new Random(seed))` once, before the first game, and nothing reseeds between games. So all the games in one JVM draw from one stream, and these things follow:

- A batch replays as a whole. You can't replay game 4 of a worker without games 1 to 3.
- The same batch seed, game count and worker count give the same games. A different worker count gives a different split and different games.
- A worker's first k games are the same in a longer run. A 20-game batch with seed 1 matched the first two games of all ten workers in the 50-game batch, 20 of 20, on winner and turn, with ten JVMs running.
- Games in one JVM aren't independent. Game 1's starting player is random, and after that the loser of the last game goes first (`GameAction.determineFirstTurnPlayer`).

Two wall clocks can break replay, and both depend on machine load. `-c` stops a game, and its thread can keep drawing from the shared stream until it notices the interrupt. `Game.AI_TIMEOUT` gives each AI decision 5 seconds. `HostedMatch` sets it from a preference for a real match, but a sim doesn't go through `HostedMatch`, so a sim runs on the default. A heuristic decision is fast enough that this limit rarely matters, and the 20 of 20 match above suggests neither clock mattered in those runs. A full-sim decision is a different story, covered next.

AI sideboarding is off in a sim (`GameRules.AISideboardingEnabled` defaults to false), so decks don't change between games of a `-n` run.

### Full-sim replay

A full-sim batch replays from its seed as long as it runs with a search budget, which the runner gives it by default. Issue #50 found out why it didn't before.

**The cause is the 5-second limit.** Four JVMs played the same `-sim full heuristic -n 10 -s 1` side by side with a trace of every top-level AI decision and every random draw:

| | Limit 5 s | Limit 600 s |
|---|---|---|
| Decisions cut short per run | 13, 14, 22, 12 | 0 |
| Traces | No two agree past game 1 | Identical, all 2,642 lines, RNG counts included |
| Games played | 10 | 6, then each JVM ran out of heap |

In the 5-second runs the first difference between each of the six pairs of traces was at a decision that expired. The search stops at a different point each time, and two things follow. The AI passes priority instead of playing, and 51 of the 61 expired decisions had a land among their candidates. And `SpellAbilityPicker.evaluateSa` restores `MyRandom` only on its normal path, so an expiry leaves the game drawing from the search's stream for the rest of the JVM's life, and every later game changes too. The evaluation thread and identity-hash order the issue also suspected showed no effect: with no expiries, the four traces matched with both still in place. The attack thread pool is a different case, listed below.

Nothing printed any of this. The `AI decision timeouts` row read 0 on every run, because that line belongs to the heuristic AI's evaluation thread, and full sim searches on the game thread.

**A longer limit doesn't fix it.** The search has no bound of its own, and the 5 seconds was hiding that. In game 7 of that batch one decision took 335 steps and 37.9 s on an 8 GB heap, and on the default 2 GB heap it ended the JVM with `OutOfMemoryError`. All four JVMs died at the same spot. So decisions don't stay short, and a limit long enough to never fire is also long enough to crash.

**So a sim can count steps instead.** With `-budget`, a decision stops after that many game copies, and `AI_TIMEOUT` is set to the game clock, which leaves `-c` as the only wall clock. A step count doesn't depend on load, so the search stops at the same place every time.

Why 100: over 7 games the 219 decisions that searched took a median of 5 steps, a 99th percentile of 76, and 96 at most apart from the 335. It has to stay well under what runs out of heap. At the time a stop passed priority, as an expiry did, and it cost about as much play as the old limit: on the same seeds the budget stopped 35 decisions where the clock cut 32 and 29, and in the both-seats batch below it stopped 22 where the clock cut 63. These counts come from Burn vs Merfolk and one short batch of Sneak Show vs Slivers. A deck with bigger boards costs more per step, in both time and memory, so check the heap before raising the number.

Issue #51 changed what a stop does to `MyRandom`. See "Stopped decisions" below. The replay runs in the table here were made before it, so their digests no longer come out of the current jar.

Replay runs, 2026-11-05, 4 workers, below-normal priority, `--clock 600`, 2 GB heaps, each compared by digest and line by line on winner, turn, stops and draws:

| Batch | Machine | Digest | Stops | Expiries |
|---|---|---|---|---|
| Burn full, Merfolk heuristic, 24 games, seed 1 | No added load, 56% mean CPU | `79babf2958ec` | 35 | 0 |
| The same | No added load, 79% mean CPU | `79babf2958ec` | 35 | 0 |
| The same | 6 CPU burners, 96% mean CPU | `79babf2958ec` | 35 | 0 |
| Same batch with `--search-budget 0` | No added load | `e4d952e05220` | 0 | 32 |
| The same | No added load | `180acbfa8d26` | 0 | 29 |
| Both seats full, 12 games, seed 2, twice | No added load | `62c43670cd62` both | 22 | 0 |
| Sneak Show full, Slivers heuristic, 4 games, seed 3, twice side by side, 2 workers each, `--clock 90` | 4 JVMs | `cc7e66c48c91` both | 7 | 0 |

Heuristic batches don't change. Twenty games with seed 1 and 4 workers replayed, and the result lines of all four workers matched the logs in `runs\t16-real-burn-merfolk` from before any of this, 104 lines of 104, with no `AI limits:` line.

Cost, same batches, steady-state games per hour over 4 workers:

| | Budget off | Budget 100 |
|---|---|---|
| Burn full, Merfolk heuristic | 602, 539 | 656, 399, and 488 under load |
| Both seats full | 212 | 339, 277 |

The machine was in use, so these move a lot between identical runs: the same 24 games averaged 23.5 s in one run and 37.4 s in the next. With one seat in full sim I can't tell the two apart. With both seats the budget was faster in these runs, which fits the clock cutting nearly three times as many decisions at 5 seconds each, but that is one run against two.

What this doesn't cover:

- The `-c` clock is still wall time. A game that hits it shows as a draw by clock, and that batch won't replay. No game here came close at `--clock 600`.
- `AiAttackController` works out forced attackers on a thread pool, so the order they join combat can depend on timing. None of the four decks here has a card that must attack, going by a search of their scripts for `MustAttack`, `EndOfTurnLeavePlay` and "attacks each combat", so this race was never tested. A deck with such creatures may still drift.
- `StormSolver` has its own wall-clock budget, for tutor storm decks, and these aren't tutor storm decks.
- Hybrid mode gets the budget from the runner, but no hybrid batch was run, and its one-step checks run on the evaluation thread, where a budget doesn't count them.
- The win split says nothing about strength. Seat 1 won 1 of 12 with the budget and 3 of 12 without it on seed 2, against 33 of 67 in the timing run below on another seed. Twelve games can't separate those.
- I did not find what holds the memory in a long search.

### Stopped decisions

Issue #51 changed three things in full sim. All three are behind `Game.AI_SIM_SEARCH`, which `forge sim` sets only when a seat is `hybrid` or `full`, so default play and heuristic seats don't see them.

**A stop plays the best line found.** A decision cut short by the budget or the clock used to pass priority and lose what the search had found. Now the search notes each line once its last action has been simulated and scored, before it looks deeper. At a stop it takes the best of those by value, and plays it only if it raises the available value, which is the rule a finished search uses in `chooseSpellAbilityToPlayImpl`. If it doesn't, or if nothing was scored yet, the decision passes. A line can be several actions long, and the later ones run as an ordinary plan. Only the real decision does this. An AI inside a simulated game still passes on a stop, and any score made after the stop is left out, because that AI may have been cut short while making it.

A stop no longer leaves the game on the search's random stream: the stopped decision puts `MyRandom` back, and the stream stays a function of the seed either way. `evaluateSa` itself is unchanged, so default play keeps the old behavior. The stopped decision also clears the `castSA` that `canPlay` leaves on its candidates, as `AiController` does after its own checks. A finished decision in the picker still leaves it there, which is how stock Forge behaves.

**Suspend isn't retried at declare blockers.** After planning, `SpellAbilityPicker` asks of each ability not at sorcery speed whether it would do better after blockers. Suspend is neither a spell nor an activated ability, so `SpellAbilityAi.isSorcerySpeed` says false, and the retry looks for it in a copy of the game where a sorcery can't be suspended. With a trace on the failure, all 136 errors in the batch below came from that retry: the real game in main phase 1, the copy at declare blockers. The picker now leaves Suspend out of the retry unless its card has flash. `isSorcerySpeed` itself is unchanged, because the heuristic AI uses it.

**A suspended card is worth 6.** `GameStateEvaluator` gives a card in hand 5 points and a card in exile none, so suspending always scored 5 below not suspending, whatever came of it. Stops weren't the cause: with stops fixed and the evaluator not, full sim suspended 0 times in 48 games. The value is one point above a card in hand, because the card still comes and its cost is paid. It was picked by that reasoning and not tuned, and it ties with hard-casting Rift Bolt at the opponent's face. `GameCopier` keeps counters only on the battlefield, so it now keeps them on suspended cards too. Without that, every score made in a copy comes out 6 short of the real game's.

**`Simulation: SA not found!` on Merfolk isn't a bug.** The same retry asks about Merfolk Sovereign's `{T}` ability, and in the copy the Sovereign has attacked. All 16 of those errors in the both-seats batch had it tapped and attacking. If it attacks it can't use the ability, so scoring that retry as a failure is the right answer, and it costs one search step. The line still prints. It will on any creature with a tap ability that the AI attacks with.

Burn vs Merfolk, budget 100, 4 workers, below-normal priority, `--clock 600`, 2 GB heaps, 2026-11-05. "Before" is the jar with only the counters added, and its first 6 games per worker came out as the `79babf2958ec` of issue #50, so the counters and the flag don't change play. "Stops only" has the Suspend timing and stop fixes but not the evaluator change.

| Burn full, Merfolk heuristic, seed 1 | Before | Stops only | After | Heuristic control |
|---|---|---|---|---|
| Games | 96 | 48 | 96 | 96 |
| Rift Bolt suspended | 2, in 1 game | 0 | 46, in 40 games | 52, in 44 games |
| Rift Bolt hard-cast, about | 53 | 25 | 24 | 30 |
| `SA not found`, all Suspend | 136 | 0 | 0 | 0 |
| Budget stops | 145 | 47 | 46 | 0 |
| Stops that played a line | 0 | 27 | 42 | 0 |
| Stops that passed | 145 | 20 | 4 | 0 |
| Stops with a land candidate | 100 | 21 | 34 | 0 |
| Land drops missed on stops | 43 | 1 | 0 | 0 |
| Wins, seat 1 and seat 2 | 39 and 57 | 17 and 31 | 47 and 49 | 41 and 53, 2 draws |
| Mean game | 27.1 s | 31.4 s | 24.4 s | 3.0 s |
| Steady-state games per hour | 539 | 464 | 592 | 4711 |

| Both seats full, seed 2, 24 games | Before | After |
|---|---|---|
| Rift Bolt suspended | 0 | 9 |
| `SA not found` | 30 Suspend, 16 Merfolk Sovereign | 7 Merfolk Sovereign |
| Budget stops | 41 | 22 |
| Stops that played a line | 0 | 21 |
| Stops that passed | 41 | 1 |
| Land drops missed on stops | 12 | 0 |
| Wins, seat 1 and seat 2 | 9 and 15 | 7 and 17 |
| Mean game | 49.3 s | 70.8 s |
| Steady-state games per hour | 297 | 205 |

Rift Bolt hard-casts are every cast line less the suspended count, so they are close, not exact.

Fewer decisions stop after the change. I don't know how much of that is the Suspend retries no longer spending steps and how much is games going differently once the AI plays on.

Replay still holds. The first 6 games of each worker in the 96-game "after" batch and a separate 24-game batch beside 4 CPU burners both gave `46c4d099b7c5`, and all 24 games matched on winner, turn, draw cause and every number on the `AI limits:` line. The two runs averaged 82% and 93% machine CPU. Heuristic output didn't change: 48 games with `--sim-mode heuristic heuristic` matched the first 12 games per worker of the "before" jar, `74aaf7d8dac2`, and 20 games with no `-sim` matched the logs in `runs\t16-real-burn-merfolk` 100 lines of 100, with no `AI limits:` line.

What this doesn't cover:

- The win rates say nothing yet. Seat 1 went from 39 of 96 to 47 of 96, which is 41% to 49% with 95% intervals of 31% to 51% and 39% to 59%. Those overlap, the change bundles three fixes, and the games were not played against the same draws. In the both-seats batch it went down, 9 to 7 of 24.
- Throughput moved both ways. One seat went from 539 to 592 games per hour, but `mvn` builds and tests ran beside the "before" batch and not the "after" one. Both seats went from 297 to 205, a mean game of 49.3 s to 70.8 s, on 24 games each with about 20 s of a second batch beside the "after" run. That drop is too big to wave away. I didn't find its cause.
- Only Rift Bolt and only this matchup. No other suspend card was played.
- A stop by the clock takes the same path as one by the budget, in `--search-budget 0` runs, and no batch ran that way. The Forge tests stop searches with the budget only.
- No batch had a hybrid seat. `AI_SIM_SEARCH` is on for hybrid, so the evaluator change reaches it through `OnePlaySafetyChecker`.
- A search that a nested AI's catch let finish is treated as stopped, by reading the source. Both-seats games can reach that path, but no test forces it.

### Game records

`docs/game-record.md` is the format. This is how they get written.

`GameRecorder` in the fork's `forge.view` package subscribes to the real game's own event bus, so the AI's copied games never reach it. `GameEventPlayerPriority` gives it a snapshot from each side, and `SnapshotLine` builds those. It holds the lines in memory and writes the file after `simulateSingleMatch` has finished with the game. Only that method sees every way a game can stop, so it tells the recorder why, and checks with the recorder before it forces the game over. The recorder takes `winner` from Forge's outcome only when the rules had ended the game by then, and a game forced over with no reason gets no file.

The turn cap sits on `GameEventTurnBegan` and marks both players drawn before it ends the game, as a draw effect does. A plain `setGameOver(Draw)` gets the false winner that a clock stop gets. A capped game is a true draw in Forge's own eyes, so after one, seat 1 goes first in the next game.

The hook only reads. It adds no draw from `MyRandom`, and only `-honest` lets the AI read the reveal log. So with `-honest` off a game plays the same with records on or off, unless the cap ends it.

Checked on 2026-11-05 with 2 workers, on a jar built from the fork's `t18-snapshot-hook`. "Before" is the jar from before the hook. Burn vs Merfolk, 20 games, seed 1, `--clock 120` unless the table says otherwise.

| Run | Before | Hook off | Hook on |
|---|---|---|---|
| No `-sim` | `84c42eeea82f` | `84c42eeea82f` | not run |
| `--sim-mode heuristic heuristic`, so the digest has random draws | `6f2c4c7c1863` | `6f2c4c7c1863` | `6f2c4c7c1863` |
| `--sim-mode full heuristic`, 6 games, seed 2, `--clock 300` | `6d38e6c38b07` | not run | `6d38e6c38b07` |

The hook's cost is under what these runs can show. The 20 heuristic games took 38.1 s with it off and 38.3 s with it on, and the 6 full-sim games 61.4 s before and 58.9 s with it on. A game file is 10 to 30 KB for 150 to 290 lines.

Real files, each loaded by `gamerecord` and converted to Parquet with nothing rejected:

| Batch | Games | End reasons |
|---|---|---|
| Burn vs Merfolk, heuristic | 20 | 19 `win`, 1 `draw_rules` |
| Burn vs Merfolk, `--clock 1` | 8 | 8 `draw_clock`, all with null `winner`, while Forge printed a winner for each |
| Burn vs Merfolk, `--turn-cap 5` | 6 | 6 `draw_turn_cap`, `final_turn` 6, last snapshot on turn 5 |
| Mono Black Devotion vs Merfolk, heuristic | 6 | 6 `win` |
| Abhorrent Oculus vs Bloodtithe Harvester, heuristic | 8 | 8 `win` |
| Conspicuous Snoop vs Merfolk, heuristic | 6 | 6 `win` |
| Burn full, Merfolk heuristic | 6 | 6 `win` |

I read these files as well as validating them. The Oculus batch has manifested cards named to their controller and null to the other side, attacks on a planeswalker, blocks, marked damage, a stack two deep and a back face. In the Devotion batch the log held Duress and Silvergill Adept reveals past end steps, and let go of a card when it was cast or played. In the Snoop batch it picked up library tops that no reveal call ever announced. A Miracle trigger puts a card's name on the stack while the card is still in hand, and that card stays on the log until it is cast.

What this doesn't cover:

- No real game had a face-down spell on the stack, or a morph at all. The face-down spell rule has a Java test only.
- No clock stop came before the first priority pass, so that no-file case has a Java test only, and so does a real exception.
- No batch had a split card, so the open question in `docs/game-record.md` about `Fire // Ice` stays open.
- Karn's restart is handled by reading the source. No run hit it.

### Tripwire

The tripwire (#20) catches forge-ai reading a hand or library that isn't the deciding player's, or the real side of a face-down card that player may not see. #21 fixes what it finds, and `docs/peek-fixes.md` lists those fixes. A batch of training games counts as honest only when it reports zero, so it leans toward a false hit over a miss.

The spec says size-only reads bypass the check because they go through a different call. They don't. `forge-ai` has 116 `getCardsIn(Hand|Library).size()` and `.isEmpty()` sites on the same call as a real read. Nothing marked a decision made during resolution either, and that's where naming, Clash and fetch-type run. So the check became two parts.

**The recording view.** `HiddenReadWatch` hangs off `Game.HIDDEN_READ_WATCH`. `Zone.getCards` asks it what to hand over. When forge-ai asks for an opponent's hand or library, it gets a view of the real list. `size()` and `isEmpty()` pass through, and any other use is one hit. `Zone.get`, `Zone.iterator` and `Zone.contains(predicate)` hand over no collection, so they count at once. `Player.getCardsIn` over several zones combines them into one such view, because copying them would be a read and `ChangeZoneAi` only asks whether the result is empty. No site in `forge-ai` changed.

Who asked is settled when the zone hands the cards over, from the first stack frame that isn't plumbing. Plumbing is a named list of `getCardsIn`-style methods, plus `java`, `jdk` and `forge.util`. What they did is settled later, by the view. Both can't happen at the point of use: in `CardLists.filter(opp.getCardsIn(Hand), ...)` the frame that iterates is a forge-game helper, and it would pass for the rules engine.

**The decider.** forge-game can't see forge-ai, so every AI decision reaches the AI through a controller call. `ScopedPlayerControllerAi` overrides each of them to bracket it with `enter` and `exit`, on `Game.AI_DECIDERS`, a stack each game has. Honest play reads it too, so it doesn't depend on the watch. That covers resolve-time choices, and it works across the heuristic AI's eval thread, because the stack isn't per thread. `AITimeoutTracker` sessions don't do this job. They skip resolution, and they open under the wrong player when one AI predicts another's attack. `HiddenReadWatchTest.everyControllerDecisionIsBracketed` fails if an upstream merge adds a decision without an override, which is how it found `payCostDuringRoll`, a method with no `@Override` on it. A forge-ai read with no decider open counts as a hit, marked `no decider: `, so a way into the AI that no bracket covers can't hide behind a zero.

**What doesn't count:**

- The AI's own zones, and cards the rules let it see, by `CardView.canBeShownTo`. An empty zone gives nothing away.
- Rules code running inside a decision: costs, legality, static abilities, `AbilityUtils.calculateAmount`. The first frame past the plumbing is forge-game, so the rules get the real list.
- Reads on a copy. `HIDDEN_READ_WATCH` is never copied, so a copy has no watch. `GameCopier` reading the real game to make one is exempt by class. What the copy holds is checked instead, under "Pruned copies" below.
- `Game.getCardState`, which looks up a card the AI already holds, and `PlayerZone.getCardsPlayerCanActivate`, which returns only what that player may play or look at.

**What it can't see:**

- Deck lists: `getRegisteredPlayer().getDeck()`. `ComputerUtil:2368`, `ChangeZoneAi:1513`, `SpecialCardAi:403` and `PlayerControllerAi:140` read an opponent's, and `-honest` closes all four by hand. Nothing would catch a fifth.
- A face-down card's real side reached some other way than `Card.getState`, `getOriginalState`, `getPaperCard` and `getRules`, such as `getStates()` or a `CardState` object kept from before it turned over.
- Rules questions the AI asks about an opponent, when forge-game does the reading. `sa.canPlay()` on an opponent's spell stays silent, and the AI learns the answer anyway. `ComputerUtilCost.canPayCost` for an opponent does hit, because it runs through `ComputerUtilMana`, which is forge-ai.
- Where a card it already holds has gone: `Zone.contains(card)`, `Card.getZone()`, `Game.getZoneOf`. #21's fix for `NamedDiscard` needs `Zone.contains(card)` to stay uncounted.
- The order of the AI's own library, which story 10 exempts (`ComputerUtilAbility:58`).
- Subgames. A `SubgameEffect` game is a new `Game` with no watch.

The Fog hand read can't happen in a real game, going by its call sites, so the ticket's Fog test calls `ComputerUtil.hasAFogEffect(opp, ai, false)` directly. The proof that resolve-time decisions are covered is in two other tests, which open no decision themselves. They put Conjurer's Ban and Whirlpool Whelm on the real stack and resolve it. `PlayerControllerAi.chooseCardName` hits on the first, and `ClashAi.chooseSinglePlayer:75` on the second, with the library size read at L67 in the same method staying silent. With plain controllers the same resolve hits as `no decider: `.

Each game prints `Tripwire: N hits` and one `Tripwire hit: N SIGNATURE` line per signature before its result lines. The hits go to stdout as well as the record for three reasons. A game that threw gets no file, a run without `-record` has no file at all, and the record has no count per signature.

Checked on 2026-11-05 with 2 workers, on a jar built from the fork's `t20-tripwire`. The digests are the ones under "Game records", with the same decks and seeds.

| Run | Digest before | Records off | Records on, tripwire off | Records on, tripwire on |
|---|---|---|---|---|
| No `-sim` | `84c42eeea82f` | `84c42eeea82f` | not run | not run |
| `--sim-mode heuristic heuristic` | `6f2c4c7c1863` | `6f2c4c7c1863` | `6f2c4c7c1863` | `6f2c4c7c1863` |
| `--sim-mode full heuristic`, 6 games, seed 2, `--clock 300` | `6d38e6c38b07` | not run | `6d38e6c38b07` | `6d38e6c38b07` |

So a game plays the same with the tripwire on. The view has to stay a `List` for that, because `Aggregates.random` draws differently from anything else. The 20 heuristic games took 41.1 s with it off and 43.9 s with it on, startup included, and the 6 full-sim games 62.1 s and 61.9 s.

Two real batches, heuristic seats, `--clock 120`, 30 games each:

| Batch | Seed | Hits per game | Games with hits | `no decider` hits |
|---|---|---|---|---|
| Burn vs Merfolk (`GAL_`) | 11 | max 506, mean 276.90 | 30 of 30 | 0 |
| Mono Black Devotion vs Bloodtithe Harvester (`GAM_`, `GAS_`) | 12 | max 563, mean 339.63 | 30 of 30 | 0 |

The signatures, by first frame, summed over both batches:

| Site | Hits | Read |
|---|---|---|
| `ComputerUtilAbility.getAvailableCards:78` | 18252 | `p.getCardsIn(Library).get(0)` for every player, on every spell choice. About 99% of all hits. The opponent's top card goes in with the candidates, and `getSpellAbilities` then keeps what the AI may play |
| `ComputerUtilMana.getAvailableManaSources:1647` | 134 | The hand of an opponent the AI is testing a payment for, through `canPayCost` and `counterSpellRestriction` |
| `ComputerUtil.getBestNearTermDiscardThreat:2455` | 48 | `CardLists.count` on the discarder's hand, inside `chooseCardsToDiscardFrom` while a discard spell resolves. Going by `DiscardEffect`, a spell like Duress reveals the hand first, so #21 should be able to answer this from the reveal log. I didn't check that game by game |
| `PlayerControllerAi.orderMoveToZoneList:665` | 38 | `getCardsInGame()` searching every zone for Volrath's Shapeshifter |
| `ComputerUtilMana.sortManaAbilities:163` | 24 | The same payment test, on the payer's hand |

None of them is the Fog hand read, which fits the reading of its call sites. `getBestNearTermDiscardThreat` and `orderMoveToZoneList` run inside controller calls that no `AITimeoutTracker` session opens, so real games exercised the new scope too, not only the tests.

An early run on the same pair had 459 hits at `ChangeZoneAi.hiddenTriggerAI:528`. That was a count read, through the several-zones overload, and it's why that overload now builds a view. The hits are gone from the final jar.

### Pruned copies

Issue #25. The search plays each line it tries on a copy of the game that `GameCopier` makes. Before #25 a copy held every card as it really was, so a full-sim seat searched with the opponent's hand, library and face-down cards in view, however clean the tripwire read.

`-prune on`, the default for a `hybrid` or `full` seat, sets `Game.AI_PRUNE_HIDDEN_INFO`. A copy made for a seat then holds this in place of each card of another player whose identity that seat can't see:

- A card the seat may see right now, such as a library top it may look at, stays as it is, where it is.
- In another player's hand and library, the cards on the seat's notepad (`Game.REVEAL_LOG`, see `docs/peek-fixes.md`) last seen in that zone fill the unseen slots from the top, in id order. The rest are placeholders: a card named `hidden`, an artifact with no mana cost, so nobody can cast it. Zone sizes and card ids stay the same.
- A face-down card anywhere else is the card the notepad wrote down for that same card in that same zone. Otherwise it is a face-down placeholder, which plays as the usual nameless 2/2.

The seat's own hand and library stay as they are, in order, as story 10 allows.

The copy is built from what the seat knows, never from where the real cards are. A card seen in hand and then put back on the library unseen, as Brainstorm does, is still in the copy's hand, which is what a person would think. Library order means nothing once the identities are gone, and the search still sees the counts.

The prune decides before any of the copier's paths runs, so the paper-card path, the fallback path that builds a card from its current state, and tokens all prune the same way. A snapshot copy (`EXPERIMENTAL_RESTORE_SNAPSHOT`) copies every card as it is, so the copier never makes one while pruning. A placeholder or a believed card takes nothing else from the real card it stands in for: no SVars, clone states or remembered objects. What left the battlefield or the graveyard this turn was public, so a card that has since gone into a hidden zone is rebuilt for revolt and morbid from its last known information.

A pruned copy gets a reveal log of its own, with what the seat had written down plus what it may see right now, so honest play on the copy goes by the same cards as on the real game. The setting is copied too, so a copy of a copy stays pruned.

**The tripwire checks copies.** Reads on a copy still don't count, because a copy has no watch. Instead `GameCopier` checks each copy it makes of the real game. Every hand, library and face-down card the seat can't know, going by the real game, has to be a placeholder in the copy or carry a name from the seat's notepad. Each kind of place that fails counts one hit per copy, as `copy: Hand`, `copy: Library` or `copy: face-down Battlefield`. A game with a searching seat prints `Tripwire copies: N checked` after its tripwire lines. The check looks only at what the copy ended up holding, so it also catches a copy made with `-prune off`. It shares the copier's test of what the seat may see, `canBeShownTo` and `canFaceDownBeShownTo`, so a mistake there would get past both.

Checked on 2026-11-07 and 11-08 with 2 workers, below-normal priority, `--clock 2400`, budget 100, records and honest play on. The 647 Forge tests in the simulation, peek-fix, tripwire, reveal log, `SimulateMatch` and recorder classes pass, 16 of them new in `GameCopierPruneTest`.

**Heuristic play didn't change.** Heuristic seats make no copies, print no `Tripwire copies:` line, and get no prune.

| Batch | Old jar, `a5b2fc7` | This jar |
|---|---|---|
| Burn vs Merfolk (`GAL_`), 20 games, seed 1 | record `d60a45654629`, result `6f2c4c7c1863` | the same |
| UR Tempo vs Dimir Tempo, 40 games, seed 2501 | result `29f389b4ee3b` in 3 runs, record `bee0c0cb5e54` twice and `100fceff1839` once | result `29f389b4ee3b` in 2 runs, record `c80e9e39ae4a` and `100fceff1839` |

The UR Tempo record digest moves between runs of the same jar, and the old jar's third run matches this jar's second. Every difference is in one game, game 5 of the worker with seed 161326939506583. There two Dragon's Rage Channelers swap which one gets the pump and which one gets blocked, with the same random draws in all five runs. That race between two identical creatures was there before #25.

**Full sim.** Boros Aggro full against Dimir Tempo heuristic, 50 games, seed 2510. With the prune on, all 50 finished. The tripwire checked 23,339 copies and found nothing in any game, and there were no stack traces. Seat 1 won 39, seat 2 won 8, and 3 games hit the clock, at turns 7, 8 and 20. Record digest `9e0d0fccac23`.

The old jar's batch stopped after 26 games, so this table compares the same 26 places in the run: the first 12 games of worker 0 and the first 14 of worker 1. Each worker plays its games in one JVM, so a game only matches across jars until the first one that went differently. The first 4 of worker 0 and the first 3 of worker 1 had the same random draws on both jars.

| Same 26 places | Old jar | Prune on |
|---|---|---|
| Wins, seat 1 and seat 2 | 22 and 2 | 19 and 4 |
| Draws | 2, both by exception | 3, all by clock |
| Budget stops | 276 | 430 |
| Stops that played a line | 69 | 81 |
| Stops that passed | 207 | 349 |
| Game time, summed | 3,254 s | 11,719 s |

The win split doesn't say whether the AI got weaker. 22 of 26 against 19 of 26 is inside the noise, and after the first few games the two columns aren't the same games. The stops did go up by about half. I think a search that can't see the opponent's answers has fewer clearly good lines, but I didn't check that decision by decision.

The game time can't be compared. The prune-on batch ran beside the 4-worker gen-0 pool, and the old jar ran after the pool had stopped. Game 1 of worker 0 was the same game on both jars and took 51 s with the prune and 28 s without. Copying itself got cheaper: on a test game with 45-card libraries, a pruned copy took 2.8 ms and an unpruned one 3.3 ms, the copy check included. The three clock draws are a question I didn't settle. They could be load, or a pruned search spending more of each turn at its budget.

The old jar threw `RuntimeException: -3` at `SpellAbilityChoicesIterator.advance:216` in game 8 of both workers, and this jar threw nothing in its 50 games. By game 8 those were different games, so this doesn't show the exception is gone.

What wasn't run: a full-sim batch with `-prune off` against the old jar, which would show that turning the prune off gives back the old games exactly. `withThePruneOffACopyHoldsEveryCard` checks one copy, and the heuristic digests check everything outside the search.

What this doesn't cover:

- The notepad only learns what the rules reveal or let a seat look at. It misses public moves into a hidden zone, such as a creature bounced to its owner's hand or a revealed library top that then gets drawn. A person knows where those went and the copy doesn't: the bounced card is a placeholder, and the drawn card is still believed to be in the library. Fixing that is a change to `RevealLog`, and it would change honest heuristic play as well.
- Believed library cards go on top. That is right for a top the seat saw and that stayed there, and wrong after a shuffle. The notepad keeps no order.
- Nothing can cast a placeholder, so in a copy the opponent never answers the line being searched with a card from hand. Before, it could answer with the real one. Nothing samples a likely hand from the cards the opponent has shown.
- The other seat's notepad isn't copied. Inside one seat's copy, the other seat's simulated decisions go by public cards under honest play, and by the copy's cards without it.
- Deck lists still go into the copy, because the copy's players are built from the real ones. `-honest` closes the four known reads of them.
- A copy made from a copy isn't checked. It is pruned, and it comes from a copy that was.
- `-mc` makes copies for Flat Monte Carlo. A run with `-mc` and heuristic seats doesn't prune unless it passes `-prune on`, and with the tripwire on those copies count as hits.

### Why each worker has its own profile

Reading the source, sharing almost works. `ExceptionHandler` gives each JVM its own log slot with `CREATE_NEW` and a `FileLock`, and a sim only reads preferences. But `FModel.initialize` checks `isDirectory()`, then throws if `mkdirs()` returns false, and `mkdirs()` returns false when another JVM got there first. So ten JVMs starting on an empty profile, which is what `mvn clean` leaves, can crash each other. Later tickets will also write more files during a run. One profile each costs a few folders, so each worker gets one.

Forge reads `forge.profile.properties` from its working directory and no flag moves it, so a separate profile needs a separate working directory. Each worker therefore runs in `runs\<batch>\worker-NN\`, not in `forge-gui`, with:

- its own `forge.profile.properties`, pointing at `user\` and `cache\` inside that folder,
- `res`, a junction to `forge-gui\res`, because Forge finds it through the working directory too,
- copies of the two decks as `decks\seat1.dck` and `decks\seat2.dck`, so they can start in different folders,
- `APPDATA` and `LOCALAPPDATA` set to folders inside it. If the properties file were ever not read, Forge's fallback would still land here.

After the JVM exits, the runner checks for a Forge log in the worker's `user\` folder as proof it used the profile. It then unlinks `res` and deletes the worker folder. `--keep-profiles` keeps them for debugging.

A run killed hard can leave a worker folder behind with the junction in it. Remove the junction first (`cmd /c rmdir runs\<batch>\worker-00\res`), because some delete tools follow junctions. The runner refuses a run folder inside the Forge clone so that `mvn clean` can never meet one.

The `java` on PATH here is Oracle's launcher, which starts the real JVM as a child process. So the runner kills a hung worker with `taskkill /T`, because killing the launcher alone leaves the JVM running.

### Measured throughput

Run 2026-11-04 on the build machine: i5-10600K, 12 logical cores, 32 GB, 10 workers, stock heuristic AI, `--clock 600`.

| | Burn vs Merfolk, seed 1 | Sneak Show vs Slivers, seed 2 |
|---|---|---|
| Games finished | 50 of 50 | 50 of 50 |
| Wins, seat 1 and seat 2 | 23 and 25 | 17 and 33 |
| Draws | 2, by rules | 0 |
| Wall time | 219.5 s | 118.4 s |
| Startup per worker, mean | 158.9 s | 70.3 s |
| Games per hour, startup included | 820 | 1521 |
| Steady-state games per hour | 3140 | 4056 |
| Usable games in 8 hours, steady rate | 23985 | 32368 |

Startup is the expensive part of a small batch. It was 15 to 20 seconds for one JVM alone and 45 to 50 for two. For ten it was 159, 142 and 70 seconds across three batches, and I don't know why it varied that much. Peak memory across ten JVMs was 6.9 GB, and under 700 MB for any one.

Each worker played five games, so the JVMs were never warm. In the first batch, mean game time fell from 18.4 seconds for each worker's first game to 5.2 for its fifth, though some of that is other workers finishing. The longer runs under "Full simulation" below show that a warm JVM is much faster, so treat these rates as a floor.

These numbers are the stock heuristic AI with no snapshot hook, no tripwire and shipped `GAL_` decks.

### Full simulation

Run 2026-11-05 on the same machine, before the search budget existed, so these games ran on the 5-second limit: Burn vs Merfolk, 4 workers, `--clock 600`, the default 2048m heap, below-normal priority, on a machine that was not kept idle, so CPU figures include whatever else was running. Each leg stopped at a fixed wall time rather than a game count, and the three legs took 28 minutes in all. The heuristic leg is a control. The table above used 10 cold workers, so it can't be compared with these directly.

| | Full on seat 1 (Burn) | Full on both seats | Heuristic control |
|---|---|---|---|
| Wall time | 720 s | 720 s | 240 s |
| Games finished | 147 | 67 | 414 |
| Wins, seat 1 and seat 2 | 65 and 82 | 33 and 34 | 167 and 242 |
| Draws | 0 | 0 | 5, by rules |
| Draws by clock or exception | 0 | 0 | 0 |
| Steady-state games per hour, per worker | 201 | 97 | 2233 |
| Mean game | 17.9 s | 37.1 s | 1.6 s |
| Median game | 12.5 s | 27.0 s | 1.4 s |
| Worst game | 81.3 s | 149.4 s | 10.4 s |
| Mean turns, worst | 6.3, 14 | 6.0, 16 | 6.9, 16 |
| Usable games in 8 hours, 4 workers | 6439 | 3093 | 70405 |
| Mean machine CPU | 56% | 66% | 68% |

Full sim on one seat is about 11 times slower than the control, and on both seats about 23 times slower. Kill criterion 1 needs about 500 usable games a night. Four workers clear that by 13 times with one seat and 6 times with both, on this matchup.

Scaling to 10 workers is an extrapolation, because no 10-worker full-sim run exists. Multiplying by 2.5 gives about 16,000 a night for one seat and 7,700 for both. That is the high end, because each JVM used more than one core: 4 workers already took 56% to 66% of a 12-thread machine. Stopping at 100% CPU gives about 11,500 and 4,700.

What this doesn't cover:

- Burn vs Merfolk games last about 6 turns. Search time grows with the choices on offer, so a slower matchup will run below these rates. No other deck pair was timed.
- Each leg killed its workers at the deadline, so 4 games per leg in progress at that moment aren't counted. The worst game could be longer than the table shows.
- No game came near the clock or turn 50. These runs were before the turn cap, so that one was read from the `Turn` line.
- Full on seat 1 won 65 of 147, and the same deck with the heuristic AI won 167 of 414. That is 44% against 41%, inside the noise, so these runs show no strength gain from full sim. They weren't built to measure one.
- These runs came before #25, so the search saw the opponent's hand and library. The hook and tripwire weren't in yet either. "Game records" below has the hook's cost.

Memory peaked at 5.2 GB across 4 JVMs. No run produced a stack trace.

### Evaluators

The full-sim search scores each state it reaches, and `-eval` picks what scores it, per seat. Issue #22 put a seam there: an evaluator gives a win probability, and `TwoLookScorer` in the fork turns it into Forge's two integers. Issue #23 added the first two evaluators behind it.

- `forge` is Forge's hand-tuned `GameStateEvaluator`, called as stock Forge calls it. It doesn't go through the seam. A seat without `-eval` plays this.
- `random` scores every state with a uniform draw. It has its own `java.util.Random`, seeded from `-s` and the seat number, and draws nothing from Forge's `MyRandom`. So a seeded run with a search budget still replays.
- `passthrough` is Forge's evaluator through the seam. It hands `TwoLookScorer` Forge's score as a probability, chosen so that the scorer gets back Forge's integer exactly. Forge's evaluator also returns its win and loss bands for a live game whose coming combat ends it. Those keep their order, at the top and bottom of the seam's range.

A seat picks one in the fork's `SimulateMatch`: the name goes through `Evaluators.scorer(name, seed)`, which returns a `SearchScorer`, and that goes on the seat's `LobbyPlayerAi` with `setSearchScorer`. The search's copies of the game share the lobby player, so they score the same way. A family of names with an argument, `prefix:argument`, is registered into `Evaluators` from outside: `Evaluators` is in forge-ai, and the ONNX evaluator and ONNX Runtime are in forge-gui-desktop, which depends on forge-ai. `SimulateMatch` registers `onnx` with `OnnxEvaluators.register()` as `forge sim` starts. The ONNX evaluator below is that family.

A game record doesn't say which evaluator a seat used. Adding it means a schema change, so it waits for a ticket that needs it.

**Random finishes games.** Run 2026-11-07 with fork commit `9327d11`: Burn against Merfolk, the two `GAL_` decks from step 5, `--sim-mode full full --evaluator random random`, seed 1, 10 games on 2 workers, the default budget of 100. All 10 finished, 4 to seat 1 and 6 to seat 2, in 5 to 12 turns and 15 to 60 seconds a game. No draws and no stack traces. The batch was clean and exited 0. It ran without records, so honest play was off and the tripwire caught the stock AI's own peeks, 306 hits a game on average. That says nothing about the evaluator.

**Passthrough doesn't play as stock does.** Every run below is Burn against Merfolk, `--sim-mode full full`, seed 2, 20 games on 2 workers, the default budget of 100, with `--records`. The comparison is record against record, game by game.

| Run | Seat 1 and seat 2 wins | Record digest | Result digest | Same winner as stock | Same record as stock |
|---|---|---|---|---|---|
| stock, no `-eval` | 8 and 12 | `7753a6080bcd` | `b5da809ebc07` | | |
| `-eval forge forge` | 8 and 12 | `7753a6080bcd` | `b5da809ebc07` | 20 of 20 | 20 of 20 |
| `passthrough`, fork `9327d11` | 6 and 10, 4 exception draws | `6f19b94fa46c` | `f3fb12f833ba` | 8 of 20 | 1 of 20 |
| `passthrough`, fork `164dd62` | 9 and 11 | `ff0943f49b68` | `95ce6b2eb447` | 9 of 20 | 1 of 20 |

The second row is the control. Stock replays itself, and `-eval forge` changes nothing but the player names.

Read the last two columns with care. One JVM plays its games from one random stream, so once a game parts from stock, every later game in that JVM starts from a different stream and is a different game. The "same winner" count after that point is a coin toss. What counts is where each JVM first parts. On fork `164dd62`, worker 0's first game is the same card by card, but its search drew differently on the way, so its second game differs from the first snapshot. Worker 1's first game parts at snapshot 112 of 118. Every later game in both workers differs from its first snapshot.

The first passthrough run turned up two faults in the second look, both fixed in `164dd62`:

- **A crash.** The second look took a sick creature out of the battlefield into its owner's `None` zone. When another card remembered that creature, Forge's combat look copied the derived state and `GameCopier` threw `Couldn't map`. That happened in 4 games, on Merrow Reejerey and Cursecatcher. The creature now goes to no zone, which the copier already skips.
- **The game's stream.** The passthrough's second look runs Forge's evaluator again, whose combat look runs the AI's attack code, which draws from `MyRandom`. Stock takes no second look, so the stream moved on where stock's didn't, and the next game in the JVM was a different game. The second look now draws from a generator of its own. Those draws still count in the `random draws` of the `AI limits:` line, so that number can't be compared with stock's.

Three causes remain, and they come from the design of the seam rather than from a fault:

- **Forge's bands lose their meaning.** Forge's evaluator gives a win or loss band when the combat it plays out for this turn ends the game. Stock's search stops looking deeper once its best line scores a win (`SimulationController.shouldRecurse`), and its effect cache stores score changes that include the band. Through the seam a band is a live score near the top or bottom of the range, so the search goes on and caches other numbers. A diagnostic scorer that gave Forge's own two numbers through the mapping, with no second look, still parted from stock: 3 of 20 records the same, and a game with the same record drew 2777 times to stock's 1658. Its scores differ from stock's in the bands and nowhere else. 14% of the states the passthrough scored had a band for Forge's value.
- **A sick lord loses its pump.** Forge counts a sick creature as 0 but keeps what its static abilities give the rest of the board. The second look removes the creature, and the pump goes with it. This is the point where worker 1's first game parted. On Merfolk's turn, stock cast Phantasmal Image as a copy of Merrow Reejerey before combat, because the copy's +1/+1 raised the available value. The passthrough held the Image and attacked first, because without the pump the cast didn't raise it. A sick lord does pump this turn's attackers, so here Forge is right and the second look is wrong. I read this from the two records and the counts below, not from the scores at that decision.
- **A sick creature can't block in the second look.** On the opponent's turn, a creature the AI flashed in is sick by Forge's rule, so the second look removes it, and Forge's combat look then plays the opponent's attack with one blocker fewer.

A throwaway scorer counted where the passthrough's available value differs from Forge's over 11,428 states it scored, played the same way: 1,803 states differ. 1,619 had a sick creature with a static ability, a lord or a copy of one. 74 were on the opponent's turn with no such creature, which I take to be the missing blocker. 110 had a band on one side. The two decks have no mana creatures, so the opposite case never came up: a sick mana creature can't tap, and Forge still counts its mana, where the second look doesn't. `EvaluatorsTest` pins that case with Llanowar Elves. The diagnostic run ran out of its 2048m heap after 4 games. Those 4 games match the `164dd62` passthrough record for record, so the counts describe the passthrough's own play.

What this doesn't cover: one matchup, one seed, 20 games. The passthrough runs took 1035 to 1259 seconds against stock's 747, and memory wasn't measured.

### The ONNX evaluator

Issue #35 lets a seat's search score states with an export bundle from `python -m setmodel export`, through the evaluator seam of #22. On the fork's `t-eval-onnx` branch, `-eval onnx:DIR` picks it per seat, with the bundle in `DIR`. Use an absolute path, because each worker runs in its own folder.

- Each folder loads once per JVM, the first time a seat names it, and is kept. Two spellings of one folder are one folder. Seats on one folder share its model, and seats on two folders get two.
- Forge checks `-eval` before any game, and that loads every bundle named. A folder that won't load stops the run with `-eval onnx:DIR can't be used: ...` and the reason, which the runner reads as Forge giving up before playing.
- `-Dforge.onnx.threads=N` gives ONNX Runtime N threads per bundle. The default is 1, because workers already run in parallel.
- `-Dforge.onnx.bundle` and `-Dforge.onnx.seats`, which picked it on the #35 branch, are gone. A run that still sets either is refused, so it can't play Forge's evaluator under the model's name.

Only a `full` or `hybrid` seat searches, and Forge refuses `onnx:DIR` for a `heuristic` seat. Each JVM writes `ONNX evaluator: loaded ...` to stderr once per bundle it loads, `ONNX evaluator: seat N searches with DIR` for each seat on a bundle, and at exit one summary line per bundle with the call count, the time per call, and the folder it came from.

The sim-runner sets `-eval` itself from `--evaluator onnx:GENERATION` (see "Seat configurations"), so `--java` only has to name the patched JDK below. The runs in this section came before that, on the #35 branch, and went through a wrapper script that added the properties:

```
@echo off
"C:\...\runs\t35-jdk21-vc14.51\bin\java.exe" "-Dforge.onnx.bundle=C:\...\runs\dryrun\bundle" -Dforge.onnx.seats=1 %*
```

```powershell
python -m simrunner batch boros-aggro-1 dimir-tempo-1 --games 10 --seed 3501 --workers 2 --forge ..\forge-wt-35 --sim-mode full heuristic --records --run-dir runs/t35-onnx-full --java C:\...\java-onnx.cmd
```

**The JDK on this machine can't run it as installed.** ONNX Runtime 1.31 needs `msvcp140.dll` 14.40 or newer. The JVM loads that file from its own `bin` folder at startup, before ONNX Runtime asks for it, and Oracle JDK 21.0.2 ships 14.31. ONNX Runtime then binds to the old copy, and the JVM dies with an access violation in `msvcp140.dll` while the evaluator starts. Loading the newer DLL from `C:\Windows\System32` first doesn't help, because by then the old one is already in the process. The evaluator now reads the file's version first and refuses with a message instead.

The fix used here leaves the installed JDK alone. It is a jlink image of the same JDK with the three runtime DLLs copied in from `System32`, which held 14.51:

```powershell
$rt = "runs\t35-jdk21-vc14.51"
& "C:\Program Files\Java\jdk-21\bin\jlink.exe" --add-modules ALL-MODULE-PATH --module-path "C:\Program Files\Java\jdk-21\jmods" --output $rt --no-header-files --no-man-pages
foreach ($d in "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll") { Copy-Item "C:\Windows\System32\$d" "$rt\bin\$d" -Force }
```

It is the same JVM build, 21.0.2+13, and 167 MB. Maven's tests need it too, through `-Djvm=...\bin\java.exe`. A newer JDK whose `bin` holds a 14.40 or newer `msvcp140.dll` would also do, but none was tried.

The ONNX Runtime jar holds the native libraries for every platform it supports, so it adds about 50 MB to the jar with dependencies, which went from 32 MB to 84 MB.

On 2026-11-07, 10 games of boros-aggro-1 (full, on the dry-run bundle) against dimir-tempo-1 (heuristic) ran with 2 workers, budget 100, records on and below-normal priority, beside the gen-0 pool batch. All 10 finished: 5 wins for seat 1, 3 for seat 2, and 2 draws by exception. Both exceptions were in the search's own bookkeeping, `SpellAbilityChoicesIterator.advance` throwing `RuntimeException: -1` and a null `getLastDecision()` in `SimulationController.doneEvaluating`. Both come from the iterator that walks a spell's choices, inside a nested search, and mean its count of open choices went wrong. A second batch, seed 3502, finished 10 of 10 with one more of the null kind. A control batch on the same jar, decks and seed 3501 with Forge's own evaluator had no exception in 10 games, and nothing earlier in `runs\` shows either one. So the model's search reaches a path the stock search didn't, and the cause isn't known yet. The comment above `evaluateSa` says the iterator breaks when a spell's choices differ between its replays, and a score-dependent cache in `SimulationController` is one way that could happen. The tripwire counted 0 hits. In the first batch the evaluator was called 9,564 and 8,871 times in the two workers, at 5.9 and 4.9 ms per call, with 29 and 22 cards per state on average. In the second it was 7,204 calls at 4.3 ms with 19 cards, and 11,380 at 8.0 ms with 42. Nearly all of that time is ONNX Runtime: the Python runtime takes 6.5 ms for 27 cards on one thread on the same loaded machine.

**The iterator exceptions don't come from the second look.** #23 fixed two faults in `TwoLookScorer`'s second look, a remembered creature put in the `None` zone and draws from the game's stream. To see whether either one caused these exceptions, the same batch ran on two jars on 2026-11-08: boros-aggro-1 (full, `onnx:` the dry-run bundle) against dimir-tempo-1 (heuristic), seed 3501, 20 games on 2 workers, budget 100, records on, below-normal priority. On the #35 jar (`forge-wt-35`, `f60d1ec`) 3 games of 20 were draws by exception: one `RuntimeException: -1` from `SpellAbilityChoicesIterator.advance` and two null `getLastDecision()` in `doneEvaluating`. On fork `t-eval-onnx` (`e7d70f3`, with #23's fixes and #25's prune) it was the same 3, in the same games, worker 0's game 4 and worker 1's games 1 and 10. The record digest was the same on both, `c2e35203042f`, and so were the evaluator's call counts, 27,188 and 17,439. So the two jars played the same games. The ONNX evaluator never calls into Forge's AI, so its second look draws nothing from the game's stream, and the `None` zone only broke a later copy, with `Couldn't map`. #25's prune changed nothing either, which fits Boros's model never scoring a placeholder in this batch.

With a budget of 100 steps and two looks per scored state, one decision can make about 200 calls, which is about a second. The model computes each card's text vector on every call, though that part depends only on the card. Splitting the export so Forge can cache it per card would cut most of the time, and that would be a new contract version.

## The decks

`forge-gui\res\geneticaidecks\` holds 786 decks at this commit, and only 7 are `GAL_`. The rest are 442 `GAS_`, 326 `GAM_`, 9 `GAP_` and 2 `GAV_`.
