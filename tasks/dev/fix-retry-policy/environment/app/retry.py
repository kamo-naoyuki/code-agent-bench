def retry_call(fn, attempts, retry_on, delay, sleep):
    for _ in range(attempts + 1):
        try:
            return fn()
        except Exception:
            sleep(delay)
    return None


def identity(value):
    return value
