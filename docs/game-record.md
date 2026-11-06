# Game record

The game record is the only interface between Forge and Python. The Java hook (#18) writes it, `gamerecord` reads it, and neither side may depend on anything else about the other.

The current `schema_version` is 1.

Any change to a field, a type or a rule here bumps the version, and this document, the hook and `gamerecord/record.py` change in the same step. The loader refuses every other version, so a file from a mismatched build fails at load time. `tests/test_gamerecord_schema_doc.py` fails if this document and the validator name different fields.

Sources for each field are Forge classes as read at commit `c72fcbfdcc0`. No hook has written a real file yet, so these mappings have never run.

## File

One gzip'd JSONL file per game, UTF-8, one JSON object per line.

- Line 1 is the metadata object.
- Every line after it is a snapshot, in game order. A file with no snapshots is invalid.

The name is `<batch_id>_<seed>_<game_number>_<deck1>_vs_<deck2>.jsonl.gz`. In a deck name, each run of characters outside `A-Za-z0-9` becomes one `-`, and the result is cut to 40 characters. The name exists for sharding. The loader takes identity from the metadata and never parses it.

The game number has to be in the name. Forge seeds one `java.util.Random` per JVM and plays all of a worker's `-n` games from that stream, so every game from a worker shares a seed and a deck pair. Without the number, each game would overwrite the last.

The hook buffers a game in memory and writes the file at game end. It writes under a temporary name that doesn't end in `.jsonl.gz` and renames the file once closed, so a killed worker can't leave a half-written game that looks finished. A game that throws writes nothing.

## Metadata line

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | Always 1 for this version. |
| `batch_id` | string matching `[A-Za-z0-9_-]+` | The batch this game belongs to. It names a folder in the Parquet output, which is why the characters are limited. |
| `seed` | integer, 0 to 2^63-1 | The `-s` value the worker's JVM got. A worker's games all share it. |
| `game_number` | integer, 1 or more | This game's place in the worker's run, the `n` of `Game Result: Game n`. |
| `seats` | array of two seat objects | Seat 1, then seat 2. |
| `winner` | integer 1 or 2, or null | The winning seat. Null for every end reason except `win`. |
| `final_turn` | integer, 1 or more | The turn the game ended on. |
| `end_reason` | string, one of the end reasons | Why the game ended. |
| `tripwire` | tripwire summary object | Hidden-information reads during this game. |

Seat 1 is the first deck given to `-d`, printed by Forge as `Ai(1)`. `game_id` is not a field. The loader derives it as `<batch_id>-<seed>-<game_number>`.

### Seat

| Field | Type | Meaning |
|---|---|---|
| `seat` | integer 1 or 2 | Which seat this is. |
| `deck` | string | The deck's name from its `.dck` file. |
| `ai_profile` | string | The AI profile that played, after defaults, such as `Default`. |
| `sim_mode` | string, one of the sim modes | The mode this seat played in, after defaults. |
| `search_budget` | integer, 0 or more | The `-budget` search steps per decision. 0 means off. |

### Sim modes

| Value | Meaning |
|---|---|
| `heuristic` | Forge's stock AI, no search. |
| `hybrid` | The `-sim hybrid` mode. |
| `full` | The `-sim full` mode. |

### Tripwire summary

The hook writes this object from the start. Until #20 lands it writes `{"enabled": false, "hits": 0, "signatures": []}`.

| Field | Type | Meaning |
|---|---|---|
| `enabled` | boolean | Whether the tripwire ran this game. |
| `hits` | integer, 0 or more | How many hidden-zone reads it caught. |
| `signatures` | array of strings | The unique stack signatures behind those hits. |

`enabled` is there because 0 hits from a tripwire that was off says nothing. A batch is clean only when every game has `enabled` true and `hits` 0.

### End reasons

| Value | Meaning |
|---|---|
| `win` | A player won under the rules. `winner` names the seat. |
| `draw_clock` | The wall clock stopped the game. `winner` is null. |
| `draw_turn_cap` | The hook's turn cap stopped the game. `winner` is null. |
| `draw_rules` | The rules ended the game as a draw, such as both players reaching 0 life at once. `winner` is null. |
| `exception` | The game threw. The hook writes no file for such a game, so this value should never appear. The loader rejects it anyway. |

Never take `winner` from Forge's outcome on a stopped game. `SimulateMatch` stops a game with `setGameOver(GameEndReason.Draw)`, `Player.onGameOver` then marks everyone who hasn't lost as a winner, and `getOutcome()` names an arbitrary seat. The hook sets the end reason from what stopped the game, and writes no winner for it.

## Snapshot lines

| Field | Type | Meaning |
|---|---|---|
| `side` | integer 1 or 2 | The seat this snapshot is written from. |
| `scoreboard` | scoreboard object | The rules-level numbers. |
| `zones` | zones object | Every card this side can see. |

The hook writes two snapshots per moment, one from each side, at every priority pass. A single line can't serve both sides, because `me_hand`, `opp_revealed` and hidden names differ between them. Each line is already one training row, labeled from `side` and `winner`.

Dedupe compares a snapshot with the previous snapshot from the same side, and skips it if the serialized forms are equal. So the two sides can have different line counts. A snapshot has no sequence number, because one would make every line differ and dedupe would never fire. The converter numbers them from their position in the file.

Everything named `me_` belongs to `side`, and everything named `opp_` belongs to the other seat.

### Scoreboard

| Field | Type | Meaning |
|---|---|---|
| `me_life` | integer | Life total. It can be negative under Platinum Angel. |
| `opp_life` | integer | Life total. |
| `me_poison` | integer, 0 or more | Poison counters. |
| `opp_poison` | integer, 0 or more | Poison counters. |
| `me_library` | integer, 0 or more | Cards in library. |
| `opp_library` | integer, 0 or more | Cards in library. |
| `opp_hand` | integer, 0 or more | Cards in the opponent's hand. |
| `turn` | integer, 1 or more | The game's turn number, at most `final_turn`. |
| `me_active` | boolean | Whether it is this side's turn. |
| `me_on_play` | boolean | Whether this side took the game's first turn. |

### Zones

All seven are required, and an empty zone is `[]`. The opponent's hand and either library never appear as cards, only as counts.

| Field | Type | Meaning |
|---|---|---|
| `battlefield` | array of cards | Both players' permanents. |
| `me_hand` | array of cards | This side's hand. |
| `me_graveyard` | array of cards | This side's graveyard. |
| `opp_graveyard` | array of cards | The opponent's graveyard. |
| `me_exile` | array of cards | This side's exile zone. |
| `opp_exile` | array of cards | The opponent's exile zone. |
| `opp_revealed` | array of cards | The cards in this side's `AiCardMemory.REVEALED_CARDS`. |

A revealed card can also sit in another zone in the same snapshot, so a card `id` can appear twice.

### Card

| Field | Type | Meaning |
|---|---|---|
| `id` | integer | `Card.getId()`, stable within a game. It exists so attachments can point at a card, and is not a model input. |
| `name` | string, or null | `Card.getName()` in the card's current state. Null only for a hidden card. |
| `face` | string, one of the faces, or null | `Card.getCurrentStateName()`. Null only for a hidden card. |
| `power` | integer, or null | `Card.getNetPower()` if the card is a creature right now, else null. |
| `toughness` | integer, or null | `Card.getNetToughness()` if the card is a creature right now, else null. |
| `tapped` | boolean | `Card.isTapped()`. |
| `counters` | object, counter type to integer 1 or more | One entry per counter type on the card, `{}` for none. Keys are `CounterType.toString()`, such as `P1P1` or `LOYALTY`. `getName()` gives `+1/+1`, which is the wrong one. |
| `summoning_sick` | boolean | `Card.isSick()`. |
| `attached_to` | integer, or null | The `id` of the card this is attached to. It must be on the battlefield in the same snapshot. |
| `attached_to_seat` | integer 1 or 2, or null | The seat of the player this is attached to, for Curse auras and the like. |
| `controller` | integer 1 or 2 | The seat of `Card.getController()`. |
| `face_down` | boolean | `Card.isFaceDown()`. |

`Card.getEntityAttachedTo()` returns a `GameEntity`, which can be a card or a player. That is why attachment takes two fields. At most one of them is set.

A face-down card is hidden when `side` may not look at it, by `Card.mayPlayerLook`. A hidden card has null `name` and null `face`, and keeps its public state: `face_down` true, its 2/2, its counters. This stops a snapshot from leaking what the opponent's morph is. When `side` may look, `name` and `face` give the card's up side, with `face` as its state once turned up, because `FaceDown` is not a legal value.

### Faces

| Value | Meaning |
|---|---|
| `Original` | The front face, or the whole card. |
| `Flipped` | A flip card's flipped half. |
| `Backside` | A double-faced card's back. |
| `Meld` | A melded card. |
| `LeftSplit` | A split card's left half. |
| `RightSplit` | A split card's right half. |
| `Secondary` | An adventure or other secondary face. |
| `PreparedSpell` | Forge's state of that name. |
| `EmptyRoom` | Forge's state of that name. |
| `SpecializeW` | A specialized white face. |
| `SpecializeU` | A specialized blue face. |
| `SpecializeB` | A specialized black face. |
| `SpecializeR` | A specialized red face. |
| `SpecializeG` | A specialized green face. |

These are Forge's `CardStateName` values minus `FaceDown`. A value Forge adds later fails the loader until this list and the version change.

## What the loader checks

`gamerecord.load_game(path)` returns a `GameRecord` with `metadata`, `snapshots` and `game_id`, or raises `GameRecordError`. Beyond the fields and types above, it enforces these rules:

- It checks `schema_version` first and refuses anything but 1.
- A field this document doesn't name is an error, the same as a missing field.
- An integer must be a JSON integer. `20.0` and `true` fail. Every integer except `seed` must fit Java's `int`.
- `exception` raises `ExceptionGameError`, a kind of `GameRecordError`.
- `winner` is set for `win` and null for every other end reason.
- `seats` is seat 1, then seat 2.
- A snapshot's `turn` can't pass `final_turn`.
- `name` and `face` are both set or both null, and null needs `face_down` true.
- `power` and `toughness` are both set or both null.
- A tripwire that was off reports no hits and no signatures. Signatures are unique. `hits` is at least the number of signatures, and hits need at least one signature.

A truncated gzip, bad JSON or bad UTF-8 also raises `GameRecordError`.

## Parquet

```
python -m gamerecord convert FOLDER OUT
```

This reads every `*.jsonl.gz` under `FOLDER` and writes `OUT/snapshots/batch_id=<id>/part-0.parquet` and `OUT/cards/batch_id=<id>/part-0.parquet`. It keeps wins only, because a draw has no label. Draws stay in the raw files.

It prints what it kept and dropped by end reason, then each rejected file and why. An exception file counts as dropped. Any other file the loader refuses is rejected, and the rest of the folder still converts. The exit code is 1 if it rejected anything.

Converting again replaces each batch it finds a win for. To read a table:

```python
import pyarrow.dataset as ds

table = ds.dataset("OUT/snapshots", format="parquet", partitioning="hive").to_table()
```

`(game_id, snapshot_index)` joins the two tables. A card-less snapshot has no rows in `cards`.

### snapshots table

One row per snapshot of a kept game.

| Column | Type | Meaning |
|---|---|---|
| `batch_id` | string (partition) | From the folder name, not stored in the file. |
| `game_id` | string | `<batch_id>-<seed>-<game_number>`. Split on this, never on rows. |
| `snapshot_index` | int32 | Position in the file, from 0. |
| `seed` | int64 | From metadata. |
| `game_number` | int32 | From metadata. |
| `final_turn` | int32 | From metadata. |
| `seat1_deck` | string | From `seats`. |
| `seat1_ai_profile` | string | From `seats`. |
| `seat1_sim_mode` | string | From `seats`. |
| `seat1_search_budget` | int32 | From `seats`. |
| `seat2_deck` | string | From `seats`. |
| `seat2_ai_profile` | string | From `seats`. |
| `seat2_sim_mode` | string | From `seats`. |
| `seat2_search_budget` | int32 | From `seats`. |
| `side` | int8 | From the snapshot. |
| `won` | bool | True when `side` equals `winner`. |
| `me_life` | int32 | From the scoreboard, as are the columns below. |
| `opp_life` | int32 | |
| `me_poison` | int32 | |
| `opp_poison` | int32 | |
| `me_library` | int32 | |
| `opp_library` | int32 | |
| `opp_hand` | int32 | |
| `turn` | int32 | |
| `me_active` | bool | |
| `me_on_play` | bool | |

### cards table

One row per card per snapshot, long format.

| Column | Type | Meaning |
|---|---|---|
| `batch_id` | string (partition) | From the folder name, not stored in the file. |
| `game_id` | string | Joins to `snapshots`. |
| `snapshot_index` | int32 | Joins to `snapshots`. |
| `zone` | string | The zones object key this card was under. |
| `id` | int32 | The card fields as written, here and below. |
| `name` | string | |
| `face` | string | |
| `power` | int32 | |
| `toughness` | int32 | |
| `tapped` | bool | |
| `counters` | map<string, int32> | |
| `summoning_sick` | bool | |
| `attached_to` | int32 | |
| `attached_to_seat` | int8 | |
| `controller` | int8 | |
| `face_down` | bool | |

## Open questions for later tickets

- Each snapshot is already one side's row, so #31 should not expand it into two rows as its text says. Doing that would double the data and give each row the other side's hand.
- #31 joins on name and face, and it drops any snapshot with an unmatched card. A hidden card has no name, so #31 needs a rule for it, or it will drop most snapshots with a morph in play.
- Name and face can't tell tokens apart. 35 token scripts are all named `Elemental Token`, with different stats and abilities. `Card` has no direct accessor for the token script, so this version doesn't record one.
- A whole split card reports the combined name `Fire // Ice`, while the parser has faces `Fire` and `Ice`. The join has to allow for that. I read this in `CardRules`, and #18's first real files should confirm it.
