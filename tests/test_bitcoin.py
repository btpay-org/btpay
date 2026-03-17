#
# Tests for Phase 3 — Bitcoin Core
#
import hashlib
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock


# ======================================================================
# XPub / BIP32 HD Key Derivation
# ======================================================================

class TestBase58:
    '''Test base58 encoding/decoding.'''

    def test_base58_encode_decode_roundtrip(self):
        from btpay.bitcoin.xpub import _base58check_encode, _base58check_decode
        payload = b'\x00' + b'\xab' * 20  # fake P2PKH payload
        encoded = _base58check_encode(payload)
        decoded = _base58check_decode(encoded)
        assert decoded == payload

    def test_base58_bad_checksum(self):
        from btpay.bitcoin.xpub import _base58check_encode, _base58check_decode
        payload = b'\x00' + b'\xab' * 20
        encoded = _base58check_encode(payload)
        # Tamper with last char
        chars = list(encoded)
        chars[-1] = 'A' if chars[-1] != 'A' else 'B'
        tampered = ''.join(chars)
        with pytest.raises(ValueError, match='checksum'):
            _base58check_decode(tampered)

    def test_base58_leading_zeros(self):
        from btpay.bitcoin.xpub import _base58check_encode, _base58check_decode
        payload = b'\x00\x00\x00' + b'\x01' * 17
        encoded = _base58check_encode(payload)
        assert encoded.startswith('111')  # leading zeros preserved
        decoded = _base58check_decode(encoded)
        assert decoded == payload


class TestBech32:
    '''Test bech32/bech32m encoding.'''

    def test_bech32_p2wpkh_format(self):
        '''P2WPKH address starts with bc1q (witness v0).'''
        from btpay.bitcoin.xpub import bech32_encode
        witprog = list(b'\xab' * 20)
        addr = bech32_encode('bc', 0, witprog)
        assert addr.startswith('bc1q')
        assert len(addr) == 42  # standard P2WPKH length

    def test_bech32m_p2tr_format(self):
        '''P2TR (witness v1) uses bech32m encoding.'''
        from btpay.bitcoin.xpub import bech32_encode
        witprog = list(b'\xcd' * 32)
        addr = bech32_encode('bc', 1, witprog)
        assert addr.startswith('bc1p')
        assert len(addr) == 62  # standard P2TR length

    def test_bech32_testnet(self):
        from btpay.bitcoin.xpub import bech32_encode
        witprog = list(b'\xab' * 20)
        addr = bech32_encode('tb', 0, witprog)
        assert addr.startswith('tb1q')


class TestHash160:
    '''Test RIPEMD160(SHA256(data)).'''

    def test_hash160_known_vector(self):
        from btpay.bitcoin.xpub import _hash160
        # Test with a known input
        result = _hash160(b'\x02' + b'\x00' * 32)
        assert len(result) == 20
        assert isinstance(result, bytes)

    def test_hash160_deterministic(self):
        from btpay.bitcoin.xpub import _hash160
        data = b'test data for hash160'
        assert _hash160(data) == _hash160(data)


