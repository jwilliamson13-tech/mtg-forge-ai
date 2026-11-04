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
| Fork branch | `mtg-forge-ai`, no commits on top of that upstream commit yet |
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

To rebuild exactly the commit in the table after the branch has moved, run `git checkout da66fb1ac4be7957461477bc8bd26628a439c747`.

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

Read from `SimulateMatch.java` at the commit above:

- `-D DIR` is the deck directory. The file names passed to `-d` resolve under it. Use an absolute path, as Forge's own help says.
- `-d A.dck B.dck` takes the deck file names, which must end in a three-character extension to count as files. The `GAL_` names contain spaces, so quote them.
- `-n` is the game count, default 1.
- `-s` is the RNG seed. Two runs with seed 42 produced the same game log, line for line, once the startup banner's timestamps and the timing lines were removed.
- `-a` is an AI profile per seat, in deck order, such as `-a Default Reckless`. An unknown name prints the valid ones and stops.
- `-c` is the clock in seconds before the game is called a draw, default 120.
- `-q` prints only the result lines, though Forge's startup messages still appear.
- `-mc on|max|off` turns on Flat Monte Carlo. It is new since `HANDOFF.md` was written and is off by default in a sim.

In tournament mode (`-t`), `-D` works differently: it loads every `.dck` in the folder, and names passed to `-d` no longer resolve under it.

## Things that will trip you up

- **Run from `forge-gui`.** The jar's version string doesn't contain "git", so Forge looks for `res\` and `forge.profile.properties` in the working directory. From anywhere else it can't find either one, and it falls back to the real user profile. This comes from reading `GuiDesktop.getAssetsDir()`. I didn't run it that way, to keep the real profile clean.
- **Quote `-D` properties in Windows PowerShell.** Version 5.1 splits an unquoted `-Dfile.encoding=UTF-8` at the dot, and Java fails with `Could not find or load main class .encoding=UTF-8`.
- **Keep `-Dsentry.enabled=false`.** `Main` starts Sentry with a DSN on the Forge team's server. Reports are off by default through the `USE_SENTRY` preference, and with this property Sentry doesn't start at all. Batches of bot games shouldn't reach that server.
- **The exit code doesn't mean the game worked.** `Main` always ends with `System.exit(0)`, even after `Could not load deck`. A game that throws prints a stack trace and is recorded as a draw, and a game past the clock prints `Stopping slow match as draw`. Read the `Game Result:` line instead.
- **Some noise is normal.** Startup prints about 20 `was not assigned to any set` lines. `Did not have activator set` is a `println` in `SpellAbilityRestriction.java`, where Forge fills in the missing activator and carries on.

## The decks

`forge-gui\res\geneticaidecks\` holds 786 decks at this commit, and only 7 are `GAL_`. The rest are 442 `GAS_`, 326 `GAM_`, 9 `GAP_` and 2 `GAV_`.
