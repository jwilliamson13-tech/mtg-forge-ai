# Deck pool

`deckpool` pulls real Legacy lists from MTGTop8, converts them to Forge deck files and splits them into training and held-out decks. Everything lands in `decks/`.

| Path | What it holds |
| --- | --- |
| `decks/raw/ID.txt` | The list as it was downloaded or typed |
| `decks/forge/ID.dck` | The Forge file made from it. `Name=` is the deck id. |
| `decks/pull.json` | The pull record: every list's archetype, own label, source, player and date, plus the lists it dropped and why |
| `decks/manifest.json` | Each deck's id, file, source, archetype and split, and the four tracked matchups |

A deck's `file` in the manifest starts with `decks/`, so tools read it from the folder above the manifest's own. The manifest can be named by a relative path from anywhere.

## Commands

```
python -m deckpool pull --cards data/cards.jsonl --cardsfolder ../forge/forge-gui/res/cardsfolder
python -m deckpool build-manifest --seed 20261106
python -m deckpool check
python -m deckpool add-deck LIST.txt --id ID --archetype NAME --split train
python -m simrunner batch ID1 ID2 --games 2 --seed 1 --records-dir runs/records
```

`pull` runs once. A second run needs `--again`, and it answers from the page cache in `runs/deckpool-cache`, so it only fetches pages it has never seen. It rewrites `pull.json` and the deck files, but it doesn't delete the files of a list that dropped out.

`build-manifest` refuses to run if `decks/manifest.json` exists, because the split is assigned once. Delete the file to rebuild it on purpose. `--leave-out ID ...` keeps decks that can't play out of the manifest, and an id that isn't in the pull fails the build. The command prints the linked groups and the highest overlap across the split.

`check` loads a manifest, runs its rules, and exits 1 if a near-copy crosses the split. Pass `--manifest PATH` for another file. It prints the same groups and overlap as `build-manifest`.

`add-deck` writes nothing unless the list passes. It refuses a main under 60 cards, an unknown card name, an id that isn't letters, digits, dashes and underscores, a split that disagrees with the archetype's other lists, and a list that would be a near-copy across the split.

## What the pull keeps

The pull takes the top 40 archetypes by share and up to three lists each, in the order the site lists them. It skips a list in three cases and moves to the next one on the page:

- Its own label names another archetype (`Bg Hogaak` on the Pox page).
- Its export doesn't convert, or has a main under 60 cards.
- Its main deck equals one already kept in that archetype. Two identical mains teach the model nothing about variation inside an archetype.

An archetype that runs out of lists keeps fewer, and `pull.json` lists it under `archetypes_short_of_three`.

## The near-copy rule

No held-out list may have a near-copy in training. Sharing an archetype name is not enough to stop that, because MTGTop8 files close relatives under different names. Before this rule, held-out `4-5c-control-2` shared 53 of its 60 main cards with train `bant-control-1`.

Overlap is the cards two mains have in common, counting copies, divided by the larger main. Two lists from different archetypes are near-copies at 0.70 or above, the `NEAR_COPY_THRESHOLD` in `deckpool/overlap.py`. A near-copy links the two archetypes. Links chain, so linked archetypes form a group and a group shares one split.

`build-manifest` shuffles the groups with the seed and holds out each one that still fits until it has 10 archetypes. It skips a group that would overshoot. If the groups can't add up to exactly 10, it fails.

Legacy decks share a blue core, so links could have chained into one giant group. They didn't. On the 2026-11-06 pool, 0.70 gives four groups and the largest has 4 of the 40 archetypes, so lands stay counted and the threshold stays where it started.

| Group | Linked by |
| --- | --- |
| 4/5c Control, Bant Control, Stoneblade, UWx Control | `bant-control-1` to all three 4/5c lists (up to 0.88), `uwx-control-1` to `stoneblade-3` (0.75) and `bant-control-1` (0.72) |
| Artifacts Prison, Mystic Forge, Painter | `mystic-forge-1` to `painter-1` (0.88) and `artifacts-prison-2` (0.75) |
| BUG Midrange, Cradle Control, Dimir Tempo, Maverick | `bug-midrange-2` to `cradle-control-1` (0.87), `cradle-control-1` to `maverick-3` (0.74), `bug-midrange-1` to `dimir-tempo-1` (0.72) |
| Boros Aggro, Mardu Aggro | `boros-aggro-1` to all three Mardu lists (0.70 to 0.72) |

`bug-midrange-2` plays like Cradle Control, with Gaea's Cradle, Green Sun's Zenith and Natural Order. It stays filed as BUG Midrange because that's its own label on the site and it does play blue (Tropical Island, Leovold). The link keeps it on the same side of the split as the Cradle lists either way.

## Reading a list

A line is a count and a name, with an optional set: `4 Lightning Bolt`, `4 [ZEN] Arid Mesa`, `1 Mountain (M21) 275`. Names match ignoring case, accents and punctuation. An unknown name fails the conversion, and the error lists every such name.

A `Sideboard` header, `// Sideboard` or an `SB:` prefix marks the sideboard, and then blank lines mean nothing. With no marker, one blank line splits main from sideboard, and two or more fail the parse, because the blocks could be card types. An Arena `Companion` block goes to the sideboard, once.

`A // B` stays whole for a split card and becomes `A` for a double-faced card. A back face or a split half alone (`Stomp`, `Insectile Aberration`, `Wear`) passes validation, and Forge loads the whole card from it. A game on 2026-11-06 with 15 copies of each played Bonecrusher Giant, Delver of Secrets and Wear // Tear, with all 60 cards in the deck. No deck in the pool uses such a name.

## Hand edits to the manifest

On 2026-11-06 Josh picked the tracked matchups and wanted a Tron deck in them. Cloudpost / Tron Ramp moved to held out and Ninja moved to train, so 10 archetypes stay held out. `build-manifest --seed 20261106` alone no longer gives this manifest.
