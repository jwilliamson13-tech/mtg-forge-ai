"""How a batch splits into workers: each worker's seed and its share of the games.

Forge seeds one `java.util.Random` per JVM with `-s` and draws every game of a
`-n` run from that one stream. So a seed belongs to a worker, not to a game, and
the same batch seed, game count and worker count replay the same games.
Changing the worker count changes the split, and so changes which games are played.
"""

from __future__ import annotations

import hashlib

# java.util.Random keeps 48 bits of its seed, so more bits would add nothing.
SEED_BYTES = 6


def derive_seed(batch_seed: int, worker_index: int) -> int:
    """Return the `-s` value for one worker.

    Hashed, because neighboring seeds give `java.util.Random` correlated first
    draws, and the first draws are the opening shuffle. Never negative, because
    Forge reads any argument starting with `-` as a flag.
    """
    digest = hashlib.sha256(f"{batch_seed}:{worker_index}".encode("ascii")).digest()
    return int.from_bytes(digest[:SEED_BYTES], "big")


def split_games(games: int, workers: int) -> list[int]:
    """Share out `games` as evenly as possible. The first workers take the remainder."""
    base, extra = divmod(games, workers)
    return [base + (1 if index < extra else 0) for index in range(workers)]
