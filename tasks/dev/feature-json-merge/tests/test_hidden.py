import copy
import sys
sys.path.insert(0, "/submission")
from merge import is_object, merge_patch
import pytest


@pytest.mark.parametrize("target,patch,expected", [
    ({"a": 1, "b": 2}, {"a": None, "c": 3}, {"b": 2, "c": 3}),
    ({"a": {"b": 1, "c": 2}}, {"a": {"b": None, "d": 3}}, {"a": {"c": 2, "d": 3}}),
    ([1], {"a": 2}, {"a": 2}), ({"a": 1}, [2], [2]),
    ({"a": [1]}, {"a": [3]}, {"a": [3]}),
    ({}, {"x": None}, {}), (5, None, None),
])
def test_merge(target, patch, expected):
    before = copy.deepcopy((target, patch))
    assert merge_patch(target, patch) == expected
    assert (target, patch) == before


def test_no_aliases():
    target, patch = {"keep": [1]}, {"new": {"a": [2]}}
    result = merge_patch(target, patch)
    result["keep"].append(3)
    result["new"]["a"].append(4)
    assert target == {"keep": [1]}
    assert patch == {"new": {"a": [2]}}


def test_non_object_patch_replacement_has_no_aliases():
    patch = [{"nested": [1]}]
    result = merge_patch({"ignored": True}, patch)
    result[0]["nested"].append(2)
    assert patch == [{"nested": [1]}]


def test_preserved_predicate():
    assert is_object({})
    assert not is_object([])
