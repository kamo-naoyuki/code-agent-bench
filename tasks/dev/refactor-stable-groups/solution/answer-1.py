def group_records(records, field):
    groups = {}
    for record in records:
        groups.setdefault(record.get(field), []).append(record)
    return list(groups.items())


def record_count(records):
    return len(records)
