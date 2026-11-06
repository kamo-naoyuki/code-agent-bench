"""Helper functions used by calc.py."""


def clamp(value, lo, hi):
    """Clamp `value` into the closed range [lo, hi]."""
    return max(lo, min(value, hi))
