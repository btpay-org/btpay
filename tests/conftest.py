import os
import shutil
import pytest
from btpay.orm.engine import MemoryStore

TEST_DATA_DIR = '/tmp/btpay_test'


# Use fast argon2 parameters during tests (production uses time_cost=3, memory_cost=65536)
import btpay.security.hashing as _hashing_mod
from argon2 import PasswordHasher as _PH
_hashing_mod._hasher = _PH(time_cost=1, memory_cost=1024, parallelism=1)


@pytest.fixture(autouse=True)
def clean_store():
    '''Clear the ORM store and test data dir before each test.'''
    # Remove stale test data so create_app doesn't load old records
    if os.path.exists(TEST_DATA_DIR):
        shutil.rmtree(TEST_DATA_DIR)

    store = MemoryStore()
    store.clear()
    yield store
    store.clear()

    # Clean up after test too
    if os.path.exists(TEST_DATA_DIR):
        shutil.rmtree(TEST_DATA_DIR)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    '''Reset all rate limiters between tests.'''
    yield
    try:
        from btpay.auth.views import _limiter
        _limiter._windows.clear()
    except ImportError:
        pass
    try:
        from btpay.security.middleware import get_route_limiter
        get_route_limiter()._windows.clear()
    except ImportError:
        pass
    try:
        from btpay.auth.decorators import _api_limiter
        _api_limiter._windows.clear()
    except ImportError:
        pass


@pytest.fixture
def app():
    '''Create a test Flask app.'''
    from app import create_app
    app = create_app({'TESTING': True, 'DATA_DIR': '/tmp/btpay_test'})
    return app
