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

Run it from this repo. `--workers` defaults to logical cores minus 2. `--ai Default Reckless` sets the AI profile per seat, in deck order. `--sim-mode full heuristic` sets the simulation mode the same way, and leaving it out leaves `-sim` off the command. `--search-budget` sets `-budget`. It defaults to 100 when a seat is in `hybrid` or `full` and to off otherwise, and `--search-budget 0` turns it off. `--clock` defaults to 2400 seconds, the wall clock in the spec, and `--heap` to 2048m. `--forge` defaults to `..\forge`. The report prints and goes to `runs\<time>-seed<seed>\report.txt`, beside one stdout and one stderr log per worker. `runs\` is gitignored. The command exits 1 if any game didn't finish, if a worker left no log in its throwaway profile, if a worker left a process holding its pipes, if a batch with `--sim-mode` doesn't get those modes back in Forge's match header, if a batch with a search budget gets no `AI limits:` lines, if a batch with records is missing a file for a game that should have one, or if a batch that asked for honest play has a game file that doesn't say it got it. Those last four mean a stale jar, and the worker table says so.

`--records` has Forge write a game record per game, into the run folder's `games\`. `--records-dir` picks another folder, and also turns records on. `--turn-cap` sets `-turncap`, needs records, and leaves Forge's 50 in place if left out. `--no-tripwire` passes `-tripwire off`, with or without records. With records the runner also passes `-honest on`, because records are training data, and `--no-honest` leaves it out. The run folder's name is the batch id, so with records on it has to be letters, digits, `_` and `-`. See "Game records" and "Tripwire" below.

### What the report counts

- **Seat 1** and **seat 2** give the deck, AI profile and simulation mode.
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
- **Tripwire hits per game** gives the max and mean over the games that reported. **Tripwire clean** is yes only when every finished game printed a count and every count is 0. A game that printed nothing means the jar has no tripwire. It fails the batch, and a jar like that never reads as a clean zero. With a `hybrid` or `full` seat a zero can't say yes. The row says `the real game is clean, search copies unchecked until #25`, because `GameCopier` is exempt and a copy has no watch, so the search can still see hidden cards. With `--no-tripwire` the row says `off`.
- **The signature table** after the worker table lists the top 20 signatures by hits across the batch, with the games each one showed up in.
- **Result digest** is a hash of every game's seat, turn, draw cause and, when Forge prints it, random draws. It only says the runs ended alike. Winner and turn alone aren't enough: the two runs that opened issue #50 agreed on all ten winners. Random draws catch most of the rest, but an AI that chooses differently without drawing leaves them unchanged.
- **Record digest** is the replay check, when records are on. It hashes every snapshot of every game file, plus the metadata less `batch_id`, `honest_play` and `tripwire`, which can differ between two runs of the same games. The same digest means the same games card by card. Record ids come from the seed, so they replay too. Digests in this file from before it existed are result digests.

The output can't separate everything:

- A game that threw after one player had already lost looks like a normal win on stdout. Only `stack traces on stderr` hints at it, and that count is per worker, not per game, because stdout and stderr have to stay on separate pipes. On one pipe a stopped game's stack trace lands in the middle of the `Game Result:` line.
- The exception signature comes from the source. I could not force a throw without patching Forge, so it is only tested against the stub. The clock signature comes from real runs with `--clock 1`.
- The report still reads stdout. The per-game end reason is in the game records, and nothing in the report reads them yet.

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
- Copies. `HIDDEN_READ_WATCH` is never copied, so a copy has no watch. `GameCopier` reading the real game to make one is exempt by class, and it counts nowhere. A search can still see hidden cards on the copy. That is #25's job, and this tripwire says nothing about it.
- `Game.getCardState`, which looks up a card the AI already holds, and `PlayerZone.getCardsPlayerCanActivate`, which returns only what that player may play or look at.

**What it can't see:**

- Deck lists: `getRegisteredPlayer().getDeck()`. `ComputerUtil:2368`, `ChangeZoneAi:1513`, `SpecialCardAi:403` and `PlayerControllerAi:140` read an opponent's, and `-honest` closes all four by hand. Nothing would catch a fifth.
- A face-down card's real side reached some other way than `Card.getState`, `getOriginalState`, `getPaperCard` and `getRules`, such as `getStates()` or a `CardState` object kept from before it turned over.
- Rules questions the AI asks about an opponent, when forge-game does the reading. `sa.canPlay()` on an opponent's spell stays silent, and the AI learns the answer anyway. `ComputerUtilCost.canPayCost` for an opponent does hit, because it runs through `ComputerUtilMana`, which is forge-ai.
- Where a card it already holds has gone: `Zone.contains(card)`, `Card.getZone()`, `Game.getZoneOf`. #21's fix for `NamedDiscard` needs `Zone.contains(card)` to stay uncounted.
- The order of the AI's own library, which story 10 exempts (`ComputerUtilAbility:58`).
- Subgames. A `SubgameEffect` game is a new `Game` with no watch.

The Fog hand read can't happen in a real game, going by its call sites, so the ticket's Fog test calls `ComputerUtil.hasAFogEffect(opp, ai, false)` directly. The proof that resolve-time decisions are covered is in two other tests, which open no decision themselves. They put Conjurer's Ban and Whirlpool Whelm on the real stack and resolve it. `PlayerControllerAi.chooseCardName` hits on the first, and `ClashAi.chooseSinglePlayer:75` on the second, with the library size read at L67 in the same method staying silent. With plain controllers the same resolve hits as `no decider: `.

Each game prints `Tripwire: N hits` and one `Tripwire hit: N SIGNATURE` line per signature before its result lines. The hits go to stdout as well as the record for two reasons. A game that threw gets no file, and the record has no count per signature.

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
- `PRUNE_HIDDEN_INFO` is still off, so the search sees the opponent's hand and library. The hook and tripwire weren't in yet either. "Game records" below has the hook's cost.

Memory peaked at 5.2 GB across 4 JVMs. No run produced a stack trace.

## The decks

`forge-gui\res\geneticaidecks\` holds 786 decks at this commit, and only 7 are `GAL_`. The rest are 442 `GAS_`, 326 `GAM_`, 9 `GAP_` and 2 `GAV_`.