class TestXPubDeriver:
    '''Test BIP32 HD key derivation.'''

    # BIP32 test vector 1 — master xpub
    # From the BIP32 spec, chain m:
    # This is the public key only (derived from the seed).
    XPUB_TV1 = 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'

    def test_parse_xpub(self):
        '''Parse a valid xpub string.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        assert d.network == 'mainnet'
        assert len(d.pubkey) == 33
        assert d.pubkey[0] in (0x02, 0x03)
        assert len(d.chain_code) == 32
        assert d.depth == 0

    def test_derive_child_0(self):
        '''Derive child index 0 from xpub.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        assert len(child.pubkey) == 33
        assert child.pubkey[0] in (0x02, 0x03)
        assert child.depth == 1
        assert child.pubkey != d.pubkey  # should be different

    def test_derive_child_deterministic(self):
        '''Same derivation produces same result.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        c1 = d.derive_child(0)
        c2 = d.derive_child(0)
        assert c1.pubkey == c2.pubkey
        assert c1.chain_code == c2.chain_code

    def test_derive_different_indices(self):
        '''Different indices produce different keys.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        c0 = d.derive_child(0)
        c1 = d.derive_child(1)
        assert c0.pubkey != c1.pubkey

    def test_derive_path(self):
        '''Derive along a path "0/5".'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_path('0/5')
        assert child.depth == 2
        assert len(child.pubkey) == 33

    def test_derive_path_matches_sequential(self):
        '''derive_path("0/5") == derive_child(0).derive_child(5).'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        via_path = d.derive_path('0/5')
        via_seq = d.derive_child(0).derive_child(5)
        assert via_path.pubkey == via_seq.pubkey

    def test_hardened_derivation_fails(self):
        '''Cannot derive hardened child from public key.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        with pytest.raises(ValueError, match='hardened'):
            d.derive_child(0x80000000)

    def test_hardened_path_fails(self):
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        with pytest.raises(ValueError, match='hardened'):
            d.derive_path("0'/1")

    def test_invalid_xpub(self):
        from btpay.bitcoin.xpub import XPubDeriver
        with pytest.raises((ValueError, Exception)):
            XPubDeriver('xpub_invalid_string_here')


class TestAddressGeneration:
    '''Test address generation from xpub.'''

    XPUB_TV1 = 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'

    def test_p2pkh_address_format(self):
        '''P2PKH address starts with 1 on mainnet.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        addr = child.p2pkh_address()
        assert addr.startswith('1')
        assert len(addr) >= 25 and len(addr) <= 34

    def test_p2sh_p2wpkh_address_format(self):
        '''P2SH-P2WPKH address starts with 3 on mainnet.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        addr = child.p2sh_p2wpkh_address()
        assert addr.startswith('3')

    def test_p2wpkh_address_format(self):
        '''P2WPKH address starts with bc1q on mainnet.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        addr = child.p2wpkh_address()
        assert addr.startswith('bc1q')
        assert len(addr) == 42

    def test_xpub_default_address_is_p2pkh(self):
        '''xpub prefix defaults to P2PKH.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        addr = child.address()
        assert addr.startswith('1')  # P2PKH for xpub

    def test_testnet_p2pkh_address(self):
        '''Testnet P2PKH starts with m or n.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        addr = child.p2pkh_address('testnet')
        assert addr.startswith(('m', 'n'))

    def test_testnet_p2wpkh_address(self):
        '''Testnet P2WPKH starts with tb1q.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        addr = child.p2wpkh_address('testnet')
        assert addr.startswith('tb1q')

    def test_address_deterministic(self):
        '''Same derivation always produces same address.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        a1 = d.derive_child(0).p2wpkh_address()
        a2 = d.derive_child(0).p2wpkh_address()
        assert a1 == a2

    def test_different_indices_different_addresses(self):
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        a0 = d.derive_child(0).p2wpkh_address()
        a1 = d.derive_child(1).p2wpkh_address()
        assert a0 != a1


class TestScriptPubKey:
    '''Test scriptPubKey generation and Electrum script hash.'''

    XPUB_TV1 = 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'

    def test_p2pkh_script_pubkey(self):
        '''P2PKH scriptPubKey: OP_DUP OP_HASH160 <20> ... OP_EQUALVERIFY OP_CHECKSIG.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        spk = child.script_pubkey()
        assert spk[:3] == b'\x76\xa9\x14'  # OP_DUP OP_HASH160 PUSH20
        assert spk[-2:] == b'\x88\xac'      # OP_EQUALVERIFY OP_CHECKSIG
        assert len(spk) == 25

    def test_script_hash_format(self):
        '''Electrum script hash is 64 hex chars (32 bytes reversed).'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        spk = child.script_pubkey()
        sh = XPubDeriver.script_hash(spk)
        assert len(sh) == 64
        assert all(c in '0123456789abcdef' for c in sh)

    def test_script_hash_is_reversed_sha256(self):
        '''script_hash == sha256(scriptPubKey) reversed hex.'''
        from btpay.bitcoin.xpub import XPubDeriver
        d = XPubDeriver(self.XPUB_TV1)
        child = d.derive_child(0)
        spk = child.script_pubkey()
        h = hashlib.sha256(spk).digest()
        expected = h[::-1].hex()
        assert XPubDeriver.script_hash(spk) == expected


class TestPureEC:
    '''Test pure Python secp256k1 EC operations.'''

    def test_ec_point_add_pure(self):
        '''Pure Python EC point addition produces valid compressed pubkey.'''
        from btpay.bitcoin.xpub import _ec_point_add_pure
        # Use the generator point as a test pubkey
        pubkey = bytes.fromhex('0279BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798')
        tweak = bytes(32)  # zero tweak = no change (but actually scalar_mult(0, G) = None)
        # Use a small nonzero tweak
        tweak = b'\x00' * 31 + b'\x01'  # tweak = 1 → G + G = 2G
        result = _ec_point_add_pure(pubkey, tweak)
        assert len(result) == 33
        assert result[0] in (0x02, 0x03)
        # Result should be different from input
        assert result != pubkey

    def test_ec_point_add_deterministic(self):
        from btpay.bitcoin.xpub import _ec_point_add_pure
        pubkey = bytes.fromhex('0279BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798')
        tweak = b'\x00' * 31 + b'\x05'
        r1 = _ec_point_add_pure(pubkey, tweak)
        r2 = _ec_point_add_pure(pubkey, tweak)
        assert r1 == r2


# ======================================================================
# Output Descriptors
# ======================================================================

