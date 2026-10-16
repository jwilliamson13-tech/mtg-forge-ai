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
      "loyalty": null,
      "defense": null,
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
| `faces` | list of Face | One face per half of the card, in file order. A plain card has one. See Faces. |

### Face

| Field | Type | Meaning |
|---|---|---|
| `name` | string | The `Name:` line. |
| `mana_cost` | ManaCost | The `ManaCost:` line. A card with no cost gets generic 0 and empty pips. |
| `types` | list of string | The `Types:` line split on spaces, in order: `Creature Bear` gives `["Creature", "Bear"]`. |
| `colors` | list of string | The `Colors:` line split on commas, in file order: `white,blue` gives `["white", "blue"]`. Mostly token scripts and back faces set it. Empty when the script has no `Colors:` line. |
| `power`, `toughness` | int, `"<var>"` or null | The `PT:` line. A runtime value such as `*` is `"<var>"`. Null when the card has no `PT:` line. |
| `loyalty` | int, `"<var>"` or null | The `Loyalty:` line, read like one half of `PT:`. `Loyalty:X` is `"<var>"`. Null when absent. |
| `defense` | int, `"<var>"` or null | A battle's `Defense:` line, read the same way. Null when absent. |
| `abilities` | list of Ability | One per ability line, in file order. |
| `warnings` | list of string | Lines the parser didn't recognize, such as `"unknown key: Frobnicate"`. A warning never stops the parse. See Errors and warnings. |

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
| `kind` | string | `spell` (`SP$`) or `activated` (`AB$`, and `ST$` for the few abilities that don't use the stack, such as Circling Vultures' discard) for `A:` lines, `trigger` for `T:`, `static` for `S:`, `replacement` for `R:`, `keyword` for `K:`. An inlined SVar body adds `sub` (`DB$`); see SVars. |
| `tokens` | list of string | Categorical tokens, `Key=Value`, in script order. The first is always `Effect=<first segment's value>`: the API effect (`DealDamage`), the trigger or static mode (`ChangesZone`, `Continuous`), the replacement event (`GameLoss`), or the keyword name. |
| `numbers` | list of KeyedNumber | Every integer the line holds, in script order: plain segment values, cost amounts (see Costs), keyword parameters (see Keywords). |
| `subabilities` | list of Ability | Same shape, one per inlined SVar, in reference order. See SVars. |

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
| `Name<amount/type/description>` | token `Cost=Name`; amount as a number under `Cost.Name`; type split like a filter under `Cost.Name`; the description is dropped | `Sac<1/Creature.Other/another creature>` gives `Cost=Sac`, `Cost.Sac=Creature`, `Cost.Sac=Other`, number `Cost.Sac=1` |
| anything else (`XMin1`, an SVar name) | a plain value | `Y` gives `Cost=Y` |

A field inside `<...>` counts as description when it has a space or starts lowercase, unless it starts like a filter (see Keywords, rule 4). Integer fields past the first get a positional key: `PutCardToLibFromGrave<1/-1/Instant>` gives `Cost.PutCardToLibFromGrave=1` and `Cost.PutCardToLibFromGrave.2=-1`.

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
| `K:Enchant:Creature.YouCtrl:creature you control` | `Effect=Enchant`, `Enchant=Creature`, `Enchant=YouCtrl` | |
| `K:Crew:1:ActivationLimit$ 1` | `Effect=Crew` | `Crew=1`, `ActivationLimit=1` |

Each parameter goes through the first rule that fits:

1. Empty: skipped. It still uses up its position.
2. Contains `$`: parsed as `|`-separated `Key$ Value` segments, like an ability line.
3. Several cost atoms, or one `Name<...>` part: parsed as a cost under the parameter's key.
4. Has a space or starts lowercase: English description, dropped. A value that starts like a filter, a capitalized word glued to `.` or `+` (`Card.Self+kicked 1`), is kept and goes to rule 5.
5. Anything else: a plain value. An integer is a number, anything else is split like a filter (see Filters).

`Protection from X` is read as `Protection` with parameter `X`, and `X` is always kept.

Known gaps, left for the coverage sweep to size: keywords Forge writes as whole English sentences (`K:CARDNAME can't attack alone.`) become one `Effect=` token holding the sentence. A textual parameter with a space, such as a partner's card name, gets dropped as description.

## Filters

A plain value (not a number, `<var>` or inlined reference) is split on `.`, `+` and `,`. Each piece becomes its own token under the segment's key:

```
A:SP$ TapAll | ValidCards$ Creature.OppCtrl+tapped
```

gives `Effect=TapAll ValidCards=Creature ValidCards=OppCtrl ValidCards=tapped`.

Snapcaster's `ValidTgts$ Instant.YouCtrl,Sorcery.YouCtrl` gives four tokens, `YouCtrl` twice. The split doesn't interpret anything. Negation stays on its piece (`!token`), comparisons stay fused (`cmcLE4`, `powerGE3`), and names keep their spaces (`namedFestering Newt`). Empty pieces are skipped. `&` lists and space-separated values are not split: `KW$ Flying & First Strike` is one token.

