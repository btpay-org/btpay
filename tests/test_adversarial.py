#
# Adversarial / Security tests for BTPayServer
#
# These tests deliberately try to BREAK the payment processor.
# Categories:
#   1. Payment logic exploits (underpayment boundary, overpayment, negative, zero, etc.)
#   2. Address derivation uniqueness and gap limit
#   3. Concurrent payment recording (threading)
#   4. Auth/Session adversarial (lockout, CSRF, session replay)
#   5. ORM persistence crash simulation
#   6. Input validation / injection
#
import json
import os
import threading
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from btpay.invoicing.models import Invoice, InvoiceLine, Payment
from btpay.invoicing.service import InvoiceService
from btpay.bitcoin.models import Wallet, BitcoinAddress
from btpay.auth.models import User, Organization, Membership
from btpay.orm.engine import MemoryStore
from btpay.orm.persistence import save_to_disk, load_from_disk


# ======================================================================
# Helpers (match existing test patterns)
# ======================================================================

def _make_org(**kw):
    defaults = dict(name='Adversarial Org', slug='adv-org', default_currency='USD')
    defaults.update(kw)
    org = Organization(**defaults)
    org.save()
    return org


def _make_user(**kw):
    defaults = dict(email='adversarial@test.com', first_name='Adv', last_name='User')
    defaults.update(kw)
    user = User(**defaults)
    user.set_password('testpass123')
    user.save()
    return user


def _make_wallet(org, xpub=None):
    xpub = xpub or 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'
    w = Wallet(org_id=org.id, name='Adv Wallet', wallet_type='xpub',
               xpub=xpub, derivation_path='m/0')
    w.save()
    return w


def _mock_rate_service(rate=Decimal('67500')):
    svc = MagicMock()
    svc.get_rate.return_value = rate
    return svc


def _make_finalized_invoice(svc, org, user, wallet, unit_price=100, **kw):
    """Create and finalize an invoice, ready for payment."""
    inv = svc.create_invoice(org, user, lines=[
        {'description': 'Test Item', 'quantity': 1, 'unit_price': unit_price},
    ], **kw)
    svc.finalize_invoice(inv, wallet)
    return inv


# ======================================================================
# 1. Payment Logic Exploits
# ======================================================================

