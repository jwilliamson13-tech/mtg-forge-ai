# Peek fixes

Issue #21. The tripwire in `docs/forge-build.md` finds forge-ai reading hidden cards. This file lists what each fixed site read, what it reads now, and what the AI does when that costs it information.

## The switch

`-honest on|off` on `forge sim` sets `Game.AI_HONEST_PLAY`. Forge's default is off, so a plain `forge sim` and every other game keep the stock AI and its peeks. `FlatMonteCarloNamedDiscardTest:90-109` pins one of those peeks, and it still passes.

`simrunner` passes `-honest on` whenever records are on, because records are training data. `--no-honest` leaves the flag out. Forge skips flags it doesn't know, so a stale jar would ignore `-honest`. With the tripwire on, that shows up as hits.

`-honest` works without `-record`. There is no reveal log then, so the AI goes by public cards alone. It can't go with `-t`. `GameCopier` copies the flag, so the AI thinks the same way on a copy. A copy has no reveal log, so what the AI was shown is missing from a search.

## What an AI may know

`forge.ai.HonestPlay` is the one place that says so. Every fix below reads through it.

- `knownCards(viewer, owner)` is the owner's public cards plus what is on the viewer's notepad, `Game.REVEAL_LOG.knownTo(viewer)`. Face-down cards the viewer can't turn over and tokens are left out.
- `rememberedIn(viewer, owner, zone)` is the notepad's cards last seen in that zone. `RevealLog.Entry.seenIn` holds that zone, and a card shown again updates it. The log can't say where a card is now, so nothing here asks.
- `handAsKnown(owner)` is for code that takes a player and can't say who is asking. It goes by `Game.AI_DECIDERS`: the real hand for the player deciding, and the remembered hand for anyone else. With no decision open it hands over the real hand, and the tripwire counts that as `no decider`, so a gap stays visible.
- `seenIn`, `seenInAnySeat` and `seenInOpponents` read a zone named by a card script. A public zone comes back whole. An opponent's hidden zone comes back as what was last seen there.

The stack of deciders moved from `HiddenReadWatch` to `Game.AI_DECIDERS`, because honest play needs it with the tripwire off. `-tripwire off -honest on` plays the same games as both on.

## The sites

Line numbers are from the commit before each fix.

### The five known sites

| Site | Read | Reads now | What the AI does now |
|---|---|---|---|
| Fog, `ComputerUtil.hasAFogEffect` | The defender's hand when `checkingOther` was false, and `REVEALED_CARDS` with `isInZone` | The hand only if the defender is the AI. Otherwise the notepad's cards seen in hand | Expects a Fog only if it saw one. Real games never reached this read, by the call sites and by 60 games of hits. The tests call it directly |
| Chalice X, `PermanentAi.checkApiLogic` | `getStrongestOpponent().getAllCards()`, the whole deck but the hand | `knownCards` | Needs at least 2 seen nonland cards at the value, and at least twice the share of its own nonland cards at that value. The stock rule compares two whole decks, and a seen count can't stand next to a deck's. With less seen it holds the Chalice |
| Card naming, `PlayerControllerAi.chooseCardName` | The opponent's library on entry for any `AILogic`, and with no logic the first nonland in every zone | `MostProminentInHumanDeck` reads `knownCards`, spells first because lands in play would always win the count. No logic reads `knownOpponentCards` | Names the spell it has seen the most of. With nothing seen it says Morphling, the stock last resort |
| Clash, `ClashAi.chooseSinglePlayer` | The opponent's top library card, compared on mana value | Nothing | Takes the first option, the stock answer when the AI can't see its own top card. With two players that is the only option. L67, the empty-library check, is a count |
| Fetch type, `ChangeZoneAi.doExilePreferenceLogic` | The opponents' libraries and hands, for `MostProminentOppType` | `knownOpponentCards` | Picks the type the opponent has shown. Cemetery Gatekeeper is the only script |

### Found by the tripwire

