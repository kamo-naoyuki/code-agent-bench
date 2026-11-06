def paginate(items, page, size):
    return {"items": items[page * size:(page + 1) * size],
            "page": page, "pages": len(items) // size, "total": len(items)}


def first(items):
    return items[0] if items else None
