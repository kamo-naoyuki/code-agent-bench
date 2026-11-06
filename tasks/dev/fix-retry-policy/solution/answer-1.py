def retry_call(fn, attempts, retry_on, delay, sleep):
    if attempts < 1:
        raise ValueError("Positive attempt budget required")
    for index in range(attempts):
        try:
            return fn()
        except retry_on:
            if index == attempts - 1:
                raise
            sleep(delay)


def identity(value):
    return value
