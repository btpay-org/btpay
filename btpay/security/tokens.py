#
# JWT Tokens
#
# Purpose-based secrets, creation and verification with expiry handling.
#
import jwt, logging
from btpay.chrono import TIME_FUTURE
from btpay.misc import u8

log = logging.getLogger(__name__)


def create_secure_token(purpose, jwt_secrets, extras=None, hours=0, seconds=0):
    '''
    Create a JWT token with an expiry.

    Args:
        purpose: string key into jwt_secrets dict (e.g. 'admin', 'login', 'api')
        jwt_secrets: dict mapping purpose -> secret string
        extras: optional dict of additional claims
        hours: token lifetime in hours
        seconds: token lifetime in seconds (added to hours)

    Returns:
        JWT token string
    '''
    exp = TIME_FUTURE(hours=hours, seconds=seconds)
    contents = {'purpose': purpose, 'exp': exp}
    if extras:
        contents.update(extras)
    return u8(jwt.encode(contents, jwt_secrets[purpose], algorithm='HS256'))


def verify_secure_token(purpose, token, jwt_secrets):
    '''
    Verify and decode a JWT token.

    Args:
        purpose: expected purpose string
        token: JWT token (str or bytes)
        jwt_secrets: dict mapping purpose -> secret string

    Returns:
        (expired: bool, contents: dict)

    Raises:
        ValueError if token is invalid or has wrong purpose
    '''
    try:
        try:
            contents = jwt.decode(token, jwt_secrets[purpose], algorithms=['HS256'])
            expired = False
        except jwt.exceptions.ExpiredSignatureError:
            contents = jwt.decode(
                token, jwt_secrets[purpose], algorithms=['HS256'],
                options=dict(verify_exp=False)
            )
            expired = True
    except Exception:
        raise ValueError("Bad security token")

    if contents.get('purpose') != purpose:
        raise ValueError("Wrong token purpose")

    return expired, contents

# EOF
