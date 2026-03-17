#
# CSRF token generation and validation
#
# NOTE: This intentionally uses direct HMAC-SHA256 rather than the JWT-based
# security/tokens.py module. CSRF tokens need to be simple, lightweight, and
# embeddable in HTML forms — JWT would add unnecessary overhead and complexity.
#
import os, hmac, hashlib, time


def generate_csrf_token(session_id, secret_key):
    '''Generate a CSRF token tied to a session.'''
    timestamp = str(int(time.time()))
    nonce = os.urandom(8).hex()
    msg = '%s:%s:%s' % (session_id, timestamp, nonce)
    sig = hmac.new(
        secret_key.encode('utf-8'),
        msg.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return '%s:%s:%s' % (timestamp, nonce, sig)


def validate_csrf_token(session_id, token, secret_key, max_age=3600):
    '''
    Validate a CSRF token.
    Returns True if valid, False otherwise.
    '''
    try:
        parts = token.split(':')
        if len(parts) != 3:
            return False

        timestamp, nonce, sig = parts

        # Check age
        ts = int(timestamp)
        if abs(time.time() - ts) > max_age:
            return False

        # Recompute signature
        msg = '%s:%s:%s' % (session_id, timestamp, nonce)
        expected = hmac.new(
            secret_key.encode('utf-8'),
            msg.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(sig, expected)
    except Exception:
        return False

# EOF
