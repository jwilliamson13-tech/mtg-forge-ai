# Tensor contract

This is the interface between the trained model and Forge's ONNX evaluator, which is Spec 3. The Java side builds its inputs from this page and `tensor-contract-v1.json`, and doesn't need to read the Python. Spec 5 owns both.

The current version is 1. `tensor-contract-v1.json` is the machine-readable copy, and `setmodel.export.build_contract` is its source. A test fails if the two differ. Any change to a name, a dtype, a shape, a field's meaning or a field's order makes a new version. Each bundle carries its own `contract.json` with the same fields, plus a `bundle` section that says which generation it is.

## Bundle

`python -m setmodel export` writes these files to one folder.

| File | What it holds |
|---|---|
| `model.onnx` | The model, weights inside. Its scaling constants are inside it too, so inputs go in raw. |
| `card_features.jsonl.gz` | Every card's precomputed tokens and numbers. See "Card table". |
| `vocab.json` | The vocabulary, in the format of `vocabulary.md`. Forge doesn't need it to run the model. It says what the ids mean. |
| `scales.json` | The scaling constants, for reading only, per numeric key, per scoreboard field and per live field. |
| `contract.json` | This contract, with the sizes of this bundle and its `bundle` section. |

`bundle` holds the `generation`, the `batch_ids` it trained on, `card_entries`, the ONNX `opset`, the `onnxruntime` and `torch` versions used, and `truncation_rates`.

## Sizes

| Letter | Size | Meaning |
|---|---|---|
| N | dynamic, 0 or more | Cards in the state. The one dynamic axis. Nothing pads the card list or cuts it. |
| A | 16 | Rows per card. Row 0 is the identity row, then one row per ability and sub-ability. |
| T | 24 | Tokens per row. |
| K | 239 | Numeric keys, the vocabulary's `numeric_keys`. |
| L | 18 | Live-state fields. |
| S | 10 | Scoreboard fields. |

K comes from the vocabulary of `data/vocab.json`, built with `min_count=5`. A smoke bundle built from a small dump has a smaller K, and its `contract.json` says so.

## Inputs

One call scores one state from one side's view.

| Name | dtype | Shape | Meaning |
|---|---|---|---|
| `scoreboard` | float32 | [S] | Raw values, in the order below. Booleans are 0 or 1. |
| `token_ids` | int64 | [N, A, T] | Vocabulary ids from the card table. 0 where the mask is 0. |
| `token_mask` | float32 | [N, A, T] | 1 where a token is present. |
| `numbers` | float32 | [N, A, K] | Raw values from the card table, unscaled. |
| `number_mask` | float32 | [N, A, K] | 1 where a key is present. |
| `live` | float32 | [N, L] | Raw live state, in the order below. |
| `zone` | int64 | [N] | The card's zone, as an index into the zone list. |

The output is `win_probability`, float32, shape [1], the chance that this side wins.

### Scoreboard fields, in order

`me_life`, `opp_life`, `me_poison`, `opp_poison`, `me_library`, `opp_library`, `opp_hand`, `turn`, `me_active`, `me_on_play`. `game-record.md` defines each one.

### Zones, in order

`battlefield` is 0, `me_hand` 1, `me_graveyard` 2, `opp_graveyard` 3, `me_exile` 4, `opp_exile` 5, `opp_revealed` 6, `stack` 7. Both players' permanents share `battlefield`. A stack item counts as one card, looked up by its source card's name and face.

### Live fields, in order

Each is a raw number from the side's own view of the card. Null fields become 0.

