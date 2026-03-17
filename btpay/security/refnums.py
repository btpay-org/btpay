#
# Reference Numbers — NaCl SecretBox encrypted model references
#
# Encodes model instances into encrypted hex reference strings.
# Format: {12 hex chars}-{remaining hex chars} (uppercase)
#
import re
from nacl.secret import SecretBox
from btpay.misc import singleton


@singleton
class ReferenceNumbers:
    '''
    Encode model instances into encrypted reference numbers.

    All model classes must be listed in class_names and the order
    must NOT change over time. Add ONLY to the end.

    NOTE: this is a singleton, construct at will.
    '''

    # Order must be stable forever. Add only to the end!
    class_names = [
        'User', 'Organization', 'Membership', 'Session', 'ApiKey',
        'Wallet', 'BitcoinAddress',
        'Invoice', 'InvoiceLine', 'Payment', 'PaymentLink',
        'ExchangeRateSnapshot',
        'WebhookEndpoint', 'WebhookDelivery',
    ]

    class_map = None

    regex = re.compile(r'^[0-9A-F]{24}-[0-9A-F]*$')
    regex_anywhere = re.compile(r'[0-9A-F]{24}-[0-9A-F]')

    # Class-level box, key, nonce — set by __init__ or reconfigure()
    _box = None
    _nonce = None

    def __init__(self, key_hex=None, nonce_hex=None):
        import os
        from btpay.orm.model import get_model_registry

        if key_hex is None:
            key_hex = os.environ.get('BTPAY_REFNUM_KEY') or \
                '0' * 64  # 32 zero bytes — dev-only fallback
        if nonce_hex is None:
            nonce_hex = os.environ.get('BTPAY_REFNUM_NONCE') or \
                '0' * 48  # 24 zero bytes — dev-only fallback

        self._apply_keys(key_hex, nonce_hex)
        self.class_map = get_model_registry()

    def _apply_keys(self, key_hex, nonce_hex):
        '''Set key, nonce, and box from hex or raw strings.'''
        import hashlib
        try:
            key = bytes.fromhex(key_hex)
        except (ValueError, AttributeError):
            # Non-hex input (e.g. Render generateValue) — derive hex via SHA-256
            key = hashlib.sha256(key_hex.encode()).digest()
        try:
            nonce = bytes.fromhex(nonce_hex)
        except (ValueError, AttributeError):
            nonce = hashlib.sha256(nonce_hex.encode()).digest()[:24]
        assert len(key) == 32, "REFNUM_KEY must be 32 bytes (64 hex chars)"
        assert len(nonce) == 24, "REFNUM_NONCE must be 24 bytes (48 hex chars)"
        # Store on class so classmethods and instance both see them
        ReferenceNumbers._cls._box = SecretBox(key)
        ReferenceNumbers._cls._nonce = nonce

    def reconfigure(self, key_hex, nonce_hex):
        '''Re-initialize keys from deployment config. Called once at app startup.'''
        self._apply_keys(key_hex, nonce_hex)

    def pack(self, instance):
        '''Encode a model instance into a reference number string.'''
        assert instance.id is not None, "Model not saved?"
        msg = f'{instance.__class__.__name__}:{instance.id}'.encode()
        enc = self._box.encrypt(msg, self._nonce)[24:]  # strip prepended nonce
        return (enc[0:12].hex() + '-' + enc[12:].hex()).upper()

    def unpack(self, s, expect_class=None, just_pk=False):
        '''Decode a reference number string back to a model instance.'''
        try:
            a, b = s.split('-')
            msg = bytes.fromhex(a + b)
            assert len(msg) >= 20
        except Exception:
            raise ValueError("Bad refnum: %s" % s)

        try:
            raw = self._box.decrypt(self._nonce + msg).decode()
            assert ':' in raw
            cls_name, pk = raw.split(':')
            pk = int(pk)
        except Exception:
            raise ValueError("Corrupt refnum: %s" % s)

        model_cls = self.class_map.get(cls_name)
        if model_cls is None:
            raise ValueError("Unknown model class: %s" % cls_name)

        if expect_class:
            if not issubclass(model_cls, expect_class):
                raise TypeError("Expected %s, got %s class" % (expect_class, model_cls))

        if just_pk:
            return (model_cls, pk)

        return model_cls.get(pk)

# EOF
