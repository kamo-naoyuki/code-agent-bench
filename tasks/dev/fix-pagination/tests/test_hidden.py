import sys
sys.path.insert(0, "/submission")
from pagination import first, paginate
import pytest


@pytest.mark.parametrize("items,page,size,selected,pages", [
    ([1, 2, 3, 4, 5], 1, 2, [1, 2], 3),
    ([1, 2, 3, 4, 5], 3, 2, [5], 3),
    ([1, 2], 2, 2, [], 1), ([], 1, 3, [], 0),
    ([1, 2, 3, 4], 2, 2, [3, 4], 2),
    ([1], 100, 5, [], 1),
])
def test_pages(items, page, size, selected, pages):
    before = list(items)
    assert paginate(items, page, size) == {
        "items": selected, "page": page, "pages": pages, "total": len(items)}
    assert items == before


@pytest.mark.parametrize("page,size", [
    (0, 2), (-1, 2), (1, 0), (1, -2), (1.0, 2), (1, 2.0),
    (True, 2), (1, False), ("1", 2),
])
def test_invalid(page, size):
    with pytest.raises(ValueError):
        paginate([1], page, size)


def test_preserved_first():
    assert first([]) is None
    assert first([4, 5]) == 4
