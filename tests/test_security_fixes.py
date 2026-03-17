#
# Tests for security fixes — refnum keying, checkout enumeration,
# API rate limiting, TOTP throttling, confirm_payment flush,
# and webhook retry timing.
#
import hashlib
import os
import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock


# ---- Helpers ----

def _make_org(**kw):
    from btpay.auth.models import Organization
    defaults = dict(name='Test Org', slug='sec-test-org', default_currency='USD')
    defaults.update(kw)
    org = Organization(**defaults)
    org.save()
    return org


def _make_user(**kw):
    from btpay.auth.models import User
    defaults = dict(email='sectest@example.com', first_name='Sec', last_name='Test')
    defaults.update(kw)
    user = User(**defaults)
    user.set_password('testpass123')
    user.save()
    return user


def _make_invoice(org, status='pending', **kw):
    from btpay.invoicing.models import Invoice
    defaults = dict(
        org_id=org.id,
        invoice_number='SEC-0001',
        status=status,
        currency='USD',
        total=Decimal('100'),
    )
    defaults.update(kw)
    inv = Invoice(**defaults)
    inv.save()
    return inv


# ======================================================================
# Fix 1: Refnum crypto keys wired to config
# ======================================================================

class TestRefnumConfigKeys:

    def test_reconfigure_changes_keys(self):
        '''reconfigure() updates the singleton keys.'''
        from btpay.security.refnums import ReferenceNumbers
        from btpay.orm.model import MemModel, BaseMixin
        from btpay.orm.columns import Text

        class ReconfigTestModel(BaseMixin, MemModel):
            name = Text()

        rn = ReferenceNumbers()
        if 'ReconfigTestModel' not in rn.class_names:
            rn.class_names.append('ReconfigTestModel')
        # Refresh class_map to include the newly defined test model
        from btpay.orm.model import get_model_registry
        rn.class_map = get_model_registry()

        # Save original keys to restore later
        original_box = ReferenceNumbers._cls._box
        original_nonce = ReferenceNumbers._cls._nonce

        obj = ReconfigTestModel(name='test')
        obj.save()

        # Pack with current (default) keys
        ref_default = rn.pack(obj)

        # Reconfigure with new keys (32 bytes key, 24 bytes nonce)
        rn.reconfigure('aa' * 32, 'bb' * 24)

        # Pack with new keys — should produce different refnum
        ref_new = rn.pack(obj)
        assert ref_default != ref_new

        # Unpack should work with new keys
        result = rn.unpack(ref_new, just_pk=True)
        assert result[1] == obj.id

        # Old refnum should fail with new keys
        with pytest.raises(ValueError, match='Corrupt refnum'):
            rn.unpack(ref_default)

        # Restore original keys so other tests aren't affected
        ReferenceNumbers._cls._box = original_box
        ReferenceNumbers._cls._nonce = original_nonce

    def test_app_startup_configures_refnums(self):
        '''App startup calls reconfigure() with config keys — using non-default
        keys proves that create_app() actually applied them.'''
        from app import create_app
        from btpay.security.refnums import ReferenceNumbers

        # Save original keys to restore later
        original_box = ReferenceNumbers._cls._box
        original_nonce = ReferenceNumbers._cls._nonce

        custom_key = 'dd' * 32    # 32 bytes
        custom_nonce = 'ee' * 24  # 24 bytes

        app = create_app({
            'TESTING': True,
            'DATA_DIR': '/tmp/btpay_test_refnum',
            'REFNUM_KEY': custom_key,
            'REFNUM_NONCE': custom_nonce,
        })

        with app.app_context():
            rn = ReferenceNumbers()
            from btpay.orm.model import get_model_registry
            rn.class_map = get_model_registry()

            from btpay.invoicing.models import Invoice
            org = _make_org()
            inv = _make_invoice(org)

            # Pack with the app-configured (non-default) keys
            ref_custom = rn.pack(inv)
            assert rn.regex.match(ref_custom)

            # Verify the refnum was produced with the custom keys:
            # temporarily switch to different keys and confirm unpacking fails
            rn.reconfigure('ff' * 32, 'aa' * 24)
            with pytest.raises(ValueError, match='Corrupt refnum'):
                rn.unpack(ref_custom)

            # Restore custom keys — unpack should succeed
            rn.reconfigure(custom_key, custom_nonce)
            result = rn.unpack(ref_custom, expect_class=Invoice)
            assert result.id == inv.id

        # Restore original keys so other tests aren't affected
        ReferenceNumbers._cls._box = original_box
        ReferenceNumbers._cls._nonce = original_nonce


