# Retry only within the attempt budget

Fix `retry_call(fn, attempts, retry_on, delay, sleep)` in `/app/retry.py`.
Call zero-argument `fn`, returning its first successful result.
`attempts` is the maximum total number of calls, including the first; if less
than 1 raise `ValueError` without calling fn.
`retry_on` is a tuple of exception classes. Retry only matching exceptions.
Re-raise the last exception unchanged when no attempts remain; nonmatching
exceptions propagate immediately. Call `sleep(delay)` exactly once between
retryable failed attempts, never before the first or after the final call.
Do not call the real system sleep; use the injected callable.
Keep `identity(value)` returning that same object. Change only `retry.py`.
