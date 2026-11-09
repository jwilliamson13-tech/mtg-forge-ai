# Game record

The game record is the only interface between Forge and Python. The Java hook (#18) writes it, `gamerecord` reads it, and neither side may depend on anything else about the other.

The current `schema_version` is 2.

Version 2 (2026-11-08) adds six scoreboard fields: `me_initiative`, `opp_initiative`, `me_monarch`, `opp_monarch`, `me_speed` and `opp_speed`. Nothing else changed. The loader still reads version 1 files, and fills those six in as false and 0, because a version 1 file doesn't record them. They can't be worked out from a version 1 file either: Forge keeps the initiative, the monarch and speed as effect objects in the command zone, which no zone here holds, so a version 1 file names them only while one of their triggers is on the stack. The monarch can change hands with no trigger at all, and speed starts at 1 without one, so carrying the last trigger forward would be wrong some of the time. `gamerecord convert` reports how many kept games came from version 1 files.

Any change to a field, a type or a rule here bumps the version once real files exist to protect, and this document, the hook and `gamerecord/record.py` change in the same step. The stack, combat and marked damage fields went in at version 1, because no game file existed yet. So did `honest_play`, opaque ids and the null `id` on log lines, because the only files by then were throwaway test batches. The loader refuses every other version, so a file from a mismatched build fails at load time. `tests/test_gamerecord_schema_doc.py` fails if this document and the validator name different fields, and `tests/test_gamerecord_real_game.py` runs the loader on a file the hook really wrote, which has to be replaced when the schema changes.

Sources for each field are Forge classes as read at commit `c72fcbfdcc0`. The hook is `GameRecorder` and `SnapshotLine` in the fork's `forge.view` package, and `-record` turns it on. See "Running batches" in `forge-build.md`.

## File

One gzip'd JSONL file per game, UTF-8, one JSON object per line.

- Line 1 is the metadata object.
- Every line after it is a snapshot, in game order. A file with no snapshots is invalid.

The name is `<batch_id>_<seed>_<game_number>_<deck1>_vs_<deck2>.jsonl.gz`. In a deck name, each run of characters outside `A-Za-z0-9` becomes one `-`, and the result is cut to 40 characters. The name exists for sharding. The loader takes identity from the metadata and never parses it.

The game number has to be in the name. Forge seeds one `java.util.Random` per JVM and plays all of a worker's `-n` games from that stream, so every game from a worker shares a seed and a deck pair. Without the number, each game would overwrite the last.

The hook buffers a game in memory and writes the file at game end. It writes under a temporary name that doesn't end in `.jsonl.gz` and renames the file once closed, so a killed worker can't leave a half-written game that looks finished.

Some games leave no file at all:

- A game that throws. So does a game where the hook itself fails, because a file with holes in it is worse than none.
- A game `SimulateMatch` has to force over with nothing to say why: the rules hadn't ended it, and no clock or cap had stopped it. Its only result would be the false winner below.
- A game with no snapshots, which the loader would reject. A clock stop before the first priority pass does this, because mulligans come first.

Every field in this document is written, null ones included. The hook has to serialize nulls, because Gson drops them by default.

## Metadata line

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | 2 for this version. The loader also reads 1. |
| `batch_id` | string matching `[A-Za-z0-9_-]+` | The batch this game belongs to. It names a folder in the Parquet output, which is why the characters are limited. |
| `seed` | integer, 0 to 2^63-1 | The `-s` value the worker's JVM got. A worker's games all share it. |
| `game_number` | integer, 1 or more | This game's place in the worker's run, the `n` of `Game Result: Game n`. |
| `seats` | array of two seat objects | Seat 1, then seat 2. |
| `winner` | integer 1 or 2, or null | The winning seat. Null for every end reason except `win`. |
| `final_turn` | integer, 1 or more | The turn the game ended on. A capped game has already begun the next turn when it stops, so a cap of 50 gives 51 here, and its last snapshot says 50. |
| `end_reason` | string, one of the end reasons | Why the game ended. |
| `honest_play` | boolean | Whether the AI played under `-honest on`, keeping to what a person in its seat could know. It is one setting for the run, so it covers both seats. See `peek-fixes.md`. |
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

With `-tripwire off`, the hook writes `{"enabled": false, "hits": 0, "signatures": []}`. "Tripwire" in `forge-build.md` says what counts as a hit.

| Field | Type | Meaning |
|---|---|---|
| `enabled` | boolean | Whether the tripwire ran this game. |
| `hits` | integer, 0 or more | How many hidden-zone reads it caught. |
| `signatures` | array of strings | The unique stack signatures behind those hits, sorted. |

`enabled` is there because 0 hits from a tripwire that was off says nothing. A batch is clean only when every game has `enabled` true and `hits` 0. With a `hybrid` or `full` seat the hits also count the search's copies, which `GameCopier` checks as it makes each one, under signatures that start `copy: `. The count of copies checked goes to stdout only, as `Tripwire copies: N checked`.

A signature is up to three `forge.ai` frames, nearest first, as `Class.method:line`, joined with ` < `. The first frame is the line that asked for the cards. A signature that starts with `no decider: ` is a forge-ai read that no controller call had opened. Line numbers belong to the jar, so compare signatures only within one build. The record has no count per signature, and the sim-runner gets those from Forge's stdout.

### End reasons

| Value | Meaning |
|---|---|
| `win` | A player won under the rules. `winner` names the seat. |
| `draw_clock` | The wall clock stopped the game. `winner` is null. |
| `draw_turn_cap` | The hook's turn cap stopped the game. `winner` is null. |
| `draw_rules` | The rules ended the game as a draw, such as both players reaching 0 life at once. `winner` is null. |
| `exception` | The game threw. The hook writes no file for such a game, so this value should never appear. The loader rejects it anyway. |

Never take `winner` from Forge's outcome on a stopped game. `SimulateMatch` stops a game with `setGameOver(GameEndReason.Draw)`, `Player.onGameOver` then marks everyone who hasn't lost as a winner, and `getOutcome()` names an arbitrary seat. The hook sets the end reason from what stopped the game, and writes no winner for it. It asks Forge for a winner only if the game was already over by the rules before `SimulateMatch` forced it.

## Snapshot lines

| Field | Type | Meaning |
|---|---|---|
| `side` | integer 1 or 2 | The seat this snapshot is written from. |
| `scoreboard` | scoreboard object | The rules-level numbers. |
| `zones` | zones object | Every card this side can see. |
| `stack` | array of stack items | The stack, top first, so in the order it resolves. `[]` when empty. |

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
| `me_initiative` | boolean | Whether this side has the initiative, `Game.getHasInitiative()`. From version 2. |
| `opp_initiative` | boolean | Whether the opponent has the initiative. At most one of the two is true. From version 2. |
| `me_monarch` | boolean | Whether this side is the monarch, `Game.getMonarch()`. From version 2. |
| `opp_monarch` | boolean | Whether the opponent is the monarch. At most one of the two is true. From version 2. |
| `me_speed` | integer, 0 to 4 | `Player.getSpeed()`. 0 means the player has no speed. From version 2. |
| `opp_speed` | integer, 0 to 4 | The opponent's speed. From version 2. |

### Zones

All seven are required, and an empty zone is `[]`. The opponent's hand and either library never appear as cards, only as counts.

| Field | Type | Meaning |
|---|---|---|
| `battlefield` | array of cards | Both players' permanents, in `id` order. |
| `me_hand` | array of cards | This side's hand. |
| `me_graveyard` | array of cards | This side's graveyard. |
| `opp_graveyard` | array of cards | The opponent's graveyard. |
| `me_exile` | array of cards | This side's exile zone. |
| `opp_exile` | array of cards | The opponent's exile zone. |
| `opp_revealed` | array of cards | This side's log of the opponent's revealed cards. See below. |

`battlefield` leaves out phased-out cards, which the rules treat as not there. It is in `id` order so that the two sides' lists don't line up, because one side's list can name what the other's hides.

### Revealed-card log

`opp_revealed` reads a log the hook keeps per side, per game, `forge.game.RevealLog`. It works the way a person with a notepad would. Forge's `AiCardMemory.REVEALED_CARDS` can't do this: it clears at the AI's own end step, and it holds own and library cards too.

- A card goes on the log the moment the opponent's hidden card is revealed to this side, from any zone. A card this side may look at without a reveal, such as a library top played face up, goes on at the next snapshot. A face-down card shown to this side counts as hidden too.
- It stays until this side can see it in a public zone. A shuffle, a return to the library or a move to a face-down exile changes nothing, because a person couldn't see that either, and dropping the card there would itself leak information.
- Own cards never go on the log.
- The line is what was written down, not the live card: `name`, `face`, `power` and `toughness` from the moment it was revealed, `controller` its owner, and every other field at its empty value. So `power` and `toughness` are the side's base values, and a logged card played face down can't be tied to its tapped, damaged or countered face-down self by matching state.
- A logged card has no id. `id` is null on these lines and nowhere else, because an id would tie the entry to its face-down self. The lines are in `name` order, not the log's own, which follows deck order.

Two limits:

- A card that goes hidden to public to hidden within one resolution, with no priority pass between, stays on the log. The fact was public by then, so nothing leaks.
- A player who has mind-slaved the opponent sees that hand, and the log doesn't record that.

The log is kept behind `Game.REVEAL_LOG`, which only `forge sim` sets, so default play has none. `knownTo(player)` is the one read, and #21's peek fixes use it too.

### Record ids

An `id` is opaque. It exists so that attachments, combat and targets can point at a card, and it is not a model input.

- It belongs to one side of one game. The two sides call the same card by different ids, and an id means nothing in another game. Join on `id` only inside `(game_id, side)`.
- A card gets a fresh id each time it changes zone, as the rules treat it as a new object. So a card has one id in hand, another on the stack and another on the battlefield, and a card that leaves the battlefield and comes back is a new card. Turning over, tapping and changing controller keep the id.
- `attached_to`, `attacking_card`, `blocking`, `blocked_by`, a stack item's `id` and a target's `card` all use the same side's ids.
- `id` is never `Card.getId()`. Forge numbers cards in deck order before the shuffle, so that number is the same card in every game of a batch. In the first batches with face-down cards, ids from the other 99 games named 121 of 121 face-down permanents.

The hook (`RecordIds` in `forge.view`) takes the id from a SHA-256 hash of `<seed>:<game_number>:<side>:<n>`, where `n` counts the ids this side has handed out, and keeps the first four bytes less the sign bit. If that id is taken, it moves on to the next `n`. The seed is in the metadata, so the card's own number can't go into the hash, or anyone could try every number. All anyone can get from an id is the order this side first saw things in. It draws nothing from `MyRandom`, and the same seed gives the same ids, so a replay's ids match.

`python -m gamerecord hiddencheck FOLDER` repeats both attacks on a folder of game files and exits 1 if either names a hidden card: an id with a name in another game, and an id the other side's rows put a name to. Both have to come to zero. On the 100 games of Abhorrent Oculus vs Bloodtithe Harvester, seed 35, Forge's ids named 121 of 121 both ways, and record ids name 0 of 121 both ways. The games are the same, with the same result digest.

Ids can't protect one thing. A file holds both sides, so the other side's row of the same moment shows a face-down card under its real name, and it can often be picked out by public state, such as being the only face-down card that player has. `hiddencheck` prints that count too, and it won't reach zero: on that batch it matches 106 of 121, the same with Forge's ids or record ids. The `id` order of `battlefield` stops position from doing the same job exactly, but a position can still hit the right card by chance. A training row has to come from one side and never be joined to the other side's rows.

### Card

| Field | Type | Meaning |
|---|---|---|
| `id` | integer, or null | The card's record id. See "Record ids". Null only on `opp_revealed`. |
| `name` | string, or null | `Card.getName()` in the card's current state. Null only for a hidden card. |
| `face` | string, one of the faces, or null | `Card.getCurrentStateName()`. Null only for a hidden card. |
| `power` | integer, or null | `Card.getNetPower()` if the card is a creature right now, else null. |
| `toughness` | integer, or null | `Card.getNetToughness()` if the card is a creature right now, else null. |
| `tapped` | boolean | `Card.isTapped()`. |
| `counters` | object, counter type to integer 1 or more | One entry per counter type on the card, `{}` for none. Keys are `CounterType.toString()`, such as `P1P1` or `LOYALTY`. `getName()` gives `+1/+1`, which is the wrong one. |
| `summoning_sick` | boolean | `Card.isSick()`, on the battlefield only. Forge's flag is true for a creature in hand. |
| `attached_to` | integer, or null | The record id of the card this is attached to. It must be on the battlefield in the same snapshot, and is null when the host is anywhere else. |
| `attached_to_seat` | integer 1 or 2, or null | The seat of the player this is attached to, for Curse auras and the like. |
| `controller` | integer 1 or 2 | The seat of `Card.getController()`. |
| `face_down` | boolean | `Card.isFaceDown()`. |
| `damage` | integer, 0 or more | `Card.getDamage()`, the damage marked on it this turn. |
| `attacking_seat` | integer 1 or 2, or null | The seat this card is attacking, from `Combat.getDefenderByAttacker`. |
| `attacking_card` | integer, or null | The record id of the planeswalker or battle this card is attacking. It must be on the battlefield in the same snapshot. |
| `blocking` | array of integers | The record id of each attacker this card blocks, sorted. All must be on the battlefield in the same snapshot. |
| `blocked_by` | array of integers | The record id of each creature blocking this card, sorted. All must be on the battlefield in the same snapshot. |

`Card.getEntityAttachedTo()` returns a `GameEntity`, which can be a card or a player. That is why attachment takes two fields. At most one of them is set. Attacking works the same way, and neither is set for a card that isn't attacking.

`blocked_by` is empty for an attacker whose blockers have all left combat, though the rules still call it blocked. The record doesn't say so.

A face-down card is hidden when `side` may not look at it, by `CardView.canFaceDownBeShownTo`, the rule Forge's UI uses. `Card.mayPlayerLook` is not enough: it hides a player's own morph from them. A hidden card has null `name` and null `face`, and keeps its public state: `face_down` true, its 2/2, its counters. This stops a snapshot from leaking what the opponent's morph is. When `side` may look, `name` and `face` give the card's up side, with `face` as its state once turned up, because `FaceDown` is not a legal value.

A face-down card outside the battlefield has null `power` and `toughness`. Forge reports the 2/2 of the face-down state for it, but a card face down in exile isn't a creature.

An empty Room's state has no name, so `name` gives the whole card's name and `face` is `EmptyRoom`.

### Stack item

| Field | Type | Meaning |
|---|---|---|
| `id` | integer | The source card's record id. A target that is a spell on the stack gives this. |
| `name` | string, or null | The source card's name, by the same rule as a card. Null only for a hidden source. |
| `face` | string, one of the faces, or null | The source card's face, by the same rule as a card. |
| `face_down` | boolean | Whether the source card is face down. A face-down spell is hidden from a side that may not look at it, exactly as a face-down card is. |
| `controller` | integer 1 or 2 | The seat of `SpellAbility.getActivatingPlayer()`. |
| `kind` | string, one of the stack kinds | What this item is. |
| `targets` | array of targets | Every target of the item and its sub-abilities, `[]` for none. |

### Stack kinds

| Value | Meaning |
|---|---|
| `spell` | A spell. |
| `ability` | An activated or triggered ability. |

### Target

| Field | Type | Meaning |
|---|---|---|
| `card` | integer, or null | The record id of a targeted card. For a targeted spell, its source card. It can be in any zone or on the stack, so it needn't appear in the snapshot's zones. |
| `seat` | integer 1 or 2, or null | The seat of a targeted player. |

Exactly one of the two is set.

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

## Replay digest

`gamerecord.game_digest(path)` returns a file's game number and a SHA-256 of the game: its metadata less `batch_id`, `honest_play` and `tripwire`, then every snapshot line as written. Those three fields are left out because they differ between two runs of the same games. The batch id is the run folder's name, the tripwire only watched, and leaving out `honest_play` is what lets a digest say honest play changed no game. `simrunner` hashes these per batch into the report's `record digest`, which is what a claim that two runs played the same games should rest on.

## What the loader checks

`gamerecord.load_game(path)` returns a `GameRecord` with `metadata`, `snapshots`, `game_id` and `honest_play`, or raises `GameRecordError`. Beyond the fields and types above, it enforces these rules:

- It checks `schema_version` first and refuses anything but 1 or 2. A version 1 file must not carry the version 2 scoreboard fields, and a version 2 file must carry all six.
- At most one player has the initiative, and at most one is the monarch.
- A field this document doesn't name is an error, the same as a missing field.
- An integer must be a JSON integer. `20.0` and `true` fail. Every integer except `seed` must fit Java's `int`.
- `exception` raises `ExceptionGameError`, a kind of `GameRecordError`.
- `winner` is set for `win` and null for every other end reason.
- `seats` is seat 1, then seat 2.
- A snapshot's `turn` can't pass `final_turn`.
- `name` and `face` are both set or both null, and null needs `face_down` true, on a card or a stack item.
- `id` is null on an `opp_revealed` card and set on every other card and stack item.
- `power` and `toughness` are both set or both null.
- A card attacks a seat or a card, not both, and the cards named by `attacking_card`, `blocking` and `blocked_by` are on the battlefield.
- A target is a card or a seat, not both and not neither.
- A tripwire that was off reports no hits and no signatures. Signatures are unique. `hits` is at least the number of signatures, and hits need at least one signature.

A truncated gzip, bad JSON or bad UTF-8 also raises `GameRecordError`.

## Parquet

```
python -m gamerecord convert FOLDER OUT
```

This reads every `*.jsonl.gz` under `FOLDER` and writes `OUT/snapshots/batch_id=<id>/part-0.parquet`, `OUT/cards/batch_id=<id>/part-0.parquet` and `OUT/stack/batch_id=<id>/part-0.parquet`. It keeps wins only, because a draw has no label. Draws stay in the raw files.

It prints what it kept and dropped by end reason, then each rejected file and why. An exception file counts as dropped. Any other file the loader refuses is rejected, and the rest of the folder still converts. The exit code is 1 if it rejected anything.

`--drop-tripwire-hits` also leaves out every game whose tripwire caught a hit, whatever its end reason, because the AI in it read hidden cards. Those count as dropped under `tripwire`, and the report names each game with its hits and signatures. The generation loop always passes it. See "Generation loop" in `forge-build.md`.

Converting again replaces each batch it finds a win for. To read a table:

```python
import pyarrow.dataset as ds

table = ds.dataset("OUT/snapshots", format="parquet", partitioning="hive").to_table()
```

`(game_id, snapshot_index)` joins the three tables. Each snapshot is one side's, so `id` only means something within that side's rows. A card-less snapshot has no rows in `cards`, and a snapshot with an empty stack has none in `stack`.

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
| `honest_play` | bool | From metadata. |
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
| `me_initiative` | bool | False for a version 1 file, as are the five below. |
| `opp_initiative` | bool | |
| `me_monarch` | bool | |
| `opp_monarch` | bool | |
| `me_speed` | int32 | 0 for a version 1 file. |
| `opp_speed` | int32 | |

### cards table

One row per card per snapshot, long format.

| Column | Type | Meaning |
|---|---|---|
| `batch_id` | string (partition) | From the folder name, not stored in the file. |
| `game_id` | string | Joins to `snapshots`. |
| `snapshot_index` | int32 | Joins to `snapshots`. |
| `zone` | string | The zones object key this card was under. |
| `id` | int32 | The card fields as written, here and below. Null for `opp_revealed`. |
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
| `damage` | int32 | |
| `attacking_seat` | int8 | |
| `attacking_card` | int32 | |
| `blocking` | list<item: int32> | |
| `blocked_by` | list<item: int32> | |

### stack table

One row per stack item per snapshot, long format.

| Column | Type | Meaning |
|---|---|---|
| `batch_id` | string (partition) | From the folder name, not stored in the file. |
| `game_id` | string | Joins to `snapshots`. |
| `snapshot_index` | int32 | Joins to `snapshots`. |
| `position` | int32 | Place on the stack, 0 for the top. |
| `id` | int32 | The stack item fields as written, here and below. |
| `name` | string | |
| `face` | string | |
| `face_down` | bool | |
| `controller` | int8 | |
| `kind` | string | |
| `targets` | list<item: struct<card: int32, seat: int8>> | |

## Open questions for later tickets

- Name and face can't tell tokens apart. 35 token scripts are all named `Elemental Token`, with different stats and abilities. `Card` has no direct accessor for the token script, so this version doesn't record one.
- A whole split card reports the combined name `Fire // Ice`, while the parser has faces `Fire` and `Ice`. The join has to allow for that. I read this in `CardRules`. #18's real batches had no split card, so it is still unconfirmed.
