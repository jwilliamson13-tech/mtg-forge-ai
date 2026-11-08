# Peek fixes

Issue #21. The tripwire in `docs/forge-build.md` finds forge-ai reading hidden cards. This file lists what each fixed site read, what it reads now, and what the AI does when that costs it information.

## The switch

`-honest on|off` on `forge sim` sets `Game.AI_HONEST_PLAY`. Forge's default is off, so a plain `forge sim` and every other game keep the stock AI and its peeks. `FlatMonteCarloNamedDiscardTest:90-109` pins one of those peeks, and it still passes.

`simrunner` passes `-honest on` whenever records are on, because records are training data. `--no-honest` leaves the flag out. Forge skips flags it doesn't know, so a stale jar would ignore `-honest`. Each game record says `honest_play`, and the runner fails a batch that asked for honest play and has a file that doesn't show it.

`-honest` works without `-record`. There is no reveal log then, so the AI goes by public cards alone. It can't go with `-t`. `GameCopier` copies the flag, so the AI thinks the same way on a copy. A pruned copy also gets a reveal log of its own, holding what the searching seat had written down, so honest play on a copy goes by the same cards. A copy made with `-prune off` has none. See "Pruned copies" in `docs/forge-build.md`.

## What an AI may know

`forge.ai.HonestPlay` is the one place that says so. Every fix below reads through it.

- `knownCards(viewer, owner)` is the owner's public cards plus what is on the viewer's notepad, `Game.REVEAL_LOG.knownTo(viewer)`. Face-down cards the viewer can't turn over and tokens are left out.
- `rememberedIn(viewer, owner, zone)` is the notepad's cards last seen in that zone. `RevealLog.Entry.seenIn` holds that zone, and a card shown again updates it. The log can't say where a card is now, so nothing here asks.
- `handAsKnown(owner)` is for code that takes a player and can't say who is asking. It goes by `Game.AI_DECIDERS`: the real hand for the player deciding, and the remembered hand for anyone else. With no decision open it hands over the real hand, and the tripwire counts that as `no decider`, so a gap stays visible.
- `opponentCards(viewer)` is what a question about all the opponents' cards goes by: `knownOpponentCards` under honest play, every card they control otherwise.
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
| `ComputerUtilAbility.getAvailableCards:78` | Every player's library top, on every spell choice. 96.5% of all hits on Burn vs Merfolk | Own top card. An opponent's card only if it is on the notepad and the AI may play it | The same, except without a log it can't play an opponent's top card under an effect like that |
| `ComputerUtilMana.getAvailableManaSources:1647` | The payer's hand, for mana from cards like Simian Spirit Guide | `handAsKnown` | Doesn't expect mana from an unseen Spirit Guide, so a `Mana Leak` can come up short. A person makes the same mistake |
| `ComputerUtilMana.sortManaAbilities:163`, `sparingSourcesFirst:981`, the color choice at L1184 | The payer's hand, for the colors it holds | `handAsKnown` | Only changes a test of an opponent's payment |
| `ComputerUtil.getBestNearTermDiscardThreat:2455` | The discarder's hand, for a land drop | `handAsKnown` plus the cards the rules handed the chooser | `DiscardEffect` shows the whole hand first in its Reveal and Look modes, and the log has it. `PeekFixesTest.duressRevealsTheHandSoTheThreatCheckNeedsNoPeek` checks that on a real Duress |
| `ComputerUtil.getCardsToDiscardFromOpponent:2368` | The discarder's deck-list key cards. The tripwire can't see this | Nothing | Falls through to the non-key logic |
| `PlayerControllerAi.orderMoveToZoneList:665` | `getCardsInGame`, looking for Volrath's Shapeshifter | `allCardsKnownTo` | Reorders only for a Shapeshifter it knows about |
| `ChangeZoneAi.hiddenOriginCanPlayAI:338` | Each defined player's zones. Show and Tell read the opponent's hand | `seenIn`, and `holdsUnseen` says an unseen zone isn't empty | Treats a nonempty hidden zone as holding something, because only the count is public |
| `AiBlockController.wouldLikeToRandomlyTrade:1404` | A predicted blocker's real side, because the blocking seat could see it. Found by the face-down check | The decider has to be able to see it too | Values the opponent's morph as a 2/2 |
| `ComputerUtilCost.checkDiscardCost:148` | The payer's hand, copied before the cost parts were checked. A Ward check on the AI's own creature makes the opponent the payer. The gen-0 pool hit it once in 676 games, on Hexing Squelcher, whose Ward costs life, so the copy went unused | `handAsKnown` | Judges an opponent's discard cost by the cards it has seen in their hand. The pool has no Ward-discard card, so the games didn't change: chunk 12 replays to the same digests, record `5f20f414971a` and result `e31aa5f7cd06`, with the tripwire at 0 |