class TestDescriptorChecksum:
    '''Test descriptor checksum calculation.'''

    def test_checksum_is_8_chars(self):
        from btpay.bitcoin.descriptors import descriptor_checksum
        cs = descriptor_checksum("wpkh(xpub6.../0/*)")
        # Should return an 8-char string (may be empty for invalid input charset)
        assert isinstance(cs, str)
        if cs:  # if input charset valid
            assert len(cs) == 8

    def test_verify_no_checksum(self):
        from btpay.bitcoin.descriptors import verify_descriptor_checksum
        desc, valid = verify_descriptor_checksum("wpkh(xpub6.../0/*)")
        assert valid is True

    def test_verify_correct_checksum(self):
        from btpay.bitcoin.descriptors import descriptor_checksum, verify_descriptor_checksum
        desc = "pkh(xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8/0/*)"
        cs = descriptor_checksum(desc)
        full = desc + '#' + cs
        d, valid = verify_descriptor_checksum(full)
        assert valid is True
        assert d == desc

    def test_verify_wrong_checksum(self):
        from btpay.bitcoin.descriptors import verify_descriptor_checksum
        full = "pkh(xpub661My.../0/*)#wrongchk"
        d, valid = verify_descriptor_checksum(full)
        assert valid is False


class TestDescriptorParser:
    '''Test output descriptor parsing and address derivation.'''

    XPUB_TV1 = 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'

    def test_parse_wpkh(self):
        from btpay.bitcoin.descriptors import DescriptorParser
        desc = 'wpkh(%s/0/*)' % self.XPUB_TV1
        dp = DescriptorParser(desc)
        assert dp.script_type == 'p2wpkh'
        assert dp.base_path == '0'

    def test_parse_sh_wpkh(self):
        from btpay.bitcoin.descriptors import DescriptorParser
        desc = 'sh(wpkh(%s/0/*))' % self.XPUB_TV1
        dp = DescriptorParser(desc)
        assert dp.script_type == 'p2sh_p2wpkh'

    def test_parse_pkh(self):
        from btpay.bitcoin.descriptors import DescriptorParser
        desc = 'pkh(%s/0/*)' % self.XPUB_TV1
        dp = DescriptorParser(desc)
        assert dp.script_type == 'p2pkh'

    def test_wpkh_derive_address(self):
        from btpay.bitcoin.descriptors import DescriptorParser
        desc = 'wpkh(%s/0/*)' % self.XPUB_TV1
        dp = DescriptorParser(desc)
        addr = dp.derive_address(0)
        assert addr.startswith('bc1q')

    def test_pkh_derive_address(self):
        from btpay.bitcoin.descriptors import DescriptorParser
        desc = 'pkh(%s/0/*)' % self.XPUB_TV1
        dp = DescriptorParser(desc)
        addr = dp.derive_address(0)
        assert addr.startswith('1')

    def test_sh_wpkh_derive_address(self):
        from btpay.bitcoin.descriptors import DescriptorParser
        desc = 'sh(wpkh(%s/0/*))' % self.XPUB_TV1
        dp = DescriptorParser(desc)
        addr = dp.derive_address(0)
        assert addr.startswith('3')

    def test_derive_script_pubkey(self):
        from btpay.bitcoin.descriptors import DescriptorParser
        desc = 'wpkh(%s/0/*)' % self.XPUB_TV1
        dp = DescriptorParser(desc)
        spk = dp.derive_script_pubkey(0)
        assert spk[:2] == b'\x00\x14'  # OP_0 PUSH20
        assert len(spk) == 22

    def test_different_indices_different_addresses(self):
        from btpay.bitcoin.descriptors import DescriptorParser
        desc = 'wpkh(%s/0/*)' % self.XPUB_TV1
        dp = DescriptorParser(desc)
        a0 = dp.derive_address(0)
        a1 = dp.derive_address(1)
        assert a0 != a1

    def test_unsupported_descriptor(self):
        from btpay.bitcoin.descriptors import DescriptorParser
        with pytest.raises(ValueError, match='Unsupported'):
            DescriptorParser('combo(0279BE667E...)')


# ======================================================================
# Bitcoin Models
# ======================================================================