# ======================================================================
# Fix 2: Public checkout rejects invoice_number, requires refnum
# ======================================================================

class TestCheckoutEnumeration:

    def _setup(self, app):
        from btpay.invoicing.models import Invoice
        from btpay.bitcoin.models import Wallet, BitcoinAddress
        org = _make_org()
        wallet = Wallet(org_id=org.id, name='W', wallet_type='xpub',
            xpub='tpubD6NzVbkrYhZ4XgiXtGrdW5XDAPFCL9h7we1vwNCpn8tGbBcgfVYjXyhWo4E1xkh56hjod1RhGjxbaTLV3X4FyWuejifB9jusQ46QzG87VKp',
            network='testnet', is_active=True)
        wallet.save()
        ba = BitcoinAddress(wallet_id=wallet.id,
            address='tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx',
            derivation_index=0, status='assigned')
        ba.save()
        inv = _make_invoice(org, status='pending',
            payment_address_id=ba.id,
            payment_methods_enabled=['onchain_btc'],
            btc_amount=Decimal('0.001'))
        return inv, org

    def test_invoice_number_returns_404(self, app):
        '''Sequential invoice number must not resolve on public checkout.'''
        with app.app_context():
            inv, org = self._setup(app)
            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.invoice_number)
            assert resp.status_code == 404

    def test_refnum_returns_200(self, app):
        '''Valid refnum resolves correctly.'''
        with app.app_context():
            inv, org = self._setup(app)
            client = app.test_client()
            resp = client.get('/checkout/%s' % inv.ref_number)
            assert resp.status_code == 200

    def test_status_json_via_refnum(self, app):
        '''status.json works via refnum.'''
        with app.app_context():
            inv, org = self._setup(app)
            client = app.test_client()
            resp = client.get('/checkout/%s/status.json' % inv.ref_number)
            assert resp.status_code == 200
            data = resp.get_json()
            assert 'status' in data

    def test_status_json_via_invoice_number_404(self, app):
        '''status.json rejects invoice_number.'''
        with app.app_context():
            inv, org = self._setup(app)
            client = app.test_client()
            resp = client.get('/checkout/%s/status.json' % inv.invoice_number)
            assert resp.status_code == 404

    def test_receipt_via_invoice_number_404(self, app):
        '''Receipt page rejects invoice_number.'''
        with app.app_context():
            inv, org = self._setup(app)
            client = app.test_client()
            resp = client.get('/checkout/%s/receipt' % inv.invoice_number)
            assert resp.status_code == 404

    def test_qr_via_invoice_number_404(self, app):
        '''QR endpoint rejects invoice_number.'''
        with app.app_context():
            inv, org = self._setup(app)
            client = app.test_client()
            resp = client.get('/checkout/%s/qr' % inv.invoice_number)
            assert resp.status_code == 404


# ======================================================================
# Fix 3: API rate limiter uses global instance
# ======================================================================

