# Handoff: learned board evaluator for Forge, feeding a Legacy deck-testing harness

Written 2026-10-16 at the end of a design conversation (Claude Code session `912a48e4`) and rewritten the same day after a grilling session that stress-tested every decision. Nothing is built yet. The original version is the first commit in this repo.

## Goal

A deck-testing harness for Legacy: two decks in, a win rate with a confidence interval out. Questions it should answer: "how much better does this card perform in this brew," "how does deck A do against deck B."

The harness is only as good as the AI piloting the decks. Forge's stock AI is weak with control and combo, which caps what the harness can say. So the project builds a learned board evaluator that replaces Forge's hand-tuned one, and the harness sits on top of the improved AI.

The model must generalize to cards it has never seen, so it never represents a card by its identity.

Forge is the open-source MTG rules engine (Java, https://github.com/Card-Forge/forge).

## When the harness is trusted

No human is the yardstick. The gate is convergence: train an AI generation, run a matchup, record the win rate. Train a stronger generation (on its own games), run the same matchup. If the number stops moving beyond the confidence interval, the AI is no longer the bottleneck for that matchup and the number is about the decks. If it moved, keep improving the AI.

Alongside it, an Elo ladder across generations (stock heuristic, stock full-sim, gen 0, gen 1, ...) gives the single strength number.

Four tracked matchups, drawn from the held-out real decks, are to be written down by the user before any model exists. Not chosen yet.

Caveat written down on purpose: convergence means "stopped improving," not "good." A depth-3 spell-only search with heuristic combat can plateau below a competent pilot. The combat diagnostic in step 6 tells you which it is.

## Kill criteria

Written before starting, so they can't be moved after:

1. After step 2: fewer than ~500 usable games per night on the user's machine means the data plan is dead as written. Rethink before building more.
2. After the first trained model: if the full model doesn't beat the no-cards ablation by at least 5 points of held-out accuracy, the card encoding isn't carrying information. Redesign the encoding; don't scale data.
3. Boredom. Named by the user as a real one.

## How we got here

Rejected, in order:

1. **Writing our own rules engine.** Forge has ~15 years of community work behind it.
2. **A deck-testing harness around stock `forge sim`.** Capped by stock AI quality. Came back as the actual goal once the AI improvement was in scope.
3. **Sampling hidden cards (determinization) or Monte Carlo playouts.** Still possible later.
4. **A fixed array indexed by card ID.** Breaks every set release, doesn't generalize.
5. **A staged plan** (hand-picked totals, then hand-written card summaries, then a learned set model). Collapsed to one model. Don't reintroduce the stage framing.
6. **Switching engines.** Checked 2026-10-16: phase.rs (`phase-rs/phase`, Rust, ~32k of 36k cards implemented by its own badge, claims Legacy, 7 months old, no RL gym) and Argentum (`wingedsheep/argentum-engine`, Kotlin, 16k cards, draft/sealed only, has a gym). Forge stays because its structured card scripts are the feature source for the whole model, and because a 7-month-old rules engine's bugs become the harness's bugs. Phase is the fallback if Forge's search proves too slow.
7. **Hand-picked count features** (land count, creature count, total power, untapped mana). Rejected: their meaning depends on which cards they are.
8. **Padding card sets to a fixed size.** Not needed; ONNX dynamic axes handle variable-length sets.
9. **`turns_left` as a second target.** Out.

## Decisions

### Model

- **One model.** Two-level set encoder from the start: tokens into ability vectors, ability vectors into a card vector, card vectors into zone summaries, zones plus scoreboard into P(win) from "my" point of view. DeepSets at each level; upgrade to attention over the battlefield only if the pair test fails (see Review).
- **Cards are described by what they do, parsed from Forge's card scripts** under `forge-gui/res/cardsfolder/` (and `res/tokenscripts/` for tokens). Examples:
  ```
  Lightning Bolt   A:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3
  Grim Lavamancer  PT:1/1  A:AB$ DealDamage | Cost$ R T ExileFromGrave<2/Card> | ValidTgts$ Any | NumDmg$ 2
  Snapcaster Mage  PT:2/1  K:Flash  T:Mode$ ChangesZone | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ TrigFlashback
  ```
- **Encoding is learned token embeddings, not curated features.** The parser splits each ability into `key=value` tokens (`A=SP`, `Effect=DealDamage`, `ValidTgts=Any`). Every non-numeric value is a vocabulary entry with a learned embedding; unknown tokens at inference map to `<unk>`. `ValidTgts$` filters like `Creature.OppCtrl+tapped` split on `.` and `+` into sub-tokens; nothing interprets them. `SVar` references inline one level.
- **Numbers stay numbers, linear.** `NumDmg$ 3` becomes the pair (key `NumDmg`, value 3); the model scales the key's embedding by the value. No log curve: the user wants the arithmetic preserved (20 damage is 20x of 1, and "damage ≥ life" is exactly the kind of threshold the model should learn). Each key is divided by a fixed per-key constant for training stability. `X` and runtime-computed values get a `<var>` token and no number. Mana cost `1R` becomes (key `Generic`, 1) plus a token `PipR`. P/T: current values from the live game object (counters and pump applied) plus printed values, four scalars.
- **Live state per card**: current P/T, tapped, counters, summoning sick, attachments, controller, face-down.
- **Zones, all as full card sets, no padding, no counts**: battlefield, own hand, graveyard, exile, and `opp_revealed` (cards the opponent legitimately showed, from `AiCardMemory.REVEALED_CARDS`). Library is a count. Opponent's hand is a count. A learned zone tag is added per card.
- **Scoreboard is rules-level only**: `me_life`, `opp_life`, `me_poison`, `opp_poison`, `me_library`, `opp_library`, `opp_hand` (count), `turn`, `me_active`, `me_on_play`. Life isn't monotonic (Death's Shadow); don't constrain it.
- **Output is P(win).** Nothing else.
- **No throwaway baseline model.** Prove the pipeline with a fake evaluator (random, or Forge's own score through the new code path). The scoreboard-only baseline is the real model trained with empty card sets (the no-cards ablation).

### Honesty

The user's rule: no cheating AI, anywhere. Checked in source 2026-10-16:

- The simulation search copies the game with `GameCopier.PRUNE_HIDDEN_INFO = false` (a hardcoded `final`), so it sees the opponent's exact hand and library order. The true-branch exists (swaps unseen cards for a blank "hidden" artifact, keeps counts) but is stub-grade: only on the `USE_FROM_PAPER_CARD` path, and a `// TODO also check REVEALED_CARDS memory` means it would also blind the AI to cards already revealed. Finish it: cover the fallback copy path, consult `REVEALED_CARDS`.
- The heuristic AI peeks in scattered places with no switch. Found so far: `ComputerUtil.hasAFogEffect` scans the defender's real hand before blocking (the `checkingOther` flag only disables it when modeling other players' blocks); `ChangeZoneAi` ~L1957 and `PermanentAi` ~L126 read opponent library/hand; `PlayerControllerAi.chooseCardName` ~L1527 reads opponent library; `ClashAi` ~L72 reads opponent top card. About 90 `forge-ai` classes were not checked. `UI_ENABLE_AI_CHEATS` is unrelated (it mana-weaves the AI's own library).
- **Decision: fix every peek before generating training data.** Method is a tripwire, not a code read: instrument the hidden-zone read path in `forge-game` (`Player.getCardsIn` / `Zone.getCards`) to log a stack trace whenever `forge-ai` code reads an opponent's hidden zone during that AI's decision. Run games, fix every site that trips, repeat until a night runs clean. Then generate data.

### Data

- **Who plays**: the stock heuristic AI (after the tripwire runs clean) plays gen-0 games. Later generations train on games the new AI plays, which also washes out "good for Forge's AI" bias.
- **Snapshot every priority pass.** Dedupe consecutive identical states. One row per snapshot per side (each moment written twice, labels flipped). Label `won` 1/0.
- **Every game weighs 1** in the loss (rows weighted 1/n_rows_in_game), so long control mirrors don't dominate.
- **Clock**: `forge sim -c` is wall-clock seconds, default 120. Use `-c 2400` as a safety net plus a turn cap of 50 enforced in the logging hook. Games hitting either are marked draws and dropped. Log draw rate per deck pair; above 5% means a loop the AI can't exit, which is a Forge bug or a deck to exclude. Both numbers tunable.
- **On disk**: one gzip'd JSONL file per game, named by seed and deck pair, written from Java (Gson is already in Forge). Python converts to Parquet nightly.
- **Schedule**: nightly window on the user's 1080 Ti machine (e.g. 23:00 to 07:00) with a scheduled hard stop; cores minus 2 parallel `forge sim` JVMs; `-s` seeds for reproducibility, `-q` quiet. The machine is in use mornings; don't overload it.
- **Decks**: format is Legacy. Training set is all of Forge's shipped `GAL_*` decks (`forge-gui/res/geneticaidecks/`, AI-generated Legacy lists) plus 30 of 40 real lists pulled once from MTGGoldfish's Legacy metagame page (one per archetype, converted to `.dck`). The other 10 real lists are held out and never trained on; the four tracked matchups come from them.
- **Splits**: by `game_id` always, never by row; held-out decks as above. One-vs-one only, no Commander.

### Forge integration

- **Fork**: the user's fork of Card-Forge/forge, one branch, never pushed upstream. Rebase on upstream occasionally; the simulation package is actively changing (commits through 2026-09-11).
- **ONNX Runtime, CPU**, `com.microsoft.onnxruntime:onnxruntime` as a Maven dependency in `forge-ai`. Model path configurable. Dynamic-axis inputs. Pin the ORT version alongside the PyTorch pin.
- **Filling `GameStateEvaluator.Score`**: it has two ints. `value` is what `SpellAbilityPicker` compares (strict `>`). `availableValue` vetoes any play that doesn't improve what's usable this turn, which is how Forge holds summon-sick creatures until main 2. Decision: `value` = model on the real board scaled to an int well inside the non-terminal band (e.g. `P(win) * 1_000_000`); `availableValue` = model on the same board with the AI's own summon-sick creatures removed. Two inferences per candidate, one model. Terminal states keep Forge's bands untouched (win `Integer.MAX_VALUE - turn*1000`, loss `Integer.MIN_VALUE + 1 + turn*1000`; `Integer.MIN_VALUE` is a "simulation failed" sentinel).
- **Headless full-sim**: `USE_FULL_SIMULATION` is an `AIOption` enum set only by the desktop lobby's right-click menu (`PlayerPanel.getAiOptions()`). `SimulateMatch.java` passes `null`, so `forge sim` is always heuristic. Add a flag to `SimulateMatch` that passes `Set.of(AIOption.USE_FULL_SIMULATION)` through `GamePlayerUtil.createAiPlayer`.

### Harness

- Output is always win rate with a confidence interval, never a bare number. Resolution is a parameter: run until the interval is that wide. Default 5 points (~400 games per arm); 2 points is ~2,400 per arm and is affordable once throughput is known.
- Same decks, swapped seats, over many games.

### Review (after every trained model)

- **Ablations**: retrain with one input removed and compare held-out accuracy. Set: no cards (scoreboard only); no graveyard/exile; no live state (printed cards only); no numbers (keys as tokens, values dropped). A review step, not a design decision.
- **Pair test**: score Platinum Angel + Lightning Greaves together, then each alone. If the pair's P(win) lift doesn't exceed the sum of the singles' lifts, DeepSets can't see the interaction; add an attention layer over the battlefield.
- **Combat diagnostic** (once, on the stock AI): watch 20 games, count whether the result was decided by a combat blunder or a spell choice. If combat dominates, routing combat through the evaluator is the next phase.

## Forge facts checked in the source

Paths under `forge-ai/src/main/java/forge/ai/` unless noted. Line numbers drift; pull fresh.

- `AiController.java`: `simMode` field, `usesFullSimulation()`, `setUseSimulation(AIOption)`. Full mode routes spell choice through `simPicker.chooseSpellAbilityToPlay`.
- `simulation/SpellAbilityPicker.java`, `simulation/SimulationController.java`: `GameCopier` copy, recurse to `DEFAULT_MAX_DEPTH = 3`. Spells only; combat stays heuristic (`AiAttackController`, `AiBlockController`).
- `simulation/GameStateEvaluator.java`: the hand-tuned score the model replaces. ~+4 per own hand card, ±2 per life point, mana-base score, per-card `evalCard` values. `Score { int value; int availableValue; }`.
- `simulation/GameCopier.java`: `PRUNE_HIDDEN_INFO` details above. `ZONES = {Battlefield, Hand, Graveyard, Library, Exile, Stack, Command}` all copied in full.
- `forge-gui-desktop/.../view/SimulateMatch.java`, `forge sim` flags as shipped: `-d` decks, `-D` deck dir (works outside tournament mode), `-n` games, `-m` best-of, `-t` Bracket/RoundRobin/Swiss, `-p` players, `-f` format (default Constructed), `-s` RNG seed, `-a` AI profile per side (positional; Cautious/Default/Experimental/Reckless), `-c` clock seconds, `-q` quiet. Deck names ending `.dck` resolve as files under `-D` or the user deck folder.
- Shipped decks under `forge-gui/res/`: `quest/precons/` (~450 WotC precons, weak), `geneticaidecks/` (~600 AI-generated, prefixed `GAL_` Legacy / `GAM_` Modern / `GAS_` Standard / `GAV_` Vintage), `quest/duels/` (~1,145 themed quest decks). `.dck` format is INI-style: `[metadata]` `Name=`, then `[Main]` with `N Card Name|SET` lines. Counts are estimates.
- Card scripts: 34,086 files under `cardsfolder/` (tree API count, 2026-10-16).

## Environment pins

- PyTorch 2.14.x via `pip install torch --index-url https://download.pytorch.org/whl/cu126`. The 1080 Ti is Pascal (sm_61); default PyPI wheels are CUDA 13 and don't run on it. 2.14 is the last release with a Pascal wheel. Don't upgrade past it without building from source.
- ONNX Runtime Java: pin whatever version matches the PyTorch exporter used.

## Next steps, in order

1. **Clone and build Forge** (Java, Maven) beside this repo (the `forge/` folder is gitignored). In parallel, **write the card-script parser** in Python against `forge-gui/res/cardsfolder/`, test-first. First checkpoint: parser handles 95% of Legacy-legal scripts without error.
2. **Measure throughput**: 50 `forge sim` games between two `GAL_` decks, timed, with cores minus 2 in parallel. Kill criterion 1 applies.
3. **Tripwire**: instrument hidden-zone reads in `forge-game`, run games, fix every `forge-ai` peek that fires, until a night runs clean.
4. **Snapshot hook**: every priority pass, JSONL per game, labels at game end, turn cap. Forge publishes events (`forge.game.event`); check whether one fires on priority before touching `PhaseHandler`.
5. **Fake evaluator** through the new seam in place of `GameStateEvaluator`. Confirm the full-sim AI still finishes games headless (needs the `SimulateMatch` flag).
6. **Combat diagnostic** on the stock AI, 20 games.
7. **Finish `PRUNE_HIDDEN_INFO`** for the search path.
8. **Pull the 40 MTGGoldfish Legacy lists**, convert to `.dck`, split 30/10. User names the four tracked matchups.
9. **Generate gen-0 data, train, ablate, pair-test, export ONNX, load in Forge.**
10. **Harness**: win rate with CI, resolution parameter, seat swapping. Run the Elo ladder and the four tracked matchups. Train gen 1 on gen 0's games; check convergence.

## Open questions

- Where exactly the priority hook attaches (event bus vs `PhaseHandler`). Build-time fact.
- The four tracked matchups. User's pick.
- Which ~90 `forge-ai` classes still hide peeks. The tripwire answers this empirically.
- Known blind spots even for the set model: two-card combos (the pair test watches for this), and threats held in hand.

## How the user works

- Wants reasons, not reassurance, and pushes back on overstated claims. Concede quickly when they're right. They were right about: dropping the staged plan, dropping the logistic baseline, rejecting humans as the yardstick, and keeping numbers linear.
- Explain with worked examples and short sentences. Long explanations lose them; a concrete example board or a single card (Grim Lavamancer) lands.
- Don't propose card-ID designs, hand-picked count features, extra stages, or extra targets.
- Ask before creating files outside this folder.

## Related

- Claude's memory note: `C:\Claude\.claude\memory\projects\mtg-forge-ai-model.md`.
- Sibling MTG project: `..\mtg-script-cards\` (transcript-to-card-image CLI). Unrelated code, same domain, same agent-skills setup.
- Card representation research: Bertram, Fürnkranz, Müller, *Learning With Generalised Card Representations* (IEEE CoG 2024), https://arxiv.org/abs/2407.05879. Draft picks, not gameplay; representation mattered little on known cards, 55% match on unseen cards.
- Alternative engines, upstream URLs: https://github.com/phase-rs/phase (Rust), https://github.com/wingedsheep/argentum-engine (Kotlin, has a gym).

## Suggested skills

- `unslop`: before writing any prose the user will read. Their global instructions require it.
- `tdd`: for the card-script parser and the snapshot logger.
- `research`: for primary sources (ONNX Runtime Java API, Forge's event bus, `forge sim` behavior in the current build).
- `codebase-design`: when deciding where the logging hook, the tripwire and the evaluator seam go inside Forge.
- `diagnosing-bugs`: if the Forge build or headless sim misbehaves.
- `prototype`: if the encoding needs a quick throwaway to compare options.