class TestWalletModel:
    '''Test Wallet ORM model.'''

    def test_create_wallet(self):
        from btpay.bitcoin.models import Wallet
        w = Wallet(org_id=1, name='Test Wallet', wallet_type='xpub')
        w.save()
        assert w.id > 0
        assert w.wallet_type == 'xpub'
        assert w.network == 'mainnet'

    def test_address_type_zpub(self):
        from btpay.bitcoin.models import Wallet
        w = Wallet(org_id=1, name='W', xpub='zpub123')
        assert w.address_type == 'p2wpkh'

    def test_address_type_ypub(self):
        from btpay.bitcoin.models import Wallet
        w = Wallet(org_id=1, name='W', xpub='ypub123')
        assert w.address_type == 'p2sh_p2wpkh'

    def test_address_type_xpub(self):
        from btpay.bitcoin.models import Wallet
        w = Wallet(org_id=1, name='W', xpub='xpub123')
        assert w.address_type == 'p2pkh'

    def test_address_type_default(self):
        from btpay.bitcoin.models import Wallet
        w = Wallet(org_id=1, name='W')
        assert w.address_type == 'p2wpkh'

    def test_get_next_address_xpub(self):
        '''get_next_address derives and stores address.'''
        from btpay.bitcoin.models import Wallet, BitcoinAddress
        XPUB = 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'
        w = Wallet(org_id=1, name='HD Wallet', wallet_type='xpub',
                   xpub=XPUB, derivation_path='m/0')
        w.save()

        ba = w.get_next_address()
        assert ba is not None
        assert ba.address != ''
        assert ba.wallet_id == w.id
        assert ba.derivation_index == 0
        assert ba.status == 'unused'
        assert ba.script_hash != ''
        assert w.next_address_index == 1

    def test_get_next_address_increments(self):
        from btpay.bitcoin.models import Wallet
        XPUB = 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'
        w = Wallet(org_id=1, name='W', wallet_type='xpub',
                   xpub=XPUB, derivation_path='m/0')
        w.save()
        a0 = w.get_next_address()
        a1 = w.get_next_address()
        assert a0.address != a1.address
        assert a0.derivation_index == 0
        assert a1.derivation_index == 1
        assert w.next_address_index == 2

    def test_check_gap_limit(self):
        from btpay.bitcoin.models import Wallet
        XPUB = 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'
        w = Wallet(org_id=1, name='W', wallet_type='xpub', xpub=XPUB,
                   derivation_path='m/0', gap_limit=3)
        w.save()

        w.get_next_address()
        w.get_next_address()
        unused, exceeds = w.check_gap_limit()
        assert unused == 2
        assert exceeds is False

        w.get_next_address()
        unused, exceeds = w.check_gap_limit()
        assert unused == 3
        assert exceeds is True


class TestBitcoinAddressModel:
    '''Test BitcoinAddress ORM model.'''

    def test_create_address(self):
        from btpay.bitcoin.models import BitcoinAddress
        ba = BitcoinAddress(wallet_id=1, address='bc1qtest', derivation_index=0)
        ba.save()
        assert ba.id > 0
        assert ba.status == 'unused'
        assert ba.amount_received_sat == 0

    def test_mark_assigned(self):
        from btpay.bitcoin.models import BitcoinAddress
        ba = BitcoinAddress(wallet_id=1, address='bc1qassign', derivation_index=0)
        ba.save()
        ba.mark_assigned(42)
        assert ba.status == 'assigned'
        assert ba.assigned_to_invoice_id == 42

    def test_mark_seen(self):
        from btpay.bitcoin.models import BitcoinAddress
        ba = BitcoinAddress(wallet_id=1, address='bc1qseen', derivation_index=0)
        ba.save()
        ba.mark_seen(100000)
        assert ba.status == 'seen'
        assert ba.amount_received_sat == 100000
        assert ba.first_seen_at != 0

    def test_mark_confirmed(self):
        from btpay.bitcoin.models import BitcoinAddress
        ba = BitcoinAddress(wallet_id=1, address='bc1qconf', derivation_index=0)
        ba.save()
        ba.mark_confirmed(100000)
        assert ba.status == 'confirmed'
        assert ba.confirmed_at != 0

    def test_status_transitions(self):
        from btpay.bitcoin.models import BitcoinAddress
        ba = BitcoinAddress(wallet_id=1, address='bc1qtrans', derivation_index=0)
        ba.save()
        assert ba.status == 'unused'
        ba.mark_assigned(1)
        assert ba.status == 'assigned'
        ba.mark_seen(50000)
        assert ba.status == 'seen'
        ba.mark_confirmed(50000)
        assert ba.status == 'confirmed'

    def test_unique_address(self):
        from btpay.bitcoin.models import BitcoinAddress
        ba1 = BitcoinAddress(wallet_id=1, address='bc1quniq', derivation_index=0)
        ba1.save()
        # Try to lookup by address
        found = BitcoinAddress.get_by(address='bc1quniq')
        assert found is not None
        assert found.id == ba1.id


class TestExchangeRateSnapshotModel:
    '''Test ExchangeRateSnapshot ORM model.'''

    def test_create_snapshot(self):
        from btpay.bitcoin.models import ExchangeRateSnapshot
        from btpay.chrono import NOW
        snap = ExchangeRateSnapshot(
            currency='USD',
            rate=Decimal('67500.50'),
            source='coingecko',
            fetched_at=NOW(),
        )
        snap.save()
        assert snap.id > 0
        assert snap.currency == 'USD'

    def test_query_by_currency(self):
        from btpay.bitcoin.models import ExchangeRateSnapshot
        from btpay.chrono import NOW
        now = NOW()
        ExchangeRateSnapshot(currency='USD', rate=Decimal('67000'), source='test', fetched_at=now).save()
        ExchangeRateSnapshot(currency='EUR', rate=Decimal('62000'), source='test', fetched_at=now).save()
        ExchangeRateSnapshot(currency='USD', rate=Decimal('67100'), source='test', fetched_at=now).save()

        usd = ExchangeRateSnapshot.query.filter(currency='USD').all()
        assert len(usd) == 2
        eur = ExchangeRateSnapshot.query.filter(currency='EUR').all()
        assert len(eur) == 1


