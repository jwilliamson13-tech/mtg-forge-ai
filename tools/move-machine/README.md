# Moving the run to another PC

Two scripts move the gen-1 run from the build PC (`DESKTOP-V5BLM96`) to the always-on PC (`DESKTOP-6DUBSJT`) without
replaying anything. The code travels through GitHub, and the git-ignored state travels as one bundle over Taildrop.

## On the source PC

Wait until no Forge job is running, then:

```powershell
powershell -ExecutionPolicy Bypass -File tools\move-machine\export-state.ps1
```

The script refuses to pack while a `java`, `simrunner` or `setmodel` process runs, or while either repo has
uncommitted or unpushed work. It packs `runs\` (minus `runs\venv`, which is rebuilt on the target), `models\`, `data\`
and `results\` into `%LOCALAPPDATA%\Temp\forgeai-move\forgeai-state-<stamp>.tgz`, about 2.5 to 3 GB. It then sends the
bundle, its `.sha256` and `setup-target.ps1` to the target's `Downloads` folder with `tailscale file cp`. Pass
`-NoSend` to pack only, or `-Target <host>` for another machine.

Leftover worker junctions (a killed batch can leave `worker-00\res` pointing into the Forge clone) are listed and left
out, so the bundle never picks up Forge's card resources.

## On the target PC

Accept the Taildrop files if Tailscale asks, then from a normal PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File $HOME\Downloads\setup-target.ps1
```

It works under `%USERPROFILE%\Projects` unless you pass `-Root`. Each step is skipped when already done, so after a
failure, fix the cause and rerun the same command. In order:

1. Checks disk (15 GB free), RAM, cores, GPU and Windows Update active hours, and warns about anything short. The
   ladder's finished batches were played at 4 workers and must resume at 4, so the box wants 6+ cores and 12+ GB.
2. Installs Git, Temurin JDK 21 and Python 3.13 by winget, and Maven 3.9.9 from archive.apache.org (checksum
   verified). It refuses a JDK whose `msvcp140.dll` is older than 14.40, because ONNX Runtime crashes on it.
3. Clones the forge fork (branch `mtg-forge-ai`) and `mtg-forge-ai`, and checks both contain the source's commits.
4. Builds the Forge jar and writes `forge.profile.properties`, so sims never touch `%APPDATA%\Forge`.
5. Builds `runs\venv` from `requirements-dev.txt`, which pins the CUDA 12.6 PyTorch for the 1080 Ti.
6. Checks the bundle's SHA-256, unpacks it, and rewrites the source's absolute paths and JDK path in every `.json`
   under `runs\` and `models\` and every `runs\*.cmd`. Without this the ladder refuses to resume: its `plan.json`
   stores model bundle paths, and a folder with different stored settings is refused.
7. Replays two seeded 10-game batches whose record digests are known: stock heuristic play (`8467fad7cb41`) and full
   sim with `onnx:gen0` (`17b0cb30a266`). Both must match, or the new machine plays different games than the old one.
   `-SkipCheck` skips this.

It ends by printing the command that resumes the gen-1 ladder:

```powershell
cmd /c start "gen1" /b /wait /belownormal "<root>\mtg-forge-ai\runs\gen1-generation.cmd" HH:MM --stop HH:MM
```

## For the Claude on the target PC

The source PC's Claude kept its notes outside this repo, so here is what you need.

**Where it stands (2026-11-13).** The gen-1 pool (1,500 games) is played, and gen 1 is trained and exported to
`models\gen1`. Kill criterion 2 passes on gen 1: the full model scored 77.04% on held-out games, against 71.44% with
no cards. The gen-1 ladder is about 20 of 72 batches in. Kill criterion 4 (`HANDOFF.md`) is decided by that ladder:
stop if gen 1's interval against the heuristic AI doesn't sit entirely above 0. Resume it with the command above.

**Rules the runs follow.**
- Nothing heavy runs beside a Forge job: no Maven builds, no training. Never build, pull or switch branches in the
  main `forge` clone while Forge steps run, because every batch starts its JVM from that clone's jar. Forge work goes
  in worktrees (`git worktree add ../forge-wt-<name> -b <branch> origin/mtg-forge-ai`).
- Use Temurin for anything that loads ONNX. The run launchers put it first on `PATH`.
- A fix to Forge needs a failing test first, then the suite (`forge.ai.simulation`, `forge.gamesimulationtests`,
  `forge.ai`) green, then the three digest batches unchanged: pair01 `17b0cb30a266`, pair02 `5760d78f960d`, stock
  `8467fad7cb41`. Fork PR #24 is a worked example.
- If a run stops on its own, find the cause, fix it and restart. Every step resumes. An unfinished 50-game chunk
  replays from its first game.
- No JoshOS writes. US spelling.

**Known open items.**
- Fable of the Mirror-Breaker token copies still throw in search (issue #82). The first fix (fork #20) wasn't enough:
  7 traces in the gen-1 pool. Those games are dropped as exception draws.
- Issue #84, "opponent is null" in `GameSimulator.resolveStack`. A draft fix exists on fork branch `sim-no-opponent`,
  untested.
- The deck pool gives every deck equal games. Twenty archetypes under 1% metagame share get about half the play. Josh
  decided on 11-13 to leave it as is.