class TestApiRateLimiter:

    def test_api_limiter_is_module_level(self):
        '''_api_limiter must be a module-level singleton, not per-request.'''
        from btpay.auth.decorators import _api_limiter
        from btpay.security.rate_limit import RateLimiter
        assert isinstance(_api_limiter, RateLimiter)

    def test_api_rate_limit_accumulates(self, app):
        '''API rate limiter accumulates across requests.'''
        from btpay.auth.models import User, Organization, Membership, ApiKey
        from btpay.security.hashing import generate_random_token

        with app.app_context():
            user = _make_user()
            org = _make_org(slug='api-rl-org')
            Membership(user_id=user.id, org_id=org.id, role='owner').save()

            raw_key = generate_random_token(32)
            key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
            ak = ApiKey(org_id=org.id, user_id=user.id, key_hash=key_hash,
                        key_prefix=raw_key[:8], label='RL Test', is_active=True)
            ak.save()

            # Override rate limit to something small for testing
            app.config['RATE_LIMIT_API'] = type('C', (), {
                'max_attempts': 3, 'window_seconds': 60})()

            client = app.test_client()
            headers = {'Authorization': 'Bearer ' + raw_key,
                       'Accept': 'application/json'}

            # First 3 requests should succeed
            for _ in range(3):
                resp = client.get('/api/v1/invoices', headers=headers)
                assert resp.status_code != 429

            # 4th request should be rate-limited
            resp = client.get('/api/v1/invoices', headers=headers)
            assert resp.status_code == 429


# ======================================================================
# Fix 4: TOTP login rate limiting
# ======================================================================

class TestTotpLoginRateLimit:

    def test_totp_login_rate_limited(self, app):
        '''TOTP login endpoint returns 429 after too many attempts.'''
        client = app.test_client()

        # We don't need a valid login_token for this — rate limit fires first
        for i in range(6):
            resp = client.post('/auth/login/totp', json={
                'login_token': 'fake-token',
                'totp_code': '000000',
            })

        # After 5+ attempts, should get 429
        resp = client.post('/auth/login/totp', json={
            'login_token': 'fake-token',
            'totp_code': '000000',
        })
        assert resp.status_code == 429
        assert 'Too many' in resp.get_json().get('error', '')


# ======================================================================
# Fix 5: confirm_payment() flushes to disk
# ======================================================================

class TestConfirmPaymentFlush:

    def test_confirm_payment_flushes(self):
        '''confirm_payment() calls _flush_to_disk().'''
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.models import Invoice, Payment

        org = _make_org(slug='flush-org')
        inv = _make_invoice(org, status='paid', invoice_number='FLUSH-001')
        payment = Payment(
            invoice_id=inv.id,
            method='onchain_btc',
            txid='abc123',
            amount_btc=Decimal('0.001'),
            amount_fiat=Decimal('100'),
            confirmations=0,
            status='pending',
        )
        payment.save()

        svc = InvoiceService(data_dir='/tmp/btpay_test_flush')
        os.makedirs('/tmp/btpay_test_flush', exist_ok=True)

        with patch.object(svc, '_flush_to_disk') as mock_flush:
            svc.confirm_payment(inv, payment, 6)
            mock_flush.assert_called_once()

        assert inv.status == 'confirmed'


# ======================================================================
# Fix 7: Webhook retry timing honors config
# ======================================================================

