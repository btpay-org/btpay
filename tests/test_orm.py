#
# Tests for the in-memory ORM
#
import pytest
import os, json, tempfile
from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import Text, Integer, Boolean, DecimalColumn, DateTimeColumn, TagsColumn, JsonColumn
from btpay.orm.engine import MemoryStore
from btpay.chrono import NOW, TIME_AGO
from decimal import Decimal


# ---- Test Models ----

class TestUser(BaseMixin, MemModel):
    email = Text(unique=True, index=True)
    name = Text()
    age = Integer(default=0)
    is_active = Boolean(default=True)
    balance = DecimalColumn()
    tags = TagsColumn()
    meta = JsonColumn()


class TestProduct(BaseMixin, MemModel):
    title = Text(required=True)
    price = DecimalColumn()
    category = Text(index=True)
    in_stock = Boolean(default=True)


# ---- CRUD Tests ----

class TestCRUD:
    def test_create_and_get(self):
        u = TestUser(email='alice@test.com', name='Alice', age=30)
        u.save()
        assert u.id is not None
        assert u.id == 1

        fetched = TestUser.get(u.id)
        assert fetched is not None
        assert fetched.email == 'alice@test.com'
        assert fetched.name == 'Alice'
        assert fetched.age == 30

    def test_bracket_syntax(self):
        u = TestUser(email='bob@test.com', name='Bob')
        u.save()
        fetched = TestUser[u.id]
        assert fetched.name == 'Bob'

    def test_update(self):
        u = TestUser(email='carol@test.com', name='Carol')
        u.save()
        pk = u.id

        u.name = 'Carol Updated'
        u.save()

        fetched = TestUser.get(pk)
        assert fetched.name == 'Carol Updated'
        assert fetched.id == pk       # same PK

    def test_delete(self):
        u = TestUser(email='dave@test.com', name='Dave')
        u.save()
        pk = u.id
        u.delete()
        assert TestUser.get(pk) is None

    def test_defaults(self):
        u = TestUser(email='eve@test.com')
        u.save()
        assert u.name is None
        assert u.age == 0
        assert u.is_active is True
        assert u.balance == Decimal('0')
        assert u.tags == set()

    def test_auto_increment(self):
        u1 = TestUser(email='a@test.com')
        u1.save()
        u2 = TestUser(email='b@test.com')
        u2.save()
        assert u2.id == u1.id + 1

    def test_get_nonexistent(self):
        assert TestUser.get(999) is None
        assert TestUser[999] is None

    def test_created_at_set(self):
        u = TestUser(email='f@test.com')
        u.save()
        assert u.created_at is not None
        assert u.updated_at is not None

    def test_unique_constraint(self):
        u1 = TestUser(email='unique@test.com')
        u1.save()
        u2 = TestUser(email='unique@test.com')
        with pytest.raises(ValueError, match="Unique constraint"):
            u2.save()


# ---- Query Tests ----

class TestQuery:
    def _seed(self):
        TestUser(email='a@x.com', name='Alice', age=25, is_active=True, tags={'vip', 'btc'}).save()
        TestUser(email='b@x.com', name='Bob', age=30, is_active=True, tags={'btc'}).save()
        TestUser(email='c@x.com', name='Carol', age=35, is_active=False, tags={'vip'}).save()

    def test_filter_exact(self):
        self._seed()
        results = TestUser.query.filter(name='Alice').all()
        assert len(results) == 1
        assert results[0].email == 'a@x.com'

    def test_filter_indexed(self):
        self._seed()
        results = TestUser.query.filter(email='b@x.com').all()
        assert len(results) == 1
        assert results[0].name == 'Bob'

    def test_filter_gt(self):
        self._seed()
        results = TestUser.query.filter(age__gt=28).all()
        assert len(results) == 2

    def test_filter_lt(self):
        self._seed()
        results = TestUser.query.filter(age__lt=30).all()
        assert len(results) == 1

    def test_filter_in(self):
        self._seed()
        results = TestUser.query.filter(age__in=[25, 35]).all()
        assert len(results) == 2

    def test_filter_contains_tags(self):
        self._seed()
        results = TestUser.query.filter(tags__contains='vip').all()
        assert len(results) == 2

    def test_filter_boolean(self):
        self._seed()
        results = TestUser.query.filter(is_active=True).all()
        assert len(results) == 2

    def test_exclude(self):
        self._seed()
        results = TestUser.query.exclude(name='Alice').all()
        assert len(results) == 2
        names = {r.name for r in results}
        assert 'Alice' not in names

    def test_order_by(self):
        self._seed()
        results = TestUser.query.order_by('age').all()
        assert results[0].age == 25
        assert results[-1].age == 35

    def test_order_by_desc(self):
        self._seed()
        results = TestUser.query.order_by('-age').all()
        assert results[0].age == 35

    def test_first(self):
        self._seed()
        result = TestUser.query.filter(name='Bob').first()
        assert result is not None
        assert result.name == 'Bob'

    def test_first_none(self):
        result = TestUser.query.filter(name='Nobody').first()
        assert result is None

    def test_count(self):
        self._seed()
        assert TestUser.query.count() == 3
        assert TestUser.query.filter(is_active=True).count() == 2

    def test_exists(self):
        self._seed()
        assert TestUser.query.filter(name='Alice').exists()
        assert not TestUser.query.filter(name='Nobody').exists()

    def test_get_by(self):
        self._seed()
        u = TestUser.get_by(email='a@x.com')
        assert u is not None
        assert u.name == 'Alice'

    def test_chained_filter(self):
        self._seed()
        results = TestUser.query.filter(is_active=True).filter(age__gte=30).all()
        assert len(results) == 1
        assert results[0].name == 'Bob'


