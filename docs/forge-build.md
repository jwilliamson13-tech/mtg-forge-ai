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
| Fork branch | `mtg-forge-ai`, that upstream commit plus the fork's own patches. `git log da66fb1..mtg-forge-ai` lists them. They add `-sim` (issue #17) and `-budget` (issue #50) |
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
- `-budget` is the number of search steps an AI decision may take, such as `-budget 100`. A step is one game copied for the search. With it, the wall-clock limit on AI decisions stops mattering, so a run replays from its seed on a busy machine too. A decision that runs out passes priority, as one that runs out of time does. Without the flag the limit stays at 5 seconds. Only `forge sim` sets a budget.
- `-c` is the clock in seconds before the game is stopped, default 120.
- `-q` prints only the result lines, though Forge's startup messages still appear.
- `-mc on|max|off` turns on Flat Monte Carlo. It is new since `HANDOFF.md` was written and is off by default in a sim.

With `-sim` or `-budget`, each game prints one more line before its `Game Result:` line. Without them, the output is what stock Forge prints.

```
AI limits: 2 deadline expiries, 0 budget stops, 5120 random draws
```

Deadline expiries and budget stops count the decisions the clock or the budget cut short in that game. Random draws is every draw from `MyRandom` in that game, including the ones the search makes, so two games that played differently almost never share it.

In tournament mode (`-t`), `-D` works differently: it loads every `.dck` in the folder, and names passed to `-d` no longer resolve under it.

## Things that will trip you up

