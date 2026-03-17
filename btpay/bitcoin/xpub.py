#
# BIP32 HD Public Key Derivation
#
# Pure Python + libsecp256k1 for EC point operations.
# No external Bitcoin library dependencies.
#
# Supports: xpub/ypub/zpub (and testnet tpub/upub/vpub)
# Address types: P2PKH, P2SH-P2WPKH, P2WPKH
#
import hashlib, hmac, struct

# ---- Version bytes for extended public keys ----

_XPUB_VERSIONS = {
    # mainnet
    b'\x04\x88\xb2\x1e': 'p2pkh',       # xpub
    b'\x04\x9d\x7c\xb2': 'p2sh_p2wpkh', # ypub
    b'\x04\xb2\x47\x46': 'p2wpkh',      # zpub
    # testnet
    b'\x04\x35\x87\xcf': 'p2pkh',       # tpub
    b'\x04\x4a\x52\x62': 'p2sh_p2wpkh', # upub
    b'\x04\x5f\x1c\xf6': 'p2wpkh',      # vpub
}

_TESTNET_VERSIONS = {
    b'\x04\x35\x87\xcf', b'\x04\x4a\x52\x62', b'\x04\x5f\x1c\xf6',
}

# Network version bytes for addresses
_P2PKH_VERSION = {
    'mainnet': b'\x00',
    'testnet': b'\x6f',
}
_P2SH_VERSION = {
    'mainnet': b'\x05',
    'testnet': b'\xc4',
}
_BECH32_HRP = {
    'mainnet': 'bc',
    'testnet': 'tb',
}


# ---- Low-level crypto helpers ----

def _hmac_sha512(key, data):
    '''HMAC-SHA512.'''
    return hmac.new(key, data, hashlib.sha512).digest()


def _hash160(data):
    '''RIPEMD160(SHA256(data)).'''
    return hashlib.new('ripemd160', hashlib.sha256(data).digest()).digest()


def _sha256d(data):
    '''Double SHA256.'''
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


# ---- Base58 encoding ----

_B58_ALPHABET = b'123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def _base58_encode(data):
    '''Encode bytes to base58 string.'''
    n = int.from_bytes(data, 'big')
    chars = []
    while n > 0:
        n, r = divmod(n, 58)
        chars.append(_B58_ALPHABET[r:r+1])
    # Preserve leading zeros
    for b in data:
        if b == 0:
            chars.append(b'1')
        else:
            break
    return b''.join(reversed(chars)).decode('ascii')


def _base58check_encode(payload):
    '''Base58Check encode: payload + 4-byte checksum.'''
    checksum = _sha256d(payload)[:4]
    return _base58_encode(payload + checksum)


def _base58check_decode(s):
    '''Base58Check decode. Returns payload bytes (without checksum).'''
    n = 0
    for c in s.encode('ascii'):
        n = n * 58 + _B58_ALPHABET.index(c)
    # Convert to 25+ bytes (xpub is 78+4=82 bytes)
    raw = n.to_bytes(max(1, (n.bit_length() + 7) // 8), 'big')
    # Pad leading 1s
    pad = 0
    for c in s:
        if c == '1':
            pad += 1
        else:
            break
    raw = b'\x00' * pad + raw
    # Verify checksum
    payload, checksum = raw[:-4], raw[-4:]
    if _sha256d(payload)[:4] != checksum:
        raise ValueError("Bad base58 checksum")
    return payload


# ---- Bech32 encoding (BIP173) ----

_BECH32_CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def _bech32_polymod(values):
    GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1ffffff) << 5) ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _bech32_create_checksum(hrp, data, spec=1):
    '''spec=1 for bech32, spec=0x2bc830a3 for bech32m.'''
    values = _bech32_hrp_expand(hrp) + data
    polymod = _bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ spec
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _convertbits(data, frombits, tobits, pad=True):
    '''Convert between bit groups.'''
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise ValueError("Invalid padding")
    return ret


def bech32_encode(hrp, witver, witprog):
    '''Encode a bech32 address.'''
    spec = 1 if witver == 0 else 0x2bc830a3  # bech32 vs bech32m
    data = [witver] + _convertbits(witprog, 8, 5)
    checksum = _bech32_create_checksum(hrp, data, spec)
    return hrp + '1' + ''.join(_BECH32_CHARSET[d] for d in data + checksum)


