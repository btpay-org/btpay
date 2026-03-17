#
# Column descriptors for the in-memory ORM.
#
import datetime
from decimal import Decimal
import pendulum


class Column:
    '''Base column descriptor.'''

    def __init__(self, default=None, required=False, index=False, unique=False):
        self.name = None            # set by metaclass
        self.default = default
        self.required = required
        self.index = index or unique
        self.unique = unique

    def get_default(self):
        if callable(self.default):
            return self.default()
        return self.default

    def validate(self, value):
        if self.required and value is None:
            raise ValueError("Column '%s' is required" % self.name)
        return value

    def to_storage(self, value):
        return value

    def from_storage(self, value):
        return value


class Text(Column):
    def __init__(self, default=None, **kw):
        super().__init__(default=default, **kw)

    def validate(self, value):
        value = super().validate(value)
        if value is not None and not isinstance(value, str):
            value = str(value)
        return value


class Integer(Column):
    def __init__(self, default=0, **kw):
        super().__init__(default=default, **kw)

    def validate(self, value):
        value = super().validate(value)
        if value is not None:
            value = int(value)
        return value


class Boolean(Column):
    def __init__(self, default=False, **kw):
        super().__init__(default=default, **kw)

    def validate(self, value):
        value = super().validate(value)
        if value is not None:
            value = bool(value)
        return value


class Float(Column):
    def __init__(self, default=0.0, **kw):
        super().__init__(default=default, **kw)

    def validate(self, value):
        value = super().validate(value)
        if value is not None:
            value = float(value)
        return value


class DecimalColumn(Column):
    '''Stored as string in JSON, used as Decimal in Python.'''

    def __init__(self, default=None, **kw):
        if default is None:
            default = Decimal('0')
        super().__init__(default=default, **kw)

    def validate(self, value):
        value = super().validate(value)
        if value is not None and not isinstance(value, Decimal):
            value = Decimal(str(value))
        return value

    def to_storage(self, value):
        if isinstance(value, Decimal):
            return str(value)
        return value

    def from_storage(self, value):
        if value is not None and not isinstance(value, Decimal):
            return Decimal(str(value))
        return value


class DateTimeColumn(Column):
    '''
    Stored as ISO 8601 string. None means unset.
    '''

    def __init__(self, default=None, **kw):
        super().__init__(default=default, **kw)

    def get_default(self):
        if callable(self.default):
            return self.default()
        return None

    def validate(self, value):
        value = super().validate(value)
        if value is None or value == 0:
            return None
        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, str):
            return pendulum.parse(value)
        if isinstance(value, (int, float)):
            # Legacy: treat as unix timestamp
            return pendulum.from_timestamp(value)
        return value

    def to_storage(self, value):
        if isinstance(value, datetime.datetime):
            return value.isoformat()
        return None

    def from_storage(self, value):
        if value is None or value == 0:
            return None
        if isinstance(value, str):
            return pendulum.parse(value)
        if isinstance(value, (int, float)):
            # Legacy: float timestamps from old data
            return pendulum.from_timestamp(value)
        return value


class JsonColumn(Column):
    '''Stores arbitrary JSON-serializable data (dicts, lists).'''

    def __init__(self, default=None, **kw):
        super().__init__(default=default, **kw)

    def get_default(self):
        if self.default is None:
            return None
        if callable(self.default):
            return self.default()
        # deep copy for mutable defaults
        import copy
        return copy.deepcopy(self.default)


class TagsColumn(Column):
    '''Stores a set of strings.'''

    def __init__(self, default=None, **kw):
        super().__init__(default=default, **kw)

    def get_default(self):
        return set()

    def validate(self, value):
        value = super().validate(value)
        if value is None:
            return set()
        if isinstance(value, (list, tuple)):
            return set(value)
        if isinstance(value, str):
            return set(v.strip() for v in value.split(',') if v.strip())
        return set(value)

    def to_storage(self, value):
        if isinstance(value, set):
            return sorted(list(value))
        if value is None:
            return []
        return list(value)

    def from_storage(self, value):
        if isinstance(value, list):
            return set(value)
        if value is None:
            return set()
        return set(value)

# EOF
