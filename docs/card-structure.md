# Parsed card structure

`cardscript.parse_card(text)` returns a `Card`. `Card.to_dict()` gives the same thing as plain JSON. This page describes that JSON so another emitter (the Java snapshot hook, later) can produce it without reading the Python.

The sweep over Forge's real scripts (`sweep.md`) turned up things the spec didn't spell out. Each is a small, deliberate addition, described in its section below: the `sub` kind for inlined bodies (Ability), resolving SVars only under listed reference keys (SVars), `SPECIALIZE` faces (Faces), variant and copied faces (Variants, Copied faces), die-roll results and the Gift and Tribute SVars (SVars), ` & ` lists (Filters), granted keywords (Granted keywords) and card names masked as `<name>` (Names).

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
| `kind` | string | `spell` (`SP$`) or `activated` (`AB$`, and `ST$` for the few abilities that don't use the stack, such as Circling Vultures' discard) for `A:` lines, `trigger` for `T:`, `static` for `S:`, `replacement` for `R:`, `keyword` for `K:`. An inlined SVar body adds `sub` (`DB$`); see SVars. The spec's kind list has no slot for a `DB$` body: it is neither cast nor activated by itself, it runs as part of the ability that names it, so it gets a kind of its own. |
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
| anything else (`Y`, an SVar name) | a plain value, by the same rules as any segment value | `Y` gives `Cost=<var>` |

Which fields are description is listed per cost part, by position, in `DROPPED_COST_FIELDS` (see Dropped). A part not listed keeps every field. Integer fields past the first get a positional key: `PutCardToLibFromGrave<1/-1/Instant>` gives `Cost.PutCardToLibFromGrave=1` and `Cost.PutCardToLibFromGrave.2=-1`.

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
3. English by position, or a flavor word: dropped. `DROPPED_KEYWORD_PARAMS` lists the positions per keyword (`Enchant` drops its second parameter, the filter's description); see Dropped.
4. Several cost atoms, or one `Name<...>` part: parsed as a cost under the parameter's key.
5. Anything else: a plain value. An integer is a number, anything else is split like a filter (see Filters).

`Protection from X` is read as `Protection` with parameter `X`, and `X` is always kept. Forge also writes protection as a filter, `K:Protection:Card.MultiColor:multicolored`, which gives `Protection=Card`, `Protection=MultiColor`. The two forms aren't unified: `Protection=red` and `Protection=Red` are different tokens, and the learned embeddings are left to place them close together.

### Granted keywords

A segment that grants, removes or tests keywords holds the same text as a `K:` line. `KEYWORD_KEYS` in `cardscript/parser.py` lists those keys: `AddKeyword`, `KW`, `Keywords`, `AddKeywords`, `AddKWs`, `PumpKeywords`, `AddHiddenKeyword`, `HiddenKeywords`, `RemoveKeyword`, `RemoveKeywords`, `CantHaveKeyword`, `ValidKeyword`, `AddsKeywords`, `SharedKeywords` and `KWChoice`. Each keyword in the value becomes the token `Key=Name`, and its parameters go through the same rules as on a `K:` line, keyed `Key.Name` (then `Key.Name.2`, ...):

| Segment | tokens | numbers |
|---|---|---|
| `AddKeyword$ Ward:1` | `AddKeyword=Ward` | `AddKeyword.Ward=1` |
| `AddKeyword$ Protection from red` | `AddKeyword=Protection`, `AddKeyword.Protection=red` | |
| `KW$ Landwalk:Forest & Haste` | `KW=Landwalk`, `KW.Landwalk=Forest`, `KW=Haste` | |
| `KWChoice$ Flying,Rampage:3` | `KWChoice=Flying`, `KWChoice=Rampage` | `KWChoice.Rampage=3` |
| `Keywords$ Enchant:Creature.IsRemembered:<text>` | `Keywords=Enchant`, `Keywords.Enchant=Creature`, `Keywords.Enchant=IsRemembered` | |
| `AddHiddenKeyword$ CARDNAME can't block.` | `AddHiddenKeyword=CARDNAME can't block.` | |

The segment key stays in front so a granted Ward 1 and the card's own `K:Ward:1` (number `Ward=1`) are told apart. Keywords in one value are joined by ` & `. `KWChoice` joins them with `,`, and so does a list of bare names (`Keywords$ Vigilance,Reach`); inside a keyword with parameters a comma belongs to the parameter (`Protection:Spell.Instant,Spell.Sorcery`). In a granted cost, `CardManaCost` (the card's own mana cost) is a cost atom like `T`: `KW$ Escape:CardManaCost ExileFromGrave<3/Card.Other/other>` parses as a cost. Gift and Tribute inline their SVar only on a `K:` line.