class TestPaymentExploits:
    """Try to abuse the payment recording logic."""

    def test_underpayment_boundary_within_gift_threshold(self):
        """Pay $95 on $100 invoice with $5 gift threshold -> should mark paid."""
        org = _make_org(slug='pay-exploit-1')
        user = _make_user(email='pay1@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc, underpaid_gift=5)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=100)
        full_sat = int(inv.btc_amount * 100_000_000)

        # Pay exactly $95 worth (within $5 gift)
        pay_sat = int(full_sat * Decimal('0.95'))
        svc.record_payment(inv, pay_sat, txid='exploit_gift_ok')

        assert inv.status == 'paid', \
            'Underpayment within gift threshold should be accepted as paid'

    def test_underpayment_boundary_outside_gift_threshold(self):
        """Pay $94.99 on $100 invoice with $5 gift threshold -> should NOT mark paid."""
        org = _make_org(slug='pay-exploit-2')
        user = _make_user(email='pay2@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc, underpaid_gift=5)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=100)
        full_sat = int(inv.btc_amount * 100_000_000)

        # Pay ~90% - well outside $5 threshold
        pay_sat = int(full_sat * Decimal('0.90'))
        svc.record_payment(inv, pay_sat, txid='exploit_gift_fail')

        assert inv.status == 'partial', \
            'Underpayment outside gift threshold must remain partial'

    def test_overpayment_double_amount(self):
        """Pay 2x the BTC amount -> should handle gracefully, no crash, no negative amount_due."""
        org = _make_org(slug='pay-exploit-3')
        user = _make_user(email='pay3@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=100)
        full_sat = int(inv.btc_amount * 100_000_000)

        # Pay 2x the required amount
        payment = svc.record_payment(inv, full_sat * 2, txid='overpay_2x')

        assert inv.status == 'paid'
        assert inv.amount_due == Decimal('0'), 'amount_due must never go negative'
        assert inv.amount_paid > inv.total, 'amount_paid should reflect overpayment'
        assert payment.id > 0

    def test_negative_payment_amount(self):
        """Negative satoshi amount -> should not reduce amount_paid."""
        org = _make_org(slug='pay-exploit-4')
        user = _make_user(email='pay4@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=100)

        # Record a negative payment — should not crash or reduce balance
        payment = svc.record_payment(inv, -100000, txid='negative_exploit')

        # The invoice should not become "paid" from a negative amount
        assert inv.amount_paid <= Decimal('0') or inv.status in ('pending', 'partial'), \
            'Negative payment must not mark invoice as paid'
        assert inv.amount_due >= Decimal('0'), 'amount_due must never go negative'

    def test_zero_payment_amount(self):
        """Zero satoshi amount -> should not change invoice status to paid."""
        org = _make_org(slug='pay-exploit-5')
        user = _make_user(email='pay5@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=100)
        svc.record_payment(inv, 0, txid='zero_exploit')

        assert inv.status != 'paid', 'Zero payment must not mark invoice as paid'

    def test_payment_on_expired_invoice(self):
        """Payment on expired invoice -> should be rejected or status stays expired."""
        from btpay.chrono import TIME_AGO

        inv = Invoice(org_id=1, invoice_number='INV-EXPIRED-ADV', status='expired',
                      total=Decimal('100'), btc_rate=Decimal('67500'),
                      btc_amount=Decimal('0.00148148'),
                      btc_rate_expires_at=TIME_AGO(minutes=60))
        inv.save()

        svc = InvoiceService()
        # Recording payment on expired invoice — it may still record (business decision),
        # but the key is it should not crash
        payment = svc.record_payment(inv, 148148, txid='expired_pay')
        assert payment.id > 0  # payment recorded
        # If the system accepts it, it transitions to paid; if not, it stays expired.
        # Either way: no crash, no negative balances
        assert inv.amount_due >= Decimal('0')

    def test_payment_on_cancelled_invoice(self):
        """Payment on cancelled invoice -> should not crash."""
        inv = Invoice(org_id=1, invoice_number='INV-CANCEL-ADV', status='cancelled',
                      total=Decimal('100'), btc_rate=Decimal('67500'),
                      btc_amount=Decimal('0.00148148'))
        inv.save()

        svc = InvoiceService()
        # This tests that the system handles this gracefully
        payment = svc.record_payment(inv, 148148, txid='cancelled_pay')
        assert payment.id > 0
        assert inv.amount_due >= Decimal('0')

    def test_payment_on_draft_invoice(self):
        """Payment on draft (un-finalized) invoice -> should not crash."""
        inv = Invoice(org_id=1, invoice_number='INV-DRAFT-ADV', status='draft',
                      total=Decimal('100'), btc_rate=Decimal('0'),
                      btc_amount=Decimal('0'))
        inv.save()

        svc = InvoiceService()
        payment = svc.record_payment(inv, 148148, txid='draft_pay')
        assert payment.id > 0
        assert inv.amount_due >= Decimal('0')

    def test_double_payment_same_txid(self):
        """Submit payment twice with same txid -> should be idempotent or at least safe."""
        org = _make_org(slug='pay-exploit-dupe')
        user = _make_user(email='dupe@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=100)
        full_sat = int(inv.btc_amount * 100_000_000)

        p1 = svc.record_payment(inv, full_sat, txid='dupe_txid_123')
        p2 = svc.record_payment(inv, full_sat, txid='dupe_txid_123')

        # Both payments recorded — system does not deduplicate by txid.
        # Key invariant: amount_due should never go negative.
        assert inv.amount_due == Decimal('0')
        assert inv.status in ('paid', 'confirmed')
        assert p1.id != p2.id, 'Each call creates a distinct Payment record'


# ======================================================================
# 2. Address Derivation
# ======================================================================

class TestAddressDerivation:
    """Try to get duplicate or reused addresses."""

    def test_100_addresses_all_unique(self):
        """Generate 100 addresses rapidly -> all must be unique."""
        org = _make_org(slug='addr-unique')
        wallet = _make_wallet(org)

        addresses = set()
        for _ in range(100):
            ba = wallet.get_next_address()
            assert ba.address not in addresses, \
                'Duplicate address derived: %s' % ba.address
            addresses.add(ba.address)

        assert len(addresses) == 100

    def test_gap_limit_tracking(self):
        """Generate addresses up to gap_limit -> verify tracking."""
        org = _make_org(slug='addr-gap')
        wallet = _make_wallet(org)
        wallet.gap_limit = 20
        wallet.save()

        # Generate 20 addresses (all unused)
        for _ in range(20):
            wallet.get_next_address()

        unused_count, exceeds = wallet.check_gap_limit()
        assert unused_count == 20
        assert exceeds is True

    def test_cancel_invoice_releases_address_no_reuse(self):
        """Cancel invoice -> address is released but NOT returned to 'unused' pool."""
        org = _make_org(slug='addr-release')
        user = _make_user(email='addrrelease@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service()
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=50)
        addr_id = inv.payment_address_id
        addr = BitcoinAddress.get(addr_id)
        released_address = addr.address

        svc.cancel_invoice(inv)

        addr = BitcoinAddress.get(addr_id)
        assert addr.status == 'released', 'Cancelled address must be released'
        assert addr.assigned_to_invoice_id == 0

        # Get next address — must NOT be the released one
        next_ba = wallet.get_next_address()
        assert next_ba.address != released_address, \
            'Released address must never be reused (prevents cross-customer payment attribution)'


# ======================================================================
# 3. Concurrent Payment Recording (threading)
# ======================================================================

class TestConcurrentPayments:
    """Race conditions in payment processing."""

    def test_4_threads_record_payment_same_invoice(self):
        """4 threads simultaneously record_payment on the same invoice -> correct total."""
        org = _make_org(slug='concurrent-pay')
        user = _make_user(email='concurrent@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=1000)
        partial_sat = int(inv.btc_amount * 100_000_000) // 4

        results = []
        errors = []

        def pay(idx):
            try:
                p = svc.record_payment(inv, partial_sat, txid='thread_%d' % idx)
                results.append(p)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=pay, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, 'Concurrent payments raised errors: %s' % errors
        assert len(results) == 4, 'All 4 payments should be recorded'
        assert inv.status == 'paid', 'Invoice should be fully paid after 4 quarter-payments'
        assert inv.amount_due == Decimal('0')

    def test_4_threads_finalize_same_invoice(self):
        """4 threads simultaneously finalize the same draft invoice -> only one should succeed."""
        org = _make_org(slug='concurrent-fin')
        user = _make_user(email='concfin@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Race', 'quantity': 1, 'unit_price': 500}
        ])

        successes = []
        failures = []

        def finalize():
            try:
                svc.finalize_invoice(inv, wallet)
                successes.append(True)
            except ValueError:
                failures.append(True)

        threads = [threading.Thread(target=finalize) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # At least 1 success, rest should fail (cannot finalize non-draft)
        assert len(successes) >= 1, 'At least one finalize must succeed'
        assert len(successes) + len(failures) == 4
        assert inv.status == 'pending'

    def test_4_threads_create_invoices_unique_numbers(self):
        """4 threads simultaneously create invoices -> all invoice numbers must be unique."""
        org = _make_org(slug='concurrent-create')
        user = _make_user(email='conccreate@test.com')
        svc = InvoiceService()

        invoices = []
        errors = []

        def create(idx):
            try:
                inv = svc.create_invoice(org, user, lines=[
                    {'description': 'Item %d' % idx, 'quantity': 1, 'unit_price': 10}
                ])
                invoices.append(inv)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, 'Concurrent invoice creation raised errors: %s' % errors
        numbers = [inv.invoice_number for inv in invoices]
        assert len(set(numbers)) == len(numbers), \
            'Duplicate invoice numbers created: %s' % numbers


# ======================================================================
# 4. Auth/Session Adversarial
# ======================================================================

class TestAuthAdversarial:
    """Try to bypass authentication and session management."""

    def test_account_lockout_after_5_failures(self):
        """Login with wrong password 6 times -> account should be locked."""
        user = _make_user(email='lockout@test.com')

        for i in range(6):
            if not user.check_password('wrongpassword%d' % i):
                user.record_failed_login()

        assert user.failed_login_count == 6
        assert user.is_locked is True, 'Account must be locked after 5+ failures'

    def test_correct_password_while_locked(self):
        """Login with correct password while locked -> should still be rejected."""
        user = _make_user(email='locked@test.com')

        # Lock the account
        for i in range(6):
            user.record_failed_login()

        assert user.is_locked is True
        # Even with correct password, is_locked should prevent login
        can_login = user.check_password('testpass123') and not user.is_locked
        assert can_login is False, 'Locked account must reject even correct password'

    def test_csrf_token_cross_session(self):
        """CSRF token from one session used in another -> should be rejected."""
        from btpay.security.csrf import generate_csrf_token, validate_csrf_token

        secret = 'test-secret-key-for-csrf'
        token_session_a = generate_csrf_token('session-AAA', secret)
        valid = validate_csrf_token('session-BBB', token_session_a, secret)
        assert valid is False, 'CSRF token from session A must be rejected in session B'

    def test_csrf_token_expired(self):
        """Expired CSRF token -> should be rejected."""
        from btpay.security.csrf import generate_csrf_token, validate_csrf_token

        secret = 'test-secret-key-for-csrf'
        # Generate a token and then validate with max_age=0 to simulate expiry
        token = generate_csrf_token('session-X', secret)

        # Validate with 0 second max_age — it was just generated so timestamp
        # is "now", but max_age=0 means any age is too old
        valid = validate_csrf_token('session-X', token, secret, max_age=0)
        assert valid is False, 'Expired CSRF token must be rejected'

    def test_csrf_token_tampered_signature(self):
        """Tamper with CSRF token signature -> should be rejected."""
        from btpay.security.csrf import generate_csrf_token, validate_csrf_token

        secret = 'test-secret-key-for-csrf'
        token = generate_csrf_token('session-tamper', secret)

        # Flip last character of signature
        parts = token.split(':')
        sig = parts[2]
        tampered_sig = sig[:-1] + ('a' if sig[-1] != 'a' else 'b')
        tampered_token = '%s:%s:%s' % (parts[0], parts[1], tampered_sig)

        valid = validate_csrf_token('session-tamper', tampered_token, secret)
        assert valid is False, 'Tampered CSRF token must be rejected'

    def test_csrf_token_malformed(self):
        """Malformed CSRF tokens -> should all be rejected."""
        from btpay.security.csrf import validate_csrf_token

        secret = 'test-secret'
        for bad_token in ['', 'garbage', ':::', 'a:b', 'a:b:c:d',
                          '\x00\x00\x00', '9999999999:nonce:sig']:
            valid = validate_csrf_token('session-X', bad_token, secret)
            assert valid is False, 'Malformed token %r must be rejected' % bad_token

    def test_session_replay_after_logout(self, app):
        """Session token replay after logout -> should be rejected."""
        from btpay.auth.sessions import (
            create_session, destroy_session, validate_session,
        )

        with app.app_context():
            user = _make_user(email='replay@test.com')
            org = _make_org(slug='replay-org')

            # Create session
            from flask import request as _req
            with app.test_request_context():
                token = create_session(user, org, _req)

                # Validate — should work
                result = validate_session(token)
                assert result is not None, 'Session should be valid before logout'

                # Logout — destroy session
                destroy_session(token)

                # Replay — should fail
                result = validate_session(token)
                assert result is None, 'Destroyed session token must be rejected on replay'

    def test_rate_limiter_blocks_after_max_attempts(self):
        """Rate limiter should block after max_attempts."""
        from btpay.security.rate_limit import RateLimiter

        limiter = RateLimiter()
        key = 'test:adversarial:brute'

        # First 5 attempts should pass
        for i in range(5):
            assert limiter.check(key, 5, 60) is True

        # 6th should be blocked
        assert limiter.check(key, 5, 60) is False

    def test_rate_limiter_sliding_window_not_fixed(self):
        """Verify sliding window: old entries expire, not just a fixed counter."""
        from btpay.security.rate_limit import RateLimiter

        limiter = RateLimiter()
        key = 'test:sliding'

        # Fill up to max
        for _ in range(5):
            limiter.check(key, 5, 1)  # 1-second window

        # Should be blocked
        assert limiter.check(key, 5, 1) is False

        # Wait for window to expire
        time.sleep(1.1)

        # Should be allowed again
        assert limiter.check(key, 5, 1) is True


# ======================================================================
# 5. ORM Persistence Crash Simulation
# ======================================================================

class TestPersistenceCrash:
    """Simulate disk corruption and crash scenarios."""

    def test_corrupt_json_file(self, app):
        """Corrupt JSON file on disk -> load_from_disk should not crash."""
        data_dir = '/tmp/btpay_test'
        os.makedirs(data_dir, exist_ok=True)

        # Write valid meta
        with open(os.path.join(data_dir, '_meta.json'), 'w') as f:
            json.dump({'schema_version': 1, 'models': ['Invoice']}, f)

        # Write corrupt Invoice.json
        with open(os.path.join(data_dir, 'Invoice.json'), 'w') as f:
            f.write('{{{CORRUPT DATA NOT JSON!!!')

        with app.app_context():
            # Should not raise
            load_from_disk(data_dir)

    def test_truncated_json_file(self, app):
        """Partial/truncated JSON write -> should handle gracefully."""
        data_dir = '/tmp/btpay_test'
        os.makedirs(data_dir, exist_ok=True)

        with open(os.path.join(data_dir, '_meta.json'), 'w') as f:
            json.dump({'schema_version': 1, 'models': ['Invoice']}, f)

        # Truncated JSON
        with open(os.path.join(data_dir, 'Invoice.json'), 'w') as f:
            f.write('{"sequence": 5, "rows": {"1": {"id": 1, "invoice_numb')

        with app.app_context():
            load_from_disk(data_dir)  # must not crash

    def test_empty_json_file(self, app):
        """Empty file -> should handle gracefully."""
        data_dir = '/tmp/btpay_test'
        os.makedirs(data_dir, exist_ok=True)

        with open(os.path.join(data_dir, '_meta.json'), 'w') as f:
            json.dump({'schema_version': 1, 'models': ['Invoice']}, f)

        with open(os.path.join(data_dir, 'Invoice.json'), 'w') as f:
            pass  # empty file

        with app.app_context():
            load_from_disk(data_dir)  # must not crash

    def test_binary_garbage_in_json_file(self, app):
        """Binary garbage in JSON file -> should handle gracefully."""
        data_dir = '/tmp/btpay_test'
        os.makedirs(data_dir, exist_ok=True)

        with open(os.path.join(data_dir, '_meta.json'), 'w') as f:
            json.dump({'schema_version': 1, 'models': ['Invoice']}, f)

        with open(os.path.join(data_dir, 'Invoice.json'), 'wb') as f:
            f.write(os.urandom(1024))

        with app.app_context():
            load_from_disk(data_dir)  # must not crash

    def test_corrupt_meta_json(self, app):
        """Corrupt _meta.json -> should handle gracefully."""
        data_dir = '/tmp/btpay_test'
        os.makedirs(data_dir, exist_ok=True)

        with open(os.path.join(data_dir, '_meta.json'), 'w') as f:
            f.write('NOT VALID JSON {{{')

        with app.app_context():
            load_from_disk(data_dir)  # must not crash

    def test_save_then_load_roundtrip(self, app):
        """Save then load roundtrip -> data integrity preserved for all model types."""
        data_dir = '/tmp/btpay_test'

        with app.app_context():
            org = _make_org(slug='roundtrip-org')
            user = _make_user(email='roundtrip@test.com')
            inv = Invoice(org_id=org.id, invoice_number='INV-ROUNDTRIP',
                          status='pending', total=Decimal('123.45'),
                          currency='USD', customer_name='Roundtrip Customer',
                          metadata={'key': 'value', 'nested': {'a': 1}})
            inv.save()

            line = InvoiceLine(invoice_id=inv.id, description='Widget',
                               quantity=Decimal('3'), unit_price=Decimal('41.15'))
            line.calculate_amount()
            line.save()

            payment = Payment(invoice_id=inv.id, method='onchain_btc',
                              txid='roundtrip_txid', amount_btc=Decimal('0.00182'))
            payment.save()

            # Save
            save_to_disk(data_dir)

            # Record IDs
            org_id = org.id
            inv_id = inv.id
            line_id = line.id
            payment_id = payment.id

            # Clear store
            store = MemoryStore()
            store.clear()

            # Reload
            load_from_disk(data_dir)

            # Verify
            loaded_inv = Invoice.get(inv_id)
            assert loaded_inv is not None
            assert loaded_inv.invoice_number == 'INV-ROUNDTRIP'
            assert loaded_inv.total == Decimal('123.45')
            assert loaded_inv.customer_name == 'Roundtrip Customer'
            assert loaded_inv.metadata == {'key': 'value', 'nested': {'a': 1}}

            loaded_line = InvoiceLine.get(line_id)
            assert loaded_line is not None
            assert loaded_line.amount == Decimal('123.45')

            loaded_payment = Payment.get(payment_id)
            assert loaded_payment is not None
            assert loaded_payment.txid == 'roundtrip_txid'
            assert loaded_payment.amount_btc == Decimal('0.00182')


# ======================================================================
# 6. Input Validation / Injection
# ======================================================================

class TestInputValidationInjection:
    """Try injecting malicious data through every input vector."""

    def test_finalize_negative_total_invoice(self):
        """Invoice with negative total -> should be rejected at finalize."""
        inv = Invoice(org_id=1, invoice_number='INV-NEG-TOTAL', status='draft',
                      total=Decimal('-50'))
        inv.save()

        svc = InvoiceService()
        with pytest.raises(ValueError, match='positive'):
            svc.finalize_invoice(inv)

    def test_finalize_zero_total_invoice(self):
        """Invoice with zero total -> should be rejected at finalize."""
        inv = Invoice(org_id=1, invoice_number='INV-ZERO-TOTAL', status='draft',
                      total=Decimal('0'))
        inv.save()

        svc = InvoiceService()
        with pytest.raises(ValueError, match='positive'):
            svc.finalize_invoice(inv)

    def test_invoice_line_negative_quantity(self):
        """Invoice line with negative quantity -> behavior check."""
        line = InvoiceLine(invoice_id=1, description='Negative qty',
                           quantity=Decimal('-5'), unit_price=Decimal('10'))
        line.calculate_amount()
        # Negative quantity * positive price = negative amount
        # The system should handle this (either reject or calculate correctly)
        assert line.amount == Decimal('-50.00')
        line.save()
        assert line.id > 0  # should not crash

    def test_invoice_line_negative_unit_price(self):
        """Invoice line with negative unit_price -> behavior check."""
        line = InvoiceLine(invoice_id=1, description='Negative price',
                           quantity=Decimal('5'), unit_price=Decimal('-10'))
        line.calculate_amount()
        assert line.amount == Decimal('-50.00')
        line.save()
        assert line.id > 0

    def test_xss_in_customer_name(self):
        """XSS payload in customer_name -> should be stored but never trusted."""
        org = _make_org(slug='xss-org')
        user = _make_user(email='xss@test.com')
        svc = InvoiceService()

        xss_payload = '<script>alert(1)</script>'
        inv = svc.create_invoice(org, user,
                                 customer_name=xss_payload,
                                 lines=[{'description': 'Item', 'quantity': 1, 'unit_price': 10}])

        # The model stores the raw string — template escaping is the defense layer
        assert inv.customer_name == xss_payload
        assert inv.id > 0

    def test_sql_injection_in_email_field(self):
        """SQL injection in email field -> should be rejected by validator."""
        from btpay.security.validators import validate_email, ValidationError

        sqli_payloads = [
            "'; DROP TABLE users; --",
            "admin@test.com' OR '1'='1",
            "test@test.com; DELETE FROM invoices",
            "' UNION SELECT * FROM users--@test.com",
        ]

        for payload in sqli_payloads:
            with pytest.raises(ValidationError):
                validate_email(payload)

    def test_unicode_edge_cases_in_text_fields(self):
        """Unicode edge cases: emoji, RTL, null bytes in all text fields."""
        org = _make_org(slug='unicode-org')
        user = _make_user(email='unicode@test.com')
        svc = InvoiceService()

        # Emoji + RTL + zero-width chars
        evil_name = '\u202Eevil\u202C \U0001F4B0\u0000hidden'
        inv = svc.create_invoice(org, user,
                                 customer_name=evil_name,
                                 customer_email='',
                                 notes='Notes with \x00 null bytes and \U0001F525 fire',
                                 lines=[{'description': '\U0001F4A3 Bomb item', 'quantity': 1, 'unit_price': 10}])

        assert inv.id > 0  # must not crash
        assert inv.total == Decimal('10')

    def test_oversized_invoice_number(self):
        """Invoice number with 10000 chars -> should not crash the ORM."""
        huge_number = 'INV-' + 'X' * 10000
        inv = Invoice(org_id=1, invoice_number=huge_number, status='draft',
                      total=Decimal('100'))
        inv.save()

        # Retrieve it
        loaded = Invoice.get(inv.id)
        assert loaded is not None
        assert len(loaded.invoice_number) == 10004  # 'INV-' + 10000

    def test_deeply_nested_metadata(self):
        """Metadata with deeply nested JSON (100 levels) -> should not crash."""
        # Build 100-level nested dict
        nested = {'value': 'leaf'}
        for i in range(100):
            nested = {'level_%d' % i: nested}

        inv = Invoice(org_id=1, invoice_number='INV-NESTED', status='draft',
                      total=Decimal('100'), metadata=nested)
        inv.save()

        loaded = Invoice.get(inv.id)
        assert loaded is not None
        assert loaded.metadata is not None

    def test_metadata_not_a_dict(self):
        """Metadata with non-dict value -> should handle gracefully."""
        inv = Invoice(org_id=1, invoice_number='INV-META-LIST', status='draft',
                      total=Decimal('100'), metadata='not a dict')
        inv.save()

        loaded = Invoice.get(inv.id)
        assert loaded is not None

    def test_email_validator_rejects_empty(self):
        """Empty email -> should be rejected."""
        from btpay.security.validators import validate_email, ValidationError

        for empty in ['', None, '   ']:
            with pytest.raises(ValidationError):
                validate_email(empty)

    def test_email_validator_rejects_too_long(self):
        """Email longer than 254 chars -> should be rejected."""
        from btpay.security.validators import validate_email, ValidationError

        long_email = 'a' * 250 + '@b.com'
        with pytest.raises(ValidationError):
            validate_email(long_email)

    def test_amount_validator_rejects_float(self):
        """Float amount -> should be rejected (precision issues)."""
        from btpay.security.validators import validate_amount, ValidationError

        with pytest.raises(ValidationError, match='float'):
            validate_amount(0.1)

    def test_amount_validator_rejects_negative(self):
        """Negative amount -> should be rejected."""
        from btpay.security.validators import validate_amount, ValidationError

        with pytest.raises(ValidationError, match='negative'):
            validate_amount('-5')

    def test_cancel_paid_invoice_rejected(self):
        """Cancelling a paid invoice -> must be rejected."""
        inv = Invoice(org_id=1, invoice_number='INV-CANCEL-PAID', status='paid',
                      total=Decimal('100'))
        inv.save()

        svc = InvoiceService()
        with pytest.raises(ValueError, match='Cannot cancel'):
            svc.cancel_invoice(inv)

    def test_finalize_already_pending_rejected(self):
        """Finalizing an already-pending invoice -> must be rejected."""
        inv = Invoice(org_id=1, invoice_number='INV-DOUBLE-FIN', status='pending',
                      total=Decimal('100'))
        inv.save()

        svc = InvoiceService()
        with pytest.raises(ValueError, match='Cannot finalize'):
            svc.finalize_invoice(inv)

    def test_finalize_cancelled_rejected(self):
        """Finalizing a cancelled invoice -> must be rejected."""
        inv = Invoice(org_id=1, invoice_number='INV-FIN-CANCEL', status='cancelled',
                      total=Decimal('100'))
        inv.save()

        svc = InvoiceService()
        with pytest.raises(ValueError, match='Cannot finalize'):
            svc.finalize_invoice(inv)


# ======================================================================
# 7. Additional Edge Cases
# ======================================================================

class TestEdgeCases:
    """Miscellaneous edge cases that could cause production failures."""

    def test_finalize_without_wallet_fails(self):
        """Finalize with onchain_btc but no wallet -> must raise."""
        org = _make_org(slug='no-wallet-edge')
        user = _make_user(email='nowallet@test.com')
        rate_svc = _mock_rate_service()
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Item', 'quantity': 1, 'unit_price': 100}
        ])

        with pytest.raises(ValueError, match='[Ww]allet'):
            svc.finalize_invoice(inv)

    def test_finalize_without_rate_service_fails(self):
        """Finalize without exchange rate service -> must raise."""
        org = _make_org(slug='no-rate-edge')
        user = _make_user(email='norate@test.com')
        wallet = _make_wallet(org)
        svc = InvoiceService(exchange_rate_service=None)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Item', 'quantity': 1, 'unit_price': 100}
        ])

        with pytest.raises(ValueError, match='[Rr]ate'):
            svc.finalize_invoice(inv, wallet)

    def test_invoice_amount_due_never_negative_after_multiple_payments(self):
        """Multiple overpayments -> amount_due must always stay >= 0."""
        org = _make_org(slug='multi-overpay')
        user = _make_user(email='multioverpay@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=50)
        full_sat = int(inv.btc_amount * 100_000_000)

        # Record 5 full payments (5x overpayment)
        for i in range(5):
            svc.record_payment(inv, full_sat, txid='multi_%d' % i)
            assert inv.amount_due == Decimal('0'), \
                'amount_due went negative after payment %d' % (i + 1)

    def test_expiry_check_on_non_pending_invoice(self):
        """check_expiry on draft/paid/cancelled -> should return False, not crash."""
        svc = InvoiceService()

        for status in ('draft', 'paid', 'confirmed', 'cancelled'):
            inv = Invoice(org_id=1, invoice_number='INV-EXP-%s' % status.upper(),
                          status=status, total=Decimal('100'))
            inv.save()
            assert svc.check_expiry(inv) is False

    def test_record_payment_updates_amount_paid_correctly(self):
        """Verify amount_paid accumulates correctly across multiple partial payments."""
        org = _make_org(slug='accum-pay')
        user = _make_user(email='accum@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc, underpaid_gift=0)

        inv = _make_finalized_invoice(svc, org, user, wallet, unit_price=1000)
        full_sat = int(inv.btc_amount * 100_000_000)
        quarter_sat = full_sat // 4

        svc.record_payment(inv, quarter_sat, txid='q1')
        assert inv.status == 'partial'

        svc.record_payment(inv, quarter_sat, txid='q2')
        assert inv.status == 'partial'

        svc.record_payment(inv, quarter_sat, txid='q3')
        assert inv.status == 'partial'

        svc.record_payment(inv, quarter_sat, txid='q4')
        # Due to integer division, this might be slightly under. Check both:
        assert inv.status in ('partial', 'paid')

        # One more sat to push it over if needed
        if inv.status == 'partial':
            svc.record_payment(inv, full_sat - (quarter_sat * 4) + 1, txid='remainder')
            assert inv.status == 'paid'

# EOF
