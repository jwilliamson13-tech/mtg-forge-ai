"""Worker seeds and the split of a batch across workers."""

import pytest

from simrunner.batch import plan_workers
from simrunner.seeds import derive_seed, split_games


def test_a_seed_is_pinned_so_old_batches_stay_replayable():
    assert derive_seed(1, 0) == 182967204537045


def test_the_same_inputs_give_the_same_seed():
    assert derive_seed(42, 3) == derive_seed(42, 3)


def test_workers_in_a_batch_get_different_seeds():
    seeds = [derive_seed(42, worker) for worker in range(64)]
    assert len(set(seeds)) == 64


def test_different_batches_get_different_seeds():
    assert derive_seed(42, 0) != derive_seed(43, 0)


def test_batch_and_worker_numbers_do_not_blur_together():
    assert derive_seed(1, 23) != derive_seed(12, 3)


@pytest.mark.parametrize("batch_seed", [0, 1, 42, 2**40, -5])
def test_a_seed_fits_what_forge_reads(batch_seed):
    seed = derive_seed(batch_seed, 0)
    assert 0 <= seed < 2**48  # never negative: Forge reads a leading `-` as a flag


def test_games_split_evenly():
    assert split_games(50, 10) == [5] * 10


def test_the_first_workers_take_the_remainder():
    assert split_games(53, 10) == [6, 6, 6, 5, 5, 5, 5, 5, 5, 5]


def test_more_workers_than_games_leaves_some_idle():
    assert split_games(2, 4) == [1, 1, 0, 0]


def test_a_plan_drops_workers_with_no_games():
    plans = plan_workers(2, 42, 4)
    assert [(plan.index, plan.games) for plan in plans] == [(0, 1), (1, 1)]


def test_a_plan_is_the_same_every_time():
    assert plan_workers(50, 42, 10) == plan_workers(50, 42, 10)


def test_a_plan_covers_every_game_with_derived_seeds():
    plans = plan_workers(53, 42, 10)
    assert sum(plan.games for plan in plans) == 53
    assert [plan.seed for plan in plans] == [derive_seed(42, index) for index in range(10)]
