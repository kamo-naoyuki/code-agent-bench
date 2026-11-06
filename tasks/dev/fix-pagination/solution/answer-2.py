def paginate(items, page, size):
    if type(page) is not int or type(size) is not int or page <= 0 or size <= 0:
        raise ValueError("Positive integer page and size required")
    pages, remainder = divmod(len(items), size)
    selected = [value for index, value in enumerate(items) if index // size == page - 1]
    return {"items": selected, "page": page, "pages": pages + bool(remainder),
            "total": len(items)}


def first(items):
    return next(iter(items), None)
