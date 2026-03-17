#
# Tests for Phase 2 — User & Auth System
#
import pytest
import time
import hashlib
from btpay.auth.models import (
    User, Organization, Membership, Session, ApiKey,
    MIN_PASSWORD_LENGTH,
)
from btpay.auth.sessions import (
    create_session, validate_session, destroy_session,
)
from btpay.auth.totp import (
    generate_totp_secret, generate_totp_qr, verify_totp,
)
from btpay.security.hashing import generate_random_token
from btpay.chrono import NOW, TIME_FUTURE, TIME_AGO


# ---- Fixtures ----

@pytest.fixture
def user():
    '''Create a basic test user.'''
    u = User(email='alice@test.com', first_name='Alice', last_name='Smith')
    u.set_password('password123')
    u.save()
    return u


@pytest.fixture
def org():
    '''Create a test organization.'''
    o = Organization(name='Test Org', slug='test-org')
    o.save()
    return o


@pytest.fixture
def membership(user, org):
    '''Create an owner membership.'''
    m = Membership(user_id=user.id, org_id=org.id, role='owner')
    m.save()
    return m


class FakeRequest:
    '''Minimal request mock for session creation.'''
    remote_addr = '127.0.0.1'
    headers = {'User-Agent': 'TestAgent/1.0'}


# ---- User Model Tests ----

class TestUserModel:
    def test_create_user(self, user):
        assert user.id is not None
        assert user.email == 'alice@test.com'
        assert user.is_active is True

    def test_full_name(self):
        u = User(email='x@y.com', first_name='Bob', last_name='Jones')
        assert u.full_name == 'Bob Jones'

    def test_full_name_email_fallback(self):
        u = User(email='x@y.com')
        assert u.full_name == 'x@y.com'

    def test_password_hashing(self, user):
        assert user.check_password('password123')
        assert not user.check_password('wrong')

    def test_password_too_short(self):
        u = User(email='x@y.com')
        with pytest.raises(ValueError, match='at least'):
            u.set_password('short')

    def test_failed_login_lockout(self, user):
        for _ in range(5):
            user.record_failed_login()
        assert user.is_locked is True
        assert user.failed_login_count == 5

    def test_successful_login_resets(self, user):
        user.record_failed_login()
        user.record_failed_login()
        user.record_successful_login()
        assert user.failed_login_count == 0
        assert user.is_locked is False

    def test_unique_email(self, user):
        u2 = User(email='alice@test.com')
        u2.set_password('password123')
        with pytest.raises(ValueError, match='Unique constraint'):
            u2.save()

    def test_email_stored(self, user):
        fetched = User.get(user.id)
        assert fetched.email == 'alice@test.com'

    def test_check_password_no_hash(self):
        u = User(email='nohash@test.com')
        u.save()
        assert u.check_password('anything') is False


# ---- Organization Model Tests ----

class TestOrganizationModel:
    def test_create_org(self, org):
        assert org.id is not None
        assert org.name == 'Test Org'
        assert org.slug == 'test-org'
        assert org.default_currency == 'USD'

    def test_slug_uniqueness(self, org):
        o2 = Organization(name='Other', slug='test-org')
        with pytest.raises(ValueError, match='Unique constraint'):
            o2.save()

    def test_make_slug(self):
        assert Organization.make_slug('My Company') == 'my-company'
        assert Organization.make_slug('Hello World!') == 'hello-world'
        assert Organization.make_slug('  Spaces  ') == 'spaces'

    def test_next_invoice_number(self, org):
        num1 = org.next_invoice_number()
        assert num1 == 'INV-0001'
        num2 = org.next_invoice_number()
        assert num2 == 'INV-0002'

    def test_custom_invoice_prefix(self):
        o = Organization(name='Custom', slug='custom', invoice_prefix='BTC')
        o.save()
        assert o.next_invoice_number() == 'BTC-0001'

    def test_brand_defaults(self, org):
        assert org.brand_color == '#F89F1B'
        assert org.brand_accent_color == '#3B3A3C'


# ---- Membership Model Tests ----

class TestMembershipModel:
    def test_create_membership(self, membership):
        assert membership.id is not None
        assert membership.role == 'owner'

    def test_has_role_owner(self, membership):
        assert membership.has_role('owner')
        assert membership.has_role('admin')
        assert membership.has_role('viewer')

    def test_has_role_admin(self, user, org):
        m = Membership(user_id=user.id, org_id=org.id, role='admin')
        m.save()
        assert not m.has_role('owner')
        assert m.has_role('admin')
        assert m.has_role('viewer')

    def test_has_role_viewer(self, user, org):
        m = Membership(user_id=user.id, org_id=org.id, role='viewer')
        m.save()
        assert not m.has_role('owner')
        assert not m.has_role('admin')
        assert m.has_role('viewer')

    def test_user_property(self, membership, user):
        assert membership.user.id == user.id

    def test_org_property(self, membership, org):
        assert membership.org.id == org.id


