#
# Phase 5 tests — API, Webhooks, Email, Serializers
#
import hashlib
import json
import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock


# ---- Fixtures ----

def _create_org():
    from btpay.auth.models import Organization
    org = Organization(
        name='Test Org', slug='test-org',
        default_currency='USD', invoice_prefix='INV',
        brand_color='#F89F1B',
    )
    org.save()
    return org


def _create_user():
    from btpay.auth.models import User
    user = User(email='admin@test.com', first_name='Admin', last_name='User')
    user.set_password('testpass123')
    user.save()
    return user


def _create_api_key(org, user):
    '''Create an API key, return (ApiKey, raw_key_string).'''
    from btpay.auth.models import ApiKey
    from btpay.security.hashing import generate_random_token

    raw_key = generate_random_token(32)
    key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()

    api_key = ApiKey(
        org_id=org.id,
        user_id=user.id,
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        label='Test Key',
        permissions=['invoices:read', 'invoices:write'],
    )
    api_key.save()
    return api_key, raw_key


def _create_invoice(org, user, status='draft', total=Decimal('100.00')):
    from btpay.invoicing.models import Invoice, InvoiceLine
    inv = Invoice(
        org_id=org.id,
        invoice_number='INV-0001',
        status=status,
        customer_email='customer@test.com',
        customer_name='John Doe',
        customer_company='Acme Inc',
        currency='USD',
        subtotal=total,
        total=total,
        created_by_user_id=user.id,
        payment_methods_enabled=['onchain_btc'],
    )
    inv.save()

    line = InvoiceLine(
        invoice_id=inv.id,
        description='Widget',
        quantity=Decimal('2'),
        unit_price=Decimal('50.00'),
        amount=Decimal('100.00'),
        sort_order=0,
    )
    line.save()

    return inv


def _auth_headers(raw_key):
    return {
        'Authorization': 'Bearer %s' % raw_key,
        'Content-Type': 'application/json',
    }


# ==== Serializer Tests ====

class TestSerializers:

    def test_serialize_invoice(self):
        from btpay.api.serializers import serialize_invoice
        org = _create_org()
        user = _create_user()
        inv = _create_invoice(org, user)
        result = serialize_invoice(inv)

        assert result['invoice_number'] == 'INV-0001'
        assert result['status'] == 'draft'
        assert result['currency'] == 'USD'
        assert result['total'] == '100.00'
        assert result['customer_name'] == 'John Doe'
        assert 'lines' in result
        assert len(result['lines']) == 1
        assert result['lines'][0]['description'] == 'Widget'

    def test_serialize_invoice_no_lines(self):
        from btpay.api.serializers import serialize_invoice
        org = _create_org()
        user = _create_user()
        inv = _create_invoice(org, user)
        result = serialize_invoice(inv, include_lines=False)

        assert 'lines' not in result
        assert result['total'] == '100.00'

    def test_serialize_invoice_with_payments(self):
        from btpay.api.serializers import serialize_invoice
        from btpay.invoicing.models import Payment
        org = _create_org()
        user = _create_user()
        inv = _create_invoice(org, user, status='paid')

        Payment(
            invoice_id=inv.id,
            method='onchain_btc',
            txid='abc123',
            amount_btc=Decimal('0.001'),
            amount_fiat=Decimal('100.00'),
            exchange_rate=Decimal('100000'),
            status='confirmed',
        ).save()

        result = serialize_invoice(inv, include_payments=True)
        assert 'payments' in result
        assert len(result['payments']) == 1
        assert result['payments'][0]['txid'] == 'abc123'

    def test_serialize_payment_link(self):
        from btpay.api.serializers import serialize_payment_link
        from btpay.invoicing.models import PaymentLink
        org = _create_org()

        pl = PaymentLink(
            org_id=org.id, slug='donate', title='Donate',
            amount=Decimal('25.00'), currency='USD',
            payment_methods_enabled=['onchain_btc'],
        )
        pl.save()

        result = serialize_payment_link(pl)
        assert result['slug'] == 'donate'
        assert result['title'] == 'Donate'
        assert result['amount'] == '25.00'
        assert result['currency'] == 'USD'

    def test_serialize_rate(self):
        from btpay.api.serializers import serialize_rate
        result = serialize_rate('USD', Decimal('98765.43'))
        assert result['currency'] == 'USD'
        assert result['rate'] == '98765.43'


# ==== Webhook Model Tests ====

