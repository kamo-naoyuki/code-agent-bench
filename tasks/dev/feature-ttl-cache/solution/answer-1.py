class TTLCache:
    def __init__(self):
        self.entries = {}

    def set(self, key, value, ttl, now):
        if ttl <= 0:
            self.entries.pop(key, None)
        else:
            self.entries[key] = (value, now + ttl)

    def get(self, key, now, default=None):
        if key not in self.entries:
            return default
        value, expiry = self.entries[key]
        if now >= expiry:
            del self.entries[key]
            return default
        return value

    def purge(self, now):
        expired = [key for key, (_, expiry) in self.entries.items() if now >= expiry]
        for key in expired:
            del self.entries[key]
        return len(expired)

    def __len__(self):
        return len(self.entries)