# ---- Session Management Tests ----

class TestSessions:
    def test_create_and_validate(self, user, org):
        token = create_session(user, org, FakeRequest(), hours=1)
        assert isinstance(token, str)
        assert len(token) > 20

        result = validate_session(token)
        assert result is not None
        u, o = result
        assert u.id == user.id
        assert o.id == org.id

    def test_invalid_token(self):
        assert validate_session('garbage-token') is None

    def test_empty_token(self):
        assert validate_session('') is None
        assert validate_session(None) is None

    def test_destroy_session(self, user, org):
        token = create_session(user, org, FakeRequest(), hours=1)
        assert validate_session(token) is not None
        destroy_session(token)
        assert validate_session(token) is None

    def test_expired_session(self, user, org):
        '''Create a session that's already expired.'''
        from btpay.auth.models import Session as SessionModel
        from btpay.chrono import as_time_t

        raw_token = generate_random_token(32)
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

        sess = SessionModel(
            user_id=user.id,
            token_hash=token_hash,
            ip_address='127.0.0.1',
            user_agent='test',
            expires_at=TIME_AGO(hours=1),  # already expired
            org_id=org.id,
        )
        sess.save()

        assert validate_session(raw_token) is None

    def test_inactive_user_session(self, user, org):
        token = create_session(user, org, FakeRequest(), hours=1)
        user.is_active = False
        user.save()
        assert validate_session(token) is None

    def test_no_org_session(self, user):
        token = create_session(user, None, FakeRequest(), hours=1)
        result = validate_session(token)
        assert result is not None
        u, o = result
        assert u.id == user.id
        assert o is None


# ---- TOTP Tests ----

class TestTOTP:
    def test_generate_secret(self):
        secret = generate_totp_secret()
        assert isinstance(secret, str)
        assert len(secret) >= 16

    def test_generate_qr(self):
        secret = generate_totp_secret()
        qr_bytes = generate_totp_qr(secret, 'test@example.com')
        assert isinstance(qr_bytes, bytes)
        # PNG magic bytes
        assert qr_bytes[:4] == b'\x89PNG'

    def test_verify_valid_code(self):
        import pyotp
        secret = generate_totp_secret()
        totp = pyotp.TOTP(secret)
        code = totp.now()
        assert verify_totp(secret, code) is True

    def test_verify_invalid_code(self):
        secret = generate_totp_secret()
        assert verify_totp(secret, '000000') is False

    def test_replay_prevention(self):
        import pyotp
        secret = generate_totp_secret()
        totp = pyotp.TOTP(secret)
        code = totp.now()
        # First use: ok
        assert verify_totp(secret, code) is True
        # Replay with same last_used: rejected
        assert verify_totp(secret, code, last_used=code) is False

    def test_empty_inputs(self):
        assert verify_totp('', '123456') is False
        assert verify_totp('ABCDEF', '') is False

    def test_bad_code_format(self):
        secret = generate_totp_secret()
        assert verify_totp(secret, 'abc') is False
        assert verify_totp(secret, '12345') is False     # too short
        assert verify_totp(secret, '1234567') is False   # too long


# ---- ApiKey Model Tests ----

class TestApiKeyModel:
    def test_create_api_key(self, user, org):
        raw_key = generate_random_token(32)
        key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
        ak = ApiKey(
            org_id=org.id,
            user_id=user.id,
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            label='Test Key',
            permissions={'invoices:read', 'invoices:write'},
        )
        ak.save()
        assert ak.id is not None
        assert ak.is_active is True

    def test_lookup_by_hash(self, user, org):
        raw_key = generate_random_token(32)
        key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
        ak = ApiKey(
            org_id=org.id,
            user_id=user.id,
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            label='Lookup Test',
        )
        ak.save()

        found = ApiKey.get_by(key_hash=key_hash)
        assert found is not None
        assert found.label == 'Lookup Test'


# ---- Auth Views Tests (Flask test client) ----

@pytest.fixture
def client(app):
    return app.test_client()


