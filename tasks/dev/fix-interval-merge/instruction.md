# Merge closed integer intervals

Fix `merge_intervals(intervals)` in `/app/intervals.py`.
Input is a list of `(start, end)` integer pairs with `start <= end`.
Return a list of tuples sorted by start, merging intervals that overlap,
including shared endpoints. Do not merge merely adjacent intervals:
`[(1, 2), (3, 4)]` stays separate. Empty input returns `[]`.
Do not mutate the input list. Duplicate and negative intervals are supported.
Keep `interval_length((start, end))` returning `end - start + 1`.
Change only `intervals.py`.
