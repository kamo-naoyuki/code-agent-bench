import copy
import sys
sys.path.insert(0, "/submission")
from grouping import group_records, record_count


def test_order_identity_and_no_mutation():
    records = [{"k": "b", "v": 1}, {"k": "a", "v": 2}, {"k": "b", "v": 3}]
    before = copy.deepcopy(records)
    result = group_records(records, "k")
    assert [key for key, _ in result] == ["b", "a"]
    assert result[0][1] == [records[0], records[2]]
    assert result[0][1][0] is records[0]
    assert result[0][1][1] is records[2]
    assert records == before


def test_missing_none_and_mixed_keys():
    records = [{}, {"k": None}, {"k": 3}, {"k": "3"}, {"k": 3}]
    result = group_records(records, "k")
    assert [key for key, _ in result] == [None, 3, "3"]
    assert result[0][1] == records[:2]
    assert len(result[1][1]) == 2


def test_empty_and_preserved_count():
    assert group_records([], "k") == []
    assert record_count([{}, {}]) == 2