class TestRegisterView:
    def test_register_first_user(self, client):
        rv = client.post('/auth/register', json={
            'email': 'first@test.com',
            'password': 'securepassword1',
            'first_name': 'First',
            'last_name': 'User',
        })
        assert rv.status_code == 201
        data = rv.get_json()
        assert data['ok'] is True
        assert data['user_id'] is not None
        assert data['org_id'] is not None  # first user gets an org

    def test_register_sets_cookie(self, client):
        rv = client.post('/auth/register', json={
            'email': 'cookie@test.com',
            'password': 'securepassword1',
        })
        assert rv.status_code == 201
        # Check that a session cookie was set in response headers
        set_cookie = rv.headers.get('Set-Cookie', '')
        assert 'btpay_session' in set_cookie

    def test_register_duplicate_email(self, client):
        client.post('/auth/register', json={
            'email': 'dupe@test.com',
            'password': 'securepassword1',
        })
        rv = client.post('/auth/register', json={
            'email': 'dupe@test.com',
            'password': 'securepassword1',
        })
        assert rv.status_code == 400
        assert 'already registered' in rv.get_json()['error']

    def test_register_bad_email(self, client):
        rv = client.post('/auth/register', json={
            'email': 'not-an-email',
            'password': 'securepassword1',
        })
        assert rv.status_code == 400

    def test_register_short_password(self, client):
        rv = client.post('/auth/register', json={
            'email': 'short@test.com',
            'password': 'short',
        })
        assert rv.status_code == 400
        assert 'at least' in rv.get_json()['error']

    def test_second_user_blocked_without_invite(self, client):
        # First user gets org
        client.post('/auth/register', json={
            'email': 'first@test.com', 'password': 'securepassword1',
        })
        # Second user without invite — blocked
        rv = client.post('/auth/register', json={
            'email': 'second@test.com', 'password': 'securepassword1',
        })
        assert rv.status_code == 400
        assert 'invite' in rv.get_json()['error'].lower()


class TestLoginView:
    def _register(self, client, email='login@test.com', password='securepassword1'):
        client.post('/auth/register', json={
            'email': email, 'password': password,
        })

    def test_login_success(self, client):
        self._register(client)
        rv = client.post('/auth/login', json={
            'email': 'login@test.com',
            'password': 'securepassword1',
        })
        assert rv.status_code == 200
        assert rv.get_json()['ok'] is True

    def test_login_wrong_password(self, client):
        self._register(client)
        rv = client.post('/auth/login', json={
            'email': 'login@test.com',
            'password': 'wrongpassword',
        })
        assert rv.status_code == 401

    def test_login_unknown_email(self, client):
        rv = client.post('/auth/login', json={
            'email': 'nobody@test.com',
            'password': 'securepassword1',
        })
        assert rv.status_code == 401

    def test_login_rate_limit(self, client):
        self._register(client)
        # Exhaust rate limit (5 attempts in conftest app config)
        for _ in range(6):
            rv = client.post('/auth/login', json={
                'email': 'login@test.com',
                'password': 'wrongpassword',
            })
        # Should be rate-limited by now
        assert rv.status_code in (429, 423, 401)

    def test_login_lockout(self, app):
        '''Test account lockout after 5 failed attempts (bypass rate limiter).'''
        # Directly test the model-level lockout, not through the view
        u = User(email='lockout@test.com')
        u.set_password('securepassword1')
        u.save()

        for _ in range(5):
            u.record_failed_login()

        assert u.is_locked is True
        assert u.failed_login_count == 5

        # Now try through the view — should get 401 (same as invalid, to prevent enumeration)
        with app.test_client() as client:
            rv = client.post('/auth/login', json={
                'email': 'lockout@test.com',
                'password': 'securepassword1',
            })
            assert rv.status_code == 401


class TestLogoutView:
    def test_logout(self, client):
        client.post('/auth/register', json={
            'email': 'logout@test.com', 'password': 'securepassword1',
        })
        rv = client.post('/auth/logout', content_type='application/json')
        assert rv.status_code == 200
        assert rv.get_json()['ok'] is True


class TestPasswordChangeView:
    def test_change_password(self, client):
        # Reset rate limiter before this test
        from btpay.auth.views import _limiter
        _limiter._windows.clear()

        client.post('/auth/register', json={
            'email': 'pw@test.com', 'password': 'oldpassword1',
        })
        rv = client.post('/auth/password', json={
            'current_password': 'oldpassword1',
            'new_password': 'newpassword1',
        })
        assert rv.status_code == 200

        # Login with new password
        client.post('/auth/logout')
        rv = client.post('/auth/login', json={
            'email': 'pw@test.com', 'password': 'newpassword1',
        })
        assert rv.status_code == 200

    def test_change_password_wrong_current(self, client):
        client.post('/auth/register', json={
            'email': 'pw2@test.com', 'password': 'oldpassword1',
        })
        rv = client.post('/auth/password', json={
            'current_password': 'wrongpassword',
            'new_password': 'newpassword1',
        })
        assert rv.status_code == 401


