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

The tests use the CPU and need no GPU. They do need torch, `onnx`, `onnxscript` and `onnxruntime`, the same way the Parquet tests need `pyarrow`.

## Commands

All but `pairtest` take the folder `gamerecord convert` wrote, the parsed card dump and the deck manifest.

```
python -m setmodel join PARQUET CARDS MANIFEST
python -m setmodel train PARQUET CARDS MANIFEST --out MODEL --generation 0
python -m setmodel export MODEL PARQUET CARDS MANIFEST --out BUNDLE
python -m setmodel ablate PARQUET CARDS MANIFEST --out ABLATION
python -m setmodel pairtest MODEL CARDS [--fixture FILE]
```

`join` prints the join report and truncation rates without training, and it doesn't need torch. `train` and `ablate` take `--epochs`, `--batch-size`, `--learning-rate`, `--width`, `--seed`, `--device` and `--min-count`. Their defaults come from `TrainConfig` in `setmodel/config.py`, except `--min-count`. `--device auto` uses the GPU if torch can see one, and the report says which device ran. The commands rebuild the vocabulary from the dump at `--min-count`, 5 by default, which gives the same table as `data/vocab.json`.

## Rows

A row is one snapshot as the game file holds it, labeled with whether that side won. Each game file already holds one snapshot per side per moment, so nothing is copied or flipped. `gamerecord convert` keeps wins only, so a drawn or stopped game gives no rows, and its label can't be wrong.

Row weight is 1 over the game's row count, counted after any drops, so every game weighs 1 in the loss and in every reported number. The loss divides by one number for the whole run, the training rows' mean weight times the batch size. A row in a short last batch has the same gradient scale as a row in a full batch.

Nothing joins or matches the two sides' snapshots. The only join key is `(game_id, snapshot_index)`, which names one line of one file. `tests/test_setmodel_dataset.py` changes the other side's line and requires this side's inputs to stay exact. Planting a join in `load_corpus` makes that test fail, whether the join names hidden cards from public state or only copies a scoreboard number across.

## What the model sees

The model's inputs are the fields below, each from the row's own snapshot. `setmodel/dataset.py` reads each table through a column list, so a column that isn't listed can't reach the model.

| Goes in | Why it is safe |
|---|---|
| All 10 scoreboard fields | Rules-level counts the side can see. The opponent's hand and library are counts. |
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
| `snapshot_index` | A join key. Its gaps depend on the other side's dedupe, so it isn't safe as a number. |
| `seat*_ai_profile`, `seat*_sim_mode`, `seat*_search_budget`, `honest_play` | Run settings, not game state. |
| Card `id`, stack `id`, `attached_to`, `attacking_card`, `blocking` and `blocked_by` values, `targets` | Opaque ids. An id is only an order-of-first-sight number, and it means nothing to a model. So what a stack item targets isn't an input. |
| The other side's snapshot, all of it | The rule above. |

A hidden card has no name, so it gets the empty card, with no tokens or numbers but with its live state and `hidden` set. It doesn't count as unmatched, so it doesn't drop the snapshot. `HIDDEN_COUNTS_AS_UNMATCHED` in `setmodel/dataset.py` flips that rule.

## Joining names

A card joins on name and face. Face picks the script where a name is on a front and a back, as `Phyrexian Token` is. A name with no entry and no split form is unmatched. The join counts it and drops its snapshot.

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

Every unmatched row is a stack item whose source is `Summon: Fenrir (58)'s Effect` or the like. Forge's effect cards aren't scripts. If a deck pool makes these common, they are worth a rule. So far they cost 36 of 665,118 snapshots.

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
| `epoch_loss` | Training loss per epoch, the weighted mean over its rows. |
| `truncation` | How often each cap bit. See `tensor-contract.md`. |
| `join` | The join report, with unmatched names and ambiguous names and their counts. |
| `device`, `torch`, `seconds`, `config` | What ran. |

Scores are null when a split is empty, because 0 would read as a result.

## Ablations

`ablate` trains five runs with the same seed and settings, into `OUT/<run>/`, and writes `ablation.md` and `ablation.json`.

| Run | What is gone |
|---|---|
| `full` | Nothing. |
| `no_cards` | Every card. This is the scoreboard-only baseline. |
| `no_graveyard_exile` | Cards in both graveyards and both exiles. |
| `no_live_state` | All live state. Cards are as printed. |
| `no_numbers` | Keyed values. Each key stays present with a value of 1, as a token would. |

The table has one row per run, with the columns `Run`, `Held-out accuracy (%)`, `Held-out log loss` and `Against full (points)`. One line under it gives kill criterion 2. The full model must beat `no_cards` by at least 5 points of held-out accuracy, the `KILL_MARGIN_POINTS` in `setmodel/review.py`. Post the real table on the issue when real data exists.

## Pair test

`pairtest` scores four boards with a trained model: empty, each card alone, and both. It prints the four probabilities and whether the pair's lift exceeds the sum of the two single lifts. If it doesn't, the design's next step is attention over the battlefield.

The fixture is a JSON file. `setmodel/pair_fixture.json` holds Platinum Angel and Lightning Greaves at 3 life. `cards` lists two cards by `name`, with optional `face` and `live` fields by the contract's names. `scoreboard` overrides any field of an even game. The cards go on the battlefield under this side's control.

## Export

`export` writes the bundle in `tensor-contract.md`, then runs held-out states through ONNX Runtime and PyTorch. It samples 300 of them from a fixed seed, and `--states` changes the count. It exits 1 if the largest difference passes the threshold, 1e-4 unless `--threshold` says otherwise. The check builds the ONNX inputs from the bundle's own card table and the PyTorch inputs from the corpus, so a table that disagrees with training fails too.

Export uses the dynamo exporter. The older TorchScript exporter also runs, but its scatter-add drifted by 0.002 at 300 cards on the same model.

## First real files

On 2026-11-06 the commands ran on the 10 `jar-smoke` games and `tests/fixtures/games/real_game.jsonl.gz`, with the full dump and a throwaway manifest. That made 1,886 snapshots and 32,764 card and stack rows, with 33 hidden cards, 0 unmatched and none dropped. Training ran on the 1080 Ti, and the export check's largest difference was 1.19e-07 over 198 states. That is a plumbing check on one held-out game. Its accuracies mean nothing, so they aren't recorded here. The run came before the fixed loss scale, so that difference belongs to the earlier weights.
