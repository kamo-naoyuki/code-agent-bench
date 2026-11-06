import sys
sys.path.insert(0, "/submission")
from ranges import format_number, parse_ranges
import pytest


@pytest.mark.parametrize("text,expected", [
    ("", []), (" \t ", []), ("1,3-5,4", [1, 3, 4, 5]),
    (" 02 - 04 , 0,2 ", [0, 2, 3, 4]),
    ("1000,999-1000", [999, 1000]), ("4-4", [4]),
])
def test_parse(text, expected):
    assert parse_ranges(text) == expected


@pytest.mark.parametrize("text", [
    ",", "1,", ",1", "3-1", "-1", "1--2", "1-2-3", "1.5",
    "x", "1001", "1-1001", "١", "+1", "1 2",
])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_ranges(text)


def test_preserved_formatter():
    assert format_number(12) == "12"
