# Training

`setmodel` turns game records and parsed cards into a trained model and an export bundle for Forge. `game-record.md` has its input tables, `card-structure.md` its card format, and `tensor-contract.md` what it hands Forge.

## Environment

Training runs on the GTX 1080 Ti, a Pascal card with compute capability 6.1. The pin is `torch==2.14.1+cu126`, from PyTorch's CUDA 12.6 index. `requirements-dev.txt` holds it and the other pins:

```
python -m venv venv
venv\Scripts\python -m pip install -r requirements-dev.txt
```

Don't upgrade past 2.14 on this card. The sources, read on 2026-11-06:

- PyTorch's `RELEASE.md` matrix lists CUDA 12.6 up to 2.14. Release 2.15 ships CUDA 13.2 and 13.4 only, and its architecture table starts at Turing, compute capability 7.5.
- `https://download.pytorch.org/whl/cu126/torch/` stops at 2.14.1. It has a Windows wheel for Python 3.10 to 3.15, so no older Python is needed. It ran on 3.13.
- The cu130 and later indexes have newer wheels, but CUDA 13 dropped Pascal, so a higher version number on those indexes doesn't help.

On the 1080 Ti, under Python 3.13.3 and driver 580.97, `torch.cuda.get_arch_list()` on the installed wheel gives `sm_50, sm_60, sm_61, sm_70, sm_75, sm_80, sm_86, sm_90`. A 4096 by 4096 matmul ran on `cuda:0`, and so did the smoke training and the real-file run below.

## Tests

The test suite needs `requirements-dev.txt` installed in a venv. Run it with the venv's Python, from the repo root:

```
venv\Scripts\python -m pytest
```

Global Python on this machine has no `onnx`, so pytest there stops at collection on `tests/test_setmodel_export.py` and runs nothing. The tests use the CPU and need no GPU. They do need torch, `onnx`, `onnxscript` and `onnxruntime`, the same way the Parquet tests need `pyarrow`.

## Commands

All but `pairtest` take the folder `gamerecord convert` wrote, the parsed card dump and the deck manifest.

```
python -m setmodel join PARQUET CARDS MANIFEST
python -m setmodel train PARQUET CARDS MANIFEST --out MODEL --generation 0
python -m setmodel export MODEL PARQUET CARDS MANIFEST --out BUNDLE
python -m setmodel ablate PARQUET CARDS MANIFEST --out ABLATION
python -m setmodel pairtest MODEL CARDS [--fixture FILE]
python -m setmodel parity MODEL PARQUET CARDS MANIFEST SCORES
```

`join` prints the join report and truncation rates without training, and it doesn't need torch. `train` and `ablate` take `--epochs`, `--batch-size`, `--learning-rate`, `--width`, `--seed`, `--device`, `--weight-decay`, `--val-share`, `--eval-every`, `--patience` and `--min-count`. Their defaults come from `TrainConfig` in `setmodel/config.py`, except `--min-count`. `--device auto` uses the GPU if torch can see one, and the report says which device ran. The commands rebuild the vocabulary from the dump at `--min-count`, 5 by default, which gives the same table as `data/vocab.json`.

## Rows

A row is one snapshot as the game file holds it, labeled with whether that side won. Each game file already holds one snapshot per side per moment, so nothing is copied or flipped. `gamerecord convert` keeps wins only, so a drawn or stopped game gives no rows, and its label can't be wrong.

Row weight is 1 over the game's row count, counted after any drops, so every game weighs 1 in the loss and in every reported number. The loss divides by one number for the whole run, the training rows' mean weight times the batch size. A row in a short last batch has the same gradient scale as a row in a full batch.

Nothing joins or matches the two sides' snapshots. The only join key is `(game_id, snapshot_index)`, which names one line of one file. `tests/test_setmodel_dataset.py` changes the other side's line and requires this side's inputs to stay exact. Planting a join in `load_corpus` makes that test fail, whether the join names hidden cards from public state or only copies a scoreboard number across.

