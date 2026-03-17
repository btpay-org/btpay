#
# Security middleware — per-route rate limiting, CSP, request logging
#
import logging
import time
from flask import request, g, jsonify, current_app

from btpay.security.rate_limit import RateLimiter

log = logging.getLogger(__name__)

# Global rate limiter instance for all routes
_route_limiter = RateLimiter()

# Default rate limits by route prefix
RATE_LIMITS = {
    '/auth/login':      (5, 60),       # 5 per minute
    '/auth/register':   (3, 60),       # 3 per minute
    '/auth/totp':       (5, 60),       # 5 per minute
    '/auth/password':   (3, 60),       # 3 per minute
    '/api/v1/':         (100, 60),     # 100 per minute
    '/checkout/':       (30, 60),      # 30 per minute
}


def register_security_middleware(app):
    '''Register all security middleware on the Flask app.'''

    @app.before_request
    def rate_limit_check():
        '''Per-route rate limiting based on IP. Disabled in demo mode.'''
        if app.config.get('DEMO_MODE'):
            return

        path = request.path
        ip = request.remote_addr or '0.0.0.0'

        for prefix, (max_req, window) in RATE_LIMITS.items():
            if path.startswith(prefix):
                key = 'route:%s:%s' % (prefix, ip)
                if not _route_limiter.check(key, max_req, window):
                    return jsonify(error='Too many requests'), 429
                break

    @app.before_request
    def record_request_start():
        '''Record request start time for logging.'''
        g._request_start = time.time()

    @app.after_request
    def add_csp_header(response):
        '''Add Content-Security-Policy header.'''
        if app.config.get('TESTING'):
            return response

        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )

        response.headers['Content-Security-Policy'] = csp
        return response

    @app.after_request
    def log_request(response):
        '''Structured request logging.'''
        duration = 0
        if hasattr(g, '_request_start'):
            duration = int((time.time() - g._request_start) * 1000)

        # Skip health checks and static files
        if request.path in ('/health',) or request.path.startswith('/static/'):
            return response

        log.info('request method=%s path=%s status=%d duration=%dms ip=%s',
                 request.method, request.path, response.status_code,
                 duration, request.remote_addr or '-')

        return response


def get_route_limiter():
    '''Get the global route rate limiter (for testing).'''
    return _route_limiter

# EOF
