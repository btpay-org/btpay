#
# Tests for data persistence, demo-to-production lifecycle, and first-user flows.
#
# These tests cover the critical data-loss bugs found during Render deployment:
# - ORM data survives save/load cycles with real model data
# - Demo exit ("Go Live") clears data and writes production flag
# - Production mode flag overrides DEMO_MODE env var on restart
# - Login redirects to register when no users exist
# - First user registration creates org and redirects to setup
# - AutoSaver shutdown save preserves data
# - Concurrent save/load doesn't corrupt data
#
import json
import os
import shutil
import tempfile
import threading
import time
from decimal import Decimal

import pytest

from btpay.auth.models import User, Organization, Membership, Session, ApiKey
from btpay.bitcoin.models import Wallet, BitcoinAddress, ExchangeRateSnapshot
from btpay.invoicing.models import Invoice, InvoiceLine, Payment, PaymentLink
from btpay.orm.engine import MemoryStore
from btpay.orm.persistence import save_to_disk, load_from_disk, AutoSaver


# ---- Helpers ----

def _register(client, email='user@test.com', password='securepass123',
              first_name='Test', last_name='User'):
    return client.post('/auth/register', json={
        'email': email, 'password': password,
        'first_name': first_name, 'last_name': last_name,
    })


def _login(client, email='user@test.com', password='securepass123'):
    return client.post('/auth/login', json={
        'email': email, 'password': password,
    })


def _extract_cookie(resp, name):
    for header in resp.headers.getlist('Set-Cookie'):
        if header.startswith(name + '='):
            return header.split('=', 1)[1].split(';')[0]
    return ''


def _csrf_token(session_token, app):
    from btpay.security.csrf import generate_csrf_token
    secret = app.config.get('SECRET_KEY', '')
    return generate_csrf_token(session_token, secret)


def _auth_setup(app, email='owner@test.com'):
    client = app.test_client()
    resp = _register(client, email, 'securepass123')
    assert resp.status_code == 201
    resp = _login(client, email, 'securepass123')
    assert resp.status_code == 200
    user = User.get_by(email=email)
    org = Organization.query.all()[0]
    token = _extract_cookie(resp, 'btpay_session')
    return client, user, org, token


# ---- Persistence: save/load round-trip ----

