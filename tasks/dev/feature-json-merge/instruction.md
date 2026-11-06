# Recursive JSON merge patch

Implement `merge_patch(target, patch)` in `/app/merge.py`.
Inputs are JSON-compatible Python values. If patch is not a dictionary,
return a deep copy of patch, replacing the whole target.
If patch is a dictionary, start with a deep copy of target when target is a
dictionary, otherwise an empty dictionary. For each patch entry: `None` deletes
the key if present; any other value is recursively merged with the old value.
Arrays are replaced, not concatenated. Never mutate or share mutable
containers with either input. Missing keys set to `None` remain absent.
Keep `is_object(value)` true only for dictionaries. Change only `merge.py`.
