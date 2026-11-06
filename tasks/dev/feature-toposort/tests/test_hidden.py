import copy
import sys
sys.path.insert(0, "/submission")
from dependencies import dependency_order, node_count
import pytest


@pytest.mark.parametrize("graph,expected", [
    ({}, []), ({"b": ["a"]}, ["a", "b"]),
    ({"z": [], "b": [], "a": []}, ["a", "b", "z"]),
    ({"c": ["a", "a"], "b": [], "a": []}, ["a", "b", "c"]),
    ({"a": ["b"], "c": []}, ["b", "a", "c"]),
    ({"d": ["b", "c"], "b": ["a"], "c": ["a"]}, ["a", "b", "c", "d"]),
])
def test_order(graph, expected):
    before = copy.deepcopy(graph)
    assert dependency_order(graph) == expected
    assert graph == before


@pytest.mark.parametrize("graph", [{"a": ["a"]}, {"a": ["b"], "b": ["a"]}])
def test_cycles(graph):
    with pytest.raises(ValueError):
        dependency_order(graph)


def test_preserved_count():
    assert node_count({"a": ["b", "b"]}) == 2
