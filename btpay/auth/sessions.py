#
# Server-side session management
#
# Sessions are stored in the ORM (Session model).
# Raw token is given to the client; we store SHA-256 hash.
#
import hashlib
from btpay.security.hashing import generate_random_token
from btpay.chrono import NOW, TIME_FUTURE, as_time_t


def _hash_token(token):
    '''SHA-256 hash of a raw session token.'''
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_session(user, org, request, hours=None):
    '''
    Create a new server-side session.
    Returns the raw token string (to be set as cookie).
    '''
    from btpay.auth.models import Session

    if hours is None:
        from flask import current_app
        hours = current_app.config.get('SESSION_COOKIE_HOURS', 720)

    raw_token = generate_random_token(32)
    token_hash = _hash_token(raw_token)

    ip = ''
    ua = ''
    if request:
        ip = request.remote_addr or ''
        ua = (request.headers.get('User-Agent') or '')[:512]

    sess = Session(
        user_id=user.id,
        token_hash=token_hash,
        ip_address=ip,
        user_agent=ua,
        expires_at=TIME_FUTURE(hours=hours),
        org_id=org.id if org else 0,
    )
    sess.save()
    return raw_token


def validate_session(token_str):
    '''
    Validate a session token.
    Returns (User, Organization) tuple or None if invalid/expired.
    '''
    from btpay.auth.models import Session, User, Organization

    if not token_str:
        return None

    token_hash = _hash_token(token_str)
    sess = Session.get_by(token_hash=token_hash)
    if sess is None:
        return None

    # Check expiry
    exp = sess.expires_at
    if exp:
        exp_t = as_time_t(exp) if not isinstance(exp, (int, float)) else exp
        now_t = as_time_t(NOW())
        if exp_t <= now_t:
            # Expired — clean up
            sess.delete()
            return None

    user = User.get(sess.user_id)
    if user is None or not user.is_active:
        sess.delete()
        return None

    org = Organization.get(sess.org_id) if sess.org_id else None
    return (user, org)


def destroy_session(token_str):
    '''Delete a session by raw token.'''
    from btpay.auth.models import Session

    if not token_str:
        return
    token_hash = _hash_token(token_str)
    sess = Session.get_by(token_hash=token_hash)
    if sess:
        sess.delete()


def set_session_cookie(response, token, hours=None):
    '''Set the session cookie on a Flask response.'''
    from flask import current_app

    if hours is None:
        hours = current_app.config.get('SESSION_COOKIE_HOURS', 720)
    cookie_name = current_app.config.get('AUTH_COOKIE_NAME', 'btpay_session')
    is_secure = not current_app.config.get('DEV_MODE', False)

    response.set_cookie(
        cookie_name,
        token,
        max_age=int(hours * 3600),
        httponly=True,
        secure=is_secure,
        samesite='Strict',
        path='/',
    )
    return response


def clear_session_cookie(response):
    '''Expire the session cookie.'''
    from flask import current_app
    cookie_name = current_app.config.get('AUTH_COOKIE_NAME', 'btpay_session')
    is_secure = not current_app.config.get('DEV_MODE', False)
    response.set_cookie(cookie_name, '', expires=0, httponly=True,
                        secure=is_secure, samesite='Strict', path='/')
    return response


def cleanup_expired_sessions():
    '''Delete expired sessions. Call periodically.'''
    from btpay.auth.models import Session
    now_t = as_time_t(NOW())
    all_sessions = Session.query.all()
    count = 0
    for sess in all_sessions:
        exp = sess.expires_at
        if exp:
            exp_t = as_time_t(exp) if not isinstance(exp, (int, float)) else exp
            if exp_t <= now_t:
                sess.delete()
                count += 1
    return count

# EOF