class TestWebhookModels:

    def test_webhook_endpoint_create(self):
        from btpay.api.webhook_models import WebhookEndpoint
        org = _create_org()

        ep = WebhookEndpoint(
            org_id=org.id,
            url='https://example.com/webhook',
            secret='test-secret',
            events=['invoice.paid', 'invoice.confirmed'],
            description='Test endpoint',
        )
        ep.save()

        assert ep.id is not None
        assert ep.url == 'https://example.com/webhook'
        assert ep.is_active is True
        assert 'invoice.paid' in ep.subscribed_events
        assert 'invoice.confirmed' in ep.subscribed_events

    def test_webhook_endpoint_wildcard(self):
        from btpay.api.webhook_models import WebhookEndpoint
        org = _create_org()

        ep = WebhookEndpoint(
            org_id=org.id,
            url='https://example.com/all',
            events=['*'],
        )
        ep.save()

        assert '*' in ep.subscribed_events

    def test_webhook_delivery_create(self):
        from btpay.api.webhook_models import WebhookEndpoint, WebhookDelivery
        org = _create_org()
        ep = WebhookEndpoint(org_id=org.id, url='https://example.com/wh').save()

        delivery = WebhookDelivery(
            endpoint_id=ep.id,
            event='invoice.paid',
            payload={'test': True},
        )
        delivery.save()

        assert delivery.id is not None
        assert delivery.event == 'invoice.paid'
        assert delivery.delivered is False
        assert delivery.attempts == 0


# ==== Webhook Dispatcher Tests ====