class TestPersistenceRoundTrip:
    '''Verify ORM data survives save → clear → load cycles.'''

    def test_user_survives_save_load(self, app):
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            u = User(email='persist@test.com', first_name='Alice', last_name='Smith')
            u.set_password('testpass123')
            u.save()
            user_id = u.id

            save_to_disk(data_dir)
            MemoryStore().clear()
            assert User.get(user_id) is None

            load_from_disk(data_dir)
            loaded = User.get(user_id)
            assert loaded is not None
            assert loaded.email == 'persist@test.com'
            assert loaded.first_name == 'Alice'
            assert loaded.check_password('testpass123')

    def test_full_model_set_survives_save_load(self, app):
        '''All model types persist correctly (regression for the data-loss bug).'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            # Create interconnected data
            user = User(email='full@test.com', first_name='Bob')
            user.set_password('pass12345')
            user.save()

            org = Organization(name='TestCorp', slug='testcorp',
                               default_currency='USD')
            org.save()

            mem = Membership(user_id=user.id, org_id=org.id, role='owner')
            mem.save()

            wallet = Wallet(org_id=org.id, name='Main Wallet',
                            wallet_type='xpub', xpub='tpubTest123',
                            network='testnet')
            wallet.save()

            inv = Invoice(org_id=org.id, user_id=user.id,
                          customer_name='Alice Corp', customer_email='alice@corp.com',
                          currency='USD', status='draft',
                          total=Decimal('100.00'))
            inv.save()

            line = InvoiceLine(invoice_id=inv.id, description='Widget',
                               quantity=Decimal('2'), unit_price=Decimal('50.00'))
            line.save()

            # Save and reload
            save_to_disk(data_dir)
            MemoryStore().clear()
            load_from_disk(data_dir)

            # Verify everything
            assert User.get(user.id).email == 'full@test.com'
            assert Organization.get(org.id).name == 'TestCorp'
            assert Membership.get(mem.id).role == 'owner'
            assert Wallet.get(wallet.id).name == 'Main Wallet'
            assert Invoice.get(inv.id).customer_name == 'Alice Corp'
            assert Invoice.get(inv.id).total == Decimal('100.00')
            assert InvoiceLine.get(line.id).description == 'Widget'

    def test_empty_save_does_not_corrupt_existing_data(self, app):
        '''Saving empty store should write empty files, not corrupt data.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            # Create user, save
            u = User(email='keeper@test.com')
            u.set_password('pass12345')
            u.save()
            save_to_disk(data_dir)

            # Verify file has data
            user_file = os.path.join(data_dir, 'User.json')
            with open(user_file) as f:
                data = json.load(f)
            assert len(data['rows']) == 1

            # Clear store and save again (simulates the on_exit bug)
            MemoryStore().clear()
            save_to_disk(data_dir)

            # File should now be empty
            with open(user_file) as f:
                data = json.load(f)
            assert len(data['rows']) == 0

    def test_save_load_with_special_characters(self, app):
        '''Data with unicode, special chars, and edge cases.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = Organization(name='Ünïcödé & <Special> "Org"', slug='unicode-org')
            org.save()

            user = User(email='münchen@test.de', first_name='José',
                        last_name="O'Brien")
            user.set_password('pässwörd123')
            user.save()

            save_to_disk(data_dir)
            MemoryStore().clear()
            load_from_disk(data_dir)

            assert Organization.get(org.id).name == 'Ünïcödé & <Special> "Org"'
            loaded_user = User.get(user.id)
            assert loaded_user.email == 'münchen@test.de'
            assert loaded_user.first_name == 'José'
            assert loaded_user.last_name == "O'Brien"
            assert loaded_user.check_password('pässwörd123')

    def test_multiple_save_load_cycles(self, app):
        '''Data survives multiple save/load cycles without drift.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            user = User(email='cycle@test.com', first_name='Cycle')
            user.set_password('pass12345')
            user.save()

            for i in range(5):
                save_to_disk(data_dir)
                MemoryStore().clear()
                load_from_disk(data_dir)

                loaded = User.get(user.id)
                assert loaded is not None, 'Lost user on cycle %d' % i
                assert loaded.email == 'cycle@test.com'
                assert loaded.check_password('pass12345')

    def test_concurrent_reads_during_save(self, app):
        '''Reading data while AutoSaver writes doesn't crash or corrupt.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            # Create some data
            for i in range(10):
                u = User(email='concurrent%d@test.com' % i)
                u.set_password('pass12345')
                u.save()

            errors = []

            def save_loop():
                try:
                    for _ in range(20):
                        save_to_disk(data_dir)
                        time.sleep(0.01)
                except Exception as e:
                    errors.append(('save', e))

            def read_loop():
                try:
                    for _ in range(20):
                        users = User.query.all()
                        assert len(users) == 10
                        time.sleep(0.01)
                except Exception as e:
                    errors.append(('read', e))

            t1 = threading.Thread(target=save_loop)
            t2 = threading.Thread(target=read_loop)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            assert not errors, 'Concurrent errors: %s' % errors


# ---- AutoSaver ----

class TestAutoSaver:
    '''Test the AutoSaver background thread.'''

    def test_autosaver_saves_on_interval(self, app):
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            saver = AutoSaver(data_dir, interval=1)
            saver.start()

            User(email='auto@test.com').save()

            # Wait for at least one save cycle
            time.sleep(2.5)
            saver.stop()

            # Verify data was saved
            user_file = os.path.join(data_dir, 'User.json')
            assert os.path.exists(user_file)
            with open(user_file) as f:
                data = json.load(f)
            assert len(data['rows']) == 1

    def test_autosaver_shutdown_save(self, app):
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            saver = AutoSaver(data_dir, interval=3600)  # long interval
            saver.start()

            User(email='shutdown@test.com').save()

            # Trigger shutdown save explicitly (no waiting for interval)
            saver.shutdown_save()
            saver.stop()

            user_file = os.path.join(data_dir, 'User.json')
            with open(user_file) as f:
                data = json.load(f)
            assert len(data['rows']) == 1


# ---- Login redirect to register ----

class TestFirstUserRedirect:
    '''When no users exist, login should redirect to register.'''

    def test_login_redirects_to_register_when_no_users(self, app):
        client = app.test_client()
        resp = client.get('/auth/login')
        assert resp.status_code == 302
        assert '/auth/register' in resp.headers['Location']

    def test_login_shows_login_when_users_exist(self, app):
        client = app.test_client()
        # Create a user first
        u = User(email='exists@test.com')
        u.set_password('pass12345')
        u.save()

        resp = client.get('/auth/login')
        assert resp.status_code == 200  # renders login page, no redirect

    def test_register_page_accessible_when_no_users(self, app):
        client = app.test_client()
        resp = client.get('/auth/register')
        assert resp.status_code == 200

    def test_register_blocked_when_users_exist_no_invite(self, app):
        client = app.test_client()
        u = User(email='first@test.com')
        u.set_password('pass12345')
        u.save()

        resp = client.get('/auth/register')
        assert resp.status_code == 302  # redirect to login
        assert '/auth/login' in resp.headers['Location']

    def test_root_redirects_to_register_when_no_users(self, app):
        '''/ → /auth/login → /auth/register chain when 0 users.'''
        client = app.test_client()
        resp = client.get('/')
        assert resp.status_code == 302
        assert '/auth/login' in resp.headers['Location']

        resp = client.get('/auth/login')
        assert resp.status_code == 302
        assert '/auth/register' in resp.headers['Location']

    def test_first_user_registration_creates_org(self, app):
        client = app.test_client()
        resp = _register(client, 'admin@test.com', 'securepass123')
        assert resp.status_code == 201

        users = User.query.all()
        assert len(users) == 1
        assert users[0].email == 'admin@test.com'

        orgs = Organization.query.all()
        assert len(orgs) == 1

        memberships = Membership.query.all()
        assert len(memberships) == 1
        assert memberships[0].role == 'owner'


# ---- Demo mode exit ----

class TestDemoModeExit:
    '''Test the "Go Live" exit-demo flow.'''

    @pytest.fixture
    def demo_app(self):
        from app import create_app
        app = create_app({
            'TESTING': True,
            'DATA_DIR': '/tmp/btpay_test',
            'DEMO_MODE': True,
        })
        # Seed demo data
        with app.app_context():
            from btpay.demo.seed import seed_demo_data
            seed_demo_data()
        return app

    def test_exit_demo_clears_data(self, demo_app):
        with demo_app.app_context():
            # Verify demo data exists
            assert User.query.count() > 0
            assert Organization.query.count() > 0
            assert Invoice.query.count() > 0

            client = demo_app.test_client()
            # Login as demo user
            resp = _login(client, 'demo', 'demo')
            assert resp.status_code == 200
            token = _extract_cookie(resp, 'btpay_session')
            csrf = _csrf_token(token, demo_app)

            # Exit demo
            resp = client.post('/settings/exit-demo', data={
                '_csrf_token': csrf,
            })
            assert resp.status_code == 302

            # Data should be cleared
            assert User.query.count() == 0
            assert Organization.query.count() == 0
            assert Invoice.query.count() == 0

    def test_exit_demo_writes_production_flag(self, demo_app):
        with demo_app.app_context():
            data_dir = demo_app.config['DATA_DIR']

            client = demo_app.test_client()
            resp = _login(client, 'demo', 'demo')
            token = _extract_cookie(resp, 'btpay_session')
            csrf = _csrf_token(token, demo_app)

            client.post('/settings/exit-demo', data={'_csrf_token': csrf})

            flag_path = os.path.join(data_dir, '_production_mode')
            assert os.path.exists(flag_path)

    def test_production_flag_overrides_demo_mode(self):
        '''After Go Live, app starts in production mode even with BTPAY_DEMO=1.'''
        data_dir = '/tmp/btpay_test'
        os.makedirs(data_dir, exist_ok=True)
        # Write the flag
        with open(os.path.join(data_dir, '_production_mode'), 'w') as f:
            f.write('1')

        from app import create_app
        app = create_app({
            'TESTING': True,
            'DATA_DIR': data_dir,
            'DEMO_MODE': True,  # would normally enable demo
        })
        # Flag should have overridden it
        assert app.config['DEMO_MODE'] is False

    def test_exit_demo_not_allowed_in_production(self, app):
        '''Exit-demo route rejects when not in demo mode.'''
        client, user, org, token = _auth_setup(app)
        csrf = _csrf_token(token, app)

        resp = client.post('/settings/exit-demo', data={'_csrf_token': csrf})
        assert resp.status_code == 302
        # Should flash error, not clear data
        assert User.query.count() > 0

    def test_go_live_card_visible_in_demo(self, demo_app):
        with demo_app.app_context():
            client = demo_app.test_client()
            resp = _login(client, 'demo', 'demo')
            resp = client.get('/settings/general')
            assert resp.status_code == 200
            assert b'Exit Demo' in resp.data

    def test_go_live_card_hidden_in_production(self, app):
        client, user, org, token = _auth_setup(app)
        resp = client.get('/settings/general')
        assert resp.status_code == 200
        assert b'Exit Demo' not in resp.data


# ---- Data integrity under stress ----

class TestDataIntegrity:
    '''Fuzz-like tests for data corruption edge cases.'''

    def test_large_dataset_persistence(self, app):
        '''100 users + 500 invoices survive save/load.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            org = Organization(name='BigCorp', slug='bigcorp')
            org.save()

            for i in range(100):
                u = User(email='user%d@test.com' % i)
                u.set_password('password%d' % i)
                u.save()
                Membership(user_id=u.id, org_id=org.id, role='viewer').save()

            for i in range(500):
                inv = Invoice(org_id=org.id, user_id=1,
                              customer_name='Customer %d' % i,
                              currency='USD', status='draft',
                              total=Decimal(str(i * 10 + 0.99)))
                inv.save()

            save_to_disk(data_dir)
            MemoryStore().clear()
            load_from_disk(data_dir)

            assert User.query.count() == 100
            assert Membership.query.count() == 100
            assert Invoice.query.count() == 500
            assert Organization.get(org.id).name == 'BigCorp'

            # Spot-check specific records
            assert User.get_by(email='user50@test.com') is not None
            assert User.get_by(email='user50@test.com').check_password('password50')
            inv_99 = Invoice.query.filter(customer_name='Customer 99').first()
            assert inv_99.total == Decimal('990.99')

    def test_decimal_precision_preserved(self, app):
        '''Decimal values don't lose precision through JSON serialization.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            test_values = [
                Decimal('0.00000001'),   # 1 satoshi in BTC
                Decimal('21000000.00'),  # max BTC supply
                Decimal('99999999.99999999'),
                Decimal('0.01'),
                Decimal('1234567890.12345678'),
            ]

            org = Organization(name='DecTest', slug='dectest')
            org.save()

            for i, val in enumerate(test_values):
                inv = Invoice(org_id=org.id, user_id=1,
                              customer_name='Dec %d' % i,
                              currency='USD', status='draft',
                              total=val)
                inv.save()

            save_to_disk(data_dir)
            MemoryStore().clear()
            load_from_disk(data_dir)

            for i, val in enumerate(test_values):
                inv = Invoice.query.filter(customer_name='Dec %d' % i).first()
                assert inv.total == val, \
                    'Decimal mismatch for %s: got %s' % (val, inv.total)

    def test_rapid_save_load_no_corruption(self, app):
        '''Rapid alternating saves and loads don't corrupt data.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            user = User(email='rapid@test.com', first_name='Rapid')
            user.set_password('pass12345')
            user.save()

            for i in range(50):
                # Modify, save, reload
                u = User.get(user.id)
                u.first_name = 'Rapid_%d' % i
                u.save()
                save_to_disk(data_dir)

            MemoryStore().clear()
            load_from_disk(data_dir)

            loaded = User.get(user.id)
            assert loaded.first_name == 'Rapid_49'
            assert loaded.check_password('pass12345')

    def test_missing_data_dir_handled_gracefully(self, app):
        '''load_from_disk with nonexistent dir doesn't crash.'''
        with app.app_context():
            load_from_disk('/tmp/btpay_nonexistent_dir_12345')
            # Should return without error, no data loaded
            assert User.query.count() == 0

    def test_corrupt_json_file_handled(self, app):
        '''A corrupt JSON file doesn't crash the entire load.'''
        with app.app_context():
            data_dir = app.config['DATA_DIR']
            os.makedirs(data_dir, exist_ok=True)

            # Create valid data and save
            user = User(email='safe@test.com')
            user.set_password('pass12345')
            user.save()
            org = Organization(name='SafeOrg', slug='safeorg')
            org.save()
            save_to_disk(data_dir)

            # Corrupt the User.json file
            user_file = os.path.join(data_dir, 'User.json')
            with open(user_file, 'w') as f:
                f.write('{corrupt json data!!!')

            # Reload — User should fail but Org should still load
            MemoryStore().clear()
            load_from_disk(data_dir)

            assert User.query.count() == 0  # corrupted, couldn't load
            assert Organization.query.count() == 1  # intact


# EOF