### Found by reading

The batches didn't reach these. I fixed them because they are the same read as a site above.

| Site | Read | Reads now |
|---|---|---|
| `NamedDiscard.knownInHand`, `chooseName`, `bestGuess` | The opponent's hand, library and a `DeckPlan` of their deck | The notepad's hand cards, then `knownCards` by copies and cost, with no deck plan. `consider` uses the same |
| `SpecialCardAi.PithingNeedle` | Deck-list key cards and `getAllCards` | Scoring over `knownCards`. With nothing worth naming it says Liliana of the Veil, the stock answer, where stock fell through to the AI's own cards |
| `ComputerUtilCard.chooseColor` | `getCardsInGame`, even when keeping only the AI's own cards | Own zones for the AI, `knownCards` for the opponent, `handAsKnown` for the active player's hand |
| `ComputerUtil.getCardPreference`, the `DiscardCost` madness check | The payer's `DeckPlan`, which reads their library. The `checkDiscardCost` test found it right behind the hand read | Only the deciding player gets its own deck plan. For an opponent the madness check is skipped |
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

## Results

Run 2026-11-05 with 2 workers, the overnight batch beside the later ones, `--clock 120`, heuristic seats, records and tripwire on, on a jar with every fix above. "Hits with `--no-honest`" is the same batch and seed with the flag off.

| Pair | Tests | Games | Hits | Hits per game with `--no-honest` | Digest same as `--no-honest` |
|---|---|---|---|---|---|
| Burn vs Merfolk (`GAL_`) | The ticket's batch | 200 | 0 | 288.93 | yes |
| Mono Blue Tempo vs Esper Control | Blue tricks | 100 | 0 | 525.59 | yes |
| Sneak Show vs Mono Black Devotion | Combo, Show and Tell, discard | 100 | 0 | 276.12 | yes |
| Mono Green Stompy vs Standard Elves | Creature combat, big boards | 100 | 0 | not run | see below |
| Abhorrent Oculus deck vs Bloodtithe Harvester | Face-down cards | 100 | 0 | 415.42 | yes |
| Dimir Faeries vs Liliana of the Veil | Flash, discard | 100 | 0 | 439.94 | yes |
| 11 more pairs, 16 games each | Naming cards, mill, tutors, stealing, 6 random pairs | 176 | 0 | 251.75 to 472.38, mean per pair | yes, all 11 |

Before the last fixes, the pairs turned up three more sites: `hiddenOriginCanPlayAI` on Sneak Show, 68 hits. `sparingSourcesFirst` on Stompy, 6737 hits. `wouldLikeToRandomlyTrade` on Oculus, 24 hits, once the face-down check was in.

Full sim, records on, clock 300:

| Run | Games | Hits |
|---|---|---|
| Burn full, Merfolk heuristic | 12 | 0 |
| Sneak Show full, Mono Black Devotion full | 8 | 0 |

That zero covered the real game only. At the time the search played on copies that held the opponent's real hand and library. #25 prunes those copies and has the tripwire check each one. "Pruned copies" in `docs/forge-build.md` has the numbers.

**The digests didn't move.** I expected them to differ, and they didn't. Every pair I compared has the same digest with `-honest` on and off, random draws included, while about 400 hits per game went to none. Stompy looked different at first, but its digests moved between runs because some decisions hit the AI's 5 second limit. With `--search-budget 100`, which removes that limit, 30 games matched at `a9403716463c`, with honest play on and with it off.

Those are result digests, which only hash how each game ended. The record digest hashes every snapshot, and it agrees too, on the three batches run with it. So honest play changed no card in these games:

