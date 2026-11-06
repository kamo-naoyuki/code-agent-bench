def group_records(records, field):
    result = []
    positions = {}
    for record in records:
        key = record.get(field)
        if key not in positions:
            positions[key] = len(result)
            result.append((key, []))
        result[positions[key]][1].append(record)
    return result


def record_count(records):
    return len(records)
