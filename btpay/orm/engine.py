#
# The central in-memory data store.
# All model data lives in Python dicts, protected by a reentrant lock.
#
import threading
from btpay.misc import singleton
from btpay.orm.indexing import HashIndex, UniqueIndex


@singleton
class MemoryStore:
    '''
    Central in-memory storage engine. Singleton.

    _tables:    model_name -> { pk: row_dict }
    _sequences: model_name -> next_pk (int)
    _indexes:   (model_name, col_name) -> HashIndex or UniqueIndex
    _schemas:   model_name -> { col_name: Column instance }
    '''

    def __init__(self):
        self._tables = {}
        self._sequences = {}
        self._indexes = {}
        self._schemas = {}
        self._lock = threading.RLock()

    def register_model(self, model_name, columns):
        '''Called by MemModelMeta to register a new model class.'''
        with self._lock:
            if model_name not in self._tables:
                self._tables[model_name] = {}
                self._sequences[model_name] = 1
                self._schemas[model_name] = columns

                # Create indexes for indexed columns
                for col_name, col in columns.items():
                    if col.unique:
                        self._indexes[(model_name, col_name)] = UniqueIndex()
                    elif col.index:
                        self._indexes[(model_name, col_name)] = HashIndex()

    def next_pk(self, model_name):
        with self._lock:
            pk = self._sequences[model_name]
            self._sequences[model_name] = pk + 1
            return pk

    def insert(self, model_name, pk, data):
        with self._lock:
            self._tables[model_name][pk] = data.copy()
            self._update_indexes(model_name, pk, None, data)

    def update(self, model_name, pk, data):
        with self._lock:
            old_data = self._tables[model_name].get(pk)
            self._tables[model_name][pk] = data.copy()
            self._update_indexes(model_name, pk, old_data, data)

    def delete(self, model_name, pk):
        with self._lock:
            old_data = self._tables[model_name].pop(pk, None)
            if old_data:
                self._remove_indexes(model_name, pk, old_data)

    def get(self, model_name, pk):
        with self._lock:
            row = self._tables.get(model_name, {}).get(pk)
            return row.copy() if row else None

    def all(self, model_name):
        with self._lock:
            table = self._tables.get(model_name, {})
            return [(pk, row.copy()) for pk, row in table.items()]

    def filter(self, model_name, **conditions):
        '''Simple filtered query. Returns list of (pk, row_dict).'''
        with self._lock:
            table = self._tables.get(model_name, {})

            # Try to use index for first equality condition
            candidate_pks = None
            remaining = {}

            for col_name, value in conditions.items():
                idx_key = (model_name, col_name)
                if idx_key in self._indexes and isinstance(self._indexes[idx_key], (HashIndex, UniqueIndex)):
                    idx = self._indexes[idx_key]
                    if isinstance(idx, UniqueIndex):
                        pk = idx.get(value)
                        candidate_pks = {pk} if pk is not None else set()
                    else:
                        candidate_pks = idx.get(value).copy()
                    # remaining conditions still need checking
                    remaining = {k: v for k, v in conditions.items() if k != col_name}
                    break
                else:
                    remaining[col_name] = value

            if candidate_pks is None:
                # no index hit, scan all rows
                candidate_pks = set(table.keys())

            results = []
            for pk in candidate_pks:
                row = table.get(pk)
                if row is None:
                    continue
                match = True
                for col_name, value in remaining.items():
                    if row.get(col_name) != value:
                        match = False
                        break
                if match:
                    results.append((pk, row.copy()))

            return results

    def count(self, model_name):
        with self._lock:
            return len(self._tables.get(model_name, {}))

    def _update_indexes(self, model_name, pk, old_data, new_data):
        '''Update indexes after insert or update.'''
        schema = self._schemas.get(model_name, {})
        for col_name, col in schema.items():
            idx_key = (model_name, col_name)
            if idx_key not in self._indexes:
                continue
            idx = self._indexes[idx_key]
            old_val = old_data.get(col_name) if old_data else None
            new_val = new_data.get(col_name)

            if old_val == new_val:
                continue

            if old_val is not None and old_data is not None:
                if isinstance(idx, UniqueIndex):
                    idx.remove(old_val, pk)
                else:
                    idx.remove(old_val, pk)

            if new_val is not None:
                idx.add(new_val, pk)

    def _remove_indexes(self, model_name, pk, data):
        '''Remove from indexes on delete.'''
        schema = self._schemas.get(model_name, {})
        for col_name, col in schema.items():
            idx_key = (model_name, col_name)
            if idx_key not in self._indexes:
                continue
            idx = self._indexes[idx_key]
            val = data.get(col_name)
            if val is not None:
                idx.remove(val, pk)

    def clear(self):
        '''Clear all data. For testing.'''
        with self._lock:
            for name in self._tables:
                self._tables[name].clear()
                self._sequences[name] = 1
            for idx in self._indexes.values():
                idx.clear()

    def get_table_data(self, model_name):
        '''Get all data for a table. For persistence.'''
        with self._lock:
            table = self._tables.get(model_name, {})
            return {str(pk): row.copy() for pk, row in table.items()}

    def get_sequence(self, model_name):
        with self._lock:
            return self._sequences.get(model_name, 1)

    def set_sequence(self, model_name, val):
        with self._lock:
            self._sequences[model_name] = val

    def load_table_data(self, model_name, data, model_cls):
        '''Load data from persistence. Called at startup.'''
        with self._lock:
            max_pk = 0
            for pk_str, row in data.items():
                pk = int(pk_str)
                if pk > max_pk:
                    max_pk = pk

                # Convert stored values back through column from_storage
                schema = self._schemas.get(model_name, {})
                for col_name, col in schema.items():
                    if col_name in row:
                        row[col_name] = col.from_storage(row[col_name])

                self._tables[model_name][pk] = row
                self._update_indexes(model_name, pk, None, row)

            self._sequences[model_name] = max_pk + 1

    def registered_models(self):
        with self._lock:
            return list(self._tables.keys())

# EOF