## What the model sees

The model's inputs are the fields below, each from the row's own snapshot. `setmodel/dataset.py` reads each table through a column list, so a column that isn't listed can't reach the model.

| Goes in | Why it is safe |
|---|---|
| All 16 scoreboard fields | Rules-level counts the side can see. The opponent's hand and library are counts. Who has the initiative, who is the monarch and each player's speed are public. |
| `name` and `face`, as the card's parsed tokens and numbers | A record sets a name only when this side may see it. The name itself never reaches the model. |
| `zone` | Where this side sees the card. The stack is an eighth zone. |
| `power`, `toughness`, `tapped`, `summoning_sick`, `face_down`, `damage` | Public state, written for this side. |
| `controller`, as `mine` | Relative to `side`, so no seat number goes in. |
| `counters`, as P1P1, M1M1, LOYALTY and a sum of the rest | Public, on face-down cards too. |
| `attacking_seat` and `attacking_card`, as `attacking`; `blocking`; `blocked_by`, as `blocked`; `attached_to` and `attached_to_seat`, as `attached` | Only as yes or no. Their values are ids. |
| A stack item's `kind`, as `stack_ability` | Public. |

| Left out | Why |
|---|---|
| `final_turn` | It tells the future, because it gives the game's length. |
| `won` | The label. |
| `seat1_deck`, `seat2_deck` | The opponent's list is hidden, and a deck's name would let the model memorize matchups. They pick the split and nothing else. |
| `side` | A seat number isn't a game state, and Forge has no reason to feed it. It makes `controller` relative and finds this side's deck for the report. |
| `game_id`, `seed`, `game_number`, `batch_id` | Run identity. The code joins, splits and tags with these and never feeds them in. |
| `snapshot_index` | A join key. Its gaps depend on the other side's dedupe, so it isn't safe as a number. The corpus keeps it so that `parity` can find a row. |
| `seat*_ai_profile`, `seat*_sim_mode`, `seat*_search_budget`, `honest_play` | Run settings, not game state. |
| Card `id`, stack `id`, `attached_to`, `attacking_card`, `blocking` and `blocked_by` values, `targets` | Opaque ids. An id is only an order-of-first-sight number, and it means nothing to a model. So what a stack item targets isn't an input. |
| The other side's snapshot, all of it | The rule above. |

A hidden card has no name, so it gets the empty card, with no tokens or numbers but with its live state and `hidden` set. It doesn't count as unmatched, so it doesn't drop the snapshot. `HIDDEN_COUNTS_AS_UNMATCHED` in `setmodel/dataset.py` flips that rule. A card named `hidden`, the placeholder #25's pruned search copies hold, is a hidden card too, in any zone. A real game never holds one, so this rule is for Forge's evaluator, and the join has it so the two sides agree.

## Joining names

A card joins on name and face. Face picks the script where a name is on a front and a back, as `Phyrexian Token` is. A name with no entry and no split form is unmatched. The join counts it and drops its snapshot, with one exception from tensor contract version 2: a stack ability whose source the dump lacks has no card behind it, and the join leaves it out of the state and keeps the snapshot. The report lists these under `stack abilities left out`, with a Forge id in an effect's name written as `(N)`.

- A whole split card, `Fire // Ice`, joins on its two halves. An empty Room joins on their identity rows only. Any face but `EmptyRoom` gets the rows of both halves, and the bundle's table has a `back` entry so it gives the same rows. None of the 3,249 real files below holds a whole name on any face, so only the fixtures test this rule.
- A face copied from another script through `CopyFaceFrom` has no name in the dump, and it joins on the name it copies.
- A name on a real card and on a token script takes the real card. 14 names do this, such as `Llanowar Elves`.
- A name on several scripts that disagree gets only what they all share. 122 token names cover 515 of the 857 token scripts, and `Elemental Token` alone has 35. The record has no token script id, so nothing says which one is in play, and name and face have to map to one entry for Forge. So `Elemental Token` is a creature elemental with no printed size and no abilities, and its real size comes from the live state. The cost is that a token's abilities are invisible when its scripts differ. An exact fix needs the token script in the record, and that means a new schema version.

