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
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from cardscript.vocab import Vocabulary, build_vocabulary
from gamerecord.record import SCOREBOARD, ZONES
from setmodel.features import EMPTY_CARD, CardTable, Caps, Encoded, densify, encode, face_slot, key_scales

# Spec 5 reads the manifest and never writes it. Spec 4 owns the file.
MANIFEST_VERSION = 1
DECK_SPLITS = ("train", "held_out")

# The name of the placeholder #25's pruned search copies hold for a card the searching side can't know,
# `GameCopier.HIDDEN_CARD_NAME` in the fork. A copy can move one into a public zone. It is encoded as a hidden card:
# no lookup, `hidden` 1. A real game never holds one, so on game files this only matters if Forge ever names a card so.
PLACEHOLDER_NAME = "hidden"

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

# How the corpus stores live state: the numbers as small ints, the yes-or-no fields as bits.
COUNT_FIELDS = ("power", "toughness", "damage", "counters_p1p1", "counters_m1m1", "counters_loyalty", "counters_other")
FLAG_FIELDS = tuple(name for name in LIVE_FIELDS if name not in COUNT_FIELDS)
COUNT = {name: index for index, name in enumerate(COUNT_FIELDS)}
FLAG_BIT = {name: bit for bit, name in enumerate(FLAG_FIELDS)}
COUNT_COLUMNS = [LIVE[name] for name in COUNT_FIELDS]
FLAG_COLUMNS = [LIVE[name] for name in FLAG_FIELDS]
WIDER = {np.dtype(np.int16): np.int32, np.dtype(np.int32): np.int64}

CHUNK_ROWS = 250_000  # card or stack rows read at a time
COUNT_SLICE_ROWS = 100_000  # snapshots per slice when counting a split's cards

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
    skipped: Counter = field(default_factory=Counter)  # stack abilities with no card behind them: name -> rows
    ambiguous: Counter = field(default_factory=Counter)  # name -> rows that got only what its scripts share
    script_counts: dict[str, int] = field(default_factory=dict)  # ambiguous name -> scripts

    def to_dict(self) -> dict:
        return {"games": self.games, "games_left": self.games_left, "snapshots": self.snapshots,
                "snapshots_dropped": self.snapshots_dropped, "card_rows": self.card_rows,
                "hidden_cards": self.hidden_cards,
                "unmatched": [{"name": name, "face": face, "rows": rows}
                              for (name, face), rows in self.unmatched.most_common()],
                "skipped": [{"name": name, "rows": rows} for name, rows in self.skipped.most_common()],
                "ambiguous": [{"name": name, "scripts": self.script_counts[name], "rows": rows}
                              for name, rows in self.ambiguous.most_common()]}

    def format(self) -> str:
        lines = [f"games {self.games}, {self.games_left} with rows left",
                 f"snapshots {self.snapshots}, {self.snapshots_dropped} dropped for an unmatched card",
                 f"card and stack rows {self.card_rows}", f"hidden cards {self.hidden_cards}",
                 f"unmatched names {len(self.unmatched)}, {self.unmatched.total()} rows"]
        lines += [f"  {rows}  {name} ({face})" for (name, face), rows in self.unmatched.most_common()]
        lines.append(f"stack abilities left out, no card behind them: {self.skipped.total()} rows, "
                     f"{len(self.skipped)} names")
        lines += [f"  {rows}  {name}" for name, rows in self.skipped.most_common()]
        lines.append(f"names on several scripts, shared features only: {len(self.ambiguous)}, "
                     f"{self.ambiguous.total()} rows")
        lines += [f"  {rows}  {name} ({self.script_counts[name]} scripts)"
                  for name, rows in self.ambiguous.most_common()]
        return "\n".join(lines) + "\n"