| Batch | Games | Record digest, honest on | Honest off |
|---|---|---|---|
| Burn vs Merfolk, seed 31 | 200 | `9cabcac7dec4` | `9cabcac7dec4` |
| Abhorrent Oculus deck vs Bloodtithe Harvester, seed 35 | 100 | `205a3c56b412` | `205a3c56b412` |
| Burn full, Merfolk heuristic, seed 2, `--clock 300` | 6 | `82f5db378cf0` | `82f5db378cf0` |

The other pairs have result digests only.

My reading, which I didn't check site by site: 96.5% of the hits on Burn vs Merfolk, 55,784 of 57,786 with `--no-honest`, are `getAvailableCards` picking up a library top the AI can't play anyway, so most of what the tripwire caught was a read that changed no choice. The cases where honest play does change a choice are in `PeekFixesTest`, and they need cards these decks barely hold. None of the decks here has Chalice of the Void, Conjurer's Ban, Cabal Therapy, Fog, Volrath's Shapeshifter or Cemetery Gatekeeper. One has Pithing Needle, one Meddling Mage and one Simian Spirit Guide. A digest also can't see a different name on a card that changed nothing later. So these batches say nothing about how well the AI plays those sites now. They show the fixes are harmless on these decks. They don't show that Fog, Chalice or Clash play well, because no game here reached them.

Flag off, nothing moved. Digests match the ones in `docs/forge-build.md`:

| Run | Digest in the build notes | Now |
|---|---|---|
| No `-sim`, 20 games, seed 1 | `84c42eeea82f` | `84c42eeea82f` |
| Heuristic seats, 20 games, seed 1 | `6f2c4c7c1863` | `6f2c4c7c1863` |
| Same, records on, tripwire off, `--no-honest` | `6f2c4c7c1863` | `6f2c4c7c1863` |
| Same, records on, tripwire on, `--no-honest` | `6f2c4c7c1863` | `6f2c4c7c1863`, with hits in all 20 games |
| Burn full, 6 games, seed 2, `--no-honest` | `6d38e6c38b07` | `6d38e6c38b07`, with hits in all 6 games |

Run again on the jar with the review's fixes, where the tripwire no longer needs records, all five still match. So do the first two with the tripwire on and with `--no-tripwire`, without records. The two record runs of the third row share a record digest, `d60a45654629`, under different batch ids.

Those hits show the sites still peek with the flag off, so nothing here hid a read from the tripwire. Honest play gives the same digest with the tripwire on and off, `6f2c4c7c1863`.

The cost is small once the payment path stays off the hidden zones. Stompy vs Elves, 30 games, took 88.9 s stock and 103.4 s with honest play and the tripwire. An earlier version took 249.9 s, because `RevealLog.knownTo` scanned both hidden zones on every payment the AI tested, and with the tripwire each read walked the stack. `writtenDown` skips that scan and leaves it to the recorder's call at each priority pass, so a new standing look reaches the AI at the next priority pass, not mid-decision.

## Where the tripwire is silent

A zero says nothing about these. None of them counts as a hit, by design:

- `ComputerUtil.java:1579`, `Player.getCardsActivatableInExternalZones`, in Fog detection. See below.
- Anything read from a forge-game frame. Rules code running inside a decision gets the real list, so `sa.canPlay()` on an opponent's spell stays silent and the AI still learns the answer.
- `Card.getZone`, and the like, on a card the AI already holds.
- The order of the AI's own library, which story 10 exempts.
- `GameCopier`, which is exempt by class. No watch sits on a copy either. The copy check covers what a copy holds, not what code reads from it.

## What this doesn't cover

- A zero covers what these decks do. Fog, Chalice and Clash have Java tests and no real game. How well a person's guess plays there is untested.
- One AI asking the other seat's controller opens that seat as the decider, so anything read inside that call counts as its own. I saw no such read, but neither the tripwire nor honest play can tell.
- `canPayCost` for an opponent still runs the rules on their cards, such as `sa.canPlay()`. `Player.getCardsActivatableInExternalZones`, which Fog detection calls, filters real cards by what that player may play, and it is exempt from the tripwire by design. A card it returns can be a library top the AI hasn't seen.
- A card on the notepad is the object seen then. Asking a card for its zone, as `Card.getZone` does, is a read the tripwire can't see, and a site that does it can still learn where a card went.
- Stompy vs Elves goes past the 5 second AI limit: 28 deadline expiries in 100 games, with the overnight batch running beside it. Its digest doesn't replay without `--search-budget`.