On 2026-11-06, a scan of 3,249 real game files gave these counts, from the earlier test batches plus `jar-smoke`:

| | Rows |
|---|---|
| Named cards and stack items | 14,412,800 |
| Token rows, all matched by name | 178,738 |
| Rows on a name with disagreeing scripts | 113,992, on 21 names |
| Unmatched | 40, from 36 snapshots |

Every unmatched row is a stack item whose source is `Summon: Fenrir (58)'s Effect` or the like. Forge's effect cards aren't scripts. So far they cost 36 of 665,118 snapshots.

The 676-game dry run made them common: under version 1 they dropped 1,292 of its 166,054 snapshots, 0.78%. Version 2 has the rule for them. On 2026-11-08 the version 2 join of the same files dropped 0 snapshots, with 0 unmatched names, and left out 1,970 stack rows on 6 names:

| Rows | Name |
|---|---|
| 1,104 | `Tamiyo, Seasoned Scholar (N)'s Effect` |
| 534 | `The Initiative` |
| 132 | `The Monarch` |
| 120 | `Start Your Engines!` |
| 44 | `Forth Eorlingas! (N)'s Effect` |
| 36 | `Chancellor of the Annex (N)'s Effect` |

The dry run's files are schema version 1, so their initiative, monarch and speed fields are 0. Ten schema version 2 games recorded the same day show how much that loses: in an initiative-stompy-1 game, 54 of 66 snapshots have a player holding the initiative and 6 show its trigger, and in a dredge-2 game against uwx-control-2, 120 of 176 have a monarch and 16 show its trigger.

## Splits

Splits go by whole games, from the manifest's deck splits. A game with a held-out deck in either seat is `held_out_decks`. Of the rest, a hash of the game id sends 10% to `held_out_games` and the others to `train`. A deck that isn't in the manifest stops the run, because it could be a held-out deck under another name.

A record's `deck` is the `.dck` file's `Name=`, and Spec 4 writes the deck id there, so the join is on the manifest's `id`.

## Model

Tokens go through an embedding and a masked mean, and each keyed number adds its key's embedding times value over the key's constant. A small MLP makes that the ability vector. Abilities pool to a card. The model adds the zone embedding and concatenates the live state, and an MLP gives the placed card. Cards pool per zone, and the zones and scoreboard go through the head to one logit.

The per-key constant is the median absolute deviation of the key's values over the distinct cards in the training rows, or their median size if most agree, or 1 for a key never seen. The model stores it. `generic`, `pip.*` and printed stats are ordinary keys.

Mean pooling can't count. One bear and five bears pool to the same `battlefield` vector, and the same goes for a graveyard's size. The spec asks for means, and the board size only reaches the model through which cards are in the mean. If the ablations disappoint, look here before the card encoding.

## Report

`train` writes `model.pt` and `metrics.json`. The artifact holds the weights, the vocabulary, the caps and `metadata`, which has `generation`, `batch_ids`, the ablation, and the row and game counts. `batch_ids` are the batches of the training rows.

| Field in `metrics.json` | Meaning |
|---|---|
| `held_out` | `log_loss`, `accuracy`, `rows` and `games` over both held-out splits. Ablations compare this. |
| `held_out_games`, `held_out_decks` | The same, per split. |
| `per_deck_accuracy` | The same, for each held-out deck in the manifest, over rows where it is this side's deck. |
| `epoch_loss` | Training loss per epoch, the weighted mean over its rows. The last entry covers a part of an epoch when the run stopped inside it. |
| `validation` | The early-stopping record: `share`, `games`, `rows`, `eval_every`, `patience`, `stopped_early`, `kept`, and `curve`. See Early stopping. |
| `truncation` | How often each cap bit. See `tensor-contract.md`. |
| `join` | The join report, with unmatched names and ambiguous names and their counts. |
| `device`, `torch`, `seconds`, `config` | What ran. |