The same split applies to keyword parameters (`Enchant:Creature.YouCtrl`) and to the type field of a cost part (`Sac<1/Creature.Other>`). The effect token (`Effect=...`) is never split.

## Dropped

`cardscript/dropped.py` holds one allow-list, `DROPPED_KEYS`. A name on it is dropped wherever it appears:

| Where | Example | Result |
|---|---|---|
| line key | `Oracle:`, `DeckHints:`, `AI:`, `Text:`, `AlternateMode:` | line skipped, no warning |
| segment key | `SpellDescription$`, `TgtPrompt$`, `AILogic$`, `IsCurse$` | no token, no number |
| SVar name | `SVar:NeedsToPlayVar:X LE12`, `SVar:AIPreference:SacCost$Creature` | never resolves; a value naming it stays a plain token |

It covers English (descriptions, prompts, titles, messages, Oracle and reminder text), Forge's AI hints (the `AI*` keys, `IsCurse`, the hint SVars) and deckbuilding lines. To drop another key, add one line to that file.

Lines starting with `#` are comments and are skipped.

## Faces

`ALTERNATE` on a line by itself starts a new face, and so does `SPECIALIZE:<COLOR>`. Each face is parsed on its own, with its own SVars. Fire // Ice:

```
Name:Fire
ManaCost:1 R
Types:Instant
A:SP$ DealDamage | ValidTgts$ Any | TgtPrompt$ ... | NumDmg$ 2 | TargetMin$ 1 | TargetMax$ 2 | DividedAsYouChoose$ 2 | SpellDescription$ ...
AlternateMode:Split
Oracle:...

ALTERNATE

Name:Ice
ManaCost:1 U
Types:Instant
A:SP$ Tap | ValidTgts$ Permanent | SubAbility$ DBDraw | SpellDescription$ ...
SVar:DBDraw:DB$ Draw
Oracle:...
```

gives two faces. Fire has cost 1 plus `R`, one spell with tokens `Effect=DealDamage ValidTgts=Any` and numbers `NumDmg=2 TargetMin=1 TargetMax=2 DividedAsYouChoose=2`. Ice has cost 1 plus `U`, one spell `Effect=Tap ValidTgts=Permanent` with one sub-ability `Effect=Draw`. An SVar defined on one face doesn't resolve on the other. A Specialize card has six faces.

`AlternateMode:` (Split, DoubleFaced, Adventure, ...) is dropped. The face list already carries the layout.

### Copied faces

A face that is only `CopyFaceFrom:<face name>` takes its rules from another script: Emeritus of Conflict's second face is `CopyFaceFrom:Lightning Bolt`. `parse_card` sees one script, so that face comes out empty (no name, no abilities) with the warning `copied face not resolved: Lightning Bolt`. A reader holding the whole dump can fill it in by face name. 23 cards do this in the 2026-10-16 sweep.

### Variants

`Variant:<id>:<Key>:...` lines describe alternate printings of one card. When `<Key>` is on the drop list the line leaves no trace: flavor names (`Variant:UniversesWithin:FlavorName:Rhilex the Accursed`, 189 lines), Attraction lights (`Variant:A:Lights:2 6`) and their `Oracle`. Any other inner key (`A`, `T`, `SVar`, `PT`, ...) gives the warning `unparsed variant line: <Key>`. Only three Unstable cards do this (Everythingamajig, Garbage Elemental, Sly Spy); their printings have different rules, so no one printing stands for the card.

## SVars

`SVar:Name:Body` lines never become abilities on their own. A value that names one pulls the body in. An SVar nobody names (`PlayMain1`, `NeedsToPlayVar`, most AI hints) leaves no trace. Hint SVars on the drop list never resolve even when named (see Dropped). SVars are scoped to a face, and an SVar used above its definition still resolves. A name defined twice keeps the last body, as Forge does.

### Inlining, one level

Snapcaster Mage:

```
T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ TrigFlashback | ...
SVar:TrigFlashback:DB$ Pump | ValidTgts$ ... | TgtZone$ Graveyard | KW$ Flashback | PumpZone$ Graveyard
```

The trigger has no `Execute=` token. Its `subabilities` holds one ability of kind `sub` with tokens `Effect=Pump ... KW=Flashback ...`.

Only values under a reference key resolve: `Execute`, `SubAbility`, `ReplaceWith`, `Choices`, the other `*SubAbility` keys, and the keys that grant abilities (`Triggers`, `StaticAbilities`, `ReplacementEffects`, `AddAbility`, `AddTrigger`, ...). The full list is `REFERENCE_KEYS` in `cardscript/parser.py`. Any other value that happens to match an SVar name stays a plain token. `Destination$ Exile` on a card that also defines `SVar:Exile` is still `Destination=Exile`.

Keyword parameters resolve for the keywords in `KEYWORD_REFERENCES`. `K:Chapter:2:DBA,DBB` gives number `Chapter=2` and two sub-abilities.

