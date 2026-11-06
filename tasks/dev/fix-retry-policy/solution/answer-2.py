def retry_call(fn, attempts, retry_on, delay, sleep):
    if attempts <= 0:
        raise ValueError("Positive attempt budget required")
    remaining = attempts
    while True:
        try:
            return fn()
        except retry_on:
            remaining -= 1
            if remaining == 0:
                raise
            sleep(delay)


def identity(value):
    return value