Scores are null when a split is empty, because 0 would read as a result.

## Early stopping

Gen 0's first real run (9,958 games, 2.45M snapshots, 7,618 training games) trained a fixed 10 epochs and overfit from epoch 2. Held-out numbers after each epoch, both held-out splits together:

| Epoch | Train loss | Held-out log loss | Held-out accuracy |
|---|---|---|---|
| 1 | 0.338 | 0.429 | 81.1% |
| 2 | 0.258 | 0.542 | 79.0% |
| 3 | 0.204 | 0.668 | 78.2% |
| 4 | 0.171 | 0.778 | 78.0% |
| 5 | 0.150 | 0.822 | 78.2% |
| 10 (shipped) | 0.106 | 1.140 | 76.1% |

Held-out predictions above 0.99 or below 0.01 went from 22% of them at epoch 1 to 52% at epoch 5. Every snapshot in a game has the game's label, so one game is about 250 near-copy rows, and the model can memorize games.

`train` now holds back `--val-share` of the training games (default 0.1) and scores them during training. The split is by whole game, never by row, and it is drawn from the training split only: a game in `held_out_games` or `held_out_decks` is never read, by the weights or by the stopping rule. The draw uses its own generator seeded from `--seed`, at least one game goes each way, and a corpus with fewer than two training games, or `--val-share 0`, has no validation and trains all `--epochs` as before.

The model is scored on the validation games every `--eval-every` batches (default 2000) and at the end of each epoch. At batch size 256 and gen 0's 1.7M fitting rows an epoch is about 6,600 batches, so that is about 3 scores in epoch 1 plus the end of each epoch. The best score so far is a copy of the weights. After `--patience` scores in a row with no lower validation log loss (default 4) the run stops, and the copy of the best weights is restored before `model.pt` and the held-out report are written. `--epochs` stays the cap. `--eval-every 0` scores at epoch ends only, which is cheaper and can miss a best point inside epoch 1. A score runs the model forward, with no backward pass, over the validation rows, about 10% of the fitting rows. That should cost about 4% of an epoch per score, an estimate from row counts that nobody has timed on gen 0 yet. At the defaults that is roughly 3 scores per epoch, so about 12% extra.

`--weight-decay` (default 0) switches the optimizer to AdamW's decoupled decay. At 0 it is plain Adam.

`metrics.json` `validation.curve` has one point per score: `step`, `epoch` (fractional inside an epoch), `train_loss` (the weighted mean since the last score), `val_log_loss` and `val_accuracy`. `validation.kept` is the curve point whose weights were saved. The held-out numbers in the report are for those weights. `train_rows` and `train_games` in the artifact's metadata still count the whole training split, validation games included. The vocabulary, caps and key scales come from the whole training split too, which is features and not labels.

The loop (`simrunner/loop.py`) calls `train` and `ablate` without these flags, so it gets the defaults, and its `train` summary carries `kept` and `stopped_early`.

## Ablations

`ablate` trains five runs with the same seed and settings, into `OUT/<run>/`, and writes `ablation.md` and `ablation.json`.

| Run | What is gone |
|---|---|
| `full` | Nothing. |
| `no_cards` | Every card. This is the scoreboard-only baseline. |
| `no_graveyard_exile` | Cards in both graveyards and both exiles. |
| `no_live_state` | All live state. Cards are as printed. |
| `no_numbers` | Keyed values. Each key stays present with a value of 1, as a token would. |

