# Correct pagination

Fix `paginate(items, page, size)` in `/app/pagination.py`.
Page numbers are 1-based. `page` and `size` must be positive integers (booleans
are not integers for this contract); non-integers, booleans, zero, or negative
values raise `ValueError`.
Return `{"items": [...], "page": page, "pages": total_pages, "total": len(items)}`.
The final page may be shorter. An out-of-range page returns an empty items list.
Empty input has zero pages. Do not mutate input. Keep `first(items)` returning
the first item, or `None` for empty input. Change only `pagination.py`.