Known gap, sized by the 2026-10-16 sweep: keywords Forge writes as whole English sentences (`K:CARDNAME can't attack alone.`) become one `Effect=` token holding the sentence, 159 lines on 155 cards.

## Filters

A plain value (not a number, `<var>` or inlined reference) is split on `.`, `+`, `,` and ` & `. Each piece becomes its own token under the segment's key:

```
A:SP$ TapAll | ValidCards$ Creature.OppCtrl+tapped
```

gives `Effect=TapAll ValidCards=Creature ValidCards=OppCtrl ValidCards=tapped`.

Snapcaster's `ValidTgts$ Instant.YouCtrl,Sorcery.YouCtrl` gives four tokens, `YouCtrl` twice. The split doesn't interpret anything. Negation stays on its piece (`!token`), comparisons stay fused (`cmcLE4`, `powerGE3`), and pieces keep their inner spaces (`hasAbility Activated`). A card name inside a piece is masked (see Names). Empty pieces are skipped. Space-separated values are not split.

` & ` (with a space each side) is Forge's other list separator, used between whole items: `AddTypes$ Legendary & Creature & Human & Villain` gives four `AddTypes=` tokens, and `SacValid$ Creature.Artifact & Creature.nonArtifact` gives `Creature`, `Artifact`, `Creature`, `nonArtifact`. The spec named only `.`, `+` and `,`; ` & ` was added after the sweep found it in 30-odd keys. An `&` without spaces (`R&D`) stays inside its piece.

The same split applies to keyword parameters (`Enchant:Creature.YouCtrl`) and to the type field of a cost part (`Sac<1/Creature.Other>`). The effect token (`Effect=...`) is never split.

## Names

The model has to know a card by what it does, never by its name, so no card name reaches a token. Wherever a script names a card, the name becomes the value `<name>` and the key stays. `<name>` is an ordinary vocabulary token, not a reserved entry: `Name=<name>` and `ValidCards=named<name>` each get their own row like any other token.

| Where | Example | Tokens |
|---|---|---|
| a `named` or `!named` filter piece, under any key | `ChangeType$ Card.namedFestering Newt` | `ChangeType=Card`, `ChangeType=named<name>` |
| a key whose value is a name (`NAME_KEYS`) | `NewName$ Absorbing Man` | `NewName=<name>` |
| the same, holding a list | `Spellbook$ Amorphous Axe,Citizen's Crowbar,...` | `Spellbook=<name>`, one per card |
| a key that holds names under one effect (`EFFECT_NAME_KEYS`) | Meld's `Primary$`, `Secondary$`; MakeCard's `Choices$ Ionize,Electrolyze` | `Choices=<name>` twice |
| a `Names:` list, under any key | `AnySupportedCard$ Names:Seething Song,Flamebreak` | `AnySupportedCard=<name>` twice |
| a keyword whose parameters are names (`KEYWORD_NAME_PARAMS`) | `K:Partner with:Toothy, Imaginary Friend:Toothy` | `Effect=Partner with`, `Partner with=<name>`, `Partner with.2=<name>` |

`NAME_KEYS` is `Name`, `NewName`, `SetName`, `DefinedName`, `Names`, `Spellbook`, `ChooseFromList`, `FromDraftNotes` and `RoomName` (a dungeon room). All the lists live in `cardscript/parser.py`, measured over the 2026-10-16 sweep. `Secondary$ True` outside Meld and `Choices$` outside MakeCard stay as they were.

In a list, names are joined by `,` with no space after it. A name's own comma has a space (`Primary$ Gisela, the Broken Blade` is one name) or is written `;` (`Spellbook$ Adagia; Windswept Bastion,...`). A `named` piece runs to the next `.`, `+` or `,`, except that `, ` is the name's own comma: Grandeur's `Discard<1/Card.namedBaru, Fist of Krosa>` gives `Cost.Discard=Card`, `Cost.Discard=named<name>`.

A value under a name key that points at a card rather than naming one is kept: `Name$ ChosenName`, `DefinedName$ Targeted`, `DefinedName$ TriggeredCard`, and the other words in `NAME_REFERENCES`, plus `Valid<Zone> ...` selections.

The face's own `name` field is kept; it identifies the parsed card, and the vocabulary never reads it. Warnings keep names too (`copied face not resolved: Lightning Bolt`), and the vocabulary skips warnings.

