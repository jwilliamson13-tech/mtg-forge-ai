"""Build training rows from the Parquet tables and the parsed card dump.

A row is one snapshot, exactly as the game file holds it: one side's view at one moment,
labeled with whether that side won.

The hidden information rule is the point of this module. A game file holds both sides,
and the other side's snapshot names what this side's hides. Matching the two on public
state picked out 106 of 121 face-down cards, so rows here are never flipped, and nothing
pairs a snapshot with the other side's. The only join key is `(game_id, snapshot_index)`,
which names one line of one file, so a card row reaches the one snapshot it was written
in. `side` is read only to make `controller` relative and to name this side's own deck.

Each table is read through a list of the columns that may be used. `docs/training.md`
says why every other column stays out. `id` means nothing to the model, and `final_turn`
would tell it the future.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

from cardscript.vocab import Vocabulary, build_vocabulary
from gamerecord.record import SCOREBOARD, ZONES
from setmodel.features import EMPTY_CARD, CardTable, Caps, Encoded, densify, encode, face_slot, key_scales

# Spec 5 reads the manifest and never writes it. Spec 4 owns the file.
MANIFEST_VERSION = 1
DECK_SPLITS = ("train", "held_out")

# A hidden card has no name, so it can't be an unmatched name. Set this True to treat it as
# one, and every snapshot holding a hidden card is dropped.
HIDDEN_COUNTS_AS_UNMATCHED = False

TRAIN = "train"
HELD_OUT_GAMES = "held_out_games"  # train decks, games kept back
HELD_OUT_DECKS = "held_out_decks"  # any game with a held-out deck in either seat
SPLITS = (TRAIN, HELD_OUT_GAMES, HELD_OUT_DECKS)
HELD_OUT_GAME_SHARE = 0.1

SCOREBOARD_FIELDS = tuple(SCOREBOARD.fields)
ZONE_NAMES = (*ZONES.fields, "stack")
ZONE_IDS = {name: index for index, name in enumerate(ZONE_NAMES)}
LIVE_FIELDS = ("power", "toughness", "is_creature", "tapped", "summoning_sick", "mine", "face_down", "hidden", "damage",
               "attacking", "blocking", "blocked", "attached", "counters_p1p1", "counters_m1m1", "counters_loyalty",
               "counters_other", "stack_ability")
LIVE = {name: index for index, name in enumerate(LIVE_FIELDS)}
NAMED_COUNTERS = {"P1P1": "counters_p1p1", "M1M1": "counters_m1m1", "LOYALTY": "counters_loyalty"}

# Every column read. Decks only pick the split. `batch_id` only tags the model.
SNAPSHOT_COLUMNS = ("batch_id", "game_id", "snapshot_index", "side", "won", "seat1_deck", "seat2_deck",
                    *SCOREBOARD_FIELDS)
CARD_COLUMNS = ("game_id", "snapshot_index", "zone", "name", "face", "power", "toughness", "tapped", "counters",
                "summoning_sick", "attached_to", "attached_to_seat", "controller", "face_down", "damage",
                "attacking_seat", "attacking_card", "blocking", "blocked_by")
STACK_COLUMNS = ("game_id", "snapshot_index", "name", "face", "face_down", "controller", "kind")

BATCH_PARTITION = ds.HivePartitioning(pa.schema([("batch_id", pa.string())]))


class DatasetError(ValueError):
    pass


def load_manifest(path: str | Path) -> dict[str, str]:
    """Deck id to split. A deck's `.dck` name is its id, so that is what a record's `deck` holds."""
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("version") != MANIFEST_VERSION:
        raise DatasetError(f"{path}: manifest version {manifest.get('version')!r}, expected {MANIFEST_VERSION}")
    splits = {}
    for deck in manifest["decks"]:
        if deck["split"] not in DECK_SPLITS:
            raise DatasetError(f"{path}: deck {deck['id']!r} has split {deck['split']!r}")
        splits[deck["id"]] = deck["split"]
    return splits


def game_split(game_id: str, decks: tuple[str, str], manifest: dict[str, str], game_share: float) -> str:
    """Which split a whole game falls in. A hash of the id, so a game stays put from run to run."""
    unknown = [deck for deck in decks if deck not in manifest]
    if unknown:
        raise DatasetError(f"game {game_id}: deck not in the manifest: {', '.join(unknown)}")
    if any(manifest[deck] == "held_out" for deck in decks):
        return HELD_OUT_DECKS
    draw = int.from_bytes(hashlib.sha256(game_id.encode("utf-8")).digest()[:8], "big") / 2**64
    return HELD_OUT_GAMES if draw < game_share else TRAIN


@dataclass
class JoinReport:
    games: int = 0
    games_left: int = 0
    snapshots: int = 0
    snapshots_dropped: int = 0
    card_rows: int = 0
    hidden_cards: int = 0
    unmatched: Counter = field(default_factory=Counter)  # (name, face) -> rows
    ambiguous: Counter = field(default_factory=Counter)  # name -> rows that got only what its scripts share
    script_counts: dict[str, int] = field(default_factory=dict)  # ambiguous name -> scripts

    def to_dict(self) -> dict:
        return {"games": self.games, "games_left": self.games_left, "snapshots": self.snapshots,
                "snapshots_dropped": self.snapshots_dropped, "card_rows": self.card_rows,
                "hidden_cards": self.hidden_cards,
                "unmatched": [{"name": name, "face": face, "rows": rows}
                              for (name, face), rows in self.unmatched.most_common()],
                "ambiguous": [{"name": name, "scripts": self.script_counts[name], "rows": rows}
                              for name, rows in self.ambiguous.most_common()]}

    def format(self) -> str:
        lines = [f"games {self.games}, {self.games_left} with rows left",
                 f"snapshots {self.snapshots}, {self.snapshots_dropped} dropped for an unmatched card",
                 f"card and stack rows {self.card_rows}", f"hidden cards {self.hidden_cards}",
                 f"unmatched names {len(self.unmatched)}, {self.unmatched.total()} rows"]
        lines += [f"  {rows}  {name} ({face})" for (name, face), rows in self.unmatched.most_common()]
        lines.append(f"names on several scripts, shared features only: {len(self.ambiguous)}, "
                     f"{self.ambiguous.total()} rows")
        lines += [f"  {rows}  {name} ({self.script_counts[name]} scripts)"
                  for name, rows in self.ambiguous.most_common()]
        return "\n".join(lines) + "\n"


@dataclass
class Corpus:
    """Rows as flat arrays. Row r's cards are `card_*[card_offsets[r]:card_offsets[r + 1]]`."""

    scoreboard: np.ndarray  # [R, S], raw values
    labels: np.ndarray  # [R]
    weights: np.ndarray  # [R], 1 over the game's row count
    game: np.ndarray  # [R], index into game_ids
    snapshot_index: np.ndarray  # [R], the row's line in its game file, for finding a row. Never a model input.
    split: np.ndarray  # [R], index into SPLITS
    own_deck: np.ndarray  # [R], index into decks
    game_ids: list[str]
    decks: list[str]
    batch_ids: list[str]  # of the training rows
    held_out_decks: list[str]  # the manifest's, for the per-deck report
    card_offsets: np.ndarray  # [R + 1]
    card_feature: np.ndarray  # [M], index into the feature arrays. 0 is the empty card.
    card_zone: np.ndarray  # [M]
    card_live: np.ndarray  # [M, L], raw values
    feature_keys: list[tuple[str, str] | None]  # (name, slot) per feature, None for the empty card
    encoded: list[Encoded]
    token_ids: np.ndarray  # [C, A, T]
    token_mask: np.ndarray
    numbers: np.ndarray  # [C, A, K]
    number_mask: np.ndarray
    report: JoinReport
    caps: Caps

    def rows(self, *splits: str) -> np.ndarray:
        return np.flatnonzero(np.isin(self.split, [SPLITS.index(split) for split in splits]))

    def cards_of(self, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Positions in the card arrays for these rows, and which entry of `rows` each one belongs to."""
        starts, lengths = self.card_offsets[rows], self.card_offsets[rows + 1] - self.card_offsets[rows]
        first = np.cumsum(lengths) - lengths
        positions = np.arange(lengths.sum()) + np.repeat(starts - first, lengths)
        return positions, np.repeat(np.arange(len(rows)), lengths)

    def key_scales(self) -> np.ndarray:
        """Per-key constants from the distinct cards in the training rows."""
        positions, _ = self.cards_of(self.rows(TRAIN))
        used = np.unique(self.card_feature[positions])
        return key_scales(self.numbers[used], self.number_mask[used])

    def truncation(self) -> dict[str, float | int]:
        """How often each cap bit, over the card rows of the training split. A card past the K cap shares `<unk>`."""
        positions, _ = self.cards_of(self.rows(TRAIN))
        seen = np.bincount(self.card_feature[positions], minlength=len(self.encoded))
        named = [(int(n), card) for n, card in zip(seen, self.encoded) if n and card.rows_before_cap]
        cards = sum(n for n, _ in named)
        rows = sum(n * card.rows_before_cap for n, card in named)
        return {"cards": cards, "ability_rows": rows,
                "abilities": _rate(sum(n for n, card in named if card.rows_before_cap > self.caps.abilities), cards),
                "tokens": _rate(sum(n * card.rows_over_token_cap for n, card in named), rows),
                "numeric_keys": _rate(sum(n * card.rows_with_merged_keys for n, card in named), rows),
                "live_state": 0.0}


def _rate(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _read(folder: Path, columns: tuple[str, ...]) -> pa.Table:
    dataset = ds.dataset(folder, format="parquet", partitioning=BATCH_PARTITION)
    return dataset.to_table(columns=list(columns)).combine_chunks()


def _array(table: pa.Table, column: str) -> pa.Array:
    return table[column].combine_chunks()


def _numpy(array: pa.Array) -> np.ndarray:
    return array.to_numpy(zero_copy_only=False)


def _numbers(table: pa.Table, column: str) -> np.ndarray:
    return _numpy(pc.fill_null(_array(table, column), 0)).astype(np.float32)


def _flag(table: pa.Table, column: str) -> np.ndarray:
    return _numpy(_array(table, column)).astype(np.float32)


def _is_set(table: pa.Table, column: str) -> np.ndarray:
    return _numpy(pc.is_valid(_array(table, column))).astype(np.float32)


def _has_items(table: pa.Table, column: str) -> np.ndarray:
    return _numpy(pc.fill_null(pc.greater(pc.list_value_length(_array(table, column)), 0), False)).astype(np.float32)


def _add_counters(table: pa.Table, live: np.ndarray) -> None:
    counters = _array(table, "counters")
    owner = np.repeat(np.arange(len(counters)), np.diff(_numpy(counters.offsets)))
    names = _numpy(counters.keys)
    amounts = _numpy(counters.items).astype(np.float32)
    other = np.ones(len(owner), dtype=bool)
    for counter, column in NAMED_COUNTERS.items():
        match = names == counter
        other &= ~match
        np.add.at(live[:, LIVE[column]], owner[match], amounts[match])
    np.add.at(live[:, LIVE["counters_other"]], owner[other], amounts[other])


def _card_live(table: pa.Table) -> np.ndarray:
    """The live state of each card row. All of it is on the row itself, so it is what this side saw."""
    live = np.zeros((table.num_rows, len(LIVE_FIELDS)), dtype=np.float32)
    live[:, LIVE["power"]] = _numbers(table, "power")
    live[:, LIVE["toughness"]] = _numbers(table, "toughness")
    live[:, LIVE["is_creature"]] = _is_set(table, "power")
    live[:, LIVE["tapped"]] = _flag(table, "tapped")
    live[:, LIVE["summoning_sick"]] = _flag(table, "summoning_sick")
    live[:, LIVE["face_down"]] = _flag(table, "face_down")
    live[:, LIVE["hidden"]] = 1 - _is_set(table, "name")
    live[:, LIVE["damage"]] = _numbers(table, "damage")
    live[:, LIVE["attacking"]] = np.maximum(_is_set(table, "attacking_seat"), _is_set(table, "attacking_card"))
    live[:, LIVE["blocking"]] = _has_items(table, "blocking")
    live[:, LIVE["blocked"]] = _has_items(table, "blocked_by")
    live[:, LIVE["attached"]] = np.maximum(_is_set(table, "attached_to"), _is_set(table, "attached_to_seat"))
    _add_counters(table, live)
    return live


def _stack_live(table: pa.Table) -> np.ndarray:
    live = np.zeros((table.num_rows, len(LIVE_FIELDS)), dtype=np.float32)
    live[:, LIVE["face_down"]] = _flag(table, "face_down")
    live[:, LIVE["hidden"]] = 1 - _is_set(table, "name")
    live[:, LIVE["stack_ability"]] = _numpy(pc.equal(_array(table, "kind"), "ability")).astype(np.float32)
    return live


class _Features:
    """The cards one corpus uses, with the empty card at index 0."""

    def __init__(self, cards: CardTable, vocab: Vocabulary, caps: Caps, report: JoinReport):
        self.cards, self.vocab, self.caps, self.report = cards, vocab, caps, report
        self.keys: list[tuple[str, str] | None] = [None]
        self.encoded: list[Encoded] = [EMPTY_CARD]
        self.index: dict[tuple[str, str], int] = {}

    def indices(self, table: pa.Table) -> np.ndarray:
        """A feature index per row: 0 for a hidden card, -1 for an unmatched name."""
        pairs = pc.binary_join_element_wise(_array(table, "name"), _array(table, "face"), "\0").dictionary_encode()
        codes = _numpy(pc.fill_null(pairs.indices, -1)).astype(np.int64)
        counts = np.bincount(codes[codes >= 0], minlength=len(pairs.dictionary))
        lookup = [self._feature(*pair.split("\0"), int(rows))
                  for pair, rows in zip(pairs.dictionary.to_pylist(), counts)]
        return np.array([*lookup, 0], dtype=np.int64)[codes]  # a null code of -1 reads the 0 on the end: hidden

    def _feature(self, name: str, face: str, rows: int) -> int:
        resolved = self.cards.resolve(name, face)
        if resolved is None:
            self.report.unmatched[(name, face)] += rows
            return -1
        if resolved.ambiguous:
            self.report.ambiguous[name] += rows
            self.report.script_counts[name] = self.cards.script_count(name)
        key = (name, face_slot(face))
        if key not in self.index:
            self.index[key] = len(self.encoded)
            self.keys.append(key)
            self.encoded.append(encode(resolved.rows, self.vocab, self.caps))
        return self.index[key]


def load_corpus(parquet: str | Path, cards: CardTable, vocab: Vocabulary, manifest: dict[str, str], *,
                game_share: float = HELD_OUT_GAME_SHARE, caps: Caps = Caps()) -> Corpus:
    """Join `parquet`'s tables, as `gamerecord convert` writes them, to the card dump."""
    parquet = Path(parquet)
    snapshots = _read(parquet / "snapshots", SNAPSHOT_COLUMNS)
    if not snapshots.num_rows:
        raise DatasetError(f"{parquet}: no snapshots")

    games = pa.array(sorted(pc.unique(_array(snapshots, "game_id")).to_pylist()))
    stride = int(pc.max(_array(snapshots, "snapshot_index")).as_py()) + 1

    def line_keys(table: pa.Table) -> np.ndarray:
        """One number per `(game_id, snapshot_index)`: a line of a game file, so one side's snapshot."""
        return _numpy(pc.index_in(_array(table, "game_id"), value_set=games)).astype(np.int64) * stride + _numpy(
            _array(table, "snapshot_index"))

    keys = line_keys(snapshots)
    order = np.argsort(keys, kind="stable")
    snapshots, keys = snapshots.take(pa.array(order)), keys[order]

    def owning_row(table: pa.Table) -> np.ndarray:
        rows_keys = line_keys(table)
        rows = np.minimum(np.searchsorted(keys, rows_keys), len(keys) - 1)
        if (keys[rows] != rows_keys).any():
            raise DatasetError(f"{parquet}: a card row has no snapshot")
        return rows

    report = JoinReport(games=len(games), snapshots=snapshots.num_rows)
    features = _Features(cards, vocab, caps, report)
    card_table, stack_table = _read(parquet / "cards", CARD_COLUMNS), _read(parquet / "stack", STACK_COLUMNS)

    card_row = np.concatenate([owning_row(card_table), owning_row(stack_table)])
    feature = np.concatenate([features.indices(card_table), features.indices(stack_table)])
    zone = np.concatenate([[ZONE_IDS[name] for name in card_table["zone"].to_pylist()],
                           np.full(stack_table.num_rows, ZONE_IDS["stack"])]).astype(np.int64)
    live = np.concatenate([_card_live(card_table), _stack_live(stack_table)])
    controller = np.concatenate([_numpy(_array(card_table, "controller")), _numpy(_array(stack_table, "controller"))])
    live[:, LIVE["mine"]] = controller == _numpy(_array(snapshots, "side"))[card_row]

    report.card_rows, report.hidden_cards = len(feature), int(live[:, LIVE["hidden"]].sum())
    bad = feature < 0
    if HIDDEN_COUNTS_AS_UNMATCHED:
        bad |= live[:, LIVE["hidden"]] > 0
    keep = np.ones(snapshots.num_rows, dtype=bool)
    keep[card_row[bad]] = False
    report.snapshots_dropped = int((~keep).sum())
    if not keep.any():
        raise DatasetError(f"{parquet}: every snapshot was dropped\n{report.format()}")

    kept_cards = keep[card_row]
    new_row = (np.cumsum(keep) - 1)[card_row[kept_cards]]
    order = np.argsort(new_row, kind="stable")
    card_row = new_row[order]
    feature, zone, live = feature[kept_cards][order], zone[kept_cards][order], live[kept_cards][order]
    snapshots = snapshots.filter(pa.array(keep))

    game = keys[keep] // stride
    rows_per_game = np.bincount(game, minlength=len(games))
    report.games_left = int((rows_per_game > 0).sum())
    game_ids = games.to_pylist()
    seat_decks = (snapshots["seat1_deck"].to_pylist(), snapshots["seat2_deck"].to_pylist())
    split_of_game = {}
    for row, code in enumerate(game.tolist()):
        if code not in split_of_game:
            pair = (seat_decks[0][row], seat_decks[1][row])
            split_of_game[code] = SPLITS.index(game_split(game_ids[code], pair, manifest, game_share))
    split = np.array([split_of_game[code] for code in game.tolist()], dtype=np.int64)

    own = [seat_decks[seat - 1][row] for row, seat in enumerate(snapshots["side"].to_pylist())]
    decks = sorted(set(own))
    deck_index = {deck: index for index, deck in enumerate(decks)}
    batch_of_row = snapshots["batch_id"].to_pylist()
    token_ids, token_mask, numbers, number_mask = densify(features.encoded, caps, len(vocab.numeric_keys))
    return Corpus(
        scoreboard=np.stack([_numpy(_array(snapshots, name)).astype(np.float32) for name in SCOREBOARD_FIELDS], axis=1),
        labels=_numpy(_array(snapshots, "won")).astype(np.float32),
        weights=1.0 / rows_per_game[game],
        game=game, snapshot_index=_numpy(_array(snapshots, "snapshot_index")).astype(np.int64), split=split,
        own_deck=np.array([deck_index[deck] for deck in own], dtype=np.int64),
        game_ids=game_ids, decks=decks,
        batch_ids=sorted({batch_of_row[row] for row in np.flatnonzero(split == SPLITS.index(TRAIN)).tolist()}),
        held_out_decks=sorted(deck for deck, where in manifest.items() if where == "held_out"),
        card_offsets=np.concatenate([[0], np.cumsum(np.bincount(card_row, minlength=snapshots.num_rows))]),
        card_feature=feature, card_zone=zone, card_live=live, feature_keys=features.keys, encoded=features.encoded,
        token_ids=token_ids, token_mask=token_mask, numbers=numbers, number_mask=number_mask, report=report, caps=caps)


def load_inputs(parquet: str | Path, cards: str | Path, manifest: str | Path,
                min_count: int) -> tuple[Corpus, Vocabulary]:
    """The corpus and vocabulary one command works from. The vocabulary comes from the dump, as in `docs/sweep.md`."""
    with open(cards, encoding="utf-8") as lines:
        vocab = build_vocabulary((json.loads(line) for line in lines), min_count=min_count)
    return load_corpus(parquet, CardTable.load(cards), vocab, load_manifest(manifest)), vocab