# ---- EC point operations ----
# Try libsecp256k1 first, fall back to pure Python

def _ec_point_add(pubkey_bytes, tweak_bytes):
    '''Add tweak to compressed public key. Returns new compressed pubkey.
       Uses libsecp256k1 if available, otherwise pure Python.'''
    try:
        from btpay.security.crypto import (
            ec_pubkey_parse, ec_pubkey_tweak_add, ec_pubkey_serialize,
            is_available,
        )
        if is_available():
            internal = ec_pubkey_parse(pubkey_bytes)
            tweaked = ec_pubkey_tweak_add(internal, tweak_bytes)
            return ec_pubkey_serialize(tweaked, compressed=True)
    except (RuntimeError, ImportError):
        pass

    # Pure Python fallback using secp256k1 curve math
    return _ec_point_add_pure(pubkey_bytes, tweak_bytes)


def _ec_point_add_pure(pubkey_bytes, tweak_bytes):
    '''Pure Python EC point addition on secp256k1 curve.'''
    P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
    N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    A = 0
    B = 7
    Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
    Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8

    def modinv(a, m=P):
        return pow(a, m - 2, m)

    def point_add(p1, p2):
        if p1 is None:
            return p2
        if p2 is None:
            return p1
        x1, y1 = p1
        x2, y2 = p2
        if x1 == x2 and y1 != y2:
            return None
        if x1 == x2:
            lam = (3 * x1 * x1 + A) * modinv(2 * y1) % P
        else:
            lam = (y2 - y1) * modinv(x2 - x1) % P
        x3 = (lam * lam - x1 - x2) % P
        y3 = (lam * (x1 - x3) - y1) % P
        return (x3, y3)

    def scalar_mult(k, point):
        result = None
        addend = point
        while k:
            if k & 1:
                result = point_add(result, addend)
            addend = point_add(addend, addend)
            k >>= 1
        return result

    def decompress_point(pub):
        if len(pub) != 33:
            raise ValueError("Expected 33-byte compressed pubkey")
        prefix = pub[0]
        x = int.from_bytes(pub[1:], 'big')
        y_sq = (pow(x, 3, P) + B) % P
        y = pow(y_sq, (P + 1) // 4, P)
        if y % 2 != (prefix - 2):
            y = P - y
        return (x, y)

    def compress_point(point):
        x, y = point
        prefix = 0x02 if y % 2 == 0 else 0x03
        return bytes([prefix]) + x.to_bytes(32, 'big')

    # Parse the parent pubkey
    parent_point = decompress_point(pubkey_bytes)

    # The tweak is a scalar — multiply by generator and add to parent
    tweak_int = int.from_bytes(tweak_bytes, 'big')
    if tweak_int >= N:
        raise ValueError("Tweak out of range")

    tweak_point = scalar_mult(tweak_int, (Gx, Gy))
    child_point = point_add(parent_point, tweak_point)
    if child_point is None:
        raise ValueError("Resulting point is at infinity")

    return compress_point(child_point)


# ---- XPubDeriver ----

class XPubDeriver:
    '''
    BIP32 extended public key deriver.

    Usage:
        d = XPubDeriver('xpub6...')
        child = d.derive_child(0)
        addr = child.p2wpkh_address()
    '''

    def __init__(self, xpub_str=None, *, pubkey=None, chain_code=None,
                 depth=0, version=None, network='mainnet'):
        if xpub_str:
            self._parse(xpub_str)
        else:
            self.pubkey = pubkey           # 33-byte compressed
            self.chain_code = chain_code   # 32 bytes
            self.depth = depth
            self.version = version or b'\x04\x88\xb2\x1e'
            self.network = network

    def _parse(self, xpub_str):
        '''Parse a base58check-encoded extended public key.'''
        raw = _base58check_decode(xpub_str)
        if len(raw) != 78:
            raise ValueError("Invalid xpub length: %d (expected 78)" % len(raw))

        self.version = raw[0:4]
        self.depth = raw[4]
        # fingerprint = raw[5:9]   # parent fingerprint (not needed for derivation)
        # child_num = raw[9:13]    # child number (not needed for derivation)
        self.chain_code = raw[13:45]
        self.pubkey = raw[45:78]

        if self.version not in _XPUB_VERSIONS:
            raise ValueError("Unknown xpub version: %s" % self.version.hex())

        self.network = 'testnet' if self.version in _TESTNET_VERSIONS else 'mainnet'

        # Validate pubkey prefix
        if self.pubkey[0] not in (0x02, 0x03):
            raise ValueError("Invalid compressed pubkey prefix: 0x%02x" % self.pubkey[0])

    def derive_child(self, index):
        '''
        BIP32 public child derivation (non-hardened only).
        index must be < 2^31.
        '''
        if index >= 0x80000000:
            raise ValueError("Cannot derive hardened child from public key")

        # Data = pubkey (33) || index (4)
        data = self.pubkey + struct.pack('>I', index)
        I = _hmac_sha512(self.chain_code, data)
        IL, IR = I[:32], I[32:]

        # Check IL is valid (< curve order)
        n = int.from_bytes(IL, 'big')
        N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        if n >= N:
            raise ValueError("Derived key is invalid (IL >= n)")

        # child_pubkey = point(IL) + parent_pubkey
        child_pubkey = _ec_point_add(self.pubkey, IL)

        return XPubDeriver(
            pubkey=child_pubkey,
            chain_code=IR,
            depth=self.depth + 1,
            version=self.version,
            network=self.network,
        )

    def derive_path(self, path):
        '''
        Derive along a relative path like "0/5" or "0".
        Does not support "m/" prefix or hardened indices here.
        '''
        path = path.strip().strip('/')
        if not path:
            return self
        node = self
        for part in path.split('/'):
            part = part.strip()
            if not part:
                continue
            if part.endswith("'") or part.endswith('h'):
                raise ValueError("Cannot derive hardened path from xpub")
            node = node.derive_child(int(part))
        return node

    def p2pkh_address(self, network=None):
        '''Legacy P2PKH address (1...).'''
        network = network or self.network
        h = _hash160(self.pubkey)
        version = _P2PKH_VERSION[network]
        return _base58check_encode(version + h)

    def p2sh_p2wpkh_address(self, network=None):
        '''Wrapped segwit P2SH-P2WPKH address (3...).'''
        network = network or self.network
        h = _hash160(self.pubkey)
        # Witness script: OP_0 <20-byte-hash>
        witness_script = b'\x00\x14' + h
        script_hash = _hash160(witness_script)
        version = _P2SH_VERSION[network]
        return _base58check_encode(version + script_hash)

    def p2wpkh_address(self, network=None):
        '''Native segwit P2WPKH address (bc1q...).'''
        network = network or self.network
        h = _hash160(self.pubkey)
        hrp = _BECH32_HRP[network]
        return bech32_encode(hrp, 0, list(h))

    def address(self, network=None):
        '''Generate address based on xpub version prefix.'''
        addr_type = _XPUB_VERSIONS.get(self.version, 'p2wpkh')
        if addr_type == 'p2pkh':
            return self.p2pkh_address(network)
        elif addr_type == 'p2sh_p2wpkh':
            return self.p2sh_p2wpkh_address(network)
        else:
            return self.p2wpkh_address(network)

    def script_pubkey(self):
        '''Generate scriptPubKey bytes for this key's address type.'''
        addr_type = _XPUB_VERSIONS.get(self.version, 'p2wpkh')
        h = _hash160(self.pubkey)

        if addr_type == 'p2pkh':
            # OP_DUP OP_HASH160 <20> <hash> OP_EQUALVERIFY OP_CHECKSIG
            return b'\x76\xa9\x14' + h + b'\x88\xac'
        elif addr_type == 'p2sh_p2wpkh':
            # OP_HASH160 <20> <hash(witness_script)> OP_EQUAL
            witness_script = b'\x00\x14' + h
            sh = _hash160(witness_script)
            return b'\xa9\x14' + sh + b'\x87'
        else:
            # P2WPKH: OP_0 <20> <hash>
            return b'\x00\x14' + h

    @staticmethod
    def script_hash(script_pubkey):
        '''
        Electrum script hash: SHA256(scriptPubKey) reversed, as hex.
        Used for blockchain.scripthash.* RPC methods.
        '''
        h = hashlib.sha256(script_pubkey).digest()
        return h[::-1].hex()

# EOF
