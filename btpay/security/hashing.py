#
# Password hashing and HMAC utilities
#
import hmac, hashlib, os
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=1,
)


def hash_password(password):
    '''Hash a password using argon2id. Returns encoded hash string.'''
    return _hasher.hash(password)


def verify_password(password, hash_str):
    '''Verify a password against an argon2id hash. Returns True/False.'''
    try:
        return _hasher.verify(hash_str, password)
    except VerifyMismatchError:
        return False


def hmac_sign(key, message):
    '''Create HMAC-SHA256 signature. Returns hex string.'''
    if isinstance(key, str):
        key = key.encode('utf-8')
    if isinstance(message, str):
        message = message.encode('utf-8')
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def hmac_verify(key, message, signature):
    '''Verify HMAC-SHA256 signature. Constant-time comparison.'''
    expected = hmac_sign(key, message)
    return hmac.compare_digest(expected, signature)


def needs_rehash(hash_str):
    '''Check if a password hash needs to be upgraded to current parameters.'''
    try:
        return _hasher.check_needs_rehash(hash_str)
    except Exception:
        return True  # unknown format, definitely needs rehash


def generate_random_token(nbytes=32):
    '''Generate a cryptographically random token, base64url encoded.'''
    import base64
    return base64.urlsafe_b64encode(os.urandom(nbytes)).decode('ascii').rstrip('=')

# EOF