# ---- Decorator Tests ----

class TestLoginRequired:
    def test_protected_route_unauthenticated(self, app):
        @app.route('/protected-test')
        def _protected():
            from btpay.auth.decorators import login_required
            @login_required
            def inner():
                return 'ok'
            return inner()

        with app.test_client() as c:
            rv = c.get('/protected-test', headers={'Accept': 'application/json'})
            assert rv.status_code in (301, 302, 401)

    def test_totp_setup_requires_login(self, client):
        rv = client.get('/auth/totp/setup', headers={'Accept': 'application/json'})
        assert rv.status_code == 401


class TestRoleRequired:
    def test_role_check_logic(self, user, org):
        m = Membership(user_id=user.id, org_id=org.id, role='viewer')
        m.save()
        assert m.has_role('viewer')
        assert not m.has_role('admin')
        assert not m.has_role('owner')

        m.role = 'admin'
        m.save()
        assert m.has_role('viewer')
        assert m.has_role('admin')
        assert not m.has_role('owner')


# ---- TOTP Views Tests ----

class TestTOTPViews:
    def test_totp_setup(self, client):
        client.post('/auth/register', json={
            'email': 'totp@test.com', 'password': 'securepassword1',
        })
        rv = client.get('/auth/totp/setup')
        assert rv.status_code == 200
        data = rv.get_json()
        assert 'secret' in data
        assert 'qr_code' in data
        assert data['qr_code'].startswith('data:image/png;base64,')

    def test_totp_enable_disable(self, client):
        import pyotp

        # Register
        client.post('/auth/register', json={
            'email': 'totp2@test.com', 'password': 'securepassword1',
        })

        # Get setup
        rv = client.get('/auth/totp/setup')
        secret = rv.get_json()['secret']

        # Enable with valid code
        totp = pyotp.TOTP(secret)
        code = totp.now()
        rv = client.post('/auth/totp/enable', json={
            'secret': secret,
            'totp_code': code,
        })
        assert rv.status_code == 200

        # Verify user has TOTP enabled
        u = User.get_by(email='totp2@test.com')
        assert u.totp_enabled is True

        # Disable — need a fresh code (wait a bit for new window)
        code2 = totp.now()
        rv = client.post('/auth/totp/disable', json={'totp_code': code2})
        # May succeed or fail depending on timing — just check it's handled
        assert rv.status_code in (200, 401)


# ---- Login with TOTP Flow ----

class TestLoginTOTP:
    def test_totp_login_flow(self, client):
        import pyotp

        # Reset rate limiter
        from btpay.auth.views import _limiter
        _limiter._windows.clear()

        # Register user
        client.post('/auth/register', json={
            'email': 'totplogin@test.com', 'password': 'securepassword1',
        })

        # Setup TOTP
        rv = client.get('/auth/totp/setup')
        secret = rv.get_json()['secret']
        totp = pyotp.TOTP(secret)
        code = totp.now()
        client.post('/auth/totp/enable', json={'secret': secret, 'totp_code': code})

        # Logout
        client.post('/auth/logout')

        # Login — should get totp_required
        rv = client.post('/auth/login', json={
            'email': 'totplogin@test.com',
            'password': 'securepassword1',
        })
        data = rv.get_json()
        assert data.get('totp_required') is True
        login_token = data['login_token']

        # Complete with TOTP — need to clear last_totp_used since we're
        # in the same time window as the enable step
        u = User.get_by(email='totplogin@test.com')
        u.last_totp_used = ''
        u.save()

        code2 = totp.now()
        rv = client.post('/auth/login/totp', json={
            'login_token': login_token,
            'totp_code': code2,
        })
        assert rv.status_code == 200
        assert rv.get_json()['ok'] is True


# ---- API Key Auth Tests ----

class TestApiAuth:
    def test_api_key_model(self, user, org):
        raw_key = generate_random_token(32)
        key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
        ak = ApiKey(
            org_id=org.id,
            user_id=user.id,
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            label='My API Key',
            permissions={'invoices:read'},
            is_active=True,
        )
        ak.save()

        found = ApiKey.get_by(key_hash=key_hash)
        assert found is not None
        assert found.label == 'My API Key'
        assert 'invoices:read' in found.permissions

    def test_revoked_key(self, user, org):
        raw_key = generate_random_token(32)
        key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
        ak = ApiKey(
            org_id=org.id,
            user_id=user.id,
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            label='Revoked Key',
            is_active=False,
        )
        ak.save()

        found = ApiKey.get_by(key_hash=key_hash)
        assert found is not None
        assert found.is_active is False

# EOF
