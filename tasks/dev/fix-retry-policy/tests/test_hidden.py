import sys
sys.path.insert(0, "/submission")
from retry import identity, retry_call
import pytest


def test_success_and_retry_order():
    events = []
    def fn():
        events.append("call")
        if events.count("call") < 3:
            raise ValueError("temporary")
        return 42
    assert retry_call(fn, 3, (ValueError,), 0.5, lambda d: events.append(d)) == 42
    assert events == ["call", 0.5, "call", 0.5, "call"]


def test_exhaustion_preserves_exception():
    events = []
    error = ValueError("same instance")
    def fn():
        events.append("call")
        raise error
    with pytest.raises(ValueError) as caught:
        retry_call(fn, 2, (ValueError,), 1, lambda d: events.append(d))
    assert caught.value is error
    assert events == ["call", 1, "call"]


def test_nonmatching_and_single_attempt():
    for retries in [(ValueError,), ()]:
        events = []
        def fn():
            events.append("call")
            raise TypeError("permanent")
        with pytest.raises(TypeError):
            retry_call(fn, 3, retries, 1, events.append)
        assert events == ["call"]
    sleeps = []
    assert retry_call(lambda: None, 1, (), 1, sleeps.append) is None
    assert sleeps == []


@pytest.mark.parametrize("attempts", [0, -1])
def test_invalid(attempts):
    events = []
    with pytest.raises(ValueError):
        retry_call(lambda: events.append("call"), attempts, (), 1, events.append)
    assert events == []


def test_preserved_identity():
    value = []
    assert identity(value) is value
