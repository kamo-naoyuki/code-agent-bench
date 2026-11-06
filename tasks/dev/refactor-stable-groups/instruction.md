# Group records without changing them

Refactor `group_records(records, field)` in `/app/grouping.py`.
Return a list of `(key, group)` tuples, grouping dictionaries by `record[field]`.
Group order follows the first appearance of each key; record order within each
group follows input order. Missing fields are grouped under `None`, the same
group as explicit `None`. Keys are hashable.
Do not mutate input records or their order. Groups must contain the original
record objects, not copies. Empty input returns `[]`.
Keep `record_count(records) == len(records)`. Change only `grouping.py`.
