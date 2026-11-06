import sys
sys.path.insert(0, "/submission")
from intervals import interval_length, merge_intervals
import pytest


@pytest.mark.parametrize("data,expected", [
    ([], []),
    ([(4, 8), (1, 5)], [(1, 8)]),
    ([(1, 10), (2, 3)], [(1, 10)]),
    ([(1, 2), (2, 4)], [(1, 4)]),
    ([(1, 2), (3, 4)], [(1, 2), (3, 4)]),
    ([(-4, 0), (-4, 0), (-1, 3)], [(-4, 3)]),
])
def test_merge(data, expected):
    before = list(data)
    assert merge_intervals(data) == expected
    assert data == before


def test_preserved_length():
    assert interval_length((-2, 3)) == 6