Two keywords name no SVar at all: Forge fetches a fixed name when it builds them. `K:Gift` inlines `SVar:GiftAbility` and `K:Tribute:3` inlines `SVar:TrigNotTribute`, each as a sub-ability of the keyword (`KEYWORD_SVARS` in `cardscript/parser.py`).

A value can list several SVars, joined by `,` or ` & `. Each becomes its own sub-ability, but only when every item names an ability SVar. `Choices$ DBA,Creature` stays a plain value, split into `Choices=DBA` and `Choices=Creature`. Cryptic Command's `Choices$ DBCounter,DBReturn,DBTapAll,DBDraw | CharmNum$ 2` gives four sub-abilities and number `CharmNum=2`.

A die roll's `ResultSubAbilities$` lists `result:Name` items. Each name inlines like any reference, and its result goes on that sub-ability: a range `1-14` as numbers `ResultSubAbilities.min=1` and `ResultSubAbilities.max=14` (a single `6` gives 6 and 6), a word such as `Else` as the token `ResultSubAbilities=Else`. Berserker's Frenzy's `1-14:MustBlock,15-20:ChooseBlock` gives two sub-abilities. Past the depth limit each item is `ResultSubAbilities=<ref>` and the ranges are gone.

The sub-ability's kind comes from its body's first key:

| Body starts with | kind |
|---|---|
| `DB$` | `sub` |
| `AB$`, `ST$` | `activated` |
| `SP$` | `spell` |
| `Event$` | `replacement` |
| `Mode$` | `static` under `StaticAbilities`, `AddStaticAbility` and the other static keys; `trigger` otherwise |

Depth is fixed at 1. A top-level ability inlines what it names. An inlined body does not: each reference inside it becomes the token `Key=<ref>`, one per named SVar. Thassa's Oracle shows the cutoff:

```
T:Mode$ ChangesZone | ... | Execute$ TrigDig
SVar:TrigDig:DB$ Dig | DigNum$ X | ChangeNum$ 1 | ... | SubAbility$ DBWin
SVar:DBWin:DB$ WinsGame | ...
```

The trigger's one sub-ability is the `Dig`, and its tokens include `SubAbility=<ref>`. `WinsGame` appears nowhere. About 13% of Forge's abilities chain deeper than one level, so `<ref>` is common. The cap also means a self-referencing SVar can't loop.

### `<var>`: runtime quantities

A value becomes `Key=<var>`, with no number, when it is:

- bare `X`, `Y` or `Z`, with or without an SVar behind it;
- an inline expression such as `TriggerCount$Amount`;
- the name of an SVar whose body is an expression: `Count$...`, `Remembered$Amount`, `SVar$Z1/Minus.Z2`, `Number$0` and the rest of the `Word$...` family. `Number$N` counts too, because Forge mostly uses it as a mutable counter.

This holds under any key, not just the reference keys. Across Forge's cardsfolder, every key that names an expression SVar holds a quantity, so there's no false hit to guard against.

A leading sign is kept as a second token, `Key.sign=-` or `Key.sign=+`. Death's Shadow:

```
S:Mode$ Continuous | AffectedDefined$ Self | AddPower$ -X | AddToughness$ -X
SVar:X:Count$YourLifeTotal
```

gives tokens `Effect=Continuous AffectedDefined=Self AddPower=<var> AddPower.sign=- AddToughness=<var> AddToughness.sign=-` and no numbers.

In costs, `X` stays the mana symbol `Cost=X`. `Y`, `Z`, SVar names and `<...>` amounts go through the same rules, so `Sac<Y/Creature>` gives `Cost.Sac=<var>`.

`<var>` and `<ref>` always show up as the value of a `Key=` token, so the model sees which quantity is runtime and which reference was cut. A printed `*` power or toughness is the bare string `"<var>"` (see Face).

## Errors and warnings

Only ability lines can fail. Anything else the parser doesn't know becomes a warning on its face and parsing goes on: `"unknown key: Frobnicate"` for an unknown line key, `"unrecognized line: ..."` for a line with no colon. In Forge's current card folder the warnings are `Loyalty`, `Variant`, `Defense` and `CopyFaceFrom` lines.

An ability segment without a `Key$`, an ability line whose first key doesn't fit its line (`T:` must start with `Mode$`, `R:` with `Event$`, `A:` with `SP$`, `AB$` or `ST$`), or an empty `K:` line raises `cardscript.ParseError`. A bad inlined body raises the same way, naming its `SVar:` line. The error carries `line` (the offending script line) and `cause` (a short reason, the same for every line that fails the same way, so the sweep can group failures by it). `str(error)` is `"<cause>: <line>"`. The causes are `segment without Key$`, `unknown ability API '<key>'` (one group per unexpected first key, such as `'Mode'` on an `A:` line), and `empty keyword`. The sweep groups any other exception under `unexpected <ExceptionType>`; see `sweep.md`.
