def paginate(items, page, size):
    if type(page) is not int or type(size) is not int or page <= 0 or size <= 0:
        raise ValueError("Positive integer page and size required")
    total = len(items)
    start = (page - 1) * size
    return {"items": list(items[start:start + size]), "page": page,
            "pages": (total + size - 1) // size, "total": total}


def first(items):
    return items[0] if items else None