| Site | Read | Reads now | What the AI does now |
|---|---|---|---|
| `ComputerUtilAbility.getAvailableCards:78` | Every player's library top, on every spell choice. 99% of all hits | Own top card. An opponent's card only if it is on the notepad and the AI may play it | The same, except without a log it can't play an opponent's top card under an effect like that |
| `ComputerUtilMana.getAvailableManaSources:1647` | The payer's hand, for mana from cards like Simian Spirit Guide | `handAsKnown` | Doesn't expect mana from an unseen Spirit Guide, so a `Mana Leak` can come up short. A person makes the same mistake |
| `ComputerUtilMana.sortManaAbilities:163`, `sparingSourcesFirst:981`, the color choice at L1184 | The payer's hand, for the colors it holds | `handAsKnown` | Only changes a test of an opponent's payment |
| `ComputerUtil.getBestNearTermDiscardThreat:2455` | The discarder's hand, for a land drop | `handAsKnown` plus the cards the rules handed the chooser | `DiscardEffect` shows the whole hand first in its Reveal and Look modes, and the log has it. `PeekFixesTest.duressRevealsTheHandSoTheThreatCheckNeedsNoPeek` checks that on a real Duress |
| `ComputerUtil.getCardsToDiscardFromOpponent:2368` | The discarder's deck-list key cards. The tripwire can't see this | Nothing | Falls through to the non-key logic |
| `PlayerControllerAi.orderMoveToZoneList:665` | `getCardsInGame`, looking for Volrath's Shapeshifter | `allCardsKnownTo` | Reorders only for a Shapeshifter it knows about |
| `ChangeZoneAi.hiddenOriginCanPlayAI:338` | Each defined player's zones. Show and Tell read the opponent's hand | `seenIn`, and `holdsUnseen` says an unseen zone isn't empty | Treats a nonempty hidden zone as holding something, because only the count is public |
| `AiBlockController.wouldLikeToRandomlyTrade:1404` | A predicted blocker's real side, because the blocking seat could see it. Found by the face-down check | The decider has to be able to see it too | Values the opponent's morph as a 2/2 |

### Found by reading

The batches didn't reach these. I fixed them because they are the same read as a site above.

| Site | Read | Reads now |
|---|---|---|
| `NamedDiscard.knownInHand`, `chooseName`, `bestGuess` | The opponent's hand, library and a `DeckPlan` of their deck | The notepad's hand cards, then `knownCards` by copies and cost, with no deck plan. `consider` uses the same |
| `SpecialCardAi.PithingNeedle` | Deck-list key cards and `getAllCards` | Scoring over `knownCards`. With nothing worth naming it says Liliana of the Veil, the stock answer, where stock fell through to the AI's own cards |
| `ComputerUtilCard.chooseColor` | `getCardsInGame`, even when keeping only the AI's own cards | Own zones for the AI, `knownCards` for the opponent, `handAsKnown` for the active player's hand |
| `ComputerUtil.chooseSomeType:2574`, the vote logic at L2700, `PlayerControllerAi` color choice at L1405 | `getCardsInGame` filtered to the opponents | `knownOpponentCards` |
| `ComputerUtilCard.getBestLandAI:265` | The land controller's hand, for Urza lands | `handAsKnown` |
| `AiController.chooseNumber:2948`, Emissary's Ploy | The related player's library | `knownCards` if that player isn't the AI |
| `ChangeZoneAi` L118, L896, `ChangeZoneAllAi` L57, L290, L293, `ChooseCardAi`, `ManifestBaseAi`, `CloneAi`, `AiCostDecision` L414 | Script-named zones, which can be a hand or library | `seenIn` and friends |
| `ComputerUtilAbility.getAvailableLandsToPlay:62` | The type of any face-down exiled card | Skips the ones the AI can't turn over |
| `ChangeZoneAi.chooseCardToHiddenOriginChangeZone:1513` | Another player's deck-list key cards, and their hand | Nothing |
| `PlayerControllerAi.sideboardAgainstOpponents:140` | The opponent's whole main deck | Only what they played |
| `RearrangeTopOfLibraryAi.confirmAction:98` | The top of an opponent's library after arranging it. A person would know this card, but the log can't say which one is on top | Nothing. It leaves the order alone |

## Face-down identity

The tripwire now watches this too. `Card.getState`, `getOriginalState` and `getPaperCard` tell `HiddenReadWatch.faceDownRead` when someone asks for the real side of a face-down card. The same rules as for zones apply: only a forge-ai frame counts, and not for a card the decider may see by `canFaceDownBeShownTo`. A hit starts with `face-down: `. Off the sim path the cost is a check of the card's face-down flag.

It still can't see `Card.getStates()` or a `CardState` object the AI kept from before the card turned over.
