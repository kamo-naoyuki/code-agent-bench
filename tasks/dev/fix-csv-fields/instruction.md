# Parse one CSV record

Fix `parse_record(text)` in `/app/csv_fields.py`, returning a list of strings.
The input is one comma-delimited CSV record with no newline characters.
Support quoted fields containing commas, doubled quotes inside quoted fields,
empty fields and trailing empty fields. Preserve whitespace in fields.
The empty string represents one empty field: `[""]`.
Malformed quoting must raise `ValueError`. A quote may appear only at the start
of a field; after a closing quote only a comma or end of record is valid.
Examples: `a,"b,c",` -> `["a", "b,c", ""]`;
`"a""b"` -> `['a"b']`. Keep `join_plain(["a", "b"]) == "a,b"`.
Change only `csv_fields.py`. Standard-library solutions are allowed.
