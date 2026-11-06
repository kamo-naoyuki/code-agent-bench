import sys
sys.path.insert(0, "/submission")
from cache import TTLCache


def test_expiry_boundary_and_none():
    cache = TTLCache()
    cache.set("a", None, 2, 10)
    assert cache.get("a", 11, "missing") is None
    assert cache.get("a", 12, "missing") == "missing"


def test_replace_and_remove():
    cache = TTLCache()
    cache.set("a", 1, 10, 0)
    cache.set("a", 2, 20, 1)
    assert cache.get("a", 15) == 2
    cache.set("a", 3, 0, 15)
    assert cache.get("a", 15, "missing") == "missing"
    cache.set("b", 1, -2, 0)
    assert len(cache) == 0


def test_purge_counts_and_stored_length():
    cache = TTLCache()
    cache.set("a", 1, 1, 0)
    cache.set("b", 2, 3, 0)
    assert len(cache) == 2
    assert cache.purge(1) == 1
    assert len(cache) == 1
    assert cache.purge(1) == 0
    assert cache.purge(3) == 1


def test_len_counts_expired_entries_until_removed():
    cache = TTLCache()
    cache.set("a", 1, 1, 0)
    cache.set("b", 2, 2, 0)
    assert cache.get("missing", 1) is None
    assert len(cache) == 2
    assert cache.purge(1) == 1
    assert len(cache) == 1


def test_missing_and_independent_instances():
    first, second = TTLCache(), TTLCache()
    first.set("x", 4, 2, 0)
    assert second.get("x", 0, "missing") == "missing"
    assert first.get("unknown", 0) is None
