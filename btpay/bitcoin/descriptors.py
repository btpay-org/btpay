#
# Output Descriptor Parsing
#
# Supports: wpkh(), sh(wpkh()), pkh()
# Taproot tr() left as stub for future.
#
import re
from btpay.bitcoin.xpub import XPubDeriver


# Descriptor checksum (BIP380-style polymod)
_INPUT_CHARSET = '0123456789()[],\'/*abcdefgh@:$%{}' \
                 'IJKLMNOPQRSTUVWXYZ&+-.;<=>?!^_|~' \
                 'ijklmnopqrstuvwxyzABCDEFGH`#"\\ '
_CHECKSUM_CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def _polymod(c, val):
    c0 = c >> 35
    c = ((c & 0x7ffffffff) << 5) ^ val
    if c0 & 1:
        c ^= 0xf5dee51989
    if c0 & 2:
        c ^= 0xa9fdca3312
    if c0 & 4:
        c ^= 0x1bab10e32d
    if c0 & 8:
        c ^= 0x3706b1677a
    if c0 & 16:
        c ^= 0x644d626ffd
    return c


def descriptor_checksum(desc):
    '''Calculate the 8-character descriptor checksum.'''
    c = 1
    cls = 0
    clscount = 0
    for ch in desc:
        pos = _INPUT_CHARSET.find(ch)
        if pos == -1:
            return ''
        c = _polymod(c, pos & 31)
        cls = cls * 3 + (pos >> 5)
        clscount += 1
        if clscount == 3:
            c = _polymod(c, cls)
            cls = 0
            clscount = 0
    if clscount > 0:
        c = _polymod(c, cls)
    for _ in range(8):
        c = _polymod(c, 0)
    c ^= 1
    return ''.join(_CHECKSUM_CHARSET[(c >> (5 * (7 - i))) & 31] for i in range(8))


def verify_descriptor_checksum(descriptor_with_checksum):
    '''Verify a descriptor's #checksum suffix. Returns (descriptor, valid).'''
    if '#' not in descriptor_with_checksum:
        return descriptor_with_checksum, True  # no checksum to verify
    parts = descriptor_with_checksum.rsplit('#', 1)
    desc = parts[0]
    checksum = parts[1]
    expected = descriptor_checksum(desc)
    return desc, checksum == expected


# ---- Descriptor parsing ----

# Pattern: [fingerprint/path] or just the key
_ORIGIN_RE = re.compile(r'\[([0-9a-fA-F]{8})(/[^\]]+)?\]')
_KEY_RE = re.compile(r'([xyztuvXYZTUV]pub[1-9A-HJ-NP-Za-km-z]{100,})')
_WILDCARD_PATH_RE = re.compile(r'/(\d+)/\*')


class DescriptorParser:
    '''
    Parse and evaluate output descriptors.

    Supported formats:
        wpkh([fingerprint/path]xpub/0/*)
        sh(wpkh([fingerprint/path]xpub/0/*))
        pkh([fingerprint/path]xpub/0/*)

    Usage:
        dp = DescriptorParser("wpkh(xpub6.../0/*)")
        addr = dp.derive_address(0)
    '''

    def __init__(self, descriptor_str):
        # Strip checksum if present
        desc, valid = verify_descriptor_checksum(descriptor_str)
        if not valid:
            raise ValueError("Invalid descriptor checksum")

        self.raw = desc
        self.script_type = self._parse_script_type(desc)
        self.xpub_str = self._extract_xpub(desc)
        self.base_path = self._extract_wildcard_path(desc)
        self.deriver = XPubDeriver(self.xpub_str)

    def _parse_script_type(self, desc):
        '''Determine the script type from the descriptor wrapper.'''
        d = desc.strip()
        if d.startswith('sh(wpkh('):
            return 'p2sh_p2wpkh'
        elif d.startswith('wpkh('):
            return 'p2wpkh'
        elif d.startswith('pkh('):
            return 'p2pkh'
        elif d.startswith('tr('):
            return 'p2tr'  # stub
        else:
            raise ValueError("Unsupported descriptor type: %s" % desc[:20])

    def _extract_xpub(self, desc):
        '''Extract the xpub key from the descriptor.'''
        m = _KEY_RE.search(desc)
        if not m:
            raise ValueError("No xpub found in descriptor")
        return m.group(1)

    def _extract_wildcard_path(self, desc):
        '''Extract the derivation path before the wildcard.
           e.g. "/0/*" → "0"
        '''
        m = _WILDCARD_PATH_RE.search(desc)
        if m:
            return m.group(1)
        return None

    def derive_address(self, index, network='mainnet'):
        '''Derive the address at the given index.'''
        child = self._derive_to_index(index)
        if self.script_type == 'p2wpkh':
            return child.p2wpkh_address(network)
        elif self.script_type == 'p2sh_p2wpkh':
            return child.p2sh_p2wpkh_address(network)
        elif self.script_type == 'p2pkh':
            return child.p2pkh_address(network)
        else:
            raise ValueError("Unsupported script type: %s" % self.script_type)

    def derive_script_pubkey(self, index):
        '''Derive the scriptPubKey at the given index.'''
        child = self._derive_to_index(index)

        from btpay.bitcoin.xpub import _hash160
        h = _hash160(child.pubkey)

        if self.script_type == 'p2wpkh':
            return b'\x00\x14' + h
        elif self.script_type == 'p2sh_p2wpkh':
            witness_script = b'\x00\x14' + h
            sh = _hash160(witness_script)
            return b'\xa9\x14' + sh + b'\x87'
        elif self.script_type == 'p2pkh':
            return b'\x76\xa9\x14' + h + b'\x88\xac'
        else:
            raise ValueError("Unsupported script type: %s" % self.script_type)

    def _derive_to_index(self, index):
        '''Derive from the xpub through the base path to the given index.'''
        node = self.deriver
        if self.base_path:
            node = node.derive_child(int(self.base_path))
        return node.derive_child(index)

# EOF