Every run, the full model and the four ablations, stops by the early-stopping rule above, on the same validation games. Kill criterion 2 therefore compares models that each stopped at their own best validation point, not models cut off at the same epoch. The split depends on the seed and the corpus, not on the ablation. The table has one row per run, with the columns `Run`, `Held-out accuracy (%)`, `Held-out log loss`, `Against full (points)` and `Kept at epoch`. One line under it gives kill criterion 2. The full model must beat `no_cards` by at least 5 points of held-out accuracy, the `KILL_MARGIN_POINTS` in `setmodel/review.py`. Post the real table on the issue when real data exists.

## Pair test

`pairtest` scores four boards with a trained model: empty, each card alone, and both. It prints the four probabilities and whether the pair's lift exceeds the sum of the two single lifts. If it doesn't, the design's next step is attention over the battlefield.

The fixture is a JSON file. `setmodel/pair_fixture.json` holds Platinum Angel and Lightning Greaves at 3 life. `cards` lists two cards by `name`, with optional `face` and `live` fields by the contract's names. `scoreboard` overrides any field of an even game. The cards go on the battlefield under this side's control.

## Export

`export` writes the bundle in `tensor-contract.md`, then runs held-out states through ONNX Runtime and PyTorch. It samples 300 of them from a fixed seed, and `--states` changes the count. It exits 1 if the largest difference passes the threshold, 1e-4 unless `--threshold` says otherwise. The check builds the ONNX inputs from the bundle's own card table and the PyTorch inputs from the corpus, so a table that disagrees with training fails too.

Export uses the dynamo exporter. The older TorchScript exporter also runs, but its scatter-add drifted by 0.002 at 300 cards on the same model.

## Parity with Forge

`export` compares ONNX with PyTorch on inputs Python built. `parity` checks the other half, the inputs Forge builds in Java. The fork's `forge.view.OnnxScoreRecords` reads game files and builds each snapshot's inputs with the code Forge's ONNX evaluator uses in a game. It runs the bundle under ONNX Runtime for Java and writes a scores file. `parity` then scores the same snapshots from Parquet with the PyTorch model the bundle came from, and compares.

```
java -cp FORGE_JAR forge.view.OnnxScoreRecords BUNDLE scores.tsv GAME_FILE_OR_FOLDER...
python -m setmodel parity MODEL PARQUET CARDS MANIFEST scores.tsv
```

The scores file is tab-separated, with the header `game_id`, `snapshot_index`, `probability`. `parity` exits 1 if the largest difference passes 1e-4, or `--threshold`, and also if no snapshot matched. A snapshot training dropped, such as one holding an unmatched effect card, has no PyTorch score. It is counted and left out.

