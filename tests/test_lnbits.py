#
# Tests for LNbits connector, client, monitor, and payment method
#
import pytest
import time
from decimal import Decimal
from unittest.mock import patch, MagicMock


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


# ======================================================================
# Story 1: LNbits connector model & validation
# ======================================================================
class TestStory_LNbitsConnectorModel:

    def test_create_lnbits_connector(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector
            conn = LNbitsConnector(
                org_id=1, name='My LNbits',
                server_url='https://lnbits.example.com',
                api_key='invoice-key-abc123',
            )
            conn.save()
            assert conn.id > 0
            assert conn.is_active is True
            assert conn.server_url == 'https://lnbits.example.com'

    def test_validate_lnbits_connector_valid(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector, validate_lnbits_connector
            conn = LNbitsConnector(
                server_url='https://lnbits.example.com',
                api_key='key123',
            )
            valid, errors = validate_lnbits_connector(conn)
            assert valid
            assert errors == []

    def test_validate_lnbits_connector_missing_url(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector, validate_lnbits_connector
            conn = LNbitsConnector(api_key='key')
            valid, errors = validate_lnbits_connector(conn)
            assert not valid
            assert 'Server URL is required' in errors

    def test_validate_lnbits_connector_bad_url(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector, validate_lnbits_connector
            conn = LNbitsConnector(server_url='not-a-url', api_key='key')
            valid, errors = validate_lnbits_connector(conn)
            assert not valid
            assert any('http' in e for e in errors)

    def test_validate_lnbits_connector_missing_key(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector, validate_lnbits_connector
            conn = LNbitsConnector(server_url='https://lnbits.example.com')
            valid, errors = validate_lnbits_connector(conn)
            assert not valid
            assert 'API key (invoice key) is required' in errors

    def test_lnbits_payment_info(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector, lnbits_payment_info
            from btpay.invoicing.models import Invoice
            conn = LNbitsConnector(
                server_url='https://lnbits.example.com',
                api_key='key',
            )
            inv = Invoice(
                invoice_number='INV-001', total=Decimal('50'),
                currency='USD', metadata={
                    'lnbits_bolt11': 'lnbc500n1p...',
                    'lnbits_payment_hash': 'abc123def456',
                    'lnbits_amount_sat': 50000,
                },
            )
            inv.save()
            info = lnbits_payment_info(conn, inv)
            assert info['bolt11'] == 'lnbc500n1p...'
            assert info['payment_hash'] == 'abc123def456'
            assert info['amount_sat'] == 50000
            assert info['amount'] == '50'


# ======================================================================
# Story 2: LNbits client (mocked HTTP)
# ======================================================================
class TestStory_LNbitsClient:

    def test_from_connector(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector, LNbitsClient
            conn = LNbitsConnector(
                server_url='https://lnbits.example.com',
                api_key='mykey',
            )
            client = LNbitsClient.from_connector(conn)
            assert client.server_url == 'https://lnbits.example.com'
            assert client.api_key == 'mykey'

    @patch('btpay.connectors.lnbits.requests.get')
    def test_test_connection_success(self, mock_get, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'name': 'Test Wallet', 'balance': 1000000}
        mock_get.return_value = mock_resp

        from btpay.connectors.lnbits import LNbitsClient
        client = LNbitsClient('https://lnbits.example.com', 'key')
        ok, info = client.test_connection()
        assert ok
        assert info['name'] == 'Test Wallet'
        assert info['balance_msat'] == 1000000

    @patch('btpay.connectors.lnbits.requests.get')
    def test_test_connection_failure(self, mock_get, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = 'Unauthorized'
        mock_get.return_value = mock_resp

        from btpay.connectors.lnbits import LNbitsClient
        client = LNbitsClient('https://lnbits.example.com', 'bad-key')
        ok, info = client.test_connection()
        assert not ok
        assert '401' in info

    @patch('btpay.connectors.lnbits.requests.post')
    def test_create_invoice_success(self, mock_post, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            'payment_hash': 'abc123',
            'payment_request': 'lnbc50000n1p...',
        }
        mock_post.return_value = mock_resp

        from btpay.connectors.lnbits import LNbitsClient
        client = LNbitsClient('https://lnbits.example.com', 'key')
        result = client.create_invoice(50000, memo='Test invoice')
        assert result['payment_hash'] == 'abc123'
        assert result['payment_request'] == 'lnbc50000n1p...'

    @patch('btpay.connectors.lnbits.requests.post')
    def test_create_invoice_failure(self, mock_post, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = 'Bad request'
        mock_post.return_value = mock_resp

        from btpay.connectors.lnbits import LNbitsClient, LNbitsError
        client = LNbitsClient('https://lnbits.example.com', 'key')
        with pytest.raises(LNbitsError):
            client.create_invoice(50000)

    @patch('btpay.connectors.lnbits.requests.get')
    def test_check_payment_paid(self, mock_get, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'paid': True, 'payment_hash': 'abc123'}
        mock_get.return_value = mock_resp

        from btpay.connectors.lnbits import LNbitsClient
        client = LNbitsClient('https://lnbits.example.com', 'key')
        result = client.check_payment('abc123')
        assert result['paid'] is True

    @patch('btpay.connectors.lnbits.requests.get')
    def test_check_payment_unpaid(self, mock_get, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'paid': False, 'payment_hash': 'abc123'}
        mock_get.return_value = mock_resp

        from btpay.connectors.lnbits import LNbitsClient
        client = LNbitsClient('https://lnbits.example.com', 'key')
        result = client.check_payment('abc123')
        assert result['paid'] is False


# ======================================================================
# Story 3: LNbits payment method registry
# ======================================================================
class TestStory_LNbitsPaymentMethod:

    def test_lnbits_method_registered(self, app):
        with app.app_context():
            from btpay.invoicing.payment_methods import get_method
            method = get_method('lnbits')
            assert method is not None
            assert method.display_name == 'Lightning (LNbits)'
            assert method.method_type == 'lightning'

    def test_lnbits_available_with_connector(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector
            from btpay.invoicing.payment_methods import get_method
            from btpay.auth.models import Organization

            org = Organization(name='Test', slug='test-lnbits')
            org.save()
            LNbitsConnector(org_id=org.id, server_url='https://lnbits.test.com',
                           api_key='key', is_active=True).save()

            method = get_method('lnbits')
            assert method.is_available(org)

    def test_lnbits_unavailable_without_connector(self, app):
        with app.app_context():
            from btpay.invoicing.payment_methods import get_method
            from btpay.auth.models import Organization

            org = Organization(name='Empty', slug='empty-lnbits')
            org.save()

            method = get_method('lnbits')
            assert not method.is_available(org)

    def test_lnbits_unavailable_when_inactive(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector
            from btpay.invoicing.payment_methods import get_method
            from btpay.auth.models import Organization

            org = Organization(name='Test', slug='test-lnbits-inactive')
            org.save()
            LNbitsConnector(org_id=org.id, server_url='https://lnbits.test.com',
                           api_key='key', is_active=False).save()

            method = get_method('lnbits')
            assert not method.is_available(org)


# ======================================================================
# Story 4: LNbits monitor
# ======================================================================
class TestStory_LNbitsMonitor:

    def test_monitor_watch_unwatch(self, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector
            from btpay.connectors.lnbits_monitor import LNbitsMonitor

            conn = LNbitsConnector(server_url='https://lnbits.test.com', api_key='key')

            monitor = LNbitsMonitor(check_interval=60)
            assert monitor.watched_count == 0

            monitor.watch(1, 'hash001', conn)
            assert monitor.watched_count == 1

            monitor.watch(2, 'hash002', conn)
            assert monitor.watched_count == 2

            monitor.unwatch(1)
            assert monitor.watched_count == 1

            monitor.unwatch(2)
            assert monitor.watched_count == 0

    @patch('btpay.connectors.lnbits.requests.get')
    def test_monitor_detects_payment(self, mock_get, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector
            from btpay.connectors.lnbits_monitor import LNbitsMonitor

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {'paid': True, 'payment_hash': 'hash001'}
            mock_get.return_value = mock_resp

            conn = LNbitsConnector(server_url='https://lnbits.test.com', api_key='key')

            monitor = LNbitsMonitor()
            results = []
            monitor.on_payment(lambda inv_id, data: results.append((inv_id, data['paid'])))

            monitor.watch(1, 'hash001', conn)
            entries = list(monitor._watched.items())
            for inv_id, entry in entries:
                monitor._check_entry(inv_id, entry)

            assert len(results) == 1
            assert results[0] == (1, True)
            assert monitor.watched_count == 0  # removed after payment

    @patch('btpay.connectors.lnbits.requests.get')
    def test_monitor_ignores_unpaid(self, mock_get, app):
        with app.app_context():
            from btpay.connectors.lnbits import LNbitsConnector
            from btpay.connectors.lnbits_monitor import LNbitsMonitor

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {'paid': False, 'payment_hash': 'hash001'}
            mock_get.return_value = mock_resp

            conn = LNbitsConnector(server_url='https://lnbits.test.com', api_key='key')

            monitor = LNbitsMonitor()
            results = []
            monitor.on_payment(lambda inv_id, data: results.append(inv_id))

            monitor.watch(1, 'hash001', conn)
            entries = list(monitor._watched.items())
            for inv_id, entry in entries:
                monitor._check_entry(inv_id, entry)

            assert len(results) == 0
            assert monitor.watched_count == 1  # still watching


# ======================================================================
# Story 5: Checkout integration
# ======================================================================
class TestStory_LNbitsCheckout:

    def test_checkout_methods_include_lnbits(self, app):
        with app.app_context():
            from btpay.auth.models import Organization
            from btpay.connectors.lnbits import LNbitsConnector
            from btpay.invoicing.models import Invoice
            from btpay.frontend.checkout_views import _get_checkout_methods

            org = Organization(name='LN Org', slug='lnbits-checkout')
            org.save()

            LNbitsConnector(org_id=org.id, server_url='https://lnbits.test.com',
                           api_key='key', is_active=True).save()

            inv = Invoice(org_id=org.id, invoice_number='LN-001', status='pending',
                         total=Decimal('50'), currency='USD',
                         payment_methods_enabled=['lnbits'],
                         metadata={'lnbits_bolt11': 'lnbc50000n1p...',
                                   'lnbits_payment_hash': 'abc123',
                                   'lnbits_amount_sat': 50000})
            inv.save()

            methods = _get_checkout_methods(inv, org)
            ln_methods = [m for m in methods if m['type'] == 'lightning']
            assert len(ln_methods) == 1
            assert ln_methods[0]['display_name'] == 'Lightning'
            assert ln_methods[0]['info']['bolt11'] == 'lnbc50000n1p...'

    def test_checkout_methods_exclude_inactive_lnbits(self, app):
        with app.app_context():
            from btpay.auth.models import Organization
            from btpay.connectors.lnbits import LNbitsConnector
            from btpay.invoicing.models import Invoice
            from btpay.frontend.checkout_views import _get_checkout_methods

            org = Organization(name='LN Off', slug='lnbits-off')
            org.save()

            LNbitsConnector(org_id=org.id, server_url='https://lnbits.test.com',
                           api_key='key', is_active=False).save()

            inv = Invoice(org_id=org.id, invoice_number='LN-002', status='pending',
                         total=Decimal('50'), currency='USD',
                         payment_methods_enabled=['lnbits'])
            inv.save()

            methods = _get_checkout_methods(inv, org)
            ln_methods = [m for m in methods if m['type'] == 'lightning']
            assert len(ln_methods) == 0


# ======================================================================
# Story 6: Config enums
# ======================================================================
class TestStory_LNbitsConfig:

    def test_connector_type_enum(self, app):
        import config_default
        assert config_default.ConnectorType.LNBITS.value == 'lnbits'

    def test_payment_method_type_enum(self, app):
        import config_default
        assert config_default.PaymentMethodType.LNBITS.value == 'lnbits'

# EOF
