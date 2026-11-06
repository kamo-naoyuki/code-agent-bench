import json


def merge_patch(target, patch):
    def apply(current, changes):
        if not isinstance(changes, dict):
            return changes
        if not isinstance(current, dict):
            current = {}
        for key in changes:
            if changes[key] is None:
                current.pop(key, None)
            else:
                current[key] = apply(current.get(key), changes[key])
        return current
    return apply(json.loads(json.dumps(target)), json.loads(json.dumps(patch)))


def is_object(value):
    return isinstance(value, dict)