# ---- Serialization Tests ----

class TestSerialization:
    def test_to_dict(self):
        u = TestUser(email='x@y.com', name='X', age=42, balance=Decimal('1.5'))
        u.save()
        d = u.to_dict()
        assert d['email'] == 'x@y.com'
        assert d['age'] == 42
        assert d['balance'] == '1.5'      # Decimal serialized to string
        assert d['id'] == u.id

    def test_from_dict(self):
        d = {'email': 'y@z.com', 'name': 'Y', 'age': 33, 'balance': '2.5'}
        u = TestUser.from_dict(d)
        assert u.email == 'y@z.com'
        assert u.balance == Decimal('2.5')

    def test_repr(self):
        u = TestUser(email='r@test.com', name='Repr')
        u.save()
        r = repr(u)
        assert 'TestUser' in r
        assert 'r@test.com' in r


# ---- Persistence Tests ----

class TestPersistence:
    def test_save_and_load(self):
        from btpay.orm.persistence import save_to_disk, load_from_disk

        u = TestUser(email='persist@test.com', name='Persist', age=99)
        u.save()
        p = TestProduct(title='Widget', price=Decimal('9.99'), category='tools')
        p.save()

        with tempfile.TemporaryDirectory() as tmpdir:
            save_to_disk(tmpdir)

            # Clear store
            store = MemoryStore()
            store.clear()

            assert TestUser.get(u.id) is None

            # Re-register models (normally done by metaclass)
            from btpay.orm.columns import Text as _T
            store.register_model('TestUser', TestUser._columns)
            store.register_model('TestProduct', TestProduct._columns)

            load_from_disk(tmpdir)

            loaded = TestUser.get(u.id)
            assert loaded is not None
            assert loaded.email == 'persist@test.com'
            assert loaded.age == 99

            loaded_p = TestProduct.get(p.id)
            assert loaded_p.title == 'Widget'
            assert loaded_p.price == Decimal('9.99')


# ---- Column Type Tests ----

class TestColumns:
    def test_decimal_column(self):
        u = TestUser(email='dec@test.com', balance=Decimal('123.456'))
        u.save()
        fetched = TestUser.get(u.id)
        assert fetched.balance == Decimal('123.456')

    def test_tags_column(self):
        u = TestUser(email='tag@test.com', tags={'a', 'b', 'c'})
        u.save()
        fetched = TestUser.get(u.id)
        assert fetched.tags == {'a', 'b', 'c'}

    def test_tags_from_string(self):
        u = TestUser(email='tag2@test.com', tags='x, y, z')
        u.save()
        fetched = TestUser.get(u.id)
        assert fetched.tags == {'x', 'y', 'z'}

    def test_json_column(self):
        u = TestUser(email='json@test.com', meta={'key': 'value', 'num': 42})
        u.save()
        fetched = TestUser.get(u.id)
        assert fetched.meta == {'key': 'value', 'num': 42}

    def test_datetime_column_default(self):
        u = TestUser(email='dt@test.com')
        u.save()
        # created_at should be set by default
        assert u.created_at != 0

    def test_boolean_column(self):
        u = TestUser(email='bool@test.com', is_active=False)
        u.save()
        fetched = TestUser.get(u.id)
        assert fetched.is_active is False

# EOF
