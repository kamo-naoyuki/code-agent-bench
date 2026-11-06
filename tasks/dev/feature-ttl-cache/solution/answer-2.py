class TTLCache:
    def __init__(self):
        self.values = {}
        self.expiry = {}

    def _remove(self, key):
        self.values.pop(key, None)
        self.expiry.pop(key, None)

    def set(self, key, value, ttl, now):
        self._remove(key)
        if ttl > 0:
            self.values[key] = value
            self.expiry[key] = now + ttl

    def get(self, key, now, default=None):
        if key in self.expiry and now >= self.expiry[key]:
            self._remove(key)
        return self.values.get(key, default)

    def purge(self, now):
        before = len(self)
        for key in list(self.expiry):
            if now >= self.expiry[key]:
                self._remove(key)
        return before - len(self)

    def __len__(self):
        return len(self.values)