@dataclass
class Corpus:
    """Rows as flat arrays. Row r's cards are `card_*[card_offsets[r]:card_offsets[r + 1]]`.

    The card arrays hold small integer types, and `live` turns positions into the contract's
    float `live` rows. 10,000 games make about 73 million card rows, so each byte a card row
    takes is 73 MB.
    """

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
    card_flags: np.ndarray  # [M], bit n is FLAG_FIELDS[n]
    card_counts: np.ndarray  # [M, len(COUNT_FIELDS)], raw values
    feature_keys: list[tuple[str, str] | None]  # (name, slot) per feature, None for the empty card
    encoded: list[Encoded]
    token_ids: np.ndarray  # [C, A, T]
    token_mask: np.ndarray
    numbers: np.ndarray  # [C, A, K]
    number_mask: np.ndarray
    report: JoinReport
    caps: Caps
    dropped_zones: tuple[int, ...] = ()  # zone ids whose cards `cards_of` leaves out, for an ablation

    def rows(self, *splits: str) -> np.ndarray:
        return np.flatnonzero(np.isin(self.split, [SPLITS.index(split) for split in splits]))

    def cards_of(self, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Positions in the card arrays for these rows, and which entry of `rows` each one belongs to."""
        starts, lengths = self.card_offsets[rows], self.card_offsets[rows + 1] - self.card_offsets[rows]
        first = np.cumsum(lengths) - lengths
        positions = np.arange(lengths.sum()) + np.repeat(starts - first, lengths)
        owner = np.repeat(np.arange(len(rows)), lengths)
        if self.dropped_zones:
            kept = ~np.isin(self.card_zone[positions], self.dropped_zones)
            positions, owner = positions[kept], owner[kept]
        return positions, owner

    def live(self, positions: np.ndarray) -> np.ndarray:
        """The contract's `live` rows, [N, L] float32, for these card positions."""
        live = np.zeros((len(positions), len(LIVE_FIELDS)), dtype=np.float32)
        live[:, COUNT_COLUMNS] = self.card_counts[positions]
        flags = self.card_flags[positions]
        for bit, column in enumerate(FLAG_COLUMNS):
            live[:, column] = (flags >> bit) & 1
        return live

    def feature_counts(self, rows: np.ndarray) -> np.ndarray:
        """How many card rows of these rows show each feature, counted a slice of rows at a time."""
        chosen = np.zeros(len(self.labels), dtype=bool)
        chosen[rows] = True
        lengths = np.diff(self.card_offsets)
        counts = np.zeros(len(self.encoded), dtype=np.int64)
        for start in range(0, len(lengths), COUNT_SLICE_ROWS):
            stop = min(start + COUNT_SLICE_ROWS, len(lengths))
            cards = slice(self.card_offsets[start], self.card_offsets[stop])
            mask = np.repeat(chosen[start:stop], lengths[start:stop])
            if self.dropped_zones:
                mask &= ~np.isin(self.card_zone[cards], self.dropped_zones)
            counts += np.bincount(self.card_feature[cards][mask], minlength=len(self.encoded))
        return counts

    def key_scales(self) -> np.ndarray:
        """Per-key constants from the distinct cards in the training rows."""
        used = np.flatnonzero(self.feature_counts(self.rows(TRAIN)))
        return key_scales(self.numbers[used], self.number_mask[used])

    def truncation(self) -> dict[str, float | int]:
        """How often each cap bit, over the card rows of the training split. A card past the K cap shares `<unk>`."""
        seen = self.feature_counts(self.rows(TRAIN))
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


def _dataset(folder: Path) -> ds.Dataset:
    return ds.dataset(folder, format="parquet", partitioning=BATCH_PARTITION)


def _read(folder: Path, columns: tuple[str, ...]) -> pa.Table:
    return _dataset(folder).to_table(columns=list(columns)).combine_chunks()


def _chunks(folder: Path, columns: tuple[str, ...]) -> Iterator[pa.Table]:
    """The table a few row groups at a time, about CHUNK_ROWS rows each, in the row order `_read` gives.

    `gamerecord convert` writes a row group per game, so a chunk is a run of whole games.
    """
    dataset = _dataset(folder)
    for path in dataset.files:
        with dataset.filesystem.open_input_file(path) as source:
            file = pq.ParquetFile(source)
            groups, rows = [], 0
            for group in range(file.num_row_groups):
                groups.append(group)
                rows += file.metadata.row_group(group).num_rows
                if rows >= CHUNK_ROWS or group == file.num_row_groups - 1:
                    yield file.read_row_groups(groups, columns=list(columns)).combine_chunks()
                    groups, rows = [], 0


def _array(table: pa.Table, column: str) -> pa.Array:
    return table[column].combine_chunks()


def _numpy(array: pa.Array) -> np.ndarray:
    return array.to_numpy(zero_copy_only=False)


def _ints(table: pa.Table, column: str) -> np.ndarray:
    return _numpy(pc.fill_null(_array(table, column), 0)).astype(np.int64)


def _flag(table: pa.Table, column: str) -> np.ndarray:
    return _numpy(_array(table, column)).astype(bool)


def _is_set(table: pa.Table, column: str) -> np.ndarray:
    return _numpy(pc.is_valid(_array(table, column)))


def _has_items(table: pa.Table, column: str) -> np.ndarray:
    return _numpy(pc.fill_null(pc.greater(pc.list_value_length(_array(table, column)), 0), False))


def _codes(array: pa.Array, values: list[str]) -> np.ndarray:
    """Each entry's index in `values`, which holds them all."""
    return _numpy(pc.index_in(array, value_set=pa.array(values, pa.string()))).astype(np.int64)


def _pack(flags: dict[str, np.ndarray], rows: int) -> np.ndarray:
    packed = np.zeros(rows, dtype=np.uint16)
    for name, values in flags.items():
        packed |= values.astype(np.uint16) << FLAG_BIT[name]
    return packed


def _add_counters(table: pa.Table, counts: np.ndarray) -> None:
    counters = _array(table, "counters")
    offsets = _numpy(counters.offsets)
    owner = np.repeat(np.arange(len(counters)), np.diff(offsets))
    entries = slice(offsets[0], offsets[-1])  # `keys` and `items` ignore a slice of the map array
    names = _numpy(counters.keys)[entries]
    amounts = _numpy(counters.items)[entries].astype(np.float64)
    other = np.ones(len(owner), dtype=bool)
    for counter, column in NAMED_COUNTERS.items():
        match = names == counter
        other &= ~match
        counts[:, COUNT[column]] += np.bincount(owner[match], amounts[match], len(counters)).astype(np.int64)
    counts[:, COUNT["counters_other"]] += np.bincount(owner[other], amounts[other], len(counters)).astype(np.int64)


def _hidden(table: pa.Table) -> np.ndarray:
    """A card this side may not look at: no name, or the placeholder a pruned search copy holds."""
    names = _array(table, "name")
    return _numpy(pc.fill_null(pc.equal(names, PLACEHOLDER_NAME), True))


def _skipped(table: pa.Table, feature: np.ndarray) -> np.ndarray:
    """Stack abilities whose source isn't a card: an effect, an emblem, The Initiative, The Monarch, speed.

    Forge gives such a source a name no card has, so the test is an ability whose named source the card table
    lacks. Tensor contract version 2 leaves these out of the state. A spell always has a card behind it, and any
    other unmatched name still drops its snapshot.
    """
    if "kind" not in table.column_names:
        return np.zeros(table.num_rows, dtype=bool)
    return (feature < 0) & _numpy(pc.fill_null(pc.equal(_array(table, "kind"), "ability"), False))


_EFFECT_NUMBER = re.compile(r" \(\d+\)")


def _tally(counter: Counter, table: pa.Table, rows: np.ndarray, *, with_face: bool) -> None:
    """Add these rows to a report counter by name. A Forge id in an effect's name, as in `X (48)'s Effect`, is dropped."""
    if not rows.any():
        return
    names = _numpy(_array(table, "name"))[rows]
    faces = _numpy(_array(table, "face"))[rows]
    for name, face in zip(names.tolist(), faces.tolist()):
        counter[(name, face) if with_face else _EFFECT_NUMBER.sub(" (N)", name)] += 1


def _card_live(table: pa.Table, mine: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Flags and counts per card row. All of it is on the row itself, so it is what this side saw."""
    counts = np.zeros((table.num_rows, len(COUNT_FIELDS)), dtype=np.int64)
    counts[:, COUNT["power"]] = _ints(table, "power")
    counts[:, COUNT["toughness"]] = _ints(table, "toughness")
    counts[:, COUNT["damage"]] = _ints(table, "damage")
    _add_counters(table, counts)
    flags = {"is_creature": _is_set(table, "power"), "tapped": _flag(table, "tapped"),
             "summoning_sick": _flag(table, "summoning_sick"), "mine": mine, "face_down": _flag(table, "face_down"),
             "hidden": _hidden(table),
             "attacking": _is_set(table, "attacking_seat") | _is_set(table, "attacking_card"),
             "blocking": _has_items(table, "blocking"), "blocked": _has_items(table, "blocked_by"),
             "attached": _is_set(table, "attached_to") | _is_set(table, "attached_to_seat")}
    return _pack(flags, table.num_rows), counts


def _stack_live(table: pa.Table, mine: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    flags = {"mine": mine, "face_down": _flag(table, "face_down"), "hidden": _hidden(table),
             "stack_ability": _numpy(pc.equal(_array(table, "kind"), "ability"))}
    return _pack(flags, table.num_rows), np.zeros((table.num_rows, len(COUNT_FIELDS)), dtype=np.int64)


def _card_zones(table: pa.Table) -> np.ndarray:
    zones = pc.dictionary_encode(_array(table, "zone"))
    ids = np.array([ZONE_IDS[name] for name in zones.dictionary.to_pylist()], dtype=np.int8)
    return ids[_numpy(zones.indices)]


def _stack_zones(table: pa.Table) -> np.ndarray:
    return np.full(table.num_rows, ZONE_IDS["stack"], dtype=np.int8)


class _Features:
    """The cards one corpus uses, with the empty card at index 0."""

    def __init__(self, cards: CardTable, vocab: Vocabulary, caps: Caps, report: JoinReport):
        self.cards, self.vocab, self.caps, self.report = cards, vocab, caps, report
        self.keys: list[tuple[str, str] | None] = [None]
        self.encoded: list[Encoded] = [EMPTY_CARD]
        self.index: dict[tuple[str, str], int] = {}
        self.of_pair: dict[str, int] = {}  # "name\0face" to its feature, or -1

    def indices(self, table: pa.Table, *, count: bool = True) -> np.ndarray:
        """A feature index per row: 0 for a hidden card or a placeholder, -1 for an unmatched name.

        `count` adds matched rows on several scripts to the join report. The second pass reads the same rows again
        and doesn't. The caller counts unmatched rows, because only it knows which of them are left out.
        """
        pairs = pc.binary_join_element_wise(_array(table, "name"), _array(table, "face"), "\0").dictionary_encode()
        codes = _numpy(pc.fill_null(pairs.indices, -1)).astype(np.int64)
        rows = np.bincount(codes[codes >= 0], minlength=len(pairs.dictionary))
        lookup = []
        for pair, n in zip(pairs.dictionary.to_pylist(), rows.tolist()):
            if pair not in self.of_pair:
                self.of_pair[pair] = self._feature(*pair.split("\0"))
            if count:
                self._count(*pair.split("\0"), self.of_pair[pair], n)
            lookup.append(self.of_pair[pair])
        return np.array([*lookup, 0], dtype=np.int64)[codes]  # a null code of -1 reads the 0 on the end: hidden

    def _feature(self, name: str, face: str) -> int:
        if name == PLACEHOLDER_NAME:
            return 0
        resolved = self.cards.resolve(name, face)
        if resolved is None:
            return -1
        key = (name, face_slot(face))
        if key not in self.index:
            self.index[key] = len(self.encoded)
            self.keys.append(key)
            self.encoded.append(encode(resolved.rows, self.vocab, self.caps))
        return self.index[key]

    def _count(self, name: str, face: str, feature: int, rows: int) -> None:
        if feature > 0 and self.cards.resolve(name, face).ambiguous:
            self.report.ambiguous[name] += rows
            self.report.script_counts[name] = self.cards.script_count(name)


class _CardArrays:
    """The corpus's card arrays, filled a chunk at a time. Counts start as int16 and widen when a value needs it."""

    def __init__(self, rows: int, features: int):
        self.feature = np.zeros(rows, dtype=np.uint16 if features <= 2**16 else np.int32)
        self.zone = np.zeros(rows, dtype=np.int8)
        self.flags = np.zeros(rows, dtype=np.uint16)
        self.counts = np.zeros((rows, len(COUNT_FIELDS)), dtype=np.int16)

    def put(self, at: np.ndarray, feature: np.ndarray, zone: np.ndarray, flags: np.ndarray, counts: np.ndarray) -> None:
        if counts.size:
            low, high = int(counts.min()), int(counts.max())
            while not np.iinfo(self.counts.dtype).min <= low <= high <= np.iinfo(self.counts.dtype).max:
                self.counts = self.counts.astype(WIDER[self.counts.dtype])
        self.feature[at], self.zone[at], self.flags[at], self.counts[at] = feature, zone, flags, counts


def _ranks(groups: np.ndarray) -> np.ndarray:
    """For each entry, how many entries before it have the same value."""
    order = np.argsort(groups, kind="stable")
    ordered = groups[order]
    starts = np.flatnonzero(np.concatenate([[True], ordered[1:] != ordered[:-1]]))
    first = np.repeat(starts, np.diff(np.concatenate([starts, [len(ordered)]])))
    ranks = np.empty(len(groups), dtype=np.int64)
    ranks[order] = np.arange(len(groups)) - first
    return ranks


def load_corpus(parquet: str | Path, cards: CardTable, vocab: Vocabulary, manifest: dict[str, str], *,
                game_share: float = HELD_OUT_GAME_SHARE, caps: Caps = Caps()) -> Corpus:
    """Join `parquet`'s tables, as `gamerecord convert` writes them, to the card dump.

    The card and stack tables are never read whole. Two passes go through them a chunk at a
    time. The first resolves names and finds the snapshots an unmatched card drops, which
    fixes where each kept card row goes. The second fills the card arrays in place. A row's
    cards are its card table rows in table order, then its stack rows.
    """
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
    snapshot_count = snapshots.num_rows
    # Everything later steps need from the table, as arrays, so the table can go before the card passes.
    side = _numpy(_array(snapshots, "side"))
    scoreboard = np.stack([_numpy(_array(snapshots, name)).astype(np.float32) for name in SCOREBOARD_FIELDS], axis=1)
    labels = _numpy(_array(snapshots, "won")).astype(np.float32)
    seats = (_array(snapshots, "seat1_deck"), _array(snapshots, "seat2_deck"))
    all_decks = sorted(set(pc.unique(seats[0]).to_pylist()) | set(pc.unique(seats[1]).to_pylist()))
    seat_codes = [_codes(seat, all_decks).astype(np.int32) for seat in seats]
    batches = pc.dictionary_encode(_array(snapshots, "batch_id"))
    batch_names, batch_code = batches.dictionary.to_pylist(), _numpy(batches.indices)
    del snapshots, seats, batches

    def owning_row(table: pa.Table) -> np.ndarray:
        rows_keys = line_keys(table)
        rows = np.minimum(np.searchsorted(keys, rows_keys), len(keys) - 1)
        if (keys[rows] != rows_keys).any():
            raise DatasetError(f"{parquet}: a card row has no snapshot")
        return rows

    report = JoinReport(games=len(games), snapshots=snapshot_count)
    features = _Features(cards, vocab, caps, report)
    tables = ((parquet / "cards", CARD_COLUMNS, _card_live, _card_zones, ()),
              (parquet / "stack", STACK_COLUMNS, _stack_live, _stack_zones, ("kind",)))

    # Pass 1: names, drops, left-out stack abilities, and each snapshot's card count.
    keep = np.ones(snapshot_count, dtype=bool)
    per_row = np.zeros(snapshot_count, dtype=np.int64)
    for folder, _, _, _, extra in tables:
        for chunk in _chunks(folder, ("game_id", "snapshot_index", "name", "face", *extra)):
            row, feature = owning_row(chunk), features.indices(chunk)
            skipped = _skipped(chunk, feature)
            hidden = _hidden(chunk)
            report.card_rows += int((~skipped).sum())
            report.hidden_cards += int(hidden.sum())
            bad = (feature < 0) & ~skipped
            _tally(report.unmatched, chunk, bad, with_face=True)
            _tally(report.skipped, chunk, skipped, with_face=False)
            if HIDDEN_COUNTS_AS_UNMATCHED:
                bad |= hidden
            keep[row[bad]] = False
            per_row += np.bincount(row[~skipped], minlength=len(per_row))
    report.snapshots_dropped = int((~keep).sum())
    if not keep.any():
        raise DatasetError(f"{parquet}: every snapshot was dropped\n{report.format()}")

    # Pass 2: each kept card row straight to its place.
    new_row = np.cumsum(keep) - 1
    card_offsets = np.concatenate([[0], np.cumsum(per_row[keep])])
    arrays = _CardArrays(int(card_offsets[-1]), len(features.encoded))
    filled = np.zeros(len(card_offsets) - 1, dtype=np.int64)
    for folder, columns, live_of, zones_of, _ in tables:
        for chunk in _chunks(folder, columns):
            row = owning_row(chunk)
            feature = features.indices(chunk, count=False)
            kept = keep[row] & ~_skipped(chunk, feature)
            if not kept.any():
                continue
            mine = _numpy(pc.fill_null(_array(chunk, "controller"), 0)) == side[row]
            flags, counts = live_of(chunk, mine)
            target = new_row[row[kept]]
            at = card_offsets[target] + filled[target] + _ranks(target)
            filled += np.bincount(target, minlength=len(filled))
            arrays.put(at, feature[kept], zones_of(chunk)[kept], flags[kept], counts[kept])
    pa.default_memory_pool().release_unused()  # hand the chunks' buffers back rather than keep them pooled

    side, scoreboard, labels, batch_code = side[keep], scoreboard[keep], labels[keep], batch_code[keep]
    seat_codes = [codes[keep] for codes in seat_codes]
    game = (keys[keep] // stride).astype(np.int32)
    rows_per_game = np.bincount(game, minlength=len(games))
    report.games_left = int((rows_per_game > 0).sum())
    game_ids = games.to_pylist()
    codes, first_rows = np.unique(game, return_index=True)  # a game's decks, read from its first row
    split_of_game = np.zeros(len(games), dtype=np.int8)
    for code, row in zip(codes.tolist(), first_rows.tolist()):
        pair = (all_decks[seat_codes[0][row]], all_decks[seat_codes[1][row]])
        split_of_game[code] = SPLITS.index(game_split(game_ids[code], pair, manifest, game_share))
    split = split_of_game[game]

    own = np.where(side == 1, seat_codes[0], seat_codes[1])
    own_codes = np.unique(own)
    train_batches = np.unique(batch_code[split == SPLITS.index(TRAIN)])
    token_ids, token_mask, numbers, number_mask = densify(features.encoded, caps, len(vocab.numeric_keys))
    return Corpus(
        scoreboard=scoreboard, labels=labels,
        weights=1.0 / rows_per_game[game],
        game=game, snapshot_index=(keys[keep] % stride).astype(np.int64), split=split, own_deck=np.searchsorted(own_codes, own).astype(np.int32),
        game_ids=game_ids, decks=[all_decks[code] for code in own_codes.tolist()],
        batch_ids=sorted(batch_names[code] for code in train_batches.tolist()),
        held_out_decks=sorted(deck for deck, where in manifest.items() if where == "held_out"),
        card_offsets=card_offsets, card_feature=arrays.feature, card_zone=arrays.zone, card_flags=arrays.flags,
        card_counts=arrays.counts, feature_keys=features.keys, encoded=features.encoded,
        token_ids=token_ids, token_mask=token_mask, numbers=numbers, number_mask=number_mask, report=report, caps=caps)


def load_inputs(parquet: str | Path, cards: str | Path, manifest: str | Path,
                min_count: int) -> tuple[Corpus, Vocabulary]:
    """The corpus and vocabulary one command works from. The vocabulary comes from the dump, as in `docs/sweep.md`."""
    with open(cards, encoding="utf-8") as lines:
        vocab = build_vocabulary((json.loads(line) for line in lines), min_count=min_count)
    return load_corpus(parquet, CardTable.load(cards), vocab, load_manifest(manifest)), vocab
