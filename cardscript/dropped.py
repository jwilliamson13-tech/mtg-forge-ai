"""Everything the parser drops on purpose, in one place.

Three allow-lists: `DROPPED_KEYS` by name, and `DROPPED_KEYWORD_PARAMS` and
`DROPPED_COST_FIELDS` by position, for the English that Forge writes inside
keyword and cost parameters. Nothing is dropped by guessing at its wording.

`DROPPED_KEYS` covers three kinds of name, because they never collide:

- line keys (`Oracle:`, `DeckHints:`): the line is skipped with no warning;
- segment keys (`SpellDescription$`, `AILogic$`): no token, no number;
- SVar names (`SVar:NeedsToPlayVar:...`): the SVar never resolves, so a value
  naming it stays a plain token instead of turning into `<var>` or a
  sub-ability.

Names come from the grammar survey of Forge's cardsfolder (text-bearing keys,
AI keys, hint SVars). To drop another key, add one line.
"""

DROPPED_KEYS = frozenset(
    {
        # English: the card's rules text, reminder and draft text.
        "Oracle",
        "Text",
        "Draft",
        # English inside abilities: descriptions, prompts, titles, messages.
        "SpellDescription",
        "TriggerDescription",
        "Description",
        "StackDescription",
        "TgtPrompt",
        "ChoiceTitle",
        "ChoiceTitleAppend",
        "ValidTgtsDesc",
        "ValidDescription",
        "ValidCardsDesc",
        "SelectPrompt",
        "CostDesc",
        "PrecostDesc",
        "ChangeTypeDesc",
        "ChangeValidDesc",
        "ChangeNumDesc",
        "AdditionalDescription",
        "AdditionalDesc",
        "ConditionDescription",
        "GiftDescription",
        "DefinedDesc",
        "DiscardValidDesc",
        "NumCardsDesc",
        "AmountDesc",
        "DamageDesc",
        "AtEOTDesc",
        "ChoiceDesc",
        "ChoicesDesc",
        "FromDesc",
        "NoPutDesc",
        "IfDesc",
        "ListTitle",
        "AnnounceTitle",
        "XAnnounceTitle",
        "RevealTitle",
        "SacMessage",
        "RepeatOptionalMessage",
        "VoteMessage",
        "ValidMessage",
        "LogMessage",
        "DiscardMessage",
        "ShowMessage",
        "OptionQuestion",
        "ChoicePrompt",
        "PrimaryPrompt",
        "OptionPrompt",
        "OptionalPrompt",
        "OptionalAbilityPrompt",
        "PromptToSkipOptionalAbility",
        "LockInText",
        "MayPlayText",
        "OrString",
        # Forge AI hints inside abilities. IsCurse only steers AI targeting.
        "AILogic",
        "AiChoiceLogic",
        "AITgts",
        "AITgtsStrict",
        "AITgtOwnCards",
        "AINoRecursiveCheck",
        "AIManaPref",
        "AILifeThreshold",
        "AIPhyrexianPayment",
        "AIExpectAmount",
        "AIMinTgts",
        "AIXMax",
        "AIBidMax",
        "AICheckSVar",
        "AISVarCompare",
        "AIRespondsToOwnAbility",
        "AIActivateLast",
        "AIPreference",
        "UnlessAI",
        "IsCurse",
        # Deckbuilding and AI lines.
        "AI",
        "DeckHas",
        "DeckHints",
        "DeckNeeds",
        "DeckRule",
        # Layout and printing metadata. The face list already shows the layout.
        "AlternateMode",
        "MeldPair",
        "SETCOLORID",
        "Lights",
        "FlavorName",  # inside `Variant:UniversesWithin:FlavorName:...`
        "HandLifeModifier",
        # AI hint SVars (`SVar:PlayMain1:TRUE`).
        "PlayMain1",
        "PlayBeforeLandDrop",
        "NeedsToPlay",
        "NeedsToPlayVar",
        "NeedsToPlayKicked",
        "NeedsToPlayKickedVar",
        "NeedsToPlayEvoked",
        "NeedsToPlayEvokedVar",
        "NeedsOrderedGraveyard",
        "AttachAILogic",
        "AttachAITgts",
        "AttachAIValid",
        "AttachAi",
        "BuffedBy",
        "AntiBuffedBy",
        "HasAttackEffect",
        "HasCombatEffect",
        "HasBlockEffect",
        "NonStackingEffect",
        "NonStackingAttachEffect",
        "NonCombatPriority",
        "SacMe",
        "DiscardMe",
        "DiscardMeByOpp",
        "DonateMe",
        "UntapMe",
        "EquipMe",
        "EnchantMe",
        "NoZeroToughnessAI",
        "MustBeBlocked",
        "EndOfTurnLeavePlay",
        "SacrificeEndCombat",
        "ManaNeededToAvoidNegativeEffect",
        "AmbushAI",
        "AgendaLogic",
        "TributeAILogic",
        "FreeSpellAI",
        "IsReanimatorCard",
        "MaxQuestEffect",
        "PreferredHandSize",
        "AIPriorityModifier",
        "AICastPreference",
        "AILandRemovalMinScore",
        "AICounterOverrideQUEST",
        "AICounterOverrideDEPLETION",
        "AIUntapPreference",
        "AIGraftPreference",
        "AICurseEffect",
        "AIPreferenceParams",
        "AIPaymentPreference",
        "AIRemoveCounterCostPriority",
        "AIEvaluationModifier",
        "AIRollPlanarDieParams",
        "AI_SpellsNeedExtraMana",
    }
)


