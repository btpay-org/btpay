#
# Tests for reference numbers (NaCl SecretBox implementation)
#
import pytest
from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import Text
from btpay.security.refnums import ReferenceNumbers


class RefTestModel(BaseMixin, MemModel):
    name = Text()


@pytest.fixture(autouse=True)
def register_test_model():
    '''Ensure RefTestModel is in the refnums class_names list for testing.'''
    rn = ReferenceNumbers()
    if 'RefTestModel' not in rn.class_names:
        rn.class_names.append('RefTestModel')
    yield


def test_pack_unpack():
    obj = RefTestModel(name='test')
    obj.save()

    rn = ReferenceNumbers()
    packed = rn.pack(obj)
    assert isinstance(packed, str)
    assert '-' in packed

    # Verify regex matches
    assert rn.regex.match(packed), "Refnum doesn't match expected format"

    # Unpack
    cls, pk = rn.unpack(packed, just_pk=True)
    assert cls == RefTestModel
    assert pk == obj.id


def test_corrupt_refnum():
    rn = ReferenceNumbers()
    with pytest.raises(ValueError, match="Bad refnum"):
        rn.unpack('GARBAGE')


def test_multiple_objects():
    rn = ReferenceNumbers()
    objs = []
    for i in range(10):
        o = RefTestModel(name='test_%d' % i)
        o.save()
        objs.append(o)

    for o in objs:
        packed = rn.pack(o)
        cls, pk = rn.unpack(packed, just_pk=True)
        assert pk == o.id


def test_refnum_via_model():
    obj = RefTestModel(name='reftest')
    obj.save()
    ref = obj.ref_number
    assert isinstance(ref, str)
    assert '-' in ref


def test_format_is_uppercase_hex():
    '''Reference numbers must be uppercase hex with dash separator.'''
    obj = RefTestModel(name='hex_test')
    obj.save()

    rn = ReferenceNumbers()
    packed = rn.pack(obj)
    parts = packed.split('-')
    assert len(parts) == 2
    assert len(parts[0]) == 24  # 12 bytes = 24 hex chars
    # All chars must be uppercase hex
    import re
    assert re.match(r'^[0-9A-F]{24}-[0-9A-F]+$', packed)


def test_tampered_refnum_rejected():
    '''Tampered refnums must be rejected by decryption.'''
    obj = RefTestModel(name='tamper_test')
    obj.save()

    rn = ReferenceNumbers()
    packed = rn.pack(obj)

    # Flip a character in the first half (encrypted payload)
    parts = packed.split('-')
    chars = list(parts[0])
    chars[0] = 'A' if chars[0] != 'A' else 'B'
    tampered = ''.join(chars) + '-' + parts[1]

    with pytest.raises(ValueError, match="Corrupt refnum"):
        rn.unpack(tampered)


def test_unique_refnums_for_different_objects():
    '''Different objects must produce different refnums.'''
    rn = ReferenceNumbers()
    obj1 = RefTestModel(name='unique_1')
    obj1.save()
    obj2 = RefTestModel(name='unique_2')
    obj2.save()

    ref1 = rn.pack(obj1)
    ref2 = rn.pack(obj2)
    assert ref1 != ref2, "Different objects must produce different refnums"

    # Both should unpack correctly
    cls1, pk1 = rn.unpack(ref1, just_pk=True)
    cls2, pk2 = rn.unpack(ref2, just_pk=True)
    assert pk1 == obj1.id
    assert pk2 == obj2.id


def test_different_keys_produce_different_refnums():
    '''Different NaCl keys must produce incompatible refnums.'''
    obj = RefTestModel(name='key_test')
    obj.save()

    rn = ReferenceNumbers()

    # Save original keys to restore later
    original_box = ReferenceNumbers._cls._box
    original_nonce = ReferenceNumbers._cls._nonce

    key1 = 'a' * 64  # 32 bytes of 0xaa
    nonce1 = 'b' * 48  # 24 bytes of 0xbb
    key2 = 'c' * 64  # 32 bytes of 0xcc
    nonce2 = 'b' * 48  # same nonce

    # Pack with key1
    rn.reconfigure(key1, nonce1)
    packed1 = rn.pack(obj)

    # Switch to key2 — should fail to unpack key1's output
    rn.reconfigure(key2, nonce2)
    with pytest.raises(ValueError, match="Corrupt refnum"):
        rn.unpack(packed1)

    # Restore original keys
    ReferenceNumbers._cls._box = original_box
    ReferenceNumbers._cls._nonce = original_nonce


def test_reconfigure_changes_keys():
    '''reconfigure() must change the encryption keys.'''
    rn = ReferenceNumbers()
    obj = RefTestModel(name='reconfig_test')
    obj.save()

    # Save original keys to restore later
    original_box = ReferenceNumbers._cls._box
    original_nonce = ReferenceNumbers._cls._nonce

    # Pack with current keys
    ref1 = rn.pack(obj)

    # Reconfigure with different keys
    new_key = 'dd' * 32
    new_nonce = 'ee' * 24
    rn.reconfigure(new_key, new_nonce)

    # Pack with new keys should produce different refnum
    ref2 = rn.pack(obj)
    assert ref1 != ref2

    # Old refnum should fail with new keys
    with pytest.raises(ValueError, match="Corrupt refnum"):
        rn.unpack(ref1)

    # Restore original keys
    ReferenceNumbers._cls._box = original_box
    ReferenceNumbers._cls._nonce = original_nonce


# EOF
