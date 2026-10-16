# Parsed card structure

`cardscript.parse_card(text)` returns a `Card`. `Card.to_dict()` gives the same thing as plain JSON. This page describes that JSON so another emitter (the Java snapshot hook, later) can produce it without reading the Python.

## Lightning Bolt

The script:

```
Name:Lightning Bolt
ManaCost:R
Types:Instant
A:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3 | SpellDescription$ CARDNAME deals 3 damage to any target.
Oracle:Lightning Bolt deals 3 damage to any target.
```

The parsed card:

```json
{
  "faces": [
    {
      "name": "Lightning Bolt",
      "mana_cost": {"generic": 0, "pips": {"R": 1}, "special": []},
      "types": ["Instant"],
      "colors": [],
      "power": null,
      "toughness": null,
      "abilities": [
        {
          "kind": "spell",
          "tokens": ["Effect=DealDamage", "ValidTgts=Any"],
          "numbers": [{"key": "NumDmg", "value": 3}],
          "subabilities": []
        }
      ],
      "warnings": []
    }
  ]
}
```

The `SpellDescription$` segment and the `Oracle:` line are gone. English text never becomes a token.

## Grim Lavamancer

```
A:AB$ DealDamage | Cost$ R T ExileFromGrave<2/Card> | ValidTgts$ Any | NumDmg$ 2 | SpellDescription$ ...
```

gives one `activated` ability:

```
tokens:  Effect=DealDamage Cost=R Cost=T Cost=ExileFromGrave Cost.ExileFromGrave=Card ValidTgts=Any
numbers: Cost.ExileFromGrave=2 NumDmg=2
```

The card has two 2s. They sit under two keys, so the model never sees one merged 2.

## Fields

### Card

| Field | Type | Meaning |
|---|---|---|
| `faces` | list of Face | One face per half of the card, in file order. A plain card has one. |

### Face

| Field | Type | Meaning |
|---|---|---|
| `name` | string | The `Name:` line. |
| `mana_cost` | ManaCost | The `ManaCost:` line. A card with no cost gets generic 0 and empty pips. |
| `types` | list of string | The `Types:` line split on spaces, in order: `Creature Bear` gives `["Creature", "Bear"]`. |
| `colors` | list of string | The `Colors:` line split on commas, in file order: `white,blue` gives `["white", "blue"]`. Mostly token scripts and back faces set it. Empty when the script has no `Colors:` line. |
| `power`, `toughness` | int, `"<var>"` or null | The `PT:` line. A runtime value such as `*` is `"<var>"`. Null when the card has no `PT:` line. |
| `abilities` | list of Ability | One per ability line, in file order. |
| `warnings` | list of string | Lines the parser didn't recognize, such as `"unknown key: Frobnicate"`. A warning never stops the parse. |

### ManaCost

| Field | Type | Meaning |
|---|---|---|
| `generic` | int | Sum of the numeric symbols. `1 G` gives 1. |
| `pips` | object, color letter to count | One entry per colored symbol (`W U B R G`), in first-seen order. `1 U U U` gives `{"U": 3}`. |
| `special` | list of string | Every other symbol, verbatim and in order: hybrid `RW`, two-brid `2R`, Phyrexian `BP`, hybrid Phyrexian `RWP`, `X`, snow `S`, colorless `C`. `1 BP BP` gives `["BP", "BP"]`. |

`ManaCost:no cost` (tokens, lands) gives generic 0, empty pips, empty special.

### Ability

| Field | Type | Meaning |
|---|---|---|
| `kind` | string | `spell` (`SP$`) or `activated` (`AB$`) for `A:` lines, `keyword` for `K:` lines. Later: `trigger`, `static`, `replacement`. |
| `tokens` | list of string | Categorical tokens, `Key=Value`, in script order. The first is always `Effect=<API effect>`, or `Effect=<keyword name>` for a keyword. |
| `numbers` | list of KeyedNumber | Every integer the line holds, in script order: plain segment values, cost amounts (see Costs), keyword parameters (see Keywords). |
| `subabilities` | list of Ability | Same shape, for inlined SVars. Empty for now. |

### KeyedNumber

| Field | Type | Meaning |
|---|---|---|
| `key` | string | Where the number came from: a segment key (`NumDmg`), a cost part (`Cost.ExileFromGrave`), or a keyword parameter (`Ward`, `Suspend.2`). |
| `value` | int | The raw integer. No scaling, no log. Scaling happens at training time. |

## Costs

Any segment whose key ends in `Cost` (`Cost$`, `UnlessCost$`, `AlternateCost$`, ...) is read as a cost: space-separated atoms, each handled by itself.

| Atom | Becomes | Example under `Cost$` |
|---|---|---|
| integer | a number under the cost key (generic mana) | `2` gives number `Cost=2` |
| mana symbol, `T`, `Q` | a token | `R` gives `Cost=R`; `U U` gives `Cost=U` twice |
| `Name<amount/type/description>` | token `Cost=Name`; amount as a number under `Cost.Name`; type as token `Cost.Name=type`; the description is dropped | `Sac<1/Creature.Other/another creature>` gives `Cost=Sac`, `Cost.Sac=Creature.Other`, number `Cost.Sac=1` |
| anything else (`XMin1`, an SVar name) | a plain value | `Y` gives `Cost=Y` |

A field inside `<...>` counts as description when it has a space or starts lowercase. Integer fields past the first get a positional key: `PutCardToLibFromGrave<1/-1/Instant>` gives `Cost.PutCardToLibFromGrave=1` and `Cost.PutCardToLibFromGrave.2=-1`.

## Keywords

Each `K:` line is one ability of kind `keyword`. The line is `K:Name[:param[:param...]]`. The name becomes `Effect=Name`. The first parameter is keyed by the name, later ones by `Name.<position>`, so two numbers on one keyword never share a key.

| Script line | tokens | numbers |
|---|---|---|
| `K:Flash` | `Effect=Flash` | |
| `K:Ward:2` | `Effect=Ward` | `Ward=2` |
| `K:Ward:PayLife<3>` | `Effect=Ward`, `Ward=PayLife` | `Ward.PayLife=3` |
| `K:Cycling:1 U` | `Effect=Cycling`, `Cycling=U` | `Cycling=1` |
| `K:Suspend:5:G` | `Effect=Suspend`, `Suspend.2=G` | `Suspend=5` |
| `K:Protection from red` | `Effect=Protection`, `Protection=red` | |
| `K:Enchant:Creature.YouCtrl:creature you control` | `Effect=Enchant`, `Enchant=Creature.YouCtrl` | |
| `K:Crew:1:ActivationLimit$ 1` | `Effect=Crew` | `Crew=1`, `ActivationLimit=1` |

Each parameter goes through the first rule that fits:

1. Empty: skipped. It still uses up its position.
2. Contains `$`: parsed as `|`-separated `Key$ Value` segments, like an ability line.
3. Several cost atoms, or one `Name<...>` part: parsed as a cost under the parameter's key.
4. Has a space or starts lowercase: English description, dropped.
5. Anything else: a plain value, a number if it's an integer and a token if not.

`Protection from X` is read as `Protection` with parameter `X`, and `X` is always kept.

Known gaps, left for the coverage sweep to size: keywords Forge writes as whole English sentences (`K:CARDNAME can't attack alone.`) become one `Effect=` token holding the sentence. A textual parameter with a space, such as a partner's card name, gets dropped as description.

## Errors

An ability segment without a `Key$`, or an empty `K:` line, raises `cardscript.ParseError`. It carries `line` (the offending script line) and `cause` (a short reason, the same for every line that fails the same way, so the sweep can group failures by it).
