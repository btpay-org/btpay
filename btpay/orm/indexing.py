#
# In-memory index structures for fast lookups
#

class HashIndex:
    '''Index mapping column values to sets of PKs. For non-unique indexed columns.'''

    def __init__(self):
        self._map = {}      # value -> set of pks

    def add(self, value, pk):
        if value not in self._map:
            self._map[value] = set()
        self._map[value].add(pk)

    def remove(self, value, pk):
        if value in self._map:
            self._map[value].discard(pk)
            if not self._map[value]:
                del self._map[value]

    def get(self, value):
        "Return set of PKs matching this value"
        return self._map.get(value, set())

    def all_pks(self):
        result = set()
        for pks in self._map.values():
            result.update(pks)
        return result

    def clear(self):
        self._map.clear()


class UniqueIndex:
    '''Index mapping column values to single PKs. For unique columns.'''

    def __init__(self):
        self._map = {}      # value -> pk

    def add(self, value, pk):
        if value in self._map and self._map[value] != pk:
            raise ValueError("Unique constraint violated: %r already exists" % value)
        self._map[value] = pk

    def remove(self, value, pk):
        if value in self._map and self._map[value] == pk:
            del self._map[value]

    def get(self, value):
        "Return PK or None"
        return self._map.get(value)

    def clear(self):
        self._map.clear()

# EOF