On 2026-11-07 it ran on 20 of the dry run's 676 game files, every 34th by name, with the dry-run model and bundle. They held 4,974 snapshots. 4,936 matched, with a largest difference of 7.15e-07 and a mean of 3.8e-08. The other 38 hold an effect card, such as `Tamiyo, Seasoned Scholar (51)'s Effect` or `The Monarch`, and training dropped them.

On 2026-11-08 it ran under tensor contract version 2 on all 676 files, with a model trained for 1 epoch on the CPU from the version 2 join of the same files and exported with a version 2 bundle (export check 1.79e-07 over 300 states). Forge's side ran on fork branch `t-contract-v2`.

| Snapshots | Matched | Largest difference | Mean |
|---|---|---|---|
| All 166,054 of the dry run | 166,054 | 2.98e-07 | 3.72e-08 |
| The 1,292 that version 1 dropped, each with a left-out stack ability | 1,292 | 2.09e-07 | 3.86e-08 |
| 1,976 from 10 schema version 2 games, recorded that day | 1,976 | 2.38e-07 | 4.26e-08 |
| Of those, 868 with the initiative, the monarch or speed set | 868 | 1.79e-07 | 3.87e-08 |
| Of those, 88 with a left-out stack ability | 88 | 1.19e-07 | 3.57e-08 |

Forge left out 1,970 stack abilities on the dry run and 88 on the new games, the same counts as the join. A version 1 bundle still runs on that jar. The dry-run v1 bundle gave the same scores, bit for bit, as on the trunk jar (`9c32892`) on the 164,762 dry-run snapshots with no left-out stack ability. On the other 1,292, which version 1 training dropped, 1,249 moved, by at most 0.316 and 0.012 on average. The placeholder case has no game file, because a real game never holds one. The fork's `OnnxEvaluatorTest` covers it twice, on the fixture below and on a pruned copy of a live game that plays a placeholder onto the battlefield.

The fork's Java tests run on a fixture that `tests/forge_fixture.py` builds: a smoke-model bundle, a game file with every kind of input the contract has, and PyTorch's score for each of its snapshots. `python -m tests.forge_fixture OUT` rebuilds it, and the fork keeps the result in `forge-gui-desktop/src/test/resources/onnx-fixture/`. From version 2 the fixture also holds the initiative, the monarch and speed on the scoreboard, The Initiative and a Tamiyo effect on the stack, and the `hidden` placeholder on the battlefield and in a graveyard. The version 1 fixture stays in `onnx-fixture-v1/`, so the fork's tests show a version 1 bundle still loads and scores what it did.

## First real files

On 2026-11-06 the commands ran on the 10 `jar-smoke` games and `tests/fixtures/games/real_game.jsonl.gz`, with the full dump and a throwaway manifest. That made 1,886 snapshots and 32,764 card and stack rows, with 33 hidden cards, 0 unmatched and none dropped. Training ran on the 1080 Ti, and the export check's largest difference was 1.19e-07 over 198 states. That is a plumbing check on one held-out game. Its accuracies mean nothing, so they aren't recorded here. The run came before the fixed loss scale, so that difference belongs to the earlier weights.

## Memory

`load_corpus` never holds a whole card or stack table. It reads each one twice, about 250,000 rows at a time. `gamerecord convert` writes a row group per game, so a chunk is a run of whole games. The first pass resolves names, counts each snapshot's card rows and finds the snapshots an unmatched card drops. That fixes where every kept card row goes, and the second pass writes each row straight to its place. A row's cards come out in the same order as before: its card table rows in table order, then its stack rows.

A card row takes 19 bytes. The feature index is a `uint16`, the zone an `int8`, and the 11 yes-or-no live fields are bits of one `uint16`. The 7 numeric live fields are `int16`, and the whole array widens to `int32` if any value doesn't fit. `Corpus.live` builds the contract's float `live` rows for one batch's positions. No ablation copies the card arrays. `no_cards` and `no_graveyard_exile` leave their zones out where a batch is built, and `no_live_state` swaps in a zero view that takes no memory.

The old join held the whole Arrow tables, turned the zone column into one Python string per row, stored live state as 18 float32s a row, and copied the card arrays twice to drop and reorder rows. An ablation copied them again. On the dry run, 676 games with 166,054 snapshots and 4.8 million card and stack rows, peak private bytes went like this:

| Command | Before | After |
|---|---|---|
| `join` | 2,917 MiB | 1,051 MiB |
| `train`, CPU, 3 epochs | 3,650 MiB | 2,073 MiB |
| `export` | 3,665 MiB | 1,957 MiB |

Before, each snapshot added about 12 KB, which put a 10,000-game join near 30 GB. Copies of the dry run with renamed games, up to 8 times its size, measured the cost now. A snapshot adds about 760 bytes to `join` and 700 to `train` on CUDA, and `ablate` peaks where `train` does. For gen 0's 2,452,446 snapshots that comes to about 2.7 GiB for `join` and 3.9 GiB for `train`. About 1 GiB of the join's figure doesn't grow with the corpus: the card dump, and buffers OpenBLAS reserves when numpy loads. `train` adds about 1.4 GiB more for PyTorch and CUDA.

Training on the CPU with more than one thread doesn't repeat itself bit for bit, because the order of float additions changes between runs. On one thread the old and new code gave the same weights and the same `metrics.json` for `train` and for all five `ablate` runs on the dry run, and the same export bundle.
