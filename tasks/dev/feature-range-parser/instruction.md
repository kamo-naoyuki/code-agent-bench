# Parse integer selections

Implement `parse_ranges(text)` in `/app/ranges.py`.
Accept comma-separated nonnegative integers and inclusive ranges such as
`"1,3-5,4"` -> `[1, 3, 4, 5]`. Ignore whitespace around tokens and around the
hyphen, accept leading zeroes, remove duplicates and sort numerically.
An empty or whitespace-only string returns `[]`.
Reject empty tokens, negative numbers, descending ranges, non-ASCII digits,
non-integers and extra hyphens with `ValueError`.
Each endpoint must be between 0 and 1000 inclusive.
Keep `format_number(value)` returning `str(value)`. Change only `ranges.py`.