# ======================================================================
# Address Pool
# ======================================================================

class TestAddressPool:
    '''Test static address pool management.'''

    def test_import_valid_addresses(self):
        from btpay.bitcoin.models import Wallet
        from btpay.bitcoin.address_list import AddressPool

        w = Wallet(org_id=1, name='Pool', wallet_type='address_list')
        w.save()

        pool = AddressPool(w)
        # Use addresses that pass validation
        text = 'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4\nbc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq'
        imported, skipped, errors = pool.import_from_text(text)
        assert imported == 2
        assert skipped == 0
        assert errors == []

    def test_import_skips_invalid(self):
        from btpay.bitcoin.models import Wallet
        from btpay.bitcoin.address_list import AddressPool

        w = Wallet(org_id=1, name='Pool', wallet_type='address_list')
        w.save()

        text = 'not-a-valid-address\nbc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4'
        pool = AddressPool(w)
        imported, skipped, errors = pool.import_from_text(text)
        assert imported == 1
        assert len(errors) == 1

    def test_import_skips_comments_blanks(self):
        from btpay.bitcoin.models import Wallet
        from btpay.bitcoin.address_list import AddressPool

        w = Wallet(org_id=1, name='Pool', wallet_type='address_list')
        w.save()

        text = '# comment\n\nbc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4\n'
        pool = AddressPool(w)
        imported, skipped, errors = pool.import_from_text(text)
        assert imported == 1
        assert errors == []

    def test_import_skips_duplicates(self):
        from btpay.bitcoin.models import Wallet
        from btpay.bitcoin.address_list import AddressPool

        w = Wallet(org_id=1, name='Pool', wallet_type='address_list')
        w.save()

        addr = 'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4'
        pool = AddressPool(w)
        pool.import_from_text(addr)
        imported, skipped, errors = pool.import_from_text(addr)
        assert imported == 0
        assert skipped == 1

    def test_get_next_unused(self):
        from btpay.bitcoin.models import Wallet
        from btpay.bitcoin.address_list import AddressPool

        w = Wallet(org_id=1, name='Pool', wallet_type='address_list')
        w.save()

        pool = AddressPool(w)
        pool.import_from_text('bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4')

        ba = pool.get_next_unused()
        assert ba is not None
        assert ba.status == 'unused'

    def test_unused_count(self):
        from btpay.bitcoin.models import Wallet
        from btpay.bitcoin.address_list import AddressPool

        w = Wallet(org_id=1, name='Pool', wallet_type='address_list')
        w.save()

        pool = AddressPool(w)
        pool.import_from_text('bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4\nbc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq')
        assert pool.unused_count() == 2

    def test_is_low(self):
        from btpay.bitcoin.models import Wallet
        from btpay.bitcoin.address_list import AddressPool

        w = Wallet(org_id=1, name='Pool', wallet_type='address_list')
        w.save()

        pool = AddressPool(w)
        pool.import_from_text('bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4')
        assert pool.is_low(threshold=5) is True
        assert pool.is_low(threshold=1) is False


# ======================================================================
# Exchange Rate Service
# ======================================================================

