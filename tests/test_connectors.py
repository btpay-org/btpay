#
# User story tests for Payment Connectors: Wire Transfer & Stablecoins
#
# Tests cover:
#   1. Wire connector model & validation
#   2. Stablecoin account model & address validation
#   3. Settings UI routes (create, toggle, delete)
#   4. Checkout multi-method display
#   5. Payment method registry integration
#   6. Demo seed data with connectors
#   7. RBAC on connector settings
#   8. Edge cases & error handling
#
import pytest
from decimal import Decimal


# ---- Fixtures ----

@pytest.fixture
def app():
    '''Create a fresh test app with all models loaded.'''
    from btpay.orm.engine import MemoryStore
    store = MemoryStore()
    for t in list(store._tables.keys()):
        store._tables[t].clear()
        store._sequences[t] = 1
        for idx in store._indexes.get(t, {}).values():
            idx.clear()

    from app import create_app
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-secret-key',
                      'DATA_DIR': '/tmp/btpay_test',
                      'AUTH_COOKIE_NAME': 'btpay_session',
                      'JWT_SECRETS': {'admin': 'a' * 32, 'login': 'l' * 32,
                                      'api': 'p' * 32, 'invite': 'i' * 32}})
    return app


def _auth_setup(app):
    '''Create admin user + org + session, return (client, user, org, token).'''
    from btpay.auth.models import User, Organization, Membership, Session
    from btpay.security.hashing import hash_password, generate_random_token
    import hashlib

    user = User(email='admin@test.com', first_name='Test', last_name='Admin')
    user.password_hash = hash_password('testpass123')
    user.save()

    org = Organization(name='Test Org', slug='test-org', default_currency='USD')
    org.save()

    Membership(user_id=user.id, org_id=org.id, role='owner').save()

    token = generate_random_token(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    from btpay.chrono import TIME_FUTURE
    Session(user_id=user.id, token_hash=token_hash,
            expires_at=TIME_FUTURE(hours=24), org_id=org.id).save()

    client = app.test_client()
    client.set_cookie('btpay_session', token, domain='localhost')
    return client, user, org, token


def _csrf_token(session_token, app):
    from btpay.security.csrf import generate_csrf_token
    return generate_csrf_token(session_token, app.config['SECRET_KEY'])


# ======================================================================
# Story 1: Wire connector model & validation
# ======================================================================
class TestStory_WireConnectorModel:

    def test_create_wire_connector(self, app):
        with app.app_context():
            from btpay.connectors.wire import WireConnector
            wc = WireConnector(
                org_id=1, name='Test Wire',
                bank_name='Test Bank', account_name='Acme Corp',
                account_number='12345678', routing_number='021000021',
                swift_code='TESTUS33', currency='USD',
            )
            wc.save()
            assert wc.id > 0
            assert wc.is_active is True

    def test_validate_wire_connector_valid(self, app):
        with app.app_context():
            from btpay.connectors.wire import WireConnector, validate_wire_connector
            wc = WireConnector(bank_name='Bank', account_name='Acme', account_number='123')
            valid, errors = validate_wire_connector(wc)
            assert valid
            assert errors == []

    def test_validate_wire_connector_iban_alternative(self, app):
        with app.app_context():
            from btpay.connectors.wire import WireConnector, validate_wire_connector
            wc = WireConnector(bank_name='Bank', account_name='Acme', iban='DE89370400440532013000')
            valid, errors = validate_wire_connector(wc)
            assert valid

    def test_validate_wire_connector_missing_bank(self, app):
        with app.app_context():
            from btpay.connectors.wire import WireConnector, validate_wire_connector
            wc = WireConnector(account_name='Acme', account_number='123')
            valid, errors = validate_wire_connector(wc)
            assert not valid
            assert 'Bank name is required' in errors

    def test_validate_wire_connector_missing_account(self, app):
        with app.app_context():
            from btpay.connectors.wire import WireConnector, validate_wire_connector
            wc = WireConnector(bank_name='Bank', account_name='Acme')
            valid, errors = validate_wire_connector(wc)
            assert not valid
            assert 'Account number or IBAN is required' in errors

    def test_validate_wire_connector_missing_beneficiary(self, app):
        with app.app_context():
            from btpay.connectors.wire import WireConnector, validate_wire_connector
            wc = WireConnector(bank_name='Bank', account_number='123')
            valid, errors = validate_wire_connector(wc)
            assert not valid
            assert 'Beneficiary / account name is required' in errors

    def test_wire_payment_info(self, app):
        with app.app_context():
            from btpay.connectors.wire import WireConnector, wire_payment_info
            from btpay.invoicing.models import Invoice
            wc = WireConnector(
                bank_name='Test Bank', account_name='Acme',
                account_number='****4567', swift_code='TESTUS33',
                currency='USD', notes='Include invoice #',
            )
            inv = Invoice(invoice_number='INV-001', total=Decimal('500'), currency='USD')
            inv.save()
            info = wire_payment_info(wc, inv)
            assert info['bank_name'] == 'Test Bank'
            assert info['amount'] == '500'
            assert info['reference'] == 'INV-001'
            assert info['notes'] == 'Include invoice #'


# ======================================================================
# Story 2: Stablecoin model & address validation
# ======================================================================
class TestStory_StablecoinModel:

    def test_create_stablecoin_account(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount(
                org_id=1, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
            )
            acct.save()
            assert acct.id > 0
            assert acct.display_label == 'USDC on Ethereum'
            assert acct.method_name == 'stable_ethereum_usdc'
            assert acct.token_symbol == 'USDC'
            assert acct.chain_name == 'Ethereum'

    def test_custom_label(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount(
                org_id=1, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
                label='Treasury USDC',
            )
            assert acct.display_label == 'Treasury USDC'

    def test_short_address(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount(
                org_id=1, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
            )
            assert acct.short_address == '0xd8dA...6045'

    def test_explorer_url(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount(
                org_id=1, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
            )
            assert 'etherscan.io' in acct.explorer_url
            assert acct.address in acct.explorer_url

    def test_explorer_url_tron(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount(org_id=1, chain='tron', token='usdt',
                address='TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf')
            assert 'tronscan.org' in acct.explorer_url

    def test_explorer_url_solana(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount(org_id=1, chain='solana', token='usdc',
                address='9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM')
            assert 'solscan.io' in acct.explorer_url

    # ---- Address validation ----

    def test_validate_evm_address_valid(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045', 'ethereum')
            assert valid, err

    def test_validate_evm_address_lowercase(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('0xd8da6bf26964af9d7eed9e03e53415d37aa96045', 'ethereum')
            assert valid, err

    def test_validate_evm_address_too_short(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('0x1234', 'ethereum')
            assert not valid
            assert 'Invalid EVM address' in err

    def test_validate_evm_address_no_prefix(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('d8da6bf26964af9d7eed9e03e53415d37aa96045', 'ethereum')
            assert not valid

    def test_validate_evm_address_arbitrum(self, app):
        '''Same validation for all EVM chains.'''
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045', 'arbitrum')
            assert valid

    def test_validate_evm_address_base(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045', 'base')
            assert valid

    def test_validate_evm_address_polygon(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045', 'polygon')
            assert valid

    def test_validate_tron_address_valid(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf', 'tron')
            assert valid, err

    def test_validate_tron_address_wrong_prefix(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('XDqSquXBgUCLYvYC4XZgrprLK589dkhSCf', 'tron')
            assert not valid
            assert 'must start with T' in err

    def test_validate_tron_address_wrong_length(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('TDqSquXBg', 'tron')
            assert not valid
            assert '34 characters' in err

    def test_validate_solana_address_valid(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM', 'solana')
            assert valid, err

    def test_validate_solana_address_too_short(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('abc', 'solana')
            assert not valid

    def test_validate_empty_address(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('', 'ethereum')
            assert not valid
            assert 'required' in err.lower()

    def test_validate_unsupported_chain(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import validate_stablecoin_address
            valid, err = validate_stablecoin_address('0x1234', 'dogecoin')
            assert not valid
            assert 'Unsupported chain' in err

    def test_stablecoin_payment_info(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import StablecoinAccount, stablecoin_payment_info
            from btpay.invoicing.models import Invoice
            acct = StablecoinAccount(org_id=1, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045')
            inv = Invoice(invoice_number='INV-001', total=Decimal('250'), currency='USD')
            inv.save()
            info = stablecoin_payment_info(acct, inv)
            assert info['token_symbol'] == 'USDC'
            assert info['chain_name'] == 'Ethereum'
            assert info['amount'] == '250'
            assert 'Only send USDC on the Ethereum network' in info['warning']


# ======================================================================
# Story 3: Wire connector settings UI
# ======================================================================
class TestStory_WireSettingsUI:

    def test_wire_settings_page_get(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            resp = client.get('/settings/connectors/wire')
            assert resp.status_code == 200
            assert b'Wire Transfer' in resp.data

    def test_wire_settings_save(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)
            resp = client.post('/settings/connectors/wire', data={
                '_csrf_token': csrf,
                'name': 'Company Wire',
                'bank_name': 'Chase Bank',
                'account_name': 'Test Corp',
                'account_number': '****9876',
                'routing_number': '021000021',
                'swift_code': 'CHASUS33',
                'currency': 'USD',
                'notes': 'Include invoice # in memo',
                'is_active': '1',
            }, follow_redirects=True)
            assert resp.status_code == 200

            from btpay.connectors.wire import WireConnector
            wc = WireConnector.query.filter(org_id=org.id).first()
            assert wc is not None
            assert wc.bank_name == 'Chase Bank'
            assert wc.swift_code == 'CHASUS33'
            assert wc.is_active

    def test_wire_settings_update_existing(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)

            # Create
            client.post('/settings/connectors/wire', data={
                '_csrf_token': csrf, 'bank_name': 'Old Bank',
                'account_name': 'Corp', 'account_number': '111', 'is_active': '1',
            }, follow_redirects=True)

            # Update
            resp = client.post('/settings/connectors/wire', data={
                '_csrf_token': csrf, 'bank_name': 'New Bank',
                'account_name': 'Corp', 'account_number': '222', 'is_active': '1',
            }, follow_redirects=True)
            assert resp.status_code == 200

            from btpay.connectors.wire import WireConnector
            connectors = WireConnector.query.filter(org_id=org.id).all()
            assert len(connectors) == 1  # only one, not duplicated
            assert connectors[0].bank_name == 'New Bank'

    def test_wire_settings_validation_error(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)
            resp = client.post('/settings/connectors/wire', data={
                '_csrf_token': csrf,
                'account_name': 'Test',
                'is_active': '1',
                # Missing bank_name and account_number
            })
            assert resp.status_code == 200  # re-renders form with errors


# ======================================================================
# Story 4: Stablecoin settings UI
# ======================================================================
class TestStory_StablecoinSettingsUI:

    def test_stablecoins_page_get(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            resp = client.get('/settings/connectors/stablecoins')
            assert resp.status_code == 200
            assert b'Stablecoin' in resp.data

    def test_add_stablecoin_account(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)
            resp = client.post('/settings/connectors/stablecoins', data={
                '_csrf_token': csrf,
                'chain': 'ethereum',
                'token': 'usdc',
                'address': '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
                'label': '',
            }, follow_redirects=True)
            assert resp.status_code == 200

            from btpay.connectors.stablecoins import StablecoinAccount
            accounts = StablecoinAccount.query.filter(org_id=org.id).all()
            assert len(accounts) == 1
            assert accounts[0].chain == 'ethereum'
            assert accounts[0].token == 'usdc'
            assert accounts[0].display_label == 'USDC on Ethereum'

    def test_add_stablecoin_tron(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)
            resp = client.post('/settings/connectors/stablecoins', data={
                '_csrf_token': csrf,
                'chain': 'tron', 'token': 'usdt',
                'address': 'TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf',
            }, follow_redirects=True)
            assert resp.status_code == 200

            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount.query.filter(org_id=org.id).first()
            assert acct.chain == 'tron'
            assert acct.display_label == 'USDT on Tron'

    def test_add_stablecoin_invalid_address(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)
            resp = client.post('/settings/connectors/stablecoins', data={
                '_csrf_token': csrf,
                'chain': 'ethereum', 'token': 'usdc',
                'address': 'not-a-valid-address',
            }, follow_redirects=True)
            assert resp.status_code == 200

            from btpay.connectors.stablecoins import StablecoinAccount
            assert StablecoinAccount.query.filter(org_id=org.id).first() is None

    def test_add_stablecoin_unsupported_chain(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)
            resp = client.post('/settings/connectors/stablecoins', data={
                '_csrf_token': csrf,
                'chain': 'dogecoin', 'token': 'usdc',
                'address': '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
            }, follow_redirects=True)
            assert resp.status_code == 200

            from btpay.connectors.stablecoins import StablecoinAccount
            assert StablecoinAccount.query.filter(org_id=org.id).first() is None

    def test_toggle_stablecoin_account(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)

            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount(org_id=org.id, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045')
            acct.save()
            assert acct.is_active

            resp = client.post('/settings/connectors/stablecoins/%d/toggle' % acct.id,
                data={'_csrf_token': csrf}, follow_redirects=True)
            assert resp.status_code == 200

            acct = StablecoinAccount.get(acct.id)
            assert not acct.is_active

    def test_delete_stablecoin_account(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)

            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount(org_id=org.id, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045')
            acct.save()
            acct_id = acct.id

            resp = client.post('/settings/connectors/stablecoins/%d/delete' % acct_id,
                data={'_csrf_token': csrf}, follow_redirects=True)
            assert resp.status_code == 200
            assert StablecoinAccount.get(acct_id) is None

    def test_toggle_wrong_org(self, app):
        '''Cannot toggle account from another org.'''
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)

            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount(org_id=9999, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045')
            acct.save()

            resp = client.post('/settings/connectors/stablecoins/%d/toggle' % acct.id,
                data={'_csrf_token': csrf}, follow_redirects=True)
            # Should flash error, not crash
            assert resp.status_code == 200

    def test_custom_label_preserved(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            csrf = _csrf_token(token, app)
            resp = client.post('/settings/connectors/stablecoins', data={
                '_csrf_token': csrf,
                'chain': 'ethereum', 'token': 'usdc',
                'address': '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
                'label': 'My Treasury',
            }, follow_redirects=True)
            assert resp.status_code == 200

            from btpay.connectors.stablecoins import StablecoinAccount
            acct = StablecoinAccount.query.filter(org_id=org.id).first()
            assert acct.display_label == 'My Treasury'


# ======================================================================
# Story 5: Settings nav has connector group
# ======================================================================
class TestStory_SettingsNav:

    def test_nav_has_connector_links(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            resp = client.get('/settings/general')
            assert resp.status_code == 200
            assert b'Payment Connectors' in resp.data
            assert b'Bitcoin Wallets' in resp.data
            assert b'Wire Transfer' in resp.data
            assert b'Stablecoins' in resp.data

    def test_legacy_wallets_redirect(self, app):
        with app.app_context():
            client, user, org, token = _auth_setup(app)
            resp = client.get('/settings/wallets')
            assert resp.status_code == 301
            assert '/settings/connectors/bitcoin' in resp.headers.get('Location', '')


# ======================================================================
# Story 6: Payment method registry with connectors
# ======================================================================
class TestStory_PaymentMethodRegistry:

    def test_wire_method_available_with_connector(self, app):
        with app.app_context():
            from btpay.connectors.wire import WireConnector
            from btpay.invoicing.payment_methods import get_method
            from btpay.auth.models import Organization

            org = Organization(name='Test', slug='test-pm')
            org.save()
            WireConnector(org_id=org.id, bank_name='Bank', account_name='Corp',
                         account_number='123', is_active=True).save()

            wire = get_method('wire')
            assert wire is not None
            assert wire.is_available(org)

    def test_wire_method_unavailable_without_connector(self, app):
        with app.app_context():
            from btpay.invoicing.payment_methods import get_method
            from btpay.auth.models import Organization

            org = Organization(name='Empty', slug='empty-pm')
            org.save()

            wire = get_method('wire')
            assert not wire.is_available(org)

    def test_stablecoin_methods_registered_dynamically(self, app):
        with app.app_context():
            from btpay.connectors.stablecoins import StablecoinAccount
            from btpay.invoicing.payment_methods import available_methods
            from btpay.auth.models import Organization

            org = Organization(name='Test', slug='test-stable-pm')
            org.save()

            StablecoinAccount(org_id=org.id, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045').save()
            StablecoinAccount(org_id=org.id, chain='tron', token='usdt',
                address='TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf').save()

            methods = available_methods(org)
            method_names = [m.name for m in methods]
            assert 'stable_ethereum_usdc' in method_names
            assert 'stable_tron_usdt' in method_names


# ======================================================================
# Story 7: Checkout with multiple payment methods
# ======================================================================
class TestStory_CheckoutMultiMethod:

    def _setup_invoice_with_connectors(self, app):
        '''Create an org with all connector types and a pending invoice.'''
        from btpay.auth.models import Organization
        from btpay.bitcoin.models import Wallet, BitcoinAddress
        from btpay.connectors.wire import WireConnector
        from btpay.connectors.stablecoins import StablecoinAccount
        from btpay.invoicing.models import Invoice

        org = Organization(name='Multi Pay Corp', slug='multi-pay', default_currency='USD')
        org.save()

        # Bitcoin wallet
        wallet = Wallet(org_id=org.id, name='BTC', wallet_type='xpub',
            xpub='tpubD6NzVbkrYhZ4XgiXtGrdW5XDAPFCL9h7we1vwNCpn8tGbBcgfVYjXyhWo4E1xkh56hjod1RhGjxbaTLV3X4FyWuejifB9jusQ46QzG87VKp',
            network='testnet', is_active=True)
        wallet.save()
        ba = BitcoinAddress(wallet_id=wallet.id, address='tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx',
            derivation_index=0, status='assigned')
        ba.save()

        # Wire connector
        WireConnector(org_id=org.id, bank_name='Chase', account_name='Multi Pay',
            account_number='****1234', swift_code='CHASUS33', is_active=True).save()

        # Stablecoins
        StablecoinAccount(org_id=org.id, chain='ethereum', token='usdc',
            address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045').save()
        StablecoinAccount(org_id=org.id, chain='arbitrum', token='usdc',
            address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045').save()
        StablecoinAccount(org_id=org.id, chain='tron', token='usdt',
            address='TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf').save()

        inv = Invoice(
            org_id=org.id, invoice_number='MULTI-001', status='pending',
            total=Decimal('500'), currency='USD', btc_amount=Decimal('0.005'),
            btc_rate=Decimal('100000'), payment_address_id=ba.id,
            payment_methods_enabled=['onchain_btc', 'wire', 'stablecoins',
                'stable_ethereum_usdc', 'stable_arbitrum_usdc', 'stable_tron_usdt'],
        )
        inv.save()
        return org, inv

    def test_checkout_shows_all_methods(self, app):
        with app.app_context():
            org, inv = self._setup_invoice_with_connectors(app)
            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.ref_number)
            assert resp.status_code == 200
            html = resp.data.decode()
            # Should have method tabs
            assert 'Bitcoin' in html
            assert 'Wire Transfer' in html
            assert 'USDC' in html
            assert 'USDT' in html

    def test_checkout_shows_bitcoin_address(self, app):
        with app.app_context():
            org, inv = self._setup_invoice_with_connectors(app)
            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.ref_number)
            html = resp.data.decode()
            assert 'tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx' in html

    def test_checkout_shows_wire_details(self, app):
        with app.app_context():
            org, inv = self._setup_invoice_with_connectors(app)
            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.ref_number)
            html = resp.data.decode()
            assert 'Chase' in html
            assert 'CHASUS33' in html

    def test_checkout_shows_stablecoin_addresses(self, app):
        with app.app_context():
            org, inv = self._setup_invoice_with_connectors(app)
            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.ref_number)
            html = resp.data.decode()
            assert '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045' in html
            assert 'Ethereum' in html
            assert 'Arbitrum' in html

    def test_checkout_shows_chain_warning(self, app):
        with app.app_context():
            org, inv = self._setup_invoice_with_connectors(app)
            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.ref_number)
            html = resp.data.decode()
            assert 'Only send' in html
            assert 'wrong network' in html

    def test_checkout_stablecoin_amount_equals_fiat(self, app):
        '''Stablecoin amount should be the fiat total (1:1 peg).'''
        with app.app_context():
            org, inv = self._setup_invoice_with_connectors(app)
            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.ref_number)
            html = resp.data.decode()
            assert '500' in html  # fiat total as stablecoin amount
            assert 'USDC' in html

    def test_checkout_btc_only_no_tabs(self, app):
        '''Single payment method = no tab selector.'''
        with app.app_context():
            from btpay.auth.models import Organization
            from btpay.bitcoin.models import Wallet, BitcoinAddress
            from btpay.invoicing.models import Invoice

            org = Organization(name='BTC Only', slug='btc-only')
            org.save()
            wallet = Wallet(org_id=org.id, name='W', wallet_type='xpub',
                xpub='tpubD6NzVbkrYhZ4XgiXtGrdW5XDAPFCL9h7we1vwNCpn8tGbBcgfVYjXyhWo4E1xkh56hjod1RhGjxbaTLV3X4FyWuejifB9jusQ46QzG87VKp',
                network='testnet', is_active=True)
            wallet.save()
            ba = BitcoinAddress(wallet_id=wallet.id, address='tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx',
                derivation_index=0, status='assigned')
            ba.save()

            inv = Invoice(org_id=org.id, invoice_number='SINGLE-001', status='pending',
                total=Decimal('100'), currency='USD', btc_amount=Decimal('0.001'),
                payment_address_id=ba.id,
                payment_methods_enabled=['onchain_btc'])
            inv.save()

            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.ref_number)
            html = resp.data.decode()
            assert 'id="method-tabs"' not in html  # no tab bar for single method


# ======================================================================
# Story 8: Demo seed includes all connectors
# ======================================================================
class TestStory_DemoSeed:

    def _fresh_seed(self, app):
        '''Clear store and seed fresh demo data. Returns summary.'''
        from btpay.orm.engine import MemoryStore
        MemoryStore().clear()
        from btpay.demo.seed import seed_demo_data
        return seed_demo_data()

    def test_demo_seed_creates_wire_connector(self, app):
        with app.app_context():
            self._fresh_seed(app)

            from btpay.connectors.wire import WireConnector
            wc = WireConnector.query.all()
            assert len(wc) >= 1
            assert wc[0].bank_name == 'First National Bank'

    def test_demo_seed_creates_stablecoin_accounts(self, app):
        with app.app_context():
            summary = self._fresh_seed(app)
            assert summary['stablecoin_accounts'] == 6

            from btpay.connectors.stablecoins import StablecoinAccount
            accounts = StablecoinAccount.query.all()
            assert len(accounts) == 6

            chains = set(a.chain for a in accounts)
            tokens = set(a.token for a in accounts)
            assert 'ethereum' in chains
            assert 'arbitrum' in chains
            assert 'tron' in chains
            assert 'usdc' in tokens
            assert 'usdt' in tokens
            assert 'dai' in tokens

    def test_demo_seed_uses_famous_addresses(self, app):
        with app.app_context():
            self._fresh_seed(app)

            from btpay.demo.seed import DEMO_EVM_ADDRESS, DEMO_TRON_ADDRESS
            from btpay.connectors.stablecoins import StablecoinAccount
            eth_acct = StablecoinAccount.query.filter(chain='ethereum', token='usdc').first()
            assert eth_acct.address == DEMO_EVM_ADDRESS  # Vitalik's address

            tron_acct = StablecoinAccount.query.filter(chain='tron').first()
            assert tron_acct.address == DEMO_TRON_ADDRESS  # Justin Sun's address

    def test_demo_invoices_have_stablecoin_methods(self, app):
        with app.app_context():
            self._fresh_seed(app)

            from btpay.invoicing.models import Invoice
            inv = Invoice.get_by(invoice_number='DEMO-0001')
            assert inv is not None
            methods = list(inv.payment_methods_enabled or [])
            assert 'stablecoins' in methods
            assert 'stable_ethereum_usdc' in methods

    def test_demo_checkout_renders_with_stablecoins(self, app):
        '''End-to-end: demo seed -> checkout page shows stablecoins.'''
        with app.app_context():
            self._fresh_seed(app)

            from btpay.invoicing.models import Invoice
            # Pick a pending invoice
            inv = Invoice.get_by(invoice_number='DEMO-0010')
            assert inv is not None
            assert inv.status == 'pending'

            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.ref_number)
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'USDC' in html
            assert 'USDT' in html


# ======================================================================
# Story 9: Supported chains & tokens registries
# ======================================================================
class TestStory_Registries:

    def test_supported_chains(self, app):
        from btpay.connectors.stablecoins import SUPPORTED_CHAINS
        assert 'ethereum' in SUPPORTED_CHAINS
        assert 'arbitrum' in SUPPORTED_CHAINS
        assert 'base' in SUPPORTED_CHAINS
        assert 'polygon' in SUPPORTED_CHAINS
        assert 'optimism' in SUPPORTED_CHAINS
        assert 'avalanche' in SUPPORTED_CHAINS
        assert 'tron' in SUPPORTED_CHAINS
        assert 'solana' in SUPPORTED_CHAINS
        assert len(SUPPORTED_CHAINS) == 8

    def test_supported_tokens(self, app):
        from btpay.connectors.stablecoins import SUPPORTED_TOKENS
        assert 'usdt' in SUPPORTED_TOKENS
        assert 'usdc' in SUPPORTED_TOKENS
        assert 'dai' in SUPPORTED_TOKENS
        assert 'pyusd' in SUPPORTED_TOKENS
        assert len(SUPPORTED_TOKENS) == 4

    def test_all_evm_chains_share_addr_type(self, app):
        from btpay.connectors.stablecoins import SUPPORTED_CHAINS
        evm_chains = ['ethereum', 'arbitrum', 'base', 'polygon', 'optimism', 'avalanche']
        for chain in evm_chains:
            assert SUPPORTED_CHAINS[chain]['addr_type'] == 'evm'

    def test_non_evm_chains(self, app):
        from btpay.connectors.stablecoins import SUPPORTED_CHAINS
        assert SUPPORTED_CHAINS['tron']['addr_type'] == 'base58'
        assert SUPPORTED_CHAINS['solana']['addr_type'] == 'base58'


# ======================================================================
# Story 10: RBAC on connector settings
# ======================================================================
class TestStory_ConnectorRBAC:

    def test_viewer_cannot_access_wire_settings(self, app):
        '''Viewer role should be denied access to connector settings.'''
        with app.app_context():
            from btpay.auth.models import User, Organization, Membership, Session
            from btpay.security.hashing import hash_password, generate_random_token
            import hashlib

            user = User(email='viewer@test.com', first_name='View', last_name='Only')
            user.password_hash = hash_password('viewpass123')
            user.save()

            org = Organization(name='RBAC Org', slug='rbac-org')
            org.save()

            Membership(user_id=user.id, org_id=org.id, role='viewer').save()

            token = generate_random_token(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            from btpay.chrono import TIME_FUTURE
            Session(user_id=user.id, token_hash=token_hash,
                    expires_at=TIME_FUTURE(hours=24), org_id=org.id).save()

            client = app.test_client()
            client.set_cookie('btpay_session', token, domain='localhost')

            # All connector routes should deny viewer
            for path in ['/settings/connectors/bitcoin',
                         '/settings/connectors/wire',
                         '/settings/connectors/stablecoins']:
                resp = client.get(path)
                assert resp.status_code in (302, 403), \
                    'Viewer should not access %s (got %d)' % (path, resp.status_code)


# ======================================================================
# Story 11: Edge cases
# ======================================================================
class TestStory_EdgeCases:

    def test_multiple_same_chain_accounts(self, app):
        '''Multiple accounts on the same chain+token should be allowed.'''
        with app.app_context():
            from btpay.connectors.stablecoins import StablecoinAccount
            a1 = StablecoinAccount(org_id=1, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045')
            a1.save()
            a2 = StablecoinAccount(org_id=1, chain='ethereum', token='usdc',
                address='0x0000000000000000000000000000000000000001')
            a2.save()
            assert a1.id != a2.id

    def test_inactive_account_not_in_checkout(self, app):
        '''Inactive stablecoin accounts should not appear in checkout.'''
        with app.app_context():
            from btpay.auth.models import Organization
            from btpay.connectors.stablecoins import StablecoinAccount
            from btpay.invoicing.models import Invoice
            from btpay.frontend.checkout_views import _get_checkout_methods

            org = Organization(name='Inactive Test', slug='inactive-test')
            org.save()

            acct = StablecoinAccount(org_id=org.id, chain='ethereum', token='usdc',
                address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
                is_active=False)
            acct.save()

            inv = Invoice(org_id=org.id, invoice_number='EDGE-001', status='pending',
                total=Decimal('100'), currency='USD',
                payment_methods_enabled=['stable_ethereum_usdc', 'stablecoins'])
            inv.save()

            methods = _get_checkout_methods(inv, org)
            stable_methods = [m for m in methods if m['type'] == 'stablecoin']
            assert len(stable_methods) == 0

    def test_wire_disabled_not_in_checkout(self, app):
        '''Inactive wire connector should not appear in checkout.'''
        with app.app_context():
            from btpay.auth.models import Organization
            from btpay.connectors.wire import WireConnector
            from btpay.invoicing.models import Invoice
            from btpay.frontend.checkout_views import _get_checkout_methods

            org = Organization(name='Wire Off', slug='wire-off')
            org.save()

            WireConnector(org_id=org.id, bank_name='Bank', account_name='Corp',
                account_number='123', is_active=False).save()

            inv = Invoice(org_id=org.id, invoice_number='EDGE-002', status='pending',
                total=Decimal('100'), currency='USD',
                payment_methods_enabled=['wire'])
            inv.save()

            methods = _get_checkout_methods(inv, org)
            wire_methods = [m for m in methods if m['type'] == 'wire']
            assert len(wire_methods) == 0

    def test_config_connector_type_enum(self, app):
        import config_default
        assert config_default.ConnectorType.BITCOIN.value == 'bitcoin'
        assert config_default.ConnectorType.WIRE.value == 'wire'
        assert config_default.ConnectorType.STABLECOIN.value == 'stablecoin'

# EOF