# Keyword parameters that are English (reminder text, a description of the
# filter before it, an extra cost's wording), by keyword name: the positions
# dropped, 1 being the first parameter after the name. `LAST` drops the final
# parameter wherever it falls (`K:Companion:Card.cmcM20:<text>` and
# `K:Companion:Special:UniqueNames:<text>`). A parameter holding `Key$ Value`
# segments is parsed even at a listed position: `K:Equip:3:::ReduceCost$ Y:<text>`
# keeps `ReduceCost`. Any other parameter is kept.
#
# Measured over Forge's cardsfolder (commit 64569999): every parameter at a
# listed position is English, and no other position holds English. To drop
# another one, add its keyword and position.
LAST = -1
DROPPED_KEYWORD_PARAMS = {
    "Affinity": {2},
    "Bands with other": {2},
    "Casualty": {3},
    "Companion": {LAST},
    "Craft": {2, 3},
    "Cumulative upkeep": {2},
    "DeckLimit": {2},
    "Devour": {3},
    "Disguise": {3},
    "Enchant": {2},
    "Equip": {3, 4, 5, 6},
    "etbCounter": {3, 4},
    "Firebending": {2},
    "Flashback": {3},
    "Hexproof": {2},
    "Landwalk": {2},
    "MustBeBlockedByAll": {2},
    "Protection": {2},
    "Specialize": {2, 3},
    "TypeCycling": {3},
}

# A keyword parameter starting with this is a flavor word for the ability
# (`K:Equip:2:Flavor Diana`, printed "Diana - Equip {2}"), dropped at any
# position.
FLAVOR_PREFIX = "Flavor "

# Cost parts with an English description field
# (`Sac<1/Creature.Other/another creature>`): the positions dropped, 1 being
# the amount. A description with a `/` in it (`artifacts and/or creatures`)
# spills into the next field, so that position is listed too. Every other
# field is kept: `SubCounter<1/TIME/.../<text>/Battlefield,Exile>` keeps its
# zones, `PutCardToLibFromGrave<1/-1/Instant>` keeps all three. Measured over
# Forge's cardsfolder (commit 64569999).
DROPPED_COST_FIELDS = {
    # Name<amount/type/description>
    "Behold": {3},
    "Discard": {3},
    "Exert": {3},
    "Exile": {3, 4},
    "ExileCtrlOrGrave": {3},
    "ExileFromGrave": {3, 4},
    "ExileFromHand": {3},
    "ExileFromStack": {3},
    "ExiledMoveToGrave": {3},
    "GainControl": {3},
    "Return": {3},
    "Reveal": {3},
    "Sac": {3, 4},
    "tapXType": {3, 4},
    "untapYType": {3},
    # Name<amount/counter type/type/description>
    "AddCounter": {4},
    "RemoveAnyCounter": {4},
    "SubCounter": {4},
    # Name<amount or type/description>
    "PayLife": {2},
    "RevealChosen": {2},
    "Unattach": {2},
}
