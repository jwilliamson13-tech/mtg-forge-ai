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
| `power`, `toughness` | int, `"<var>"` or null | The `PT:` line. A runtime value such as `*` is `"<var>"`. Null when the card has no `PT:` line. |
| `abilities` | list of Ability | One per ability line, in file order. |
| `warnings` | list of string | Lines the parser didn't recognize, such as `"unknown key: Frobnicate"`. A warning never stops the parse. |

### ManaCost

| Field | Type | Meaning |
|---|---|---|
| `generic` | int | Sum of the numeric symbols. `1 G` gives 1. |
| `pips` | object, color letter to count | One entry per colored symbol (`W U B R G`), in first-seen order. `1 U U U` gives `{"U": 3}`. |
| `special` | list of string | Every other symbol, verbatim and in order (hybrid, Phyrexian, `X`, snow, colorless `C`). |

### Ability

| Field | Type | Meaning |
|---|---|---|
| `kind` | string | `spell` (`SP$`) or `activated` (`AB$`) for `A:` lines. Later: `trigger`, `static`, `replacement`, `keyword`. |
| `tokens` | list of string | Categorical tokens, `Key=Value`, in script order. The first is always `Effect=<API effect>`. |
| `numbers` | list of KeyedNumber | Every segment whose value is an integer, in script order. |
| `subabilities` | list of Ability | Same shape, for inlined SVars. Empty for now. |

### KeyedNumber

| Field | Type | Meaning |
|---|---|---|
| `key` | string | The segment key, such as `NumDmg`. |
| `value` | int | The raw integer. No scaling, no log. Scaling happens at training time. |

## Errors

An ability segment without a `Key$` raises `cardscript.ParseError`. It carries `line` (the offending script line) and `cause` (a short reason, the same for every line that fails the same way, so the sweep can group failures by it).
