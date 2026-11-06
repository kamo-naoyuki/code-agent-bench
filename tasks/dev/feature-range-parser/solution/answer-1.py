import re


def parse_ranges(text):
    if not text.strip():
        return []
    values = set()
    for token in text.split(","):
        match = re.fullmatch(r"\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?", token)
        if not match:
            raise ValueError("Invalid token")
        start = int(match[1])
        end = int(match[2]) if match[2] is not None else start
        if not 0 <= start <= end <= 1000:
            raise ValueError("Invalid bounds")
        values.update(range(start, end + 1))
    return sorted(values)


def format_number(value):
    return str(value)
