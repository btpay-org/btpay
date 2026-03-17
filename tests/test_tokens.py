#
# Tests for JWT tokens
#
import pytest
import time
from btpay.security.tokens import create_secure_token, verify_secure_token


SECRETS = {
    'test': 'test-secret-key-1234',
    'admin': 'admin-secret-key-5678',
}


def test_create_and_verify():
    token = create_secure_token('test', SECRETS, hours=1)
    assert isinstance(token, str)

    expired, contents = verify_secure_token('test', token, SECRETS)
    assert expired is False
    assert contents['purpose'] == 'test'


def test_extras():
    token = create_secure_token('test', SECRETS, extras={'user': 42}, hours=1)
    expired, contents = verify_secure_token('test', token, SECRETS)
    assert contents['user'] == 42


def test_wrong_purpose():
    token = create_secure_token('test', SECRETS, hours=1)
    with pytest.raises(ValueError):
        verify_secure_token('admin', token, SECRETS)


def test_bad_token():
    with pytest.raises(ValueError, match="Bad security token"):
        verify_secure_token('test', 'garbage', SECRETS)


def test_expired_token():
    token = create_secure_token('test', SECRETS, seconds=0)
    time.sleep(0.1)
    expired, contents = verify_secure_token('test', token, SECRETS)
    assert expired is True

# EOF
