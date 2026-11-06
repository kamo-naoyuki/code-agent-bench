def group_records(records, field):
    records.sort(key=lambda row: str(row.get(field)))
    result = {}
    for row in records:
        key = row.pop(field, None)
        result.setdefault(key, []).append(row)
    return list(result.items())


def record_count(records):
    return len(records)
