#
# Base model class for the in-memory ORM.
#
# - Model[pk] to fetch by primary key
# - .save() / .delete() / .reload()
# - .query.filter(...).all() / .first() / .count()
# - BaseMixin with created_at, updated_at, ref_number, short_token
#
from hashlib import sha256
from btpay.orm.engine import MemoryStore
from btpay.orm.columns import Column, DateTimeColumn
from btpay.orm.query import QuerySet
from btpay.chrono import NOW

# Registry of all model classes (for persistence, refnums, etc.)
_model_registry = {}


class MemModelMeta(type):
    '''Metaclass that collects Column definitions and registers models.'''

    def __new__(mcs, name, bases, namespace):
        columns = {}

        # Collect columns from base classes
        for base in bases:
            if hasattr(base, '_columns'):
                columns.update(base._columns)
            # Also scan base class attrs for Column instances (e.g. BaseMixin)
            for attr_name in dir(base):
                attr_val = getattr(base, attr_name, None)
                if isinstance(attr_val, Column) and attr_name not in columns:
                    attr_val.name = attr_name
                    columns[attr_name] = attr_val

        # Collect columns from this class
        for attr_name, attr_val in namespace.items():
            if isinstance(attr_val, Column):
                attr_val.name = attr_name
                columns[attr_name] = attr_val

        namespace['_columns'] = columns
        cls = super().__new__(mcs, name, bases, namespace)

        # Register with the engine (skip the base MemModel itself)
        if name != 'MemModel' and any(hasattr(b, '_columns') for b in bases):
            store = MemoryStore()
            store.register_model(name, columns)
            _model_registry[name] = cls

        return cls

    def __getitem__(cls, pk):
        '''Allow Model[pk] syntax to fetch by primary key.'''
        return cls.get(pk)


class MemModel(metaclass=MemModelMeta):
    '''
    Base model. All data models inherit from this.

    Usage:
        class User(BaseMixin, MemModel):
            email = Text(unique=True, index=True)
            name = Text()

        u = User(email='a@b.com', name='Alice')
        u.save()
        print(User[u.id].name)
    '''

    def __init__(self, **kwargs):
        self.id = None
        self._dirty = True

        # Set defaults then overrides
        for col_name, col in self._columns.items():
            val = kwargs.get(col_name, col.get_default())
            object.__setattr__(self, col_name, val)

        # Set any extra kwargs not in columns
        for k, v in kwargs.items():
            if k not in self._columns:
                object.__setattr__(self, k, v)

    def save(self):
        '''Insert or update this instance in the store.'''
        store = MemoryStore()
        model_name = self.__class__.__name__
        import logging as _log
        _log.getLogger('btpay.orm.model').debug(
            "save %s: store id=%x, table rows=%d" % (
                model_name, id(store), len(store._tables.get(model_name, {}))))

        # Run before-save hook
        if hasattr(self, '_before_save'):
            self._before_save()

        # Validate and collect data
        data = {}
        for col_name, col in self._columns.items():
            val = getattr(self, col_name, col.get_default())
            val = col.validate(val)
            data[col_name] = val
            object.__setattr__(self, col_name, val)

        if self.id is None:
            # Insert
            self.id = store.next_pk(model_name)
            store.insert(model_name, self.id, data)
            if hasattr(self, '_after_insert'):
                self._after_insert()
        else:
            # Update
            store.update(model_name, self.id, data)

        self._dirty = False
        # Clear cached properties
        self.__dict__.pop('ref_number', None)
        self.__dict__.pop('short_token', None)
        return self

    def delete(self):
        '''Remove this instance from the store.'''
        if self.id is None:
            return
        store = MemoryStore()
        store.delete(self.__class__.__name__, self.id)
        self.id = None

    def reload(self):
        '''Refresh from the store.'''
        if self.id is None:
            return self
        store = MemoryStore()
        data = store.get(self.__class__.__name__, self.id)
        if data:
            for col_name in self._columns:
                if col_name in data:
                    object.__setattr__(self, col_name, data[col_name])
        return self

    @classmethod
    def get(cls, pk):
        '''Fetch by primary key. Returns instance or None.'''
        if pk is None:
            return None
        store = MemoryStore()
        data = store.get(cls.__name__, int(pk))
        if data is None:
            return None
        return cls._from_row(int(pk), data)

    @classmethod
    def get_by(cls, **kwargs):
        '''Fetch first match. Returns instance or None.'''
        return cls.query.filter(**kwargs).first()

    @classmethod
    def _from_row(cls, pk, data):
        '''Create instance from stored row data.'''
        inst = object.__new__(cls)
        inst.id = pk
        inst._dirty = False
        for col_name, col in cls._columns.items():
            val = data.get(col_name, col.get_default())
            object.__setattr__(inst, col_name, val)
        return inst

    @property
    def query(self):
        '''Instance access to queryset (also available as class property via metaclass).'''
        return QuerySet(self.__class__)

    # Make query work as a class-level property too
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def to_dict(self):
        '''Serialize to plain dict (for JSON persistence).'''
        data = {'id': self.id}
        for col_name, col in self._columns.items():
            val = getattr(self, col_name)
            data[col_name] = col.to_storage(val)
        return data

    @classmethod
    def from_dict(cls, d):
        '''Deserialize from dict.'''
        kwargs = {}
        for col_name, col in cls._columns.items():
            if col_name in d:
                kwargs[col_name] = col.from_storage(d[col_name])
        inst = cls(**kwargs)
        if 'id' in d and d['id'] is not None:
            inst.id = d['id']
        return inst

    def __repr__(self):
        cols = []
        for col_name in self._columns:
            if hasattr(self, col_name):
                cols.append('%s=%r' % (col_name, getattr(self, col_name)))
        return '%s(id=%r, %s)' % (self.__class__.__name__, self.id, ', '.join(cols))


# Make .query work as a class attribute (returns fresh QuerySet)
_orig_getattr = MemModelMeta.__getattribute__

def _class_getattr(cls, name):
    if name == 'query':
        return QuerySet(cls)
    return _orig_getattr(cls, name)

MemModelMeta.__getattribute__ = _class_getattr


class BaseMixin:
    '''
    Common columns for all BTPay models.
    Must be listed before MemModel in inheritance.
    '''
    created_at = DateTimeColumn(default=NOW)
    updated_at = DateTimeColumn(default=0)

    def _before_save(self):
        self.updated_at = NOW()

    @property
    def short_token(self):
        '''One-way hash token for this instance. Cached in __dict__.'''
        if 'short_token' not in self.__dict__:
            msg = (self.__class__.__name__ + str(self.id)).encode('utf-8')
            val = sha256(sha256(msg).digest()).hexdigest()[0:24]
            self.__dict__['short_token'] = val
        return self.__dict__['short_token']

    @property
    def ref_number(self):
        '''Encrypted reference number. Cached in __dict__.'''
        if 'ref_number' not in self.__dict__:
            from btpay.security.refnums import ReferenceNumbers
            val = ReferenceNumbers().pack(self)
            self.__dict__['ref_number'] = val
        return self.__dict__['ref_number']

    @property
    def summary(self):
        return repr(self)


def get_model_registry():
    '''Return dict of model_name -> model_class.'''
    return dict(_model_registry)

# EOF