| Index | Field | Value |
|---|---|---|
| 0 | `power` | Current power, 0 if the card isn't a creature. |
| 1 | `toughness` | Current toughness, 0 if the card isn't a creature. |
| 2 | `is_creature` | 1 if the card has power and toughness now. |
| 3 | `tapped` | 1 if tapped. |
| 4 | `summoning_sick` | 1 if sick, on the battlefield only. |
| 5 | `mine` | 1 if this side controls it. For `opp_revealed`, 0. For a stack item, 1 if this side activated or cast it. |
| 6 | `face_down` | 1 if face down. |
| 7 | `hidden` | 1 if this side may not look at it. |
| 8 | `damage` | Damage marked this turn. |
| 9 | `attacking` | 1 if attacking a seat or a card. |
| 10 | `blocking` | 1 if blocking anything. |
| 11 | `blocked` | 1 if a creature blocks it. |
| 12 | `attached` | 1 if attached to a card or a player. |
| 13 | `counters_p1p1` | Count of `P1P1` counters. |
| 14 | `counters_m1m1` | Count of `M1M1` counters. |
| 15 | `counters_loyalty` | Count of `LOYALTY` counters. |
| 16 | `counters_other` | Sum of every other counter type. |
| 17 | `stack_ability` | 1 for a stack item that is an ability, 0 for a spell and for any card. |

An `opp_revealed` card carries what the record wrote down, so it has `power` and `toughness` from the moment of the reveal and zeros in every other field. A stack item sets only `mine`, `face_down`, `hidden` and `stack_ability`.

Forge must build these from what the side can see, exactly as `game-record.md` builds a snapshot. The model trained on nothing else. Hidden cards take the rule below, and the opponent's hand and either library never appear as cards.

## Card table

One JSON object per line, in `card_features.jsonl.gz`:

```json
{"name": "Lightning Bolt", "slot": "front", "tokens": [[9, 40], [5, 3, 12]], "numbers": [[[1, 0.0], [2, 1.0]], [[7, 3.0]]]}
```

- `tokens[a]` is row `a`'s vocabulary ids. They fill `token_ids[n, a, 0..]`, with `token_mask` 1 for each.
- `numbers[a]` is row `a`'s `[key, value]` pairs. Each sets `numbers[n, a, key]` to the value, with `number_mask` 1.
- Everything past the lists stays 0, masks included.

To find a card's line:

1. A hidden card has no name, so it has no lookup. All its masks are 0, and `hidden` is 1 in `live`.
2. Map the record's face to a slot. `Original` is `front`, `EmptyRoom` is `empty_room`, and any other face is `back`.
3. Take the entry for (name, slot), or else the name's entry under any slot.
4. If the table has no such name, all masks are 0. Training dropped such snapshots, so the model has never seen this case with `hidden` 0, and a real run should log it.

A whole split name, `Fire // Ice`, has its own entries, one per slot. `front` and `back` hold the rows of both halves. `empty_room` is the empty Room, and it holds only the two identity rows.

A token name on several scripts has one entry, holding only what those scripts share. `training.md` says why. The live power and toughness still come from the game.

## Truncation

The file's `truncation` section holds the rules. Version 1 fixes them, and the card table already applies them, so Forge doesn't.

- Abilities: row 0 stays. Past A rows, the oldest other rows go.
- Tokens: past T tokens in a row, the oldest go.
- Numbers: two numbers under one key in a row add up, because `numbers` holds one value per key. A key below the vocabulary cutoff adds into the `<unk>` key at index 0.

A and T are about padding a card's inside, not the board. `truncation_rates` in the bundle reports how often each cap bit in the training corpus. Over the whole dump, 0.03% of faces pass A and 0.004% of ability rows pass T.

## Runtime

`export` checks the bundle under the Python `onnxruntime` named in `bundle.onnxruntime`. Pin ONNX Runtime Java to that version, or repeat the check under the Java version before trusting it.

The fork pins ONNX Runtime Java 1.31.1, the version the dry-run bundle names. `python -m setmodel parity` compares Forge's scores with PyTorch's on real game files, and `training.md` has the first result: 4,936 snapshots, largest difference 7.15e-07. Run it again for each new bundle.

On Windows, ONNX Runtime 1.31 needs `msvcp140.dll` 14.40 or newer, and the JVM loads that file from its own `bin` folder. Oracle JDK 21.0.2 ships 14.31, and with it the JVM crashes inside ONNX Runtime as the evaluator starts. Forge's evaluator checks the file's version first and refuses with a message. `forge-build.md` says how to get a JVM that works.
