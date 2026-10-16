# Vocabulary

The encoder gives every categorical token a learned embedding and every numeric key a learned embedding that it scales by the value. `cardscript.vocab` decides which tokens and keys get a row, and at what index. It reads the parsed structure in `card-structure.md`.

```python
from cardscript.vocab import Vocabulary, build_vocabulary

vocab = build_vocabulary(cards, min_count=5)   # Card objects or their to_dict() form
vocab.save("vocab.json")
vocab = Vocabulary.load("vocab.json")

vocab.index("Effect=DealDamage")   # token row
vocab.key_index("NumDmg")          # numeric key row
```

`cards` can be any iterable, so a JSONL dump streams straight in: `build_vocabulary(json.loads(line) for line in f)`.

## Grim Lavamancer

The face gives these tokens:

```
type=Creature type=Human type=Wizard
kind=activated Effect=DealDamage Cost=R Cost=T Cost=ExileFromGrave Cost.ExileFromGrave=Card ValidTgts=Any
```

and these numbers:

```
generic=0 pip.R=1 power=1 toughness=1 Cost.ExileFromGrave=2 NumDmg=2
```

`Name` is not in either list. The model never sees a card's name: the face's `name` field is skipped, and a name inside an ability is already `<name>` in the parsed tokens (`card-structure.md`, Names). `Name=<name>`, `Spellbook=<name>` and `ValidCards=named<name>` are ordinary tokens with their own rows.

## What counts

`face_tokens(face)` and `face_numbers(face)` produce the two lists. The builder calls them, and the encoder should call them too, so the two can't disagree about token names.

Ability tokens are the `tokens` lists from the parsed structure, unchanged, for every ability and every inlined sub-ability. Each ability also gets one `kind=<kind>` token. The face's identity fields become tokens and numbers under lowercase keys:

| Field | Tokens | Numbers |
|---|---|---|
| `types` | `type=Creature`, one per type | |
| `colors` | `color=red`, one per color | |
| `mana_cost.generic` | | `generic`, always present, 0 included |
| `mana_cost.pips` | | `pip.U` with the pip count: `1 U U U` gives `pip.U=3` |
| `mana_cost.special` | `pip=BP`, one per symbol | |
| `power`, `toughness` | `power=<var>` for a `*` value | `power`, `toughness` for an integer |
| `loyalty`, `defense` | `loyalty=<var>` for `X` | `loyalty`, `defense` for an integer |
| ability `kind` | `kind=trigger`, one per ability and sub-ability | |
| ability `tokens` | as parsed | |
| ability `numbers` | | as parsed |

Forge's keys are capitalized, so an identity token doesn't collide with an ability token. `Type$ Creature` in an ability stays `Type=Creature`; the face's type is `type=Creature`. A sweep of the cardsfolder on 2026-10-16 found four lowercase Forge keys (`etbCounter`, `staticAbilities`, `sVars`, `cmcGT`), none of them a name used here.

Names and warnings are skipped.

## Cutoff

A token or key is kept when it appears on at least `min_count` cards. Each card counts once, however often it repeats the token: Dismember's two `pip=BP` count as one. The default `min_count=1` keeps everything seen.

Kept tokens come after the reserved ones, most frequent first, ties in string order. The same cards and cutoff always give the same file.

## Reserved markers

`tokens[0:3]` is always `<unk>`, `<var>`, `<ref>`, even for an empty corpus or a cutoff nothing passes. `numeric_keys[0]` is always `<unk>`. `Vocabulary.load` refuses a file that breaks this.

The parser never emits a bare `<var>` or `<ref>`. Both appear as the value of a keyed token, such as `AddPower=<var>` or `SubAbility=<ref>`, and a `*` power becomes `power=<var>`. Those keyed tokens are ordinary entries: a common one, such as `SubAbility=<ref>`, gets its own row. That row tells the model which quantity is runtime or which reference was cut.

The reserved rows are the fallbacks. `vocab.index(token)` resolves in this order:

1. The token has its own row: that row.
2. It ends in `=<var>`: the `<var>` row (1).
3. It ends in `=<ref>`: the `<ref>` row (2).
4. Anything else: the `<unk>` row (0).

So a rare `AddLoyalty=<var>` still reads as "runtime value" and not as "unknown". The sign token beside it (`AddPower.sign=-`) is an ordinary token and falls back to `<unk>` when rare. The encoder can rely on rows 0, 1 and 2 whatever corpus built the file.

`vocab.key_index(key)` returns the key's row, or 0 for a key below the cutoff. The number still multiplies the `<unk>` key row.

## File format

`save` writes JSON:

```json
{
 "format": 1,
 "min_count": 5,
 "tokens": ["<unk>", "<var>", "<ref>", "Effect=DealDamage", "..."],
 "numeric_keys": ["<unk>", "generic", "power", "..."]
}
```

An entry's index is its position in its list.
