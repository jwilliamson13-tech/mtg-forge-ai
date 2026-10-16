# Handoff: learned board evaluator for Forge's MTG AI

Written 2026-10-16 at the end of a design conversation (Claude Code session `912a48e4`). Nothing is built yet. This folder holds only this file.

## Goal

Replace Forge's hand-tuned board evaluator with a learned model that predicts win probability from a game state. The model must generalize to cards it has never seen, so it can never represent a card by its identity.

Forge is the open-source MTG rules engine (Java, https://github.com/Card-Forge/forge). The user plays it and wants a better AI for it. Think "chess engine evaluation function", adapted to a game with hidden information.

## How we got here

The conversation went through and rejected these, in order:

1. **Writing our own rules engine.** Forge has about 15 years of community work behind it. Not worth redoing.
2. **A deck-testing harness around `forge sim`.** Feasible, but the user decided it wasn't worth building on its own. Its results are capped by Forge AI quality, which is weak with control and combo.
3. **Sampling hidden cards (determinization) or Monte Carlo playouts.** Both are still possible later. The user picked the learned evaluator instead.
4. **A fixed array indexed by card ID.** Rejected. It breaks every set release, needs meta-specific work, and doesn't generalize.
5. **A staged plan** (Stage 0 hand-picked totals, then Stage 1 hand-written card summaries, then Stage 2 learned set model). The user pushed back until it collapsed to one model. Don't reintroduce the stage framing.

## Decisions

- **One model.** It's a set-based network (DeepSets to start, a transformer later if card interactions need it). Inputs are the scoreboard columns plus a set of cards per zone, per player. Output is P(win) from "my" point of view.
- **Cards are described by what they do, parsed from Forge's card scripts.** Every card Forge can play has a structured script, so any card the AI will face is already described. Examples from `forge-gui/res/cardsfolder/`:
  ```
  Lightning Bolt   A:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3
  Grim Lavamancer  PT:1/1  A:AB$ DealDamage | Cost$ R T ExileFromGrave<2/Card> | ValidTgts$ Any | NumDmg$ 2
  Snapcaster Mage  PT:2/1  K:Flash  T:Mode$ ChangesZone | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ TrigFlashback
  ```
  A card's feature row is its script features (effect types, numbers, targets, costs, triggers, keywords, P/T, types, mana cost) plus its live state from the game object (current P/T, tapped, counters, summoning sick, attachments, controller).
- **As few hand-picked features as possible.** The user rejected count-style stand-ins such as land count, creature count, total power and untapped mana. Their meaning depends on which cards they are: one indestructible, hexproof Platinum Angel beats a pile of creatures. The board and your own hand go in as actual card sets.
- **The scoreboard columns are limited to rules-level quantities:** `me_life`, `opp_life`, `me_poison`, `opp_poison`, `me_library`, `opp_library`, `opp_hand` (count only), `turn`, `me_active`, `me_on_play`. Life isn't strictly monotonic (Death's Shadow), so don't constrain the model to treat more life as always better.
- **Hidden information stays hidden.** Use only the opponent's hand count, plus any cards they revealed if you track that. Your own hand goes in as cards.
- **No throwaway baseline model.** Prove the pipeline with a fake evaluator: a random number, or Forge's own score sent through the new code path. The scoreboard-only baseline is the real model trained with empty card sets, as an ablation.
- **Run the model through ONNX** inside Forge (`com.microsoft.onnxruntime` for Java), not a Python server. The search calls the evaluator at every node, so speed matters. Pad each zone to a fixed maximum and pass a mask, so the exported model has fixed input shapes.
- **Data layout:** one row per snapshot per side (each moment written twice, labels flipped). Snapshot at main phase 1 and end of turn to start. Label `won` as 1 or 0, and drop draws (Forge's `-c` clock defaults to 120 s and ends long games as draws). Optional second target: `turns_left`. One-vs-one only, no Commander.
- **Splits:** by `game_id` always, never by row. Also hold out whole decks (for example train on 40, test on 10) to test generalization. Use 30 to 50 decks across aggro, midrange, control and combo, so the model doesn't learn deck identity.
- **Evaluation in play:** run the new AI against stock Forge with the same decks and swapped seats, over many games. Compare against Forge's evaluator, and against the model with cards removed.

## Forge facts checked in the source (2026-10-15)

All paths are under `forge-ai/src/main/java/forge/ai/`:

- `AiController.java` has `USE_FULL_SIMULATION` and `USE_HYBRID_SIMULATION` modes (`usesFullSimulation()`, about line 110). In full mode, spell choice goes through `simPicker.chooseSpellAbilityToPlay` (about line 1366).
- `simulation/SpellAbilityPicker.java` and `simulation/SimulationController.java` copy the game with `GameCopier` and recurse to `DEFAULT_MAX_DEPTH = 3`. The search only chooses spells. Combat stays heuristic (`AiAttackController`, `AiBlockController`).
- `simulation/GameStateEvaluator.java` is the hand-tuned linear score our model replaces: about 4 points per card in hand, ±2 per life point, a mana-base score, and per-card values. It has a "prefer winning sooner" turn penalty (`isWinning`, `GAME_OVER_TURN_PENALTY`). Keep the model's output compatible with how the picker compares scores. The score is an `int`, so P(win) needs scaling.
- `simulation/GameCopier.java` has `PRUNE_HIDDEN_INFO = false`, so the current search sees the opponent's real hand and library order. Our evaluator must not read those zones.
- The simulation package is actively maintained. There were commits through 2026-09-11 ("Architecture to skip GUI updates when simulating", "refactor AI Attack"). Pull fresh before building, and expect line numbers to drift.
- Headless batch runs use `forge sim -d deckA deckB -n N [-f format] [-c clock] [-q]`, documented at https://github.com/Card-Forge/forge/wiki/ai. A 2017 forum thread found that decks had to live inside Forge's user deck folder, and that `-D` only worked with `-t` tournament mode. Recheck both.

## Next steps, in order

1. **Clone and build Forge** (Java and Maven) in this folder. Run `forge sim` on 50 games between two decks and time it. That number decides how much data a night of simulation produces. Not measured yet.
2. **Write the card-script parser** in Python, against `forge-gui/res/cardsfolder/`. It only needs the card files, so it can start before the build works. Its decisions (which script fields become features, how `$` parameters and `SVar` chains are handled) determine how well the model generalizes. Build it test-first.
3. **Set the maximum zone sizes** for padding (battlefield, hand, graveyard, exile) before writing the hook. The hook writes data in that shape.
4. **Add the logging hook in Forge** that writes snapshots (scoreboard columns plus padded card sets with live state) and labels each one with the winner at game end. Forge publishes game events (`forge.game.event`), which may be the cleanest place to attach it. Not verified.
5. **Plug in the fake evaluator** through the new code path in place of `GameStateEvaluator`. Confirm the full-simulation AI still finishes games and the comparison runs.
6. **Train the set model, export it to ONNX, load it in Forge, and run the comparisons.**

## Open questions

- Which script fields become features, and how to encode free-form `ValidTgts$` filters and `SVar` chains. This is the main design problem in step 2.
- Whether graveyard and exile go in as full card sets or counts. They matter for decks like Snapcaster and Lavamancer.
- How the full-simulation AI performs today. Nobody has played against it yet in this project. Do that once Forge builds.
- Labels come from stock AI playing both sides, so the model learns "good for Forge's AI." The planned fix is to retrain on games the new AI plays. Not urgent.
- Known blind spots even for the set model: two-card combos, and threats held in hand.

## How the user works

- Data scientist. Wants reasons, not reassurance, and will push back on any overstated claim. Concede quickly when they're right. They were right twice in this conversation.
- Explain designs with worked examples and tables. A concrete example board cleared up confusion that a prose explanation caused.
- Don't propose card-ID designs, hand-picked count features, or extra stages.
- Ask before creating files outside this project folder.

## Related

- Claude's memory note for this project: `C:\Claude\.claude\memory\projects\mtg-forge-ai-model.md`. Update it when decisions change.
- Sibling MTG project: `..\mtg-script-cards\` (transcript-to-card-image CLI). Unrelated code, same domain.
- Card representation research: Bertram, Fürnkranz, Müller, *Learning With Generalised Card Representations* (IEEE CoG 2024), https://arxiv.org/abs/2407.05879. That paper predicts draft picks, not gameplay. The representation choice mattered little on known cards, and the model matched 55% of human picks on cards it had never seen.
- Other open engines, if Forge's search turns out too slow: Argentum Engine (Kotlin, has an RL gym) https://github.com/rwaldron/argentum-engine and phase.rs (Rust, immutable state) https://github.com/lgray/phase.

## Suggested skills

- `unslop`: before writing any prose the user will read. Their global instructions require it.
- `tdd`: for the card-script parser and the snapshot logger. Both are pure transformations with clear inputs and outputs.
- `research`: for anything that needs primary sources, such as the ONNX Runtime Java API, Forge's event bus, or `forge sim` flag behavior in the current build.
- `codebase-design`: when deciding where the logging hook and the evaluator seam go inside Forge.
- `diagnosing-bugs`: if the Forge build or headless sim runs misbehave.
- `prototype`: if the card feature encoding needs a quick throwaway to compare options.