Left as they are: words that are both a card name and a game term (`Vigilance`, `Exile`, `Plains`, `Delirium`, creature types such as `Atog`); Forge mechanics named after the card that introduced them, which are Forge's own vocabulary (`Effect=Fog`, `Effect=Balance`, the static mode `Panharmonicon`, the player keywords `AddKeyword=Shaman's Trance` and `AddKeyword=Piracy`); and two hidden-keyword sentences, `AddHiddenKeyword$ CARDNAME count as Muscle Burst.` and `... Flame Burst.`, which name the card they help count.

## Dropped

`cardscript/dropped.py` holds every allow-list of things dropped on purpose. Nothing is dropped by guessing from its wording.

`DROPPED_KEYS` drops by name. A name on it is dropped wherever it appears:

| Where | Example | Result |
|---|---|---|
| line key | `Oracle:`, `DeckHints:`, `AI:`, `Text:`, `AlternateMode:` | line skipped, no warning |
| segment key | `SpellDescription$`, `TgtPrompt$`, `AILogic$`, `IsCurse$` | no token, no number |
| SVar name | `SVar:NeedsToPlayVar:X LE12`, `SVar:AIPreference:SacCost$Creature` | never resolves; a value naming it stays a plain token |

It covers English (descriptions, prompts, titles, messages, Oracle and reminder text), Forge's AI hints (the `AI*` keys, `IsCurse`, the hint SVars) and deckbuilding lines. To drop another key, add one line to that file.

Forge also writes English inside keyword and cost parameters. Those are dropped by position, from two lists measured over the cardsfolder:

| List | Example | Result |
|---|---|---|
| `DROPPED_KEYWORD_PARAMS`: keyword name to positions (1 is the first parameter; `LAST` is the final one wherever it falls) | `K:Enchant:Creature.YouCtrl:creature you control` | `Enchant` drops position 2; `Enchant=Creature`, `Enchant=YouCtrl` stay |
| `FLAVOR_PREFIX`: a keyword parameter starting `Flavor ` | `K:Equip:2:Flavor Diana` | dropped at any position |
| `DROPPED_COST_FIELDS`: cost part to positions (1 is the amount) | `Sac<1/Creature.Other/another creature>` | `Sac` drops position 3 |

A parameter holding `Key$ Value` segments is parsed even at a listed position. Anything not listed is kept, whatever it looks like: `K:Partner:Friends forever` gives `Partner=Friends forever`, and Anavolver's `Card.Self+kicked 1` stays a filter. When a new card brings a new description slot, its text shows up as a rare token until a line is added to the list.

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

This is narrower than the spec's wording, "a value naming an SVar whose body begins with `DB$`", on purpose: inlining every matching value would turn `Destination=Exile` into a sub-ability. The cost is that a new reference key is missed until someone adds it, so the checkpoint sweep audits it: of 43,318 ability-shaped SVars in Forge's scripts, all but 7 are reached through a listed key (`sweep.md`, The checkpoint run).

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

In costs, `X` stays the mana symbol `Cost=X`. `Y`, `Z`, SVar names and every kept `<...>` field go through the same rules, so `Sac<Y/Creature>` gives `Cost.Sac=<var>` and Clay Golem's `RollDice<1/8/X>` gives `Cost.RollDice=<var>` with numbers `Cost.RollDice=1` and `Cost.RollDice.2=8`.

`<var>` and `<ref>` always show up as the value of a `Key=` token, so the model sees which quantity is runtime and which reference was cut. A printed `*` power or toughness is the bare string `"<var>"` (see Face).

## Errors and warnings

Only ability lines can fail. Anything else the parser doesn't know becomes a warning on its face and parsing goes on: `"unknown key: Frobnicate"` for an unknown line key, `"unrecognized line: ..."` for a line with no colon. Two warnings name a known gap instead: `copied face not resolved: <face>` (see Copied faces) and `unparsed variant line: <key>` (see Variants). In the 2026-10-16 sweep those are the only warnings, on 26 cards.

An ability segment without a `Key$`, an ability line whose first key doesn't fit its line (`T:` must start with `Mode$`, `R:` with `Event$`, `A:` with `SP$`, `AB$` or `ST$`), or an empty `K:` line raises `cardscript.ParseError`. A bad inlined body raises the same way, naming its `SVar:` line. The error carries `line` (the offending script line) and `cause` (a short reason, the same for every line that fails the same way, so the sweep can group failures by it). `str(error)` is `"<cause>: <line>"`. The causes are `segment without Key$`, `unknown ability API '<key>'` (one group per unexpected first key, such as `'Mode'` on an `A:` line), and `empty keyword`. The sweep groups any other exception under `unexpected <ExceptionType>`; see `sweep.md`.
