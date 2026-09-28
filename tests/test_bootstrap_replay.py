"""Shared block-bootstrap replay statistics; also exercised indirectly through
app.research.evaluation and app.us.research_validation's own test suites."""
from decimal import Decimal as D
from random import Random

from app.bootstrap_replay import bootstrap_statistics, quantile, sample_blocks


class Starts:
    def __init__(self, starts):
        self.starts = iter(starts)

    def randrange(self, stop):
        return next(self.starts)


def test_exact_block_convention_and_quantile():
    values = tuple(map(D, (1, 2, 3, 4)))
    assert sample_blocks(values, 2, Starts((2, 0))) == tuple(map(D, (3, 4, 1, 2)))
    assert sample_blocks(values, 4, Random(123)) == values
    assert quantile((D(0), D(10), D(20)), D('.25')) == 5
    assert quantile((D(7),), D('.95')) == 7


def test_bootstrap_statistics_tracks_realized_drawdown():
    mean, total, drawdown = bootstrap_statistics(
        tuple(map(D, (10, -30, 20))), D(100))
    assert (mean, total, drawdown) == (D(0), D(0), D(30))
