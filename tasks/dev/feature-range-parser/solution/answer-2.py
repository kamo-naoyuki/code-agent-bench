def parse_ranges(text):
    if not text.strip():
        return []
    found = [False] * 1001
    for token in text.split(","):
        parts = [part.strip() for part in token.strip().split("-")]
        if len(parts) not in (1, 2) or any(
            not part or any(char not in "0123456789" for char in part) for part in parts
        ):
            raise ValueError("Invalid token")
        start, end = int(parts[0]), int(parts[-1])
        if not 0 <= start <= end <= 1000:
            raise ValueError("Invalid bounds")
        for number in range(start, end + 1):
            found[number] = True
    return [number for number, present in enumerate(found) if present]


def format_number(value):
    return str(value)