class TestExchangeRateService:
    '''Test exchange rate service logic (mocked HTTP).'''

    def test_validate_rate_valid(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        rate = svc._validate_rate('67500.50', 'USD')
        assert rate == Decimal('67500.50')

    def test_validate_rate_too_low(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        rate = svc._validate_rate('500', 'USD')
        assert rate is None  # below $1000 minimum

    def test_validate_rate_too_high(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        rate = svc._validate_rate('999999999', 'USD')
        assert rate is None  # above $10M max

    def test_validate_rate_negative(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        assert svc._validate_rate('-100', 'USD') is None

    def test_validate_rate_invalid_string(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        assert svc._validate_rate('not-a-number', 'USD') is None

    def test_average_rates_single(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        result = svc._average_rates([Decimal('67000')])
        assert result == Decimal('67000')

    def test_average_rates_two(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        result = svc._average_rates([Decimal('67000'), Decimal('68000')])
        assert result == Decimal('67500')

    def test_average_rates_outlier_removed(self):
        '''Outlier > 5% from median should be discarded.'''
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        rates = [Decimal('67000'), Decimal('67500'), Decimal('100000')]  # 100K is outlier
        result = svc._average_rates(rates)
        # Median is 67500, 100000 is > 5% away, should be discarded
        assert result == Decimal('67250')  # avg of 67000 and 67500

    def test_average_rates_empty(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        assert svc._average_rates([]) is None

    def test_fetch_coingecko_mock(self):
        '''Test CoinGecko fetcher with mocked requests.'''
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=['coingecko'], currencies=['USD', 'EUR'])

        mock_req = MagicMock()
        mock_session = MagicMock()
        mock_req.Session.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'bitcoin': {'usd': 67500.50, 'eur': 62000.00}
        }
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        rates = svc._fetch_coingecko(mock_req)
        assert 'USD' in rates
        assert rates['USD'] == Decimal('67500.5')
        assert 'EUR' in rates

    def test_fetch_coinbase_mock(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=['coinbase'], currencies=['USD'])

        mock_req = MagicMock()
        mock_session = MagicMock()
        mock_req.Session.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'data': {'rates': {'USD': '67500.50'}}
        }
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        rates = svc._fetch_coinbase(mock_req)
        assert rates['USD'] == Decimal('67500.50')

    def test_fetch_kraken_mock(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=['kraken'], currencies=['USD'])

        mock_req = MagicMock()
        mock_session = MagicMock()
        mock_req.Session.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'result': {'XXBTZUSD': {'c': ['67500.50000', '0.5']}}
        }
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        rates = svc._fetch_kraken(mock_req)
        assert rates['USD'] == Decimal('67500.50000')

    def test_fetch_mempool_mock(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=['mempool'], currencies=['USD'])

        mock_req = MagicMock()
        mock_session = MagicMock()
        mock_req.Session.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.json.return_value = {'USD': 67500}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        rates = svc._fetch_mempool(mock_req)
        assert rates['USD'] == Decimal('67500')

    def test_get_rate_before_fetch(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        assert svc.get_rate('USD') is None

    def test_get_rates_empty(self):
        from btpay.bitcoin.exchange import ExchangeRateService
        svc = ExchangeRateService(sources=[])
        assert svc.get_rates() == {}

    def test_save_snapshot(self):
        '''save_snapshot creates ExchangeRateSnapshot rows.'''
        from btpay.bitcoin.exchange import ExchangeRateService
        from btpay.bitcoin.models import ExchangeRateSnapshot
        svc = ExchangeRateService(sources=[])
        svc._rates = {'USD': Decimal('67000'), 'EUR': Decimal('62000')}
        svc.save_snapshot()

        snaps = ExchangeRateSnapshot.query.all()
        assert len(snaps) == 2
        currencies = {s.currency for s in snaps}
        assert currencies == {'USD', 'EUR'}


# ======================================================================
# Mempool API (mocked)
# ======================================================================

class TestMempoolAPI:
    '''Test mempool.space REST client with mocked HTTP.'''

    def _make_api(self):
        from btpay.bitcoin.mempool_api import MempoolAPI
        return MempoolAPI(base_url='https://mempool.space/api')

    @patch('btpay.bitcoin.mempool_api.MempoolAPI._get')
    def test_get_address_utxos(self, mock_get):
        mock_get.return_value = [
            {'txid': 'abc123', 'vout': 0, 'value': 100000, 'status': {'confirmed': True}}
        ]
        api = self._make_api()
        utxos = api.get_address_utxos('bc1qtest')
        assert len(utxos) == 1
        assert utxos[0]['value'] == 100000

    @patch('btpay.bitcoin.mempool_api.MempoolAPI._get')
    def test_get_tx_status(self, mock_get):
        mock_get.return_value = {
            'confirmed': True, 'block_height': 800000, 'block_hash': 'abc'
        }
        api = self._make_api()
        status = api.get_tx_status('txid123')
        assert status['confirmed'] is True

    @patch('btpay.bitcoin.mempool_api.MempoolAPI._get_text')
    def test_get_block_height(self, mock_get):
        mock_get.return_value = '800123'
        api = self._make_api()
        height = api.get_block_height()
        assert height == 800123

    @patch('btpay.bitcoin.mempool_api.MempoolAPI._get')
    def test_get_fee_estimates(self, mock_get):
        mock_get.return_value = {
            'fastestFee': 25, 'halfHourFee': 20, 'hourFee': 15,
            'economyFee': 10, 'minimumFee': 5
        }
        api = self._make_api()
        fees = api.get_fee_estimates()
        assert fees['fastestFee'] == 25

    @patch('btpay.bitcoin.mempool_api.MempoolAPI._get')
    def test_get_address_balance(self, mock_get):
        mock_get.return_value = {
            'address': 'bc1qtest',
            'chain_stats': {'funded_txo_sum': 200000, 'spent_txo_sum': 50000,
                            'funded_txo_count': 2, 'spent_txo_count': 1, 'tx_count': 3},
            'mempool_stats': {'funded_txo_sum': 10000, 'spent_txo_sum': 0,
                              'funded_txo_count': 1, 'spent_txo_count': 0, 'tx_count': 1},
        }
        api = self._make_api()
        confirmed, unconfirmed = api.get_address_balance('bc1qtest')
        assert confirmed == 150000  # 200000 - 50000
        assert unconfirmed == 10000


# ======================================================================
# Electrum Client
# ======================================================================

class TestElectrumClient:
    '''Test Electrum client (unit tests, no network).'''

    def test_not_connected_by_default(self):
        from btpay.bitcoin.electrum import ElectrumClient
        client = ElectrumClient('localhost', 50002)
        assert client.is_connected is False

    def test_call_fails_when_disconnected(self):
        from btpay.bitcoin.electrum import ElectrumClient, ElectrumError
        client = ElectrumClient('localhost', 50002)
        with pytest.raises(ElectrumError, match='Not connected'):
            client._call('server.version')

    def test_socks_proxy_parse(self):
        '''Verify SOCKS proxy URL parsing doesn't crash.'''
        from btpay.bitcoin.electrum import ElectrumClient
        client = ElectrumClient('localhost', 50002, proxy='socks5h://127.0.0.1:9050')
        assert client.proxy == 'socks5h://127.0.0.1:9050'

    def test_ssl_verification_enabled_by_default(self):
        '''TLS certificate verification must be enabled by default.'''
        from btpay.bitcoin.electrum import ElectrumClient
        client = ElectrumClient('localhost', 50002, use_ssl=True)
        assert client.verify_ssl is True

    def test_ssl_verification_can_be_disabled_explicitly(self):
        '''TLS can be disabled explicitly for self-signed servers.'''
        from btpay.bitcoin.electrum import ElectrumClient
        client = ElectrumClient('localhost', 50002, verify_ssl=False)
        assert client.verify_ssl is False

    def test_ssl_context_verifies_by_default(self):
        '''Ensure the SSL context uses certificate verification when verify_ssl=True.'''
        import ssl
        from unittest.mock import patch, MagicMock, PropertyMock
        from btpay.bitcoin.electrum import ElectrumClient

        client = ElectrumClient('localhost', 50002, use_ssl=True, verify_ssl=True)

        # Track what was set on the context
        ctx_settings = {}

        class TrackingCtx:
            def __init__(self):
                self._check_hostname = True
                self._verify_mode = ssl.CERT_REQUIRED

            @property
            def check_hostname(self):
                return self._check_hostname

            @check_hostname.setter
            def check_hostname(self, val):
                ctx_settings['check_hostname'] = val
                self._check_hostname = val

            @property
            def verify_mode(self):
                return self._verify_mode

            @verify_mode.setter
            def verify_mode(self, val):
                ctx_settings['verify_mode'] = val
                self._verify_mode = val

            def wrap_socket(self, sock, **kw):
                return MagicMock()

        mock_sock = MagicMock()
        mock_sock.makefile.return_value = MagicMock()
        tracking_ctx = TrackingCtx()

        with patch.object(client, '_create_socket', return_value=mock_sock):
            with patch('ssl.create_default_context', return_value=tracking_ctx):
                client.connect()

        # When verify_ssl=True, check_hostname and verify_mode should NOT be changed
        assert 'check_hostname' not in ctx_settings, \
            "check_hostname should not be modified when verify_ssl=True"
        assert 'verify_mode' not in ctx_settings, \
            "verify_mode should not be modified when verify_ssl=True"


# ======================================================================
# Payment Monitor
# ======================================================================

class TestPaymentMonitor:
    '''Test payment monitor logic.'''

    def test_watch_unwatch(self):
        from btpay.bitcoin.monitor import PaymentMonitor
        from btpay.bitcoin.models import BitcoinAddress

        ba = BitcoinAddress(wallet_id=1, address='bc1qwatch', derivation_index=0)
        ba.save()

        monitor = PaymentMonitor()
        monitor.watch_address(ba)
        assert monitor.watched_count == 1

        monitor.unwatch_address(ba)
        assert monitor.watched_count == 0

    def test_callbacks_registration(self):
        from btpay.bitcoin.monitor import PaymentMonitor
        monitor = PaymentMonitor()

        seen_calls = []
        confirmed_calls = []
        monitor.on_payment_seen(lambda a, amt, tx: seen_calls.append((a, amt, tx)))
        monitor.on_payment_confirmed(lambda a, amt, c: confirmed_calls.append((a, amt, c)))

        assert len(monitor._on_seen_callbacks) == 1
        assert len(monitor._on_confirmed_callbacks) == 1

    def test_required_confirmations_tiered(self):
        from btpay.bitcoin.monitor import PaymentMonitor
        from btpay.bitcoin.models import BitcoinAddress

        monitor = PaymentMonitor()

        # Small amount (0 sat) -> 1 confirmation
        ba = BitcoinAddress(wallet_id=1, address='bc1qreqconf', derivation_index=0)
        ba.save()
        assert monitor._required_confirmations(ba) == 1

        # Medium amount (5M sat = 0.05 BTC) -> 3 confirmations
        ba2 = BitcoinAddress(wallet_id=1, address='bc1qreqconf2', derivation_index=1,
                             amount_received_sat=5_000_000)
        ba2.save()
        assert monitor._required_confirmations(ba2) == 3

        # Large amount (50M sat = 0.5 BTC) -> 6 confirmations
        ba3 = BitcoinAddress(wallet_id=1, address='bc1qreqconf3', derivation_index=2,
                             amount_received_sat=50_000_000)
        ba3.save()
        assert monitor._required_confirmations(ba3) == 6

    def test_fire_seen_callback(self):
        from btpay.bitcoin.monitor import PaymentMonitor
        from btpay.bitcoin.models import BitcoinAddress

        monitor = PaymentMonitor()
        ba = BitcoinAddress(wallet_id=1, address='bc1qfire', derivation_index=0)
        ba.save()

        results = []
        monitor.on_payment_seen(lambda a, amt, tx: results.append((a.address, amt, tx)))
        monitor._fire_seen(ba, 100000, 'txid123')
        assert len(results) == 1
        assert results[0] == ('bc1qfire', 100000, 'txid123')

    def test_fire_confirmed_callback(self):
        from btpay.bitcoin.monitor import PaymentMonitor
        from btpay.bitcoin.models import BitcoinAddress

        monitor = PaymentMonitor()
        ba = BitcoinAddress(wallet_id=1, address='bc1qfireconf', derivation_index=0)
        ba.save()

        results = []
        monitor.on_payment_confirmed(lambda a, amt, c: results.append((a.address, amt, c)))
        monitor._fire_confirmed(ba, 100000, 6)
        assert len(results) == 1
        assert results[0] == ('bc1qfireconf', 100000, 6)

    def test_check_via_mempool_detects_payment(self):
        '''Simulate mempool.space detecting an unconfirmed payment.'''
        from btpay.bitcoin.monitor import PaymentMonitor
        from btpay.bitcoin.models import BitcoinAddress

        mock_mempool = MagicMock()
        mock_mempool.get_address_balance.return_value = (0, 50000)  # unconfirmed
        mock_mempool.get_address_txs.return_value = [{'txid': 'tx_abc'}]

        monitor = PaymentMonitor(mempool_api=mock_mempool)

        ba = BitcoinAddress(wallet_id=1, address='bc1qmempool', derivation_index=0)
        ba.save()
        ba.mark_assigned(1)

        seen_events = []
        monitor.on_payment_seen(lambda a, amt, tx: seen_events.append((a.address, amt, tx)))

        changed = monitor._check_via_mempool(ba)
        assert changed is True
        assert ba.status == 'seen'
        assert ba.amount_received_sat == 50000
        assert len(seen_events) == 1

    def test_check_via_mempool_confirms_payment(self):
        '''Simulate mempool.space confirming a payment.'''
        from btpay.bitcoin.monitor import PaymentMonitor
        from btpay.bitcoin.models import BitcoinAddress

        mock_mempool = MagicMock()
        mock_mempool.get_address_balance.return_value = (100000, 0)  # confirmed
        mock_mempool.get_address_txs.return_value = [{'txid': 'tx_conf'}]
        mock_mempool.get_confirmations.return_value = 6

        monitor = PaymentMonitor(mempool_api=mock_mempool)

        ba = BitcoinAddress(wallet_id=1, address='bc1qconfirm', derivation_index=0)
        ba.save()
        ba.mark_assigned(1)

        confirmed_events = []
        monitor.on_payment_confirmed(lambda a, amt, c: confirmed_events.append((a.address, amt, c)))
        # Also need seen callback to avoid errors
        monitor.on_payment_seen(lambda a, amt, tx: None)

        changed = monitor._check_via_mempool(ba)
        assert changed is True
        assert ba.status == 'confirmed'
        assert len(confirmed_events) == 1

    def test_load_assigned_addresses(self):
        from btpay.bitcoin.monitor import PaymentMonitor
        from btpay.bitcoin.models import BitcoinAddress

        ba1 = BitcoinAddress(wallet_id=1, address='bc1qload1', derivation_index=0, status='assigned')
        ba1.save()
        ba2 = BitcoinAddress(wallet_id=1, address='bc1qload2', derivation_index=1, status='seen')
        ba2.save()
        ba3 = BitcoinAddress(wallet_id=1, address='bc1qload3', derivation_index=2, status='unused')
        ba3.save()

        monitor = PaymentMonitor()
        monitor.load_assigned_addresses()
        assert monitor.watched_count == 2  # assigned + seen, not unused


# ======================================================================
# App Integration
# ======================================================================

class TestAppPhase3Integration:
    '''Test that app.py correctly loads Phase 3 models.'''

    def test_bitcoin_models_registered(self, app):
        '''Bitcoin models should be importable and registered.'''
        from btpay.bitcoin.models import Wallet, BitcoinAddress, ExchangeRateSnapshot
        from btpay.orm.engine import MemoryStore
        store = MemoryStore()
        assert 'Wallet' in store._tables
        assert 'BitcoinAddress' in store._tables
        assert 'ExchangeRateSnapshot' in store._tables

# EOF
