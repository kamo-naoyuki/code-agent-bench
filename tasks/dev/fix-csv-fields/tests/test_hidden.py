import sys
sys.path.insert(0, "/submission")
from csv_fields import join_plain, parse_record
import pytest


@pytest.mark.parametrize("text,expected", [
    ("", [""]), ("a,b", ["a", "b"]), (",,", ["", "", ""]),
    ('a,"b,c",', ["a", "b,c", ""]), ('"a""b"', ['a"b']),
    ('"", a ', ["", " a "]), ('"x,y","z""q"', ["x,y", 'z"q']),
    ('" a "', [" a "]),
])
def test_parse(text, expected):
    assert parse_record(text) == expected


@pytest.mark.parametrize("text", ['"open', 'a"b', '"closed"x', '"a",b"'])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_record(text)


def test_preserved_join():
    assert join_plain(["a", "b"]) == "a,b"
