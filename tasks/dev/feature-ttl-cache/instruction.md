# Expiring cache with explicit time

Implement `TTLCache` in `/app/cache.py`.
`set(key, value, ttl, now)` stores a value until `now + ttl`.
`get(key, now, default=None)` returns the value only when `now < expiry`,
otherwise the default. Expired entries may be removed.
`ttl <= 0` removes an existing key and stores nothing.
Setting a key again replaces its value and expiry.
`purge(now)` removes all expired entries and returns the number removed by that
call. `len(cache)` counts currently stored entries, including expired entries
not yet removed by `get` or `purge`.
Time is supplied numerically by the caller; never read the system clock or sleep.
Keys are strings; values may be any Python object, including `None`.
Change only `cache.py`.
