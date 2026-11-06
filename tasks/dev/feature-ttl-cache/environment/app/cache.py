class TTLCache:
    def __init__(self):
        self.entries = {}

    def set(self, key, value, ttl, now):
        self.entries[key] = value

    def get(self, key, now, default=None):
        return self.entries.get(key, default)

    def purge(self, now):
        return 0

    def __len__(self):
        return len(self.entries)
