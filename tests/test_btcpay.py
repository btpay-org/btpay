#
# Tests for BTCPay Server connector, client, monitor, and payment method
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
# Story 1: BTCPay connector model & validation
# ======================================================================
class TestStory_BTCPayConnectorModel:

    def test_create_btcpay_connector(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector
            conn = BTCPayConnector(
                org_id=1, name='My BTCPay',
                server_url='https://btcpay.example.com',
                api_key='test-api-key-123',
                store_id='store-id-456',
            )
            conn.save()
            assert conn.id > 0
            assert conn.is_active is True
            assert conn.server_url == 'https://btcpay.example.com'

    def test_validate_btcpay_connector_valid(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector, validate_btcpay_connector
            conn = BTCPayConnector(
                server_url='https://btcpay.example.com',
                api_key='key123', store_id='store456',
            )
            valid, errors = validate_btcpay_connector(conn)
            assert valid
            assert errors == []

    def test_validate_btcpay_connector_missing_url(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector, validate_btcpay_connector
            conn = BTCPayConnector(api_key='key', store_id='store')
            valid, errors = validate_btcpay_connector(conn)
            assert not valid
            assert 'Server URL is required' in errors

    def test_validate_btcpay_connector_bad_url(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector, validate_btcpay_connector
            conn = BTCPayConnector(server_url='not-a-url', api_key='key', store_id='store')
            valid, errors = validate_btcpay_connector(conn)
            assert not valid
            assert any('http' in e for e in errors)

    def test_validate_btcpay_connector_missing_key(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector, validate_btcpay_connector
            conn = BTCPayConnector(server_url='https://x.com', store_id='store')
            valid, errors = validate_btcpay_connector(conn)
            assert not valid
            assert 'API key is required' in errors

    def test_validate_btcpay_connector_missing_store(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector, validate_btcpay_connector
            conn = BTCPayConnector(server_url='https://x.com', api_key='key')
            valid, errors = validate_btcpay_connector(conn)
            assert not valid
            assert 'Store ID is required' in errors

    def test_btcpay_payment_info(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector, btcpay_payment_info
            from btpay.invoicing.models import Invoice
            conn = BTCPayConnector(
                server_url='https://btcpay.example.com',
                api_key='key', store_id='store',
            )
            inv = Invoice(
                invoice_number='INV-001', total=Decimal('500'),
                currency='USD', metadata={
                    'btcpay_invoice_id': 'bp-inv-123',
                    'btcpay_checkout_url': 'https://btcpay.example.com/i/bp-inv-123',
                },
            )
            inv.save()
            info = btcpay_payment_info(conn, inv)
            assert info['btcpay_invoice_id'] == 'bp-inv-123'
            assert info['checkout_url'] == 'https://btcpay.example.com/i/bp-inv-123'
            assert info['amount'] == '500'


# ======================================================================
# Story 2: BTCPay client (mocked HTTP)
# ======================================================================
class TestStory_BTCPayClient:

    def test_from_connector(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector, BTCPayClient
            conn = BTCPayConnector(
                server_url='https://btcpay.example.com',
                api_key='mykey', store_id='mystore',
            )
            client = BTCPayClient.from_connector(conn)
            assert client.server_url == 'https://btcpay.example.com'
            assert client.api_key == 'mykey'
            assert client.store_id == 'mystore'

    @patch('btpay.connectors.btcpay.requests.get')
    def test_test_connection_success(self, mock_get, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'name': 'Test Store', 'id': 'store123'}
        mock_get.return_value = mock_resp

        from btpay.connectors.btcpay import BTCPayClient
        client = BTCPayClient('https://btcpay.example.com', 'key', 'store123')
        ok, info = client.test_connection()
        assert ok
        assert info['name'] == 'Test Store'

    @patch('btpay.connectors.btcpay.requests.get')
    def test_test_connection_failure(self, mock_get, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = 'Unauthorized'
        mock_get.return_value = mock_resp

        from btpay.connectors.btcpay import BTCPayClient
        client = BTCPayClient('https://btcpay.example.com', 'bad-key', 'store')
        ok, info = client.test_connection()
        assert not ok
        assert '401' in info

    @patch('btpay.connectors.btcpay.requests.post')
    def test_create_invoice_success(self, mock_post, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'id': 'inv123',
            'checkoutLink': 'https://btcpay.example.com/i/inv123',
            'status': 'New',
        }
        mock_post.return_value = mock_resp

        from btpay.connectors.btcpay import BTCPayClient
        client = BTCPayClient('https://btcpay.example.com', 'key', 'store')
        result = client.create_invoice(100.00, 'USD', order_id='INV-001')
        assert result['id'] == 'inv123'
        assert 'checkoutLink' in result

    @patch('btpay.connectors.btcpay.requests.post')
    def test_create_invoice_failure(self, mock_post, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = 'Bad request'
        mock_post.return_value = mock_resp

        from btpay.connectors.btcpay import BTCPayClient, BTCPayError
        client = BTCPayClient('https://btcpay.example.com', 'key', 'store')
        with pytest.raises(BTCPayError):
            client.create_invoice(100.00, 'USD')

    @patch('btpay.connectors.btcpay.requests.get')
    def test_get_invoice_success(self, mock_get, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'id': 'inv123', 'status': 'Settled'}
        mock_get.return_value = mock_resp

        from btpay.connectors.btcpay import BTCPayClient
        client = BTCPayClient('https://btcpay.example.com', 'key', 'store')
        result = client.get_invoice('inv123')
        assert result['status'] == 'Settled'

    @patch('btpay.connectors.btcpay.requests.get')
    def test_get_invoice_payment_methods(self, mock_get, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {'paymentMethod': 'BTC-OnChain', 'destination': 'bc1q...', 'amount': '0.001'},
        ]
        mock_get.return_value = mock_resp

        from btpay.connectors.btcpay import BTCPayClient
        client = BTCPayClient('https://btcpay.example.com', 'key', 'store')
        methods = client.get_invoice_payment_methods('inv123')
        assert len(methods) == 1
        assert methods[0]['paymentMethod'] == 'BTC-OnChain'


# ======================================================================
# Story 3: BTCPay payment method registry
# ======================================================================
class TestStory_BTCPayPaymentMethod:

    def test_btcpay_method_registered(self, app):
        with app.app_context():
            from btpay.invoicing.payment_methods import get_method
            method = get_method('btcpay')
            assert method is not None
            assert method.display_name == 'BTCPay Server'
            assert method.method_type == 'btcpay'

    def test_btcpay_available_with_connector(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector
            from btpay.invoicing.payment_methods import get_method
            from btpay.auth.models import Organization

            org = Organization(name='Test', slug='test-btcpay')
            org.save()
            BTCPayConnector(org_id=org.id, server_url='https://btcpay.test.com',
                           api_key='key', store_id='store', is_active=True).save()

            method = get_method('btcpay')
            assert method.is_available(org)

    def test_btcpay_unavailable_without_connector(self, app):
        with app.app_context():
            from btpay.invoicing.payment_methods import get_method
            from btpay.auth.models import Organization

            org = Organization(name='Empty', slug='empty-btcpay')
            org.save()

            method = get_method('btcpay')
            assert not method.is_available(org)

    def test_btcpay_unavailable_when_inactive(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector
            from btpay.invoicing.payment_methods import get_method
            from btpay.auth.models import Organization

            org = Organization(name='Test', slug='test-btcpay-inactive')
            org.save()
            BTCPayConnector(org_id=org.id, server_url='https://btcpay.test.com',
                           api_key='key', store_id='store', is_active=False).save()

            method = get_method('btcpay')
            assert not method.is_available(org)


# ======================================================================
# Story 4: BTCPay monitor
# ======================================================================
class TestStory_BTCPayMonitor:

    def test_monitor_watch_unwatch(self, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector
            from btpay.connectors.btcpay_monitor import BTCPayMonitor

            conn = BTCPayConnector(server_url='https://btcpay.test.com',
                                  api_key='key', store_id='store')

            monitor = BTCPayMonitor(check_interval=60)
            assert monitor.watched_count == 0

            monitor.watch(1, 'bp-inv-001', conn)
            assert monitor.watched_count == 1

            monitor.watch(2, 'bp-inv-002', conn)
            assert monitor.watched_count == 2

            monitor.unwatch(1)
            assert monitor.watched_count == 1

            monitor.unwatch(2)
            assert monitor.watched_count == 0

    @patch('btpay.connectors.btcpay.requests.get')
    def test_monitor_detects_settled(self, mock_get, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector
            from btpay.connectors.btcpay_monitor import BTCPayMonitor

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {'id': 'bp-inv-001', 'status': 'Settled'}
            mock_get.return_value = mock_resp

            conn = BTCPayConnector(server_url='https://btcpay.test.com',
                                  api_key='key', store_id='store')

            monitor = BTCPayMonitor()
            results = []
            monitor.on_payment(lambda inv_id, status, data: results.append((inv_id, status)))

            monitor.watch(1, 'bp-inv-001', conn)
            # Manually trigger check
            entries = list(monitor._watched.items())
            for inv_id, entry in entries:
                monitor._check_entry(inv_id, entry)

            assert len(results) == 1
            assert results[0] == (1, 'Settled')
            assert monitor.watched_count == 0  # removed after settlement

    @patch('btpay.connectors.btcpay.requests.get')
    def test_monitor_ignores_new_status(self, mock_get, app):
        with app.app_context():
            from btpay.connectors.btcpay import BTCPayConnector
            from btpay.connectors.btcpay_monitor import BTCPayMonitor

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {'id': 'bp-inv-001', 'status': 'New'}
            mock_get.return_value = mock_resp

            conn = BTCPayConnector(server_url='https://btcpay.test.com',
                                  api_key='key', store_id='store')

            monitor = BTCPayMonitor()
            results = []
            monitor.on_payment(lambda inv_id, status, data: results.append((inv_id, status)))

            monitor.watch(1, 'bp-inv-001', conn)
            entries = list(monitor._watched.items())
            for inv_id, entry in entries:
                monitor._check_entry(inv_id, entry)

            assert len(results) == 0
            assert monitor.watched_count == 1  # still watching


# ======================================================================
# Story 5: Checkout integration
# ======================================================================
class TestStory_BTCPayCheckout:

    def test_checkout_methods_include_btcpay(self, app):
        with app.app_context():
            from btpay.auth.models import Organization
            from btpay.connectors.btcpay import BTCPayConnector
            from btpay.invoicing.models import Invoice
            from btpay.frontend.checkout_views import _get_checkout_methods

            org = Organization(name='BTCPay Org', slug='btcpay-checkout')
            org.save()

            BTCPayConnector(org_id=org.id, server_url='https://btcpay.test.com',
                           api_key='key', store_id='store', is_active=True).save()

            inv = Invoice(org_id=org.id, invoice_number='BP-001', status='pending',
                         total=Decimal('100'), currency='USD',
                         payment_methods_enabled=['btcpay'],
                         metadata={'btcpay_invoice_id': 'bp-123',
                                   'btcpay_checkout_url': 'https://btcpay.test.com/i/bp-123'})
            inv.save()

            methods = _get_checkout_methods(inv, org)
            btcpay_methods = [m for m in methods if m['type'] == 'btcpay']
            assert len(btcpay_methods) == 1
            assert btcpay_methods[0]['display_name'] == 'BTCPay Server'
            assert btcpay_methods[0]['info']['btcpay_invoice_id'] == 'bp-123'

    def test_checkout_methods_exclude_inactive_btcpay(self, app):
        with app.app_context():
            from btpay.auth.models import Organization
            from btpay.connectors.btcpay import BTCPayConnector
            from btpay.invoicing.models import Invoice
            from btpay.frontend.checkout_views import _get_checkout_methods

            org = Organization(name='BTCPay Off', slug='btcpay-off')
            org.save()

            BTCPayConnector(org_id=org.id, server_url='https://btcpay.test.com',
                           api_key='key', store_id='store', is_active=False).save()

            inv = Invoice(org_id=org.id, invoice_number='BP-002', status='pending',
                         total=Decimal('100'), currency='USD',
                         payment_methods_enabled=['btcpay'])
            inv.save()

            methods = _get_checkout_methods(inv, org)
            btcpay_methods = [m for m in methods if m['type'] == 'btcpay']
            assert len(btcpay_methods) == 0


# ======================================================================
# Story 6: Config enums
# ======================================================================
class TestStory_BTCPayConfig:

    def test_connector_type_enum(self, app):
        import config_default
        assert config_default.ConnectorType.BTCPAY.value == 'btcpay'

    def test_payment_method_type_enum(self, app):
        import config_default
        assert config_default.PaymentMethodType.BTCPAY.value == 'btcpay'

# EOF
