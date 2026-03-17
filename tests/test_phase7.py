#
# Phase 7 tests — middleware, logging, CLI, frontend integration
#
import json
import time
import logging
import pytest


# ---- Security Middleware ----

class TestSecurityMiddleware:
    '''Test per-route rate limiting and CSP.'''

    def test_rate_limiting(self, app):
        from btpay.security.middleware import get_route_limiter
        limiter = get_route_limiter()
        # Clear state
        limiter.reset('route:/auth/login:127.0.0.1')

        client = app.test_client()

        # 5 login attempts should be allowed
        for i in range(5):
            resp = client.post('/auth/login', json={'email': 'x@x.com', 'password': 'x'})
            assert resp.status_code != 429, 'Attempt %d should not be rate limited' % (i + 1)

        # 6th should be rate limited
        resp = client.post('/auth/login', json={'email': 'x@x.com', 'password': 'x'})
        assert resp.status_code == 429

        # Clean up
        limiter.reset('route:/auth/login:127.0.0.1')

    def test_csp_header_present(self):
        '''CSP is only added in non-testing mode.'''
        from app import create_app
        non_test_app = create_app({'TESTING': False, 'DATA_DIR': '/tmp/btpay_test', 'DEV_MODE': True})
        client = non_test_app.test_client()
        resp = client.get('/health')
        assert 'Content-Security-Policy' in resp.headers
        csp = resp.headers['Content-Security-Policy']
        assert "frame-ancestors 'none'" in csp
        assert "default-src 'self'" in csp

    def test_security_headers(self, app):
        client = app.test_client()
        resp = client.get('/health')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
        assert resp.headers.get('X-Frame-Options') == 'DENY'
        assert resp.headers.get('Referrer-Policy') == 'no-referrer'
        assert 'Permissions-Policy' in resp.headers

    def test_api_rate_limit_higher(self, app):
        from btpay.security.middleware import get_route_limiter
        limiter = get_route_limiter()
        limiter.reset('route:/api/v1/:127.0.0.1')

        client = app.test_client()
        # API has 100/min limit — first request should pass
        resp = client.get('/api/v1/invoices')
        assert resp.status_code != 429  # should be 401 (no auth), not 429

        limiter.reset('route:/api/v1/:127.0.0.1')


# ---- Structured Logging ----

class TestStructuredLogging:
    '''Test JSON and dev log formatters.'''

    def test_json_formatter(self):
        from btpay.logging_config import JsonFormatter
        fmt = JsonFormatter()

        record = logging.LogRecord(
            name='test', level=logging.INFO, pathname='', lineno=0,
            msg='hello world', args=(), exc_info=None,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert data['level'] == 'info'
        assert data['msg'] == 'hello world'
        assert 'ts' in data

    def test_json_formatter_with_exception(self):
        from btpay.logging_config import JsonFormatter
        fmt = JsonFormatter()

        try:
            raise ValueError('test error')
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name='test', level=logging.ERROR, pathname='', lineno=0,
            msg='oops', args=(), exc_info=exc_info,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert 'exception' in data
        assert 'ValueError' in data['exception']

    def test_dev_formatter(self):
        from btpay.logging_config import DevFormatter
        fmt = DevFormatter()

        record = logging.LogRecord(
            name='btpay.auth', level=logging.WARNING, pathname='', lineno=0,
            msg='watch out', args=(), exc_info=None,
        )
        output = fmt.format(record)
        assert 'watch out' in output
        assert 'auth' in output  # name is shortened


# ---- Frontend Integration ----

class TestFrontendIntegration:
    '''Test that frontend blueprints are registered and routes work.'''

    def test_index_redirects(self, app):
        client = app.test_client()
        resp = client.get('/')
        assert resp.status_code == 302  # redirect to login

    def test_dashboard_requires_auth(self, app):
        client = app.test_client()
        resp = client.get('/dashboard')
        assert resp.status_code == 302  # redirect to login

    def test_invoices_requires_auth(self, app):
        client = app.test_client()
        resp = client.get('/invoices/')
        assert resp.status_code == 302

    def test_settings_requires_auth(self, app):
        client = app.test_client()
        resp = client.get('/settings/general')
        assert resp.status_code == 302

    def test_checkout_public(self, app):
        '''Checkout routes should not require auth.'''
        client = app.test_client()
        resp = client.get('/checkout/NONEXISTENT')
        # Should return 404 for nonexistent invoice, not 302 redirect
        assert resp.status_code == 404

    def test_health_check(self, app):
        client = app.test_client()
        resp = client.get('/health')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'ok'

    def test_filters_registered(self, app):
        '''Check that Jinja2 filters are registered.'''
        env = app.jinja_env
        assert 'btc' in env.filters
        assert 'currency' in env.filters
        assert 'time_ago' in env.filters
        assert 'status_badge' in env.filters
        assert 'truncate_address' in env.filters

    def test_context_processor(self, app):
        '''Check that context processors inject expected vars.'''
        with app.test_request_context('/'):
            from flask import g
            g.user = None
            g.org = None
            ctx = {}
            for proc in app.template_context_processors[None]:
                result = proc()
                if result:
                    ctx.update(result)
            assert 'now' in ctx
            assert 'dev_mode' in ctx


# ---- Filters ----

class TestFilters:
    '''Test Jinja2 template filters.'''

    def test_btc_format(self):
        from btpay.frontend.filters import btc_format
        from decimal import Decimal
        assert btc_format(Decimal('1.00000000')) == '1.0'
        assert btc_format(Decimal('0.00100000')) == '0.001'
        assert btc_format(Decimal('0.12345678')) == '0.12345678'
        assert btc_format(0) == '0'
        assert btc_format(None) == '0'

    def test_currency_format(self):
        from btpay.frontend.filters import currency_format
        from decimal import Decimal
        result = currency_format(Decimal('1234.56'), 'USD')
        assert '$' in result
        assert '1,234.56' in result or '1234.56' in result

    def test_time_ago(self):
        from btpay.frontend.filters import time_ago
        from btpay.chrono import NOW, TIME_AGO
        result = time_ago(TIME_AGO(minutes=5))
        assert '5m ago' == result or 'min' in result

    def test_status_badge(self):
        from btpay.frontend.filters import status_badge
        result = status_badge('paid')
        assert 'Paid' in result
        assert 'bg-green' in result
        assert '<span' in result

    def test_truncate_address(self):
        from btpay.frontend.filters import truncate_address
        addr = 'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4'
        result = truncate_address(addr, 8, 6)
        assert result.startswith('bc1qw508')
        assert result.endswith('8f3t4')
        assert '...' in result

    def test_satoshi_format(self):
        from btpay.frontend.filters import satoshi_format
        assert satoshi_format(100000000) == '100,000,000'
        assert satoshi_format(0) == '0'
