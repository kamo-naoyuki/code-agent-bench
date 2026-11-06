def merge_intervals(intervals):
    pending = sorted(intervals, reverse=True)
    result = []
    while pending:
        start, end = pending.pop()
        while pending and pending[-1][0] <= end:
            _, next_end = pending.pop()
            end = max(end, next_end)
        result.append((start, end))
    return result


def interval_length(interval):
    start, end = interval
    return end + 1 - start
