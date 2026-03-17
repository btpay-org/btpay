#
# Input validation
#
import re
from decimal import Decimal, InvalidOperation


class ValidationError(ValueError):
    '''Raised when input validation fails.'''
    pass


_EMAIL_RE = re.compile(
    r'^[a-zA-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[a-zA-Z0-9]'
    r'(?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?'
    r'(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'
)

_URL_RE = re.compile(r'^https?://[^\s<>"{}|\\^`\[\]]+$')

# Bech32 character set
_BECH32_CHARS = set('qpzry9x8gf2tvdw0s3jn54khce6mua7l')


def validate_email(addr):
    '''Validate and normalize an email address.'''
    if not addr or not isinstance(addr, str):
        raise ValidationError("Email address is required")
    addr = addr.strip().lower()
    if len(addr) > 254:
        raise ValidationError("Email address too long")
    if not _EMAIL_RE.match(addr):
        raise ValidationError("Invalid email address")
    return addr


def validate_url(url):
    '''Validate a URL (must be http or https).'''
    if not url or not isinstance(url, str):
        raise ValidationError("URL is required")
    url = url.strip()
    if not _URL_RE.match(url):
        raise ValidationError("Invalid URL")
    return url


def validate_external_url(url):
    '''Validate a URL and block SSRF attempts (internal/private IPs).'''
    import ipaddress
    from urllib.parse import urlparse

    url = validate_url(url)
    parsed = urlparse(url)
    hostname = parsed.hostname

    if not hostname:
        raise ValidationError("URL has no hostname")

    # Block obvious private hostnames
    if hostname in ('localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]'):
        raise ValidationError("URL must not point to localhost")

    # Block .local, .internal, .arpa domains
    if hostname.endswith(('.local', '.internal', '.arpa', '.localhost')):
        raise ValidationError("URL must not point to internal hosts")

    # Try to parse as IP and block private/reserved ranges
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None  # Not an IP — it's a hostname, which is fine

    if ip is not None:
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
            raise ValidationError("URL must not point to private/reserved IP ranges")

    # Block metadata service IPs (cloud providers)
    if hostname in ('169.254.169.254', 'metadata.google.internal',
                    'metadata.internal'):
        raise ValidationError("URL must not point to cloud metadata services")

    return url


def validate_btc_address(addr, testnet=False):
    '''
    Validate a Bitcoin address.
    Supports mainnet: P2PKH (1...), P2SH (3...), Bech32/P2WPKH (bc1q...), Bech32m/P2TR (bc1p...)
    Supports testnet (testnet=True): P2PKH (m/n...), P2SH (2...), Bech32 (tb1...)
    '''
    if not addr or not isinstance(addr, str):
        raise ValidationError("Bitcoin address is required")
    addr = addr.strip()

    lower = addr.lower()

    # Bech32 / Bech32m (native segwit, taproot)
    if lower.startswith('bc1') or (testnet and lower.startswith('tb1')):
        prefix_len = 3  # bc1 or tb1
        if len(lower) < 14 or len(lower) > 74:
            raise ValidationError("Invalid bech32 address length")
        # Check character set (after prefix)
        for c in lower[prefix_len:]:
            if c not in _BECH32_CHARS:
                raise ValidationError("Invalid bech32 character: %s" % c)
        return addr

    # Base58Check — mainnet P2PKH (1), P2SH (3)
    if addr[0] in ('1', '3'):
        if not (25 <= len(addr) <= 34):
            raise ValidationError("Invalid base58 address length")
        try:
            _base58_decode_check(addr)
        except Exception:
            raise ValidationError("Invalid base58check address")
        return addr

    # Base58Check — testnet P2PKH (m, n), P2SH (2)
    if testnet and addr[0] in ('m', 'n', '2'):
        if not (25 <= len(addr) <= 34):
            raise ValidationError("Invalid base58 address length")
        try:
            _base58_decode_check(addr)
        except Exception:
            raise ValidationError("Invalid base58check address")
        return addr

    raise ValidationError("Unrecognized Bitcoin address format")


def _base58_decode_check(s):
    '''Decode and verify base58check encoding.'''
    import hashlib
    ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    n = 0
    for c in s:
        n = n * 58 + ALPHABET.index(c)
    # Convert to bytes — determine length from the integer size
    byte_length = (n.bit_length() + 7) // 8
    # Minimum 25 bytes for addresses, 78 bytes for extended keys
    byte_length = max(byte_length, 25)
    result = n.to_bytes(byte_length, 'big')
    payload, checksum = result[:-4], result[-4:]
    h = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if h != checksum:
        raise ValueError("Bad checksum")
    return payload


def validate_xpub(xpub):
    '''Validate an extended public key (xpub, ypub, zpub).'''
    if not xpub or not isinstance(xpub, str):
        raise ValidationError("Extended public key is required")
    xpub = xpub.strip()

    valid_prefixes = ('xpub', 'ypub', 'zpub', 'tpub', 'upub', 'vpub')
    if not any(xpub.startswith(p) for p in valid_prefixes):
        raise ValidationError("Invalid xpub prefix (expected xpub/ypub/zpub/tpub/upub/vpub)")

    if len(xpub) != 111:
        raise ValidationError("Invalid xpub length (expected 111 characters)")

    # Verify base58check
    try:
        _base58_decode_check(xpub)
    except Exception:
        raise ValidationError("Invalid xpub checksum")

    return xpub


def validate_amount(amount, min_val=None, max_val=None, allow_zero=False):
    '''Validate a numeric amount. Pass a string or Decimal, not a float.'''
    if amount is None:
        raise ValidationError("Amount is required")
    if isinstance(amount, float):
        raise ValidationError("Amount must be a string or Decimal, not float (floats have precision issues)")
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise ValidationError("Invalid amount")

    if not allow_zero and amount == 0:
        raise ValidationError("Amount cannot be zero")
    if amount < 0:
        raise ValidationError("Amount cannot be negative")
    if min_val is not None and amount < Decimal(str(min_val)):
        raise ValidationError("Amount below minimum (%s)" % min_val)
    if max_val is not None and amount > Decimal(str(max_val)):
        raise ValidationError("Amount above maximum (%s)" % max_val)

    return amount

# EOF