- **Run from `forge-gui`.** The jar's version string doesn't contain "git", so Forge looks for `res\` and `forge.profile.properties` in the working directory. From anywhere else it can't find either one, and it falls back to the real user profile. This comes from reading `GuiDesktop.getAssetsDir()`. I didn't run it that way, to keep the real profile clean.
- **Quote `-D` properties in Windows PowerShell.** Version 5.1 splits an unquoted `-Dfile.encoding=UTF-8` at the dot, and Java fails with `Could not find or load main class .encoding=UTF-8`.
- **Keep `-Dsentry.enabled=false`.** `Main` starts Sentry with a DSN on the Forge team's server. Reports are off by default through the `USE_SENTRY` preference, and with this property Sentry doesn't start at all. Batches of bot games shouldn't reach that server.
- **The exit code doesn't mean the game worked.** `Main` always ends with `System.exit(0)`, even after `Could not load deck`. Only a `Game Result:` line proves a game was played.
- **A stopped game prints a winner.** A game past the clock prints `Stopping slow match as draw`, then `Game Result: ... has won!` for an arbitrary seat. `SimulateMatch` calls `setGameOver(Draw)`, but `Player.onGameOver` marks everyone who hasn't lost as a winner, so both `Game Outcome:` lines say "has won" and `isDraw()` is false. The running score in `Match Result:` counts these fake wins too. I saw this on a real run with `-c 1`. By the source, a game that throws ends the same way, without the clock's line. The sim-runner counts both as draws.
- **Forge skips flags it doesn't know.** A jar built before `-sim` plays a heuristic game with `-sim full` and doesn't complain. So the `[sim=mode]` tag in the output is the proof that a mode took effect, not the flag on the command line. The same goes for `-budget`, where the proof is the `AI limits:` line.
- **Some noise is normal.** Startup prints about 20 `was not assigned to any set` lines. `Did not have activator set` is a `println` in `SpellAbilityRestriction.java`, where Forge fills in the missing activator and carries on. In full sim, `Simulation: SA not found!` on stderr comes from `GameSimulator`: the search couldn't find that ability on its copy of the game, scored it as a failed simulation and moved on. It isn't a crash, but the search can't choose that ability. On the Burn deck it is Suspend.

## Running batches

`simrunner` plays two decks against each other across parallel `forge sim` JVMs and reports throughput. It needs Python 3.11 or newer and nothing outside the standard library.

```powershell
$decks = "..\forge\forge-gui\res\geneticaidecks"
python -m simrunner batch "$decks\GAL_19_18_Burn Red Burn-sligh Generated Deck_27_18.dck" "$decks\GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19.dck" --games 50 --seed 1 --workers 10 --clock 600
```

Run it from this repo. `--workers` defaults to logical cores minus 2. `--ai Default Reckless` sets the AI profile per seat, in deck order. `--sim-mode full heuristic` sets the simulation mode the same way, and leaving it out leaves `-sim` off the command. `--search-budget` sets `-budget`. It defaults to 100 when a seat is in `hybrid` or `full` and to off otherwise, and `--search-budget 0` turns it off. `--clock` defaults to 2400 seconds, the wall clock in the spec, and `--heap` to 2048m. `--forge` defaults to `..\forge`. The report prints and goes to `runs\<time>-seed<seed>\report.txt`, beside one stdout and one stderr log per worker. `runs\` is gitignored. The command exits 1 if any game didn't finish, if a worker left no log in its throwaway profile, if a batch with `--sim-mode` doesn't get those modes back in Forge's match header, or if a batch with a search budget gets no `AI limits:` lines. Those last two mean a stale jar, and the worker table says so.

### What the report counts

- **Seat 1** and **seat 2** give the deck, AI profile and simulation mode.
- **Attempted** is what you asked for. **Finished** is every game with a `Game Result:` line. Games not finished is the difference, and covers a deck that won't load, an unknown AI profile and a JVM that died.
- **Wins** come from the `Ai(n)-` prefix, so they are per seat, and seat n holds deck n for the whole batch.
- **Draws by rules** are games Forge printed as a draw. In the runs below that meant both players lost.
- **Draws by clock** are games with the `Stopping slow match as draw` line. **Draws by exception** are games where both `Game Outcome:` lines say "has won" without that line. Both print a fake winner, and neither counts as a win.
- **Usable** means decisive, because draws get dropped before training.
- **Startup** runs from launching the JVM to Forge printing its match header. **Steady-state games per hour** is each worker's games divided by the time from that header to its last result, summed over workers.
- **Search budget** is the steps per decision, or off.
- **AI decision timeouts** counts the `AI eval thread at timeout:` lines, one narrow kind of expiry that the heuristic AI prints. **AI deadline expiries** counts every decision the clock cut short, and includes those. **AI search budget stops** counts the decisions the budget cut short. The last two come from the `AI limits:` lines and say `not reported` for a run without `-sim` or `-budget`.
- **Result digest** is a hash of every game's seat, turn, draw cause and, when Forge prints it, random draws. Two runs with the same digest played the same games. Winner and turn alone aren't enough: the two runs that opened issue #50 agreed on all ten winners.

The output can't separate everything:

- A game that threw after one player had already lost looks like a normal win on stdout. Only `stack traces on stderr` hints at it, and that count is per worker, not per game, because stdout and stderr have to stay on separate pipes. On one pipe a stopped game's stack trace lands in the middle of the `Game Result:` line.
- The exception signature comes from the source. I could not force a throw without patching Forge, so it is only tested against the stub. The clock signature comes from real runs with `--clock 1`.
- A draw by the turn cap doesn't exist yet, and neither does a per-game end reason. Those arrive with the game records.

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

Why 100: over 7 games the 219 decisions that searched took a median of 5 steps, a 99th percentile of 76, and 96 at most apart from the 335. It has to stay well under what runs out of heap. A stop is the same as an expiry for the AI, and it costs about as much play as the old limit did: on the same seeds the budget stopped 35 decisions where the clock cut 32 and 29, and in the both-seats batch below it stopped 22 where the clock cut 63. These counts come from Burn vs Merfolk and one short batch of Sneak Show vs Slivers. A deck with bigger boards costs more per step, in both time and memory, so check the heap before raising the number.

A stop leaves `MyRandom` on the search's stream, as an expiry does. That no longer harms replay, because the stream then depends only on the seed. I left it alone because fixing it changes default play.

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
- No game came near the clock or turn 50. Forge has no turn cap yet, so that one was read from the `Turn` line.
- Full on seat 1 won 65 of 147, and the same deck with the heuristic AI won 167 of 414. That is 44% against 41%, inside the noise, so these runs show no strength gain from full sim. They weren't built to measure one.
- `PRUNE_HIDDEN_INFO` is still off, so the search sees the opponent's hand and library. The hook and tripwire aren't in yet either, and both will add cost.

Memory peaked at 5.2 GB across 4 JVMs. No run produced a stack trace.

## The decks

`forge-gui\res\geneticaidecks\` holds 786 decks at this commit, and only 7 are `GAL_`. The rest are 442 `GAS_`, 326 `GAM_`, 9 `GAP_` and 2 `GAV_`.
