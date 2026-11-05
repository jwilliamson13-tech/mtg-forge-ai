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
| Fork branch | `mtg-forge-ai`, that upstream commit plus the fork's own patches. `git log da66fb1..mtg-forge-ai` lists them. The first adds `-sim` (issue #17) |
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
- `-c` is the clock in seconds before the game is stopped, default 120.
- `-q` prints only the result lines, though Forge's startup messages still appear.
- `-mc on|max|off` turns on Flat Monte Carlo. It is new since `HANDOFF.md` was written and is off by default in a sim.

In tournament mode (`-t`), `-D` works differently: it loads every `.dck` in the folder, and names passed to `-d` no longer resolve under it.

## Things that will trip you up

- **Run from `forge-gui`.** The jar's version string doesn't contain "git", so Forge looks for `res\` and `forge.profile.properties` in the working directory. From anywhere else it can't find either one, and it falls back to the real user profile. This comes from reading `GuiDesktop.getAssetsDir()`. I didn't run it that way, to keep the real profile clean.
- **Quote `-D` properties in Windows PowerShell.** Version 5.1 splits an unquoted `-Dfile.encoding=UTF-8` at the dot, and Java fails with `Could not find or load main class .encoding=UTF-8`.
- **Keep `-Dsentry.enabled=false`.** `Main` starts Sentry with a DSN on the Forge team's server. Reports are off by default through the `USE_SENTRY` preference, and with this property Sentry doesn't start at all. Batches of bot games shouldn't reach that server.
- **The exit code doesn't mean the game worked.** `Main` always ends with `System.exit(0)`, even after `Could not load deck`. Only a `Game Result:` line proves a game was played.
- **A stopped game prints a winner.** A game past the clock prints `Stopping slow match as draw`, then `Game Result: ... has won!` for an arbitrary seat. `SimulateMatch` calls `setGameOver(Draw)`, but `Player.onGameOver` marks everyone who hasn't lost as a winner, so both `Game Outcome:` lines say "has won" and `isDraw()` is false. The running score in `Match Result:` counts these fake wins too. I saw this on a real run with `-c 1`. By the source, a game that throws ends the same way, without the clock's line. The sim-runner counts both as draws.
- **Forge skips flags it doesn't know.** A jar built before `-sim` plays a heuristic game with `-sim full` and doesn't complain. So the `[sim=mode]` tag in the output is the proof that a mode took effect, not the flag on the command line.
- **Some noise is normal.** Startup prints about 20 `was not assigned to any set` lines. `Did not have activator set` is a `println` in `SpellAbilityRestriction.java`, where Forge fills in the missing activator and carries on. In full sim, `Simulation: SA not found!` on stderr comes from `GameSimulator`: the search couldn't find that ability on its copy of the game, scored it as a failed simulation and moved on. It isn't a crash, but the search can't choose that ability. On the Burn deck it is Suspend.

## Running batches

`simrunner` plays two decks against each other across parallel `forge sim` JVMs and reports throughput. It needs Python 3.11 or newer and nothing outside the standard library.

```powershell
$decks = "..\forge\forge-gui\res\geneticaidecks"
python -m simrunner batch "$decks\GAL_19_18_Burn Red Burn-sligh Generated Deck_27_18.dck" "$decks\GAL_1_28_Legacy Merfolk Merfolks Generated Deck_5_19.dck" --games 50 --seed 1 --workers 10 --clock 600
```

Run it from this repo. `--workers` defaults to logical cores minus 2. `--ai Default Reckless` sets the AI profile per seat, in deck order. `--sim-mode full heuristic` sets the simulation mode the same way, and leaving it out leaves `-sim` off the command. `--clock` defaults to 2400 seconds, the wall clock in the spec, and `--heap` to 2048m. `--forge` defaults to `..\forge`. The report prints and goes to `runs\<time>-seed<seed>\report.txt`, beside one stdout and one stderr log per worker. `runs\` is gitignored. The command exits 1 if any game didn't finish, if a worker left no log in its throwaway profile, or if a batch with `--sim-mode` doesn't get those modes back in Forge's match header. That last one means a stale jar, and the worker table says so.

### What the report counts

- **Seat 1** and **seat 2** give the deck, AI profile and simulation mode.
- **Attempted** is what you asked for. **Finished** is every game with a `Game Result:` line. Games not finished is the difference, and covers a deck that won't load, an unknown AI profile and a JVM that died.
- **Wins** come from the `Ai(n)-` prefix, so they are per seat, and seat n holds deck n for the whole batch.
- **Draws by rules** are games Forge printed as a draw. In the runs below that meant both players lost.
- **Draws by clock** are games with the `Stopping slow match as draw` line. **Draws by exception** are games where both `Game Outcome:` lines say "has won" without that line. Both print a fake winner, and neither counts as a win.
- **Usable** means decisive, because draws get dropped before training.
- **Startup** runs from launching the JVM to Forge printing its match header. **Steady-state games per hour** is each worker's games divided by the time from that header to its last result, summed over workers.
- **AI decision timeouts** counts the `AI eval thread at timeout:` lines. **Result digest** is a hash of every game's seat, turn and draw cause. Two runs with the same digest played the same games.

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

Two wall clocks can break replay, and both depend on machine load. `-c` stops a game, and its thread can keep drawing from the shared stream until it notices the interrupt. `Game.AI_TIMEOUT` gives each AI decision 5 seconds. That one is only partly visible: `AI decision timeouts` counts the eval timeout, while `AITimeoutTracker.Expired` cuts a decision short without printing anything. The 20 of 20 match above suggests neither mattered in those runs.

Full sim replays less exactly. Two runs of the 10-game smoke batch with seed 1 agreed on all ten winners and on nine of the ten final turns. Game 8 ended on turn 5 in one run and turn 6 in the other, and I didn't find out why. The search's time limits are the first place to look. So don't count on a full-sim result digest matching between runs.

AI sideboarding is off in a sim (`GameRules.AISideboardingEnabled` defaults to false), so decks don't change between games of a `-n` run.

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

Run 2026-11-05 on the same machine: Burn vs Merfolk, 4 workers, `--clock 600`, the default 2048m heap, below-normal priority, on a machine that was not kept idle, so CPU figures include whatever else was running. Each leg stopped at a fixed wall time rather than a game count, and the three legs took 28 minutes in all. The heuristic leg is a control. The table above used 10 cold workers, so it can't be compared with these directly.

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
