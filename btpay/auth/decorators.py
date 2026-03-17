#
# Auth decorators — @login_required, @role_required, @api_auth, @csrf_protect
#
import hashlib
from functools import wraps
from flask import request, g, redirect, url_for, jsonify, current_app
from btpay.security.rate_limit import RateLimiter

# Module-level rate limiter for API key auth (must be shared across requests)
_api_limiter = RateLimiter()


def login_required(f):
    '''Require a valid session. Sets g.user, g.org, g.session_token.'''
    @wraps(f)
    def wrapper(*args, **kwargs):
        from btpay.auth.sessions import validate_session

        cookie_name = current_app.config.get('AUTH_COOKIE_NAME', 'btpay_session')
        token = request.cookies.get(cookie_name)

        if not token:
            if _is_api_request():
                return jsonify(error='Authentication required'), 401
            return redirect(url_for('auth.login_page'))

        result = validate_session(token)
        if result is None:
            if _is_api_request():
                return jsonify(error='Session expired'), 401
            return redirect(url_for('auth.login_page'))

        g.user, g.org = result
        g.session_token = token
        return f(*args, **kwargs)

    return wrapper


def role_required(min_role):
    '''Require minimum role in current org. Must be used after @login_required.'''
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from btpay.auth.models import Membership

            if not hasattr(g, 'user') or not hasattr(g, 'org'):
                return jsonify(error='Authentication required'), 401

            if g.org is None:
                return jsonify(error='No organization selected'), 403

            membership = Membership.query.filter(
                user_id=g.user.id,
                org_id=g.org.id,
            ).first()

            if membership is None or not membership.has_role(min_role):
                return jsonify(error='Insufficient permissions'), 403

            g.membership = membership
            return f(*args, **kwargs)

        return wrapper
    return decorator


def api_auth(f):
    '''Authenticate via API key (Bearer token). Sets g.user, g.org, g.api_key.'''
    @wraps(f)
    def wrapper(*args, **kwargs):
        from btpay.auth.models import ApiKey, User, Organization

        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify(error='API key required'), 401

        raw_key = auth_header[7:].strip()
        if not raw_key:
            return jsonify(error='API key required'), 401

        # Hash the key for lookup
        key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
        api_key = ApiKey.get_by(key_hash=key_hash)

        if api_key is None or not api_key.is_active:
            return jsonify(error='Invalid API key'), 401

        # Rate limit by key prefix
        rl_cfg = current_app.config.get('RATE_LIMIT_API')
        max_attempts = getattr(rl_cfg, 'max_attempts', 100) if rl_cfg else 100
        window = getattr(rl_cfg, 'window_seconds', 60) if rl_cfg else 60

        if not _api_limiter.check('api:' + api_key.key_prefix, max_attempts, window):
            return jsonify(error='Rate limit exceeded'), 429

        user = User.get(api_key.user_id)
        org = Organization.get(api_key.org_id)

        if user is None or not user.is_active:
            return jsonify(error='Invalid API key'), 401

        # Update last used
        from btpay.chrono import NOW
        api_key.last_used_at = NOW()
        api_key.save()

        g.user = user
        g.org = org
        g.api_key = api_key
        return f(*args, **kwargs)

    return wrapper


def csrf_protect(f):
    '''Validate CSRF token on state-changing requests (POST, PUT, DELETE).'''
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE'):
            from btpay.security.csrf import validate_csrf_token

            token = (request.form.get('_csrf_token')
                     or request.headers.get('X-CSRF-Token')
                     or '')

            session_id = ''
            if hasattr(g, 'session_token') and g.session_token:
                session_id = g.session_token
            elif hasattr(g, 'user') and g.user:
                session_id = str(g.user.id)

            secret = current_app.config.get('SECRET_KEY', '')

            if not validate_csrf_token(session_id, token, secret):
                if _is_api_request():
                    return jsonify(error='Invalid CSRF token'), 403
                return 'Forbidden', 403

        return f(*args, **kwargs)

    return wrapper


def _is_api_request():
    '''Check if this looks like an API/JSON request.'''
    accept = request.headers.get('Accept', '')
    return ('application/json' in accept
            or request.path.startswith('/api/')
            or request.is_json)

# EOF
