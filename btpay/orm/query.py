#
# QuerySet — chained filtering, sorting, terminal operations.
#
# Usage:
#   Model.query.filter(status='paid').order_by('-created_at').all()
#   Model.query.filter(amount__gt=100).first()
#   Model.query.filter(tags__contains='VIP').count()
#
from btpay.orm.engine import MemoryStore


class QuerySet:
    def __init__(self, model_cls):
        self._model_cls = model_cls
        self._filters = []
        self._excludes = []
        self._order_field = None
        self._order_reverse = False

    def _clone(self):
        qs = QuerySet(self._model_cls)
        qs._filters = list(self._filters)
        qs._excludes = list(self._excludes)
        qs._order_field = self._order_field
        qs._order_reverse = self._order_reverse
        return qs

    def filter(self, **kwargs):
        qs = self._clone()
        qs._filters.append(kwargs)
        return qs

    def exclude(self, **kwargs):
        qs = self._clone()
        qs._excludes.append(kwargs)
        return qs

    def order_by(self, field):
        qs = self._clone()
        if field.startswith('-'):
            qs._order_field = field[1:]
            qs._order_reverse = True
        else:
            qs._order_field = field
            qs._order_reverse = False
        return qs

    def _match_row(self, row):
        '''Check if a row matches all filter/exclude conditions.'''
        for filt in self._filters:
            if not self._match_conditions(row, filt):
                return False
        for excl in self._excludes:
            if self._match_conditions(row, excl):
                return False
        return True

    def _match_conditions(self, row, conditions):
        '''Check if a row matches a set of conditions (AND).'''
        for key, value in conditions.items():
            if '__' in key:
                col_name, op = key.rsplit('__', 1)
            else:
                col_name, op = key, 'exact'

            row_val = row.get(col_name)

            if op == 'exact':
                if row_val != value:
                    return False
            elif op == 'gt':
                if row_val is None or row_val <= value:
                    return False
            elif op == 'gte':
                if row_val is None or row_val < value:
                    return False
            elif op == 'lt':
                if row_val is None or row_val >= value:
                    return False
            elif op == 'lte':
                if row_val is None or row_val > value:
                    return False
            elif op == 'in':
                if row_val not in value:
                    return False
            elif op == 'contains':
                # for sets/tags
                if isinstance(row_val, (set, list)):
                    if value not in row_val:
                        return False
                elif isinstance(row_val, str):
                    if value not in row_val:
                        return False
                else:
                    return False
            elif op == 'iexact':
                if not isinstance(row_val, str) or row_val.lower() != str(value).lower():
                    return False
            elif op == 'startswith':
                if not isinstance(row_val, str) or not row_val.startswith(str(value)):
                    return False
            elif op == 'ne':
                if row_val == value:
                    return False
            else:
                raise ValueError("Unknown filter operator: %s" % op)

        return True

    def _execute(self):
        '''Execute the query and return list of (pk, row_dict) tuples.'''
        store = MemoryStore()
        model_name = self._model_cls.__name__

        # Try to use engine-level filter for simple exact-match filters
        if len(self._filters) == 1 and len(self._excludes) == 0:
            filt = self._filters[0]
            simple = all('__' not in k for k in filt.keys())
            if simple:
                results = store.filter(model_name, **filt)
                return results

        # Fallback: scan all rows
        all_rows = store.all(model_name)
        results = [(pk, row) for pk, row in all_rows if self._match_row(row)]
        return results

    def _sort_results(self, results):
        if self._order_field:
            def sort_key(item):
                val = item[1].get(self._order_field)
                if val is None:
                    return (1, '')       # nulls last
                return (0, val)
            results.sort(key=sort_key, reverse=self._order_reverse)
        return results

    def _to_instances(self, results):
        results = self._sort_results(results)
        instances = []
        for pk, row in results:
            inst = self._model_cls._from_row(pk, row)
            instances.append(inst)
        return instances

    def all(self):
        '''Execute and return list of model instances.'''
        results = self._execute()
        return self._to_instances(results)

    def first(self):
        '''Execute and return first match or None.'''
        instances = self.all()
        return instances[0] if instances else None

    def count(self):
        '''Execute and return count of matches.'''
        return len(self._execute())

    def exists(self):
        '''Execute and return True if any matches.'''
        return self.count() > 0

# EOF