class TestWebhookDispatcher:

    def test_sign_payload(self):
        from btpay.api.webhooks import WebhookDispatcher
        sig = WebhookDispatcher._sign('{"test":1}', 'secret123')
        assert len(sig) == 64  # hex SHA256

    def test_verify_signature(self):
        from btpay.api.webhooks import WebhookDispatcher
        payload = '{"event":"test"}'
        secret = 'mysecret'
        sig = WebhookDispatcher._sign(payload, secret)
        assert WebhookDispatcher.verify_signature(payload, sig, secret) is True
        assert WebhookDispatcher.verify_signature(payload, 'wrong', secret) is False

    def test_sign_empty_secret(self):
        from btpay.api.webhooks import WebhookDispatcher
        sig = WebhookDispatcher._sign('{"test":1}', '')
        assert sig == ''

    @patch('btpay.api.webhooks.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch('btpay.api.webhooks.requests.post')
    def test_dispatch_delivery(self, mock_post, mock_dns):
        from btpay.api.webhooks import WebhookDispatcher
        from btpay.api.webhook_models import WebhookEndpoint, WebhookDelivery
        org = _create_org()

        ep = WebhookEndpoint(
            org_id=org.id,
            url='https://example.com/hook',
            secret='test-secret',
            events=['invoice.paid'],
        )
        ep.save()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'OK'
        mock_post.return_value = mock_resp

        dispatcher = WebhookDispatcher(retry_delays=[])
        dispatcher._deliver(ep, 'invoice.paid', {'invoice_id': 1})

        # Check delivery was created
        deliveries = WebhookDelivery.query.filter(endpoint_id=ep.id).all()
        assert len(deliveries) == 1
        assert deliveries[0].delivered is True
        assert deliveries[0].response_status == 200

    @patch('btpay.api.webhooks.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch('btpay.api.webhooks.requests.post')
    def test_dispatch_failure(self, mock_post, mock_dns):
        from btpay.api.webhooks import WebhookDispatcher
        from btpay.api.webhook_models import WebhookEndpoint, WebhookDelivery
        org = _create_org()

        ep = WebhookEndpoint(
            org_id=org.id,
            url='https://example.com/hook',
            secret='sec',
            events=['*'],
        )
        ep.save()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = 'Internal Server Error'
        mock_post.return_value = mock_resp

        dispatcher = WebhookDispatcher(retry_delays=[])  # no retries
        dispatcher._deliver(ep, 'invoice.paid', {'id': 1})

        deliveries = WebhookDelivery.query.filter(endpoint_id=ep.id).all()
        assert len(deliveries) == 1
        assert deliveries[0].delivered is False
        assert deliveries[0].error == 'HTTP 500'


# ==== Email Tests ====

class TestEmailService:

    def test_not_configured(self):
        from btpay.email.service import EmailService
        svc = EmailService({})
        assert svc.is_configured() is False
        assert svc.send('a@b.com', 'Subj', '<p>Hi</p>') is False

    def test_configured(self):
        from btpay.email.service import EmailService
        svc = EmailService({'server': 'smtp.example.com', 'port': 587})
        assert svc.is_configured() is True

    def test_for_org_with_org_smtp(self):
        from btpay.email.service import EmailService
        org = _create_org()
        org.smtp_config = {'server': 'org-smtp.example.com', 'port': 587}
        org.save()

        svc = EmailService.for_org(org, {'SMTP_CONFIG': {'server': 'app-smtp.example.com'}})
        assert svc.is_configured() is True
        assert svc._get('server') == 'org-smtp.example.com'

    def test_for_org_fallback_to_app(self):
        from btpay.email.service import EmailService
        from btpay.dictobj import DictObj
        org = _create_org()

        app_config = {'SMTP_CONFIG': DictObj(server='app-smtp.example.com', port=587)}
        svc = EmailService.for_org(org, app_config)
        assert svc._get('server') == 'app-smtp.example.com'

    @patch('btpay.email.service.smtplib')
    def test_send_email(self, mock_smtplib):
        from btpay.email.service import EmailService

        mock_smtp = MagicMock()
        mock_smtplib.SMTP.return_value = mock_smtp

        svc = EmailService({
            'server': 'smtp.test.com',
            'port': 587,
            'username': 'user',
            'password': 'pass',
            'from_address': 'noreply@test.com',
            'use_tls': True,
        })

        result = svc.send('to@test.com', 'Test Subject', '<p>Hello</p>')
        assert result is True
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with('user', 'pass')
        mock_smtp.sendmail.assert_called_once()
        mock_smtp.quit.assert_called_once()

    @patch('btpay.email.service.smtplib')
    def test_send_ssl_port(self, mock_smtplib):
        from btpay.email.service import EmailService

        mock_smtp = MagicMock()
        mock_smtplib.SMTP_SSL.return_value = mock_smtp

        svc = EmailService({
            'server': 'smtp.test.com',
            'port': 465,
            'from_address': 'noreply@test.com',
        })

        result = svc.send('to@test.com', 'Test', '<p>Hi</p>')
        assert result is True
        mock_smtplib.SMTP_SSL.assert_called_once()

    def test_send_invoice_created_no_email(self):
        from btpay.email.service import EmailService
        from btpay.invoicing.models import Invoice
        org = _create_org()
        inv = Invoice(org_id=org.id, invoice_number='X-001', customer_email='')
        inv.save()

        svc = EmailService({'server': 'smtp.test.com'})
        result = svc.send_invoice_created(inv, org)
        assert result is False


# ==== Email Template Tests ====

class TestEmailTemplates:

    def test_render_invoice_created(self):
        from btpay.email.templates import render_invoice_created
        org = _create_org()
        user = _create_user()
        inv = _create_invoice(org, user)

        html = render_invoice_created(inv, org, 'https://example.com/pay')
        assert 'INV-0001' in html
        assert 'Test Org' in html
        assert 'Pay Now' in html
        assert 'Widget' in html
        assert '$100.00' in html

    def test_render_payment_received(self):
        from btpay.email.templates import render_payment_received
        from btpay.invoicing.models import Payment
        org = _create_org()
        user = _create_user()
        inv = _create_invoice(org, user)

        payment = Payment(
            invoice_id=inv.id, method='onchain_btc',
            amount_btc=Decimal('0.001'), amount_fiat=Decimal('100.00'),
            status='pending',
        )
        payment.save()

        html = render_payment_received(inv, payment, org)
        assert 'Payment Received' in html
        assert 'INV-0001' in html
        assert '0.001' in html

    def test_render_payment_confirmed(self):
        from btpay.email.templates import render_payment_confirmed
        from btpay.invoicing.models import Payment
        org = _create_org()
        user = _create_user()
        inv = _create_invoice(org, user)

        payment = Payment(
            invoice_id=inv.id, method='onchain_btc',
            amount_btc=Decimal('0.001'), amount_fiat=Decimal('100.00'),
            confirmations=6, status='confirmed',
        )
        payment.save()

        html = render_payment_confirmed(inv, payment, org)
        assert 'Payment Confirmed' in html
        assert 'Payment Complete' in html
        assert '6' in html


# ==== API Route Tests (Flask test client) ====

class TestAPIRoutes:

    def _setup_api(self, app):
        '''Create org, user, API key and return (client, org, user, raw_key).'''
        with app.app_context():
            org = _create_org()
            user = _create_user()
            api_key, raw_key = _create_api_key(org, user)
            return app.test_client(), org, user, raw_key

    def test_no_auth(self, app):
        client = app.test_client()
        resp = client.get('/api/v1/invoices')
        assert resp.status_code == 401

    def test_bad_auth(self, app):
        client = app.test_client()
        resp = client.get('/api/v1/invoices',
                          headers={'Authorization': 'Bearer invalid-key'})
        assert resp.status_code == 401

    def test_list_invoices_empty(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.get('/api/v1/invoices',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['invoices'] == []
            assert data['total'] == 0

    def test_create_invoice(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            # Need exchange rate service mock
            from unittest.mock import MagicMock
            svc = MagicMock()
            svc.get_rate.return_value = Decimal('100000')
            app._exchange_rate_service = svc

            resp = client.post('/api/v1/invoices',
                               headers=_auth_headers(raw_key),
                               json={
                                   'lines': [
                                       {'description': 'Widget A', 'quantity': 1, 'unit_price': '50.00'},
                                       {'description': 'Widget B', 'quantity': 2, 'unit_price': '25.00'},
                                   ],
                                   'customer_email': 'cust@test.com',
                                   'customer_name': 'Jane',
                                   'currency': 'USD',
                               })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data['status'] == 'draft'
            assert data['customer_email'] == 'cust@test.com'
            assert data['customer_name'] == 'Jane'
            assert Decimal(data['total']) == Decimal('100.00')

    def test_create_invoice_no_lines(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.post('/api/v1/invoices',
                               headers=_auth_headers(raw_key),
                               json={})
            assert resp.status_code == 400
            assert 'line item' in resp.get_json()['error'].lower()

    def test_get_invoice(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            inv = _create_invoice(org, user)
            resp = client.get('/api/v1/invoices/INV-0001',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['invoice_number'] == 'INV-0001'

    def test_get_invoice_not_found(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.get('/api/v1/invoices/NOPE-999',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 404

    def test_invoice_status(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            inv = _create_invoice(org, user, status='pending')
            resp = client.get('/api/v1/invoices/INV-0001/status',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['status'] == 'pending'
            assert data['total'] == '100.00'
            assert data['amount_due'] == '100.00'

    def test_cancel_invoice(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            inv = _create_invoice(org, user, status='draft')
            resp = client.delete('/api/v1/invoices/INV-0001',
                                 headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['ok'] is True

    def test_list_payment_links_empty(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.get('/api/v1/payment-links',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            assert resp.get_json()['payment_links'] == []

    def test_create_payment_link(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.post('/api/v1/payment-links',
                               headers=_auth_headers(raw_key),
                               json={
                                   'title': 'Donate',
                                   'slug': 'donate',
                                   'amount': '10.00',
                                   'currency': 'USD',
                               })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data['slug'] == 'donate'
            assert data['title'] == 'Donate'
            assert data['amount'] == '10.00'

    def test_create_payment_link_no_title(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.post('/api/v1/payment-links',
                               headers=_auth_headers(raw_key),
                               json={})
            assert resp.status_code == 400
            assert 'title' in resp.get_json()['error'].lower()

    def test_create_payment_link_auto_slug(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.post('/api/v1/payment-links',
                               headers=_auth_headers(raw_key),
                               json={'title': 'My Cool Payment'})
            assert resp.status_code == 201
            data = resp.get_json()
            assert data['slug'] == 'my-cool-payment'

    def test_create_payment_link_duplicate_slug(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            from btpay.invoicing.models import PaymentLink
            PaymentLink(org_id=org.id, slug='donate', title='Old').save()

            resp = client.post('/api/v1/payment-links',
                               headers=_auth_headers(raw_key),
                               json={'title': 'New', 'slug': 'donate'})
            assert resp.status_code == 400
            assert 'slug' in resp.get_json()['error'].lower()

    def test_delete_payment_link(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            from btpay.invoicing.models import PaymentLink
            pl = PaymentLink(org_id=org.id, slug='to-delete', title='Del')
            pl.save()

            resp = client.delete('/api/v1/payment-links/to-delete',
                                 headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            assert resp.get_json()['ok'] is True

            # Verify deactivated
            pl2 = PaymentLink.get(pl.id)
            assert pl2.is_active is False

    def test_delete_payment_link_not_found(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.delete('/api/v1/payment-links/nonexistent',
                                 headers=_auth_headers(raw_key))
            assert resp.status_code == 404

    def test_get_rates(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            from unittest.mock import MagicMock
            svc = MagicMock()
            svc.get_rates.return_value = {
                'USD': Decimal('98765.43'),
                'EUR': Decimal('91234.56'),
            }
            app._exchange_rate_service = svc

            resp = client.get('/api/v1/rates',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data['rates']) == 2
            currencies = [r['currency'] for r in data['rates']]
            assert 'USD' in currencies
            assert 'EUR' in currencies

    def test_get_rates_no_service(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            # Remove rate service
            if hasattr(app, '_exchange_rate_service'):
                delattr(app, '_exchange_rate_service')

            resp = client.get('/api/v1/rates',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 503

    def test_list_webhooks_empty(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.get('/api/v1/webhooks',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            assert resp.get_json()['webhooks'] == []

    def test_create_webhook(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.post('/api/v1/webhooks',
                               headers=_auth_headers(raw_key),
                               json={
                                   'url': 'https://example.com/hook',
                                   'events': ['invoice.paid', 'invoice.confirmed'],
                                   'description': 'My webhook',
                               })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data['url'] == 'https://example.com/hook'
            assert 'secret' in data  # shown once
            assert len(data['secret']) > 0
            assert set(data['events']) == {'invoice.paid', 'invoice.confirmed'}

    def test_create_webhook_no_url(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.post('/api/v1/webhooks',
                               headers=_auth_headers(raw_key),
                               json={})
            assert resp.status_code == 400

    def test_delete_webhook(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            from btpay.api.webhook_models import WebhookEndpoint
            ep = WebhookEndpoint(
                org_id=org.id, url='https://del.example.com', events=['*'],
            )
            ep.save()

            resp = client.delete('/api/v1/webhooks/%d' % ep.id,
                                 headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            assert resp.get_json()['ok'] is True

    def test_delete_webhook_not_found(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            resp = client.delete('/api/v1/webhooks/99999',
                                 headers=_auth_headers(raw_key))
            assert resp.status_code == 404

    def test_invoice_lookup_by_id_rejected(self, app):
        '''Raw integer ID lookup is disabled to prevent enumeration.'''
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            inv = _create_invoice(org, user)
            resp = client.get('/api/v1/invoices/%d' % inv.id,
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 404

    def test_different_org_invoice_not_visible(self, app):
        '''Ensure invoices from another org are not accessible.'''
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            from btpay.auth.models import Organization
            from btpay.invoicing.models import Invoice

            other_org = Organization(name='Other Org', slug='other-org')
            other_org.save()

            inv = Invoice(
                org_id=other_org.id,
                invoice_number='OTHER-001',
                status='pending',
            )
            inv.save()

            resp = client.get('/api/v1/invoices/OTHER-001',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 404

    def test_list_invoices_with_filter(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            from btpay.invoicing.models import Invoice
            Invoice(org_id=org.id, invoice_number='INV-A', status='draft').save()
            Invoice(org_id=org.id, invoice_number='INV-B', status='paid').save()
            Invoice(org_id=org.id, invoice_number='INV-C', status='paid').save()

            resp = client.get('/api/v1/invoices?status=paid',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['total'] == 2
            for inv in data['invoices']:
                assert inv['status'] == 'paid'

    def test_list_invoices_pagination(self, app):
        client, org, user, raw_key = self._setup_api(app)
        with app.app_context():
            from btpay.invoicing.models import Invoice
            for i in range(5):
                Invoice(org_id=org.id, invoice_number='INV-%03d' % i, status='draft').save()

            resp = client.get('/api/v1/invoices?limit=2&offset=1',
                              headers=_auth_headers(raw_key))
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['total'] == 5
            assert data['limit'] == 2
            assert data['offset'] == 1
            assert len(data['invoices']) == 2


# ==== App Integration Test ====

class TestAppPhase5Integration:

    def test_api_blueprint_registered(self, app):
        '''Verify API blueprint is registered and health check works.'''
        with app.app_context():
            client = app.test_client()

            # Health check still works
            resp = client.get('/health')
            assert resp.status_code == 200

            # API endpoint exists (returns 401 without auth)
            resp = client.get('/api/v1/invoices')
            assert resp.status_code == 401

    def test_webhook_models_loaded(self, app):
        '''Verify webhook models are importable and registered.'''
        with app.app_context():
            from btpay.api.webhook_models import WebhookEndpoint, WebhookDelivery
            ep = WebhookEndpoint(org_id=1, url='https://test.com')
            ep.save()
            assert ep.id is not None

# EOF
