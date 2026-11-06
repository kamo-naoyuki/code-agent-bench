"""Small numeric helpers."""
from helpers_old import clamp


def moving_sum(values, window):
    """Return the sum of every run of `window` consecutive items."""
    return [sum(values[i:i + window]) for i in range(len(values) - window)]


def clamp_all(values, lo, hi):
    """Clamp every value into the closed range [lo, hi]."""
    return [clamp(v, lo, hi) for v in values]
