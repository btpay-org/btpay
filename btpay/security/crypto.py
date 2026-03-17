#
# libsecp256k1 bindings via cffi
#
# Uses the original C library for all elliptic curve operations.
# Platform-aware: loads .dylib on Mac, .so on Linux/FreeBSD.
#
import os, sys, ctypes, ctypes.util, hashlib, logging

log = logging.getLogger(__name__)

_lib = None
_ctx = None


def _find_library():
    '''Find libsecp256k1 on the system.'''

    # Try standard paths first
    path = ctypes.util.find_library('secp256k1')
    if path:
        return path

    # Platform-specific search
    if sys.platform == 'darwin':
        candidates = [
            '/opt/homebrew/lib/libsecp256k1.dylib',
            '/usr/local/lib/libsecp256k1.dylib',
        ]
    elif 'freebsd' in sys.platform:
        candidates = [
            '/usr/local/lib/libsecp256k1.so',
            '/usr/local/lib/libsecp256k1.so.2',
        ]
    else:
        # Linux
        candidates = [
            '/usr/lib/libsecp256k1.so',
            '/usr/lib/x86_64-linux-gnu/libsecp256k1.so',
            '/usr/lib/aarch64-linux-gnu/libsecp256k1.so',
            '/usr/local/lib/libsecp256k1.so',
        ]

    for c in candidates:
        if os.path.exists(c):
            return c

    # Try local build
    local = os.path.join(os.path.dirname(__file__), '..', '..', 'lib', 'libsecp256k1')
    for ext in ('.dylib', '.so'):
        p = local + ext
        if os.path.exists(p):
            return p

    return None


def _load():
    '''Load libsecp256k1 and create context.'''
    global _lib, _ctx

    if _lib is not None:
        return

    path = _find_library()
    if path is None:
        raise RuntimeError(
            "libsecp256k1 not found. Install it:\n"
            "  Mac:     brew install libsecp256k1\n"
            "  Debian:  apt install libsecp256k1-dev\n"
            "  FreeBSD: pkg install libsecp256k1\n"
            "  Or:      make build-secp256k1"
        )

    _lib = ctypes.CDLL(path)

    # Create context with SIGN | VERIFY flags
    SECP256K1_CONTEXT_SIGN = 0x201
    SECP256K1_CONTEXT_VERIFY = 0x101
    _lib.secp256k1_context_create.restype = ctypes.c_void_p
    _ctx = _lib.secp256k1_context_create(SECP256K1_CONTEXT_SIGN | SECP256K1_CONTEXT_VERIFY)

    log.info("Loaded libsecp256k1 from %s" % path)


def ec_pubkey_create(seckey):
    '''Derive public key from 32-byte secret key. Returns 64-byte internal pubkey.'''
    _load()
    assert len(seckey) == 32
    pubkey = ctypes.create_string_buffer(64)
    ret = _lib.secp256k1_ec_pubkey_create(_ctx, pubkey, seckey)
    if ret != 1:
        raise ValueError("Invalid secret key")
    return pubkey.raw


def ec_pubkey_serialize(pubkey, compressed=True):
    '''Serialize internal pubkey to bytes (33 compressed, 65 uncompressed).'''
    _load()
    SECP256K1_EC_COMPRESSED = 0x0102
    SECP256K1_EC_UNCOMPRESSED = 0x0002
    flags = SECP256K1_EC_COMPRESSED if compressed else SECP256K1_EC_UNCOMPRESSED
    outlen = 33 if compressed else 65
    output = ctypes.create_string_buffer(outlen)
    outputlen = ctypes.c_size_t(outlen)
    _lib.secp256k1_ec_pubkey_serialize(_ctx, output, ctypes.byref(outputlen), pubkey, flags)
    return output.raw[:outputlen.value]


def ec_pubkey_tweak_add(pubkey, tweak):
    '''Add a 32-byte tweak to a public key (for BIP32 child derivation).'''
    _load()
    assert len(tweak) == 32
    # Copy pubkey to mutable buffer
    pubkey_buf = ctypes.create_string_buffer(pubkey, 64)
    ret = _lib.secp256k1_ec_pubkey_tweak_add(_ctx, pubkey_buf, tweak)
    if ret != 1:
        raise ValueError("Invalid tweak")
    return pubkey_buf.raw


def ec_pubkey_parse(data):
    '''Parse serialized public key (33 or 65 bytes) to internal format.'''
    _load()
    pubkey = ctypes.create_string_buffer(64)
    ret = _lib.secp256k1_ec_pubkey_parse(_ctx, pubkey, data, len(data))
    if ret != 1:
        raise ValueError("Invalid public key")
    return pubkey.raw


def ecdsa_sign(seckey, msg32):
    '''Sign a 32-byte message hash with a secret key.'''
    _load()
    assert len(seckey) == 32
    assert len(msg32) == 32
    sig = ctypes.create_string_buffer(64)
    ret = _lib.secp256k1_ecdsa_sign(_ctx, sig, msg32, seckey, None, None)
    if ret != 1:
        raise ValueError("Failed to sign")
    return sig.raw


def ecdsa_verify(pubkey, msg32, sig):
    '''Verify an ECDSA signature. Returns True/False.'''
    _load()
    ret = _lib.secp256k1_ecdsa_verify(_ctx, sig, msg32, pubkey)
    return ret == 1


def is_available():
    '''Check if libsecp256k1 is available on this system.'''
    try:
        _load()
        return True
    except RuntimeError:
        return False

# EOF