class TestWebhookRetryTiming:

    @patch('btpay.api.webhooks.time.sleep')
    @patch('btpay.api.webhooks.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch('btpay.api.webhooks.requests.post')
    def test_retry_honors_configured_delays(self, mock_post, mock_dns, mock_sleep):
        '''Retry delays should match configured values, not be capped at 10s.'''
        from btpay.api.webhooks import WebhookDispatcher
        from btpay.api.webhook_models import WebhookEndpoint, WebhookDelivery

        org = _make_org(slug='wh-retry-org')
        ep = WebhookEndpoint(
            org_id=org.id, url='https://example.com/hook',
            secret='s', events=['*'])
        ep.save()

        # Always fail
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = 'Error'
        mock_post.return_value = mock_resp

        delays = [60, 300, 900]
        dispatcher = WebhookDispatcher(retry_delays=delays)
        dispatcher._deliver(ep, 'test.event', {'id': 1})

        # Verify sleep was called with the configured delays (not capped at 10)
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_args == delays

    def test_max_sleep_caps_for_tests(self):
        '''_max_sleep parameter caps delays for fast tests.'''
        from btpay.api.webhooks import WebhookDispatcher
        d = WebhookDispatcher(retry_delays=[60, 300], _max_sleep=0)
        assert d._max_sleep == 0

    def test_default_no_cap(self):
        '''Default construction has no sleep cap.'''
        from btpay.api.webhooks import WebhookDispatcher
        d = WebhookDispatcher()
        assert d._max_sleep is None


# ======================================================================
# SSRF protection: redirect and DNS rebinding
# ======================================================================

class TestWebhookSsrfProtection:

    @patch('btpay.api.webhooks.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.1', 443))])
    @patch('btpay.api.webhooks.requests.post')
    def test_private_ip_blocked(self, mock_post, mock_dns):
        '''Webhook to hostname resolving to private IP is blocked.'''
        from btpay.api.webhooks import WebhookDispatcher
        from btpay.api.webhook_models import WebhookEndpoint, WebhookDelivery

        org = _make_org(slug='ssrf-priv-org')
        ep = WebhookEndpoint(org_id=org.id, url='https://evil.com/hook',
                             secret='s', events=['*'])
        ep.save()

        delivery = WebhookDelivery(endpoint_id=ep.id, event='test', payload={})
        delivery.save()

        dispatcher = WebhookDispatcher(retry_delays=[])
        result = dispatcher._attempt(ep.url, '{}', 'sig', delivery)

        assert result is False
        assert 'SSRF blocked' in delivery.error
        mock_post.assert_not_called()

    @patch('btpay.api.webhooks.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch('btpay.api.webhooks.requests.post')
    def test_redirects_disabled(self, mock_post, mock_dns):
        '''requests.post is called with allow_redirects=False.'''
        from btpay.api.webhooks import WebhookDispatcher
        from btpay.api.webhook_models import WebhookEndpoint, WebhookDelivery

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'OK'
        mock_post.return_value = mock_resp

        org = _make_org(slug='ssrf-redir-org')
        ep = WebhookEndpoint(org_id=org.id, url='https://example.com/hook',
                             secret='s', events=['*'])
        ep.save()

        delivery = WebhookDelivery(endpoint_id=ep.id, event='test', payload={})
        delivery.save()

        dispatcher = WebhookDispatcher(retry_delays=[])
        dispatcher._attempt(ep.url, '{}', 'sig', delivery)

        # Verify allow_redirects=False was passed
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs['allow_redirects'] is False

    @patch('btpay.api.webhooks.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch('btpay.api.webhooks.requests.post')
    def test_uses_original_url_for_tls_verification(self, mock_post, mock_dns):
        '''requests.post uses the original URL so TLS hostname verification can work.'''
        from btpay.api.webhooks import WebhookDispatcher
        from btpay.api.webhook_models import WebhookEndpoint, WebhookDelivery

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'OK'
        mock_post.return_value = mock_resp

        org = _make_org(slug='ssrf-ip-org')
        ep = WebhookEndpoint(org_id=org.id, url='https://example.com/hook',
                             secret='s', events=['*'])
        ep.save()

        delivery = WebhookDelivery(endpoint_id=ep.id, event='test', payload={})
        delivery.save()

        dispatcher = WebhookDispatcher(retry_delays=[])
        dispatcher._attempt(ep.url, '{}', 'sig', delivery)

        # The URL passed to requests.post should keep the original hostname.
        call_url = mock_post.call_args[0][0]
        assert call_url == 'https://example.com/hook'

    @patch('btpay.api.webhooks.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch('btpay.api.webhooks.requests.post')
    def test_tls_verification_remains_enabled(self, mock_post, mock_dns):
        '''Webhook delivery must not disable TLS verification.'''
        from btpay.api.webhooks import WebhookDispatcher
        from btpay.api.webhook_models import WebhookEndpoint, WebhookDelivery

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'OK'
        mock_post.return_value = mock_resp

        org = _make_org(slug='ssrf-tls-org')
        ep = WebhookEndpoint(org_id=org.id, url='https://example.com/hook',
                             secret='s', events=['*'])
        ep.save()

        delivery = WebhookDelivery(endpoint_id=ep.id, event='test', payload={})
        delivery.save()

        dispatcher = WebhookDispatcher(retry_delays=[])
        dispatcher._attempt(ep.url, '{}', 'sig', delivery)

        call_kwargs = mock_post.call_args[1]
        assert 'verify' not in call_kwargs


# EOF
