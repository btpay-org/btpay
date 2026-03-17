#
# Tests for Phase 4 — Invoicing & Payments
#
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch


# ======================================================================
# Helpers
# ======================================================================

def _make_org(**kw):
    from btpay.auth.models import Organization
    defaults = dict(name='Test Org', slug='test-org', default_currency='USD')
    defaults.update(kw)
    org = Organization(**defaults)
    org.save()
    return org


def _make_user(**kw):
    from btpay.auth.models import User
    defaults = dict(email='test@example.com', first_name='Test', last_name='User')
    defaults.update(kw)
    user = User(**defaults)
    user.set_password('testpass123')
    user.save()
    return user


def _make_wallet(org, xpub=None):
    from btpay.bitcoin.models import Wallet
    xpub = xpub or 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'
    w = Wallet(org_id=org.id, name='Test Wallet', wallet_type='xpub',
               xpub=xpub, derivation_path='m/0')
    w.save()
    return w


def _mock_rate_service(rate=Decimal('67500')):
    svc = MagicMock()
    svc.get_rate.return_value = rate
    return svc


# ======================================================================
# Invoice Model
# ======================================================================

class TestInvoiceModel:

    def test_create_invoice(self):
        from btpay.invoicing.models import Invoice
        inv = Invoice(org_id=1, invoice_number='INV-0001', status='draft',
                      currency='USD', total=Decimal('100'))
        inv.save()
        assert inv.id > 0
        assert inv.status == 'draft'
        assert inv.total == Decimal('100')

    def test_is_draft(self):
        from btpay.invoicing.models import Invoice
        inv = Invoice(org_id=1, invoice_number='INV-0002', status='draft')
        inv.save()
        assert inv.is_draft is True
        assert inv.is_pending is False
        assert inv.is_paid is False

    def test_is_pending(self):
        from btpay.invoicing.models import Invoice
        inv = Invoice(org_id=1, invoice_number='INV-0003', status='pending')
        inv.save()
        assert inv.is_pending is True
        assert inv.is_draft is False

    def test_is_paid(self):
        from btpay.invoicing.models import Invoice
        inv = Invoice(org_id=1, invoice_number='INV-0004', status='paid')
        inv.save()
        assert inv.is_paid is True

    def test_is_paid_includes_confirmed(self):
        from btpay.invoicing.models import Invoice
        inv = Invoice(org_id=1, invoice_number='INV-0005', status='confirmed')
        inv.save()
        assert inv.is_paid is True

    def test_amount_due(self):
        from btpay.invoicing.models import Invoice
        inv = Invoice(org_id=1, invoice_number='INV-0006',
                      total=Decimal('250'), amount_paid=Decimal('100'))
        inv.save()
        assert inv.amount_due == Decimal('150')

    def test_amount_due_fully_paid(self):
        from btpay.invoicing.models import Invoice
        inv = Invoice(org_id=1, invoice_number='INV-0007',
                      total=Decimal('100'), amount_paid=Decimal('100'))
        inv.save()
        assert inv.amount_due == Decimal('0')

    def test_amount_due_overpaid(self):
        from btpay.invoicing.models import Invoice
        inv = Invoice(org_id=1, invoice_number='INV-0008',
                      total=Decimal('100'), amount_paid=Decimal('150'))
        inv.save()
        assert inv.amount_due == Decimal('0')  # never negative

    def test_unique_invoice_number(self):
        from btpay.invoicing.models import Invoice
        inv1 = Invoice(org_id=1, invoice_number='INV-UNIQUE')
        inv1.save()
        found = Invoice.get_by(invoice_number='INV-UNIQUE')
        assert found.id == inv1.id


class TestInvoiceLineModel:

    def test_create_line(self):
        from btpay.invoicing.models import InvoiceLine
        line = InvoiceLine(invoice_id=1, description='Widget',
                           quantity=Decimal('2'), unit_price=Decimal('25'))
        line.calculate_amount()
        line.save()
        assert line.amount == Decimal('50.00')

    def test_calculate_amount(self):
        from btpay.invoicing.models import InvoiceLine
        line = InvoiceLine(invoice_id=1, description='Service',
                           quantity=Decimal('3'), unit_price=Decimal('33.33'))
        line.calculate_amount()
        assert line.amount == Decimal('99.99')


class TestInvoiceTotals:

    def test_recalculate_simple(self):
        from btpay.invoicing.models import Invoice, InvoiceLine
        inv = Invoice(org_id=1, invoice_number='INV-CALC1')
        inv.save()

        line1 = InvoiceLine(invoice_id=inv.id, description='A',
                            quantity=Decimal('1'), unit_price=Decimal('100'))
        line1.calculate_amount()
        line1.save()

        line2 = InvoiceLine(invoice_id=inv.id, description='B',
                            quantity=Decimal('2'), unit_price=Decimal('50'))
        line2.calculate_amount()
        line2.save()

        inv.recalculate_totals()
        assert inv.subtotal == Decimal('200')
        assert inv.total == Decimal('200')

    def test_recalculate_with_tax(self):
        from btpay.invoicing.models import Invoice, InvoiceLine
        inv = Invoice(org_id=1, invoice_number='INV-CALC2', tax_rate=Decimal('10'))
        inv.save()

        line = InvoiceLine(invoice_id=inv.id, description='Item',
                           quantity=Decimal('1'), unit_price=Decimal('100'))
        line.calculate_amount()
        line.save()

        inv.recalculate_totals()
        assert inv.subtotal == Decimal('100')
        assert inv.tax_amount == Decimal('10.00')
        assert inv.total == Decimal('110.00')

    def test_recalculate_with_discount(self):
        from btpay.invoicing.models import Invoice, InvoiceLine
        inv = Invoice(org_id=1, invoice_number='INV-CALC3',
                      discount_amount=Decimal('20'))
        inv.save()

        line = InvoiceLine(invoice_id=inv.id, description='Item',
                           quantity=Decimal('1'), unit_price=Decimal('100'))
        line.calculate_amount()
        line.save()

        inv.recalculate_totals()
        assert inv.total == Decimal('80')

    def test_recalculate_negative_floor(self):
        from btpay.invoicing.models import Invoice, InvoiceLine
        inv = Invoice(org_id=1, invoice_number='INV-CALC4',
                      discount_amount=Decimal('500'))
        inv.save()

        line = InvoiceLine(invoice_id=inv.id, description='Item',
                           quantity=Decimal('1'), unit_price=Decimal('100'))
        line.calculate_amount()
        line.save()

        inv.recalculate_totals()
        assert inv.total == Decimal('0')  # floor at 0


class TestPaymentModel:

    def test_create_payment(self):
        from btpay.invoicing.models import Payment
        p = Payment(invoice_id=1, method='onchain_btc',
                    txid='abc123', amount_btc=Decimal('0.001'))
        p.save()
        assert p.id > 0
        assert p.status == 'pending'

    def test_mark_confirmed(self):
        from btpay.invoicing.models import Payment
        p = Payment(invoice_id=1, method='onchain_btc')
        p.save()
        p.mark_confirmed(6)
        assert p.status == 'confirmed'
        assert p.confirmations == 6


class TestPaymentLinkModel:

    def test_create_payment_link(self):
        from btpay.invoicing.models import PaymentLink
        pl = PaymentLink(org_id=1, slug='buy-widget', title='Buy Widget',
                         amount=Decimal('50'), currency='USD')
        pl.save()
        assert pl.id > 0
        assert pl.is_active is True

    def test_fixed_amount(self):
        from btpay.invoicing.models import PaymentLink
        pl = PaymentLink(org_id=1, slug='fixed', title='Fixed',
                         amount=Decimal('100'))
        assert pl.is_fixed_amount is True

    def test_open_amount(self):
        from btpay.invoicing.models import PaymentLink
        pl = PaymentLink(org_id=1, slug='open', title='Open')
        assert pl.is_fixed_amount is False

    def test_unique_slug(self):
        from btpay.invoicing.models import PaymentLink
        pl1 = PaymentLink(org_id=1, slug='unique-slug', title='Test')
        pl1.save()
        found = PaymentLink.get_by(slug='unique-slug')
        assert found.id == pl1.id


# ======================================================================
# Invoice Service
# ======================================================================

class TestInvoiceService:

    def test_create_invoice_with_lines(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        svc = InvoiceService()

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Widget A', 'quantity': 2, 'unit_price': 50},
            {'description': 'Widget B', 'quantity': 1, 'unit_price': 75},
        ])

        assert inv.status == 'draft'
        assert inv.subtotal == Decimal('175')
        assert inv.total == Decimal('175')
        assert inv.invoice_number == 'INV-0001'
        assert inv.org_id == org.id
        assert inv.created_by_user_id == user.id

    def test_invoice_number_increments(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        svc = InvoiceService()

        inv1 = svc.create_invoice(org, user, lines=[
            {'description': 'A', 'quantity': 1, 'unit_price': 10}
        ])
        inv2 = svc.create_invoice(org, user, lines=[
            {'description': 'B', 'quantity': 1, 'unit_price': 20}
        ])

        assert inv1.invoice_number == 'INV-0001'
        assert inv2.invoice_number == 'INV-0002'

    def test_create_invoice_with_tax(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        svc = InvoiceService()

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Service', 'quantity': 1, 'unit_price': 200},
        ], tax_rate=13)

        assert inv.subtotal == Decimal('200')
        assert inv.tax_amount == Decimal('26.00')
        assert inv.total == Decimal('226.00')

    def test_finalize_invoice(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc, quote_deadline=30)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Product', 'quantity': 1, 'unit_price': 675},
        ])

        svc.finalize_invoice(inv, wallet)

        assert inv.status == 'pending'
        assert inv.btc_rate == Decimal('67500')
        assert inv.btc_amount > 0
        assert inv.payment_address_id > 0
        assert inv.btc_rate_locked_at != 0
        assert inv.btc_rate_expires_at != 0

    def test_finalize_assigns_unique_address(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service()
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv1 = svc.create_invoice(org, user, lines=[
            {'description': 'A', 'quantity': 1, 'unit_price': 100}
        ])
        inv2 = svc.create_invoice(org, user, lines=[
            {'description': 'B', 'quantity': 1, 'unit_price': 200}
        ])

        svc.finalize_invoice(inv1, wallet)
        svc.finalize_invoice(inv2, wallet)

        assert inv1.payment_address_id != inv2.payment_address_id

    def test_finalize_rejects_non_draft(self):
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.models import Invoice
        svc = InvoiceService()

        inv = Invoice(org_id=1, invoice_number='INV-NODRAFT', status='pending',
                      total=Decimal('100'))
        inv.save()

        with pytest.raises(ValueError, match='Cannot finalize'):
            svc.finalize_invoice(inv)

    def test_finalize_rejects_zero_total(self):
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.models import Invoice
        svc = InvoiceService()

        inv = Invoice(org_id=1, invoice_number='INV-ZERO', status='draft',
                      total=Decimal('0'))
        inv.save()

        with pytest.raises(ValueError, match='positive'):
            svc.finalize_invoice(inv)

    def test_record_payment_full(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Test', 'quantity': 1, 'unit_price': 675}
        ])
        svc.finalize_invoice(inv, wallet)

        # Pay full amount
        amount_sat = int(inv.btc_amount * 100_000_000)
        payment = svc.record_payment(inv, amount_sat, txid='txid_full')

        assert payment.id > 0
        assert inv.status == 'paid'
        assert inv.paid_at != 0

    def test_record_payment_partial(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc, underpaid_gift=5)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Test', 'quantity': 1, 'unit_price': 1000}
        ])
        svc.finalize_invoice(inv, wallet)

        # Pay half
        half_sat = int(inv.btc_amount * 100_000_000) // 2
        svc.record_payment(inv, half_sat, txid='txid_half')

        assert inv.status == 'partial'

    def test_record_payment_underpaid_gift(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc, underpaid_gift=10)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Test', 'quantity': 1, 'unit_price': 100}
        ])
        svc.finalize_invoice(inv, wallet)

        # Pay slightly less than full (within $10 gift threshold)
        almost_full_sat = int(inv.btc_amount * 100_000_000) - 1000  # ~$0.67 short
        svc.record_payment(inv, almost_full_sat, txid='txid_gift')

        # Should be accepted as paid
        assert inv.status == 'paid'

    def test_confirm_payment(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Test', 'quantity': 1, 'unit_price': 675}
        ])
        svc.finalize_invoice(inv, wallet)

        amount_sat = int(inv.btc_amount * 100_000_000)
        payment = svc.record_payment(inv, amount_sat, txid='txid_conf')
        assert inv.status == 'paid'

        svc.confirm_payment(inv, payment, 6)
        assert inv.status == 'confirmed'
        assert inv.confirmed_at != 0
        assert payment.status == 'confirmed'

    def test_cancel_draft_invoice(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        svc = InvoiceService()

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Cancel me', 'quantity': 1, 'unit_price': 50}
        ])
        svc.cancel_invoice(inv)
        assert inv.status == 'cancelled'
        assert inv.cancelled_at != 0

    def test_cancel_pending_releases_address(self):
        from btpay.invoicing.service import InvoiceService
        from btpay.bitcoin.models import BitcoinAddress
        org = _make_org()
        user = _make_user()
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service()
        svc = InvoiceService(exchange_rate_service=rate_svc)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Item', 'quantity': 1, 'unit_price': 100}
        ])
        svc.finalize_invoice(inv, wallet)
        addr_id = inv.payment_address_id

        svc.cancel_invoice(inv)
        assert inv.status == 'cancelled'

        addr = BitcoinAddress.get(addr_id)
        assert addr.status == 'released'
        assert addr.assigned_to_invoice_id == 0

    def test_cancel_paid_fails(self):
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.models import Invoice
        svc = InvoiceService()

        inv = Invoice(org_id=1, invoice_number='INV-PAID', status='paid',
                      total=Decimal('100'))
        inv.save()

        with pytest.raises(ValueError, match='Cannot cancel'):
            svc.cancel_invoice(inv)

    def test_check_expiry(self):
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.models import Invoice
        from btpay.chrono import TIME_AGO

        svc = InvoiceService()
        inv = Invoice(org_id=1, invoice_number='INV-EXP', status='pending',
                      total=Decimal('100'),
                      btc_rate_expires_at=TIME_AGO(minutes=5))
        inv.save()

        expired = svc.check_expiry(inv)
        assert expired is True
        assert inv.status == 'expired'

    def test_check_expiry_not_expired(self):
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.models import Invoice
        from btpay.chrono import TIME_FUTURE

        svc = InvoiceService()
        inv = Invoice(org_id=1, invoice_number='INV-NEXP', status='pending',
                      total=Decimal('100'),
                      btc_rate_expires_at=TIME_FUTURE(minutes=30))
        inv.save()

        expired = svc.check_expiry(inv)
        assert expired is False
        assert inv.status == 'pending'

    def test_btc_rate_with_markup(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org()
        user = _make_user()
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service(Decimal('67500'))
        svc = InvoiceService(exchange_rate_service=rate_svc, markup_percent=1)

        inv = svc.create_invoice(org, user, lines=[
            {'description': 'Item', 'quantity': 1, 'unit_price': 675}
        ])
        svc.finalize_invoice(inv, wallet)

        # Rate should be 67500 * 1.01 = 68175
        assert inv.btc_rate == Decimal('68175.00')

    def test_generate_invoice_number_custom_prefix(self):
        from btpay.invoicing.service import InvoiceService
        org = _make_org(invoice_prefix='BILL')
        svc = InvoiceService()
        num = svc.generate_invoice_number(org)
        assert num == 'BILL-0001'


# ======================================================================
# Payment Methods
# ======================================================================

class TestPaymentMethods:

    def test_onchain_btc_registered(self):
        from btpay.invoicing.payment_methods import PAYMENT_METHODS
        assert 'onchain_btc' in PAYMENT_METHODS

    def test_wire_registered(self):
        from btpay.invoicing.payment_methods import PAYMENT_METHODS
        assert 'wire' in PAYMENT_METHODS

    def test_onchain_btc_available_with_wallet(self):
        from btpay.invoicing.payment_methods import get_method
        org = _make_org()
        _make_wallet(org)
        btc = get_method('onchain_btc')
        assert btc.is_available(org) is True

    def test_onchain_btc_unavailable_without_wallet(self):
        from btpay.invoicing.payment_methods import get_method
        org = _make_org(slug='no-wallet-org')
        btc = get_method('onchain_btc')
        assert btc.is_available(org) is False

    def test_wire_available_with_info(self):
        from btpay.invoicing.payment_methods import get_method
        org = _make_org(slug='wire-org', wire_info={
            'bank_name': 'Test Bank', 'account_number': '123'
        })
        wire = get_method('wire')
        assert wire.is_available(org) is True

    def test_wire_unavailable_without_info(self):
        from btpay.invoicing.payment_methods import get_method
        org = _make_org(slug='nowire-org')
        wire = get_method('wire')
        assert wire.is_available(org) is False

    def test_available_methods(self):
        from btpay.invoicing.payment_methods import available_methods
        org = _make_org(slug='avail-org', wire_info={'bank_name': 'B', 'account_number': '1'})
        _make_wallet(org)
        methods = available_methods(org)
        names = [m.name for m in methods]
        assert 'onchain_btc' in names
        assert 'wire' in names


# ======================================================================
# Checkout
# ======================================================================

class TestCheckoutService:

    def test_start_checkout_finalizes_draft(self):
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.checkout import CheckoutService
        org = _make_org(slug='checkout-org')
        user = _make_user(email='checkout@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service()
        inv_svc = InvoiceService(exchange_rate_service=rate_svc)
        checkout = CheckoutService(inv_svc)

        inv = inv_svc.create_invoice(org, user, lines=[
            {'description': 'Item', 'quantity': 1, 'unit_price': 100}
        ])

        info = checkout.start_checkout(inv, wallet)
        assert inv.status == 'pending'
        assert 'invoice_number' in info
        assert 'btc' in info
        assert info['btc']['address'] != ''

    def test_check_payment_status(self):
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.checkout import CheckoutService
        org = _make_org(slug='status-org')
        user = _make_user(email='status@test.com')
        wallet = _make_wallet(org)
        rate_svc = _mock_rate_service()
        inv_svc = InvoiceService(exchange_rate_service=rate_svc)
        checkout = CheckoutService(inv_svc)

        inv = inv_svc.create_invoice(org, user, lines=[
            {'description': 'Item', 'quantity': 1, 'unit_price': 100}
        ])
        checkout.start_checkout(inv, wallet)

        status = checkout.check_payment_status(inv)
        assert status['status'] == 'pending'
        assert 'amount_due' in status
        assert 'quote_expires_in' in status

    def test_handle_underpayment_within_threshold(self):
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.checkout import CheckoutService
        from btpay.invoicing.models import Invoice

        inv_svc = InvoiceService(underpaid_gift=5)
        checkout = CheckoutService(inv_svc)

        inv = Invoice(org_id=1, invoice_number='INV-UP', status='pending',
                      total=Decimal('100'), btc_rate=Decimal('67500'),
                      currency='USD')
        inv.save()

        # Underpaid by ~$3 worth of sats (within $5 threshold)
        received = 147000   # ~$99.225
        expected = 148148   # ~$100
        accept, msg = checkout.handle_underpayment(inv, received, expected)
        assert accept is True

    def test_handle_overpayment(self):
        from btpay.invoicing.checkout import CheckoutService
        from btpay.invoicing.service import InvoiceService
        from btpay.invoicing.models import Invoice

        inv_svc = InvoiceService()
        checkout = CheckoutService(inv_svc)

        inv = Invoice(org_id=1, invoice_number='INV-OP', status='pending',
                      total=Decimal('100'), btc_rate=Decimal('67500'),
                      currency='USD')
        inv.save()

        accept, msg = checkout.handle_overpayment(inv, 200000, 148148)
        assert accept is True
        assert 'Overpaid' in msg


# ======================================================================
# Wire Info
# ======================================================================

class TestWireInfo:

    def test_format_wire_info(self):
        from btpay.invoicing.wire_info import format_wire_info
        info = {
            'bank_name': 'Test Bank',
            'account_name': 'Acme Corp',
            'account_number': '123456789',
            'routing_number': '021000021',
            'swift_code': 'TESTUS33',
        }
        result = format_wire_info(info, 'INV-0001')
        assert 'Test Bank' in result
        assert 'Acme Corp' in result
        assert '123456789' in result
        assert 'INV-0001' in result

    def test_format_wire_info_empty(self):
        from btpay.invoicing.wire_info import format_wire_info
        assert format_wire_info({}) == ''
        assert format_wire_info(None) == ''

    def test_validate_wire_info_valid(self):
        from btpay.invoicing.wire_info import validate_wire_info
        info = {
            'bank_name': 'Test Bank',
            'account_name': 'Acme Corp',
            'account_number': '123456789',
        }
        valid, errors = validate_wire_info(info)
        assert valid is True
        assert errors == []

    def test_validate_wire_info_missing_fields(self):
        from btpay.invoicing.wire_info import validate_wire_info
        valid, errors = validate_wire_info({'bank_name': 'Test'})
        assert valid is False
        assert len(errors) >= 1

    def test_validate_wire_info_iban_accepted(self):
        from btpay.invoicing.wire_info import validate_wire_info
        info = {
            'bank_name': 'European Bank',
            'account_name': 'Euro Corp',
            'iban': 'DE89370400440532013000',
        }
        valid, errors = validate_wire_info(info)
        assert valid is True


# ======================================================================
# PDF Generation
# ======================================================================

class TestPDFGeneration:

    def test_generate_invoice_pdf(self):
        '''PDF generation produces bytes.'''
        from btpay.invoicing.pdf import generate_invoice_pdf
        from btpay.invoicing.models import Invoice, InvoiceLine

        org = _make_org(slug='pdf-org')
        inv = Invoice(org_id=org.id, invoice_number='INV-PDF1', status='pending',
                      total=Decimal('250'), subtotal=Decimal('250'),
                      currency='USD', customer_name='John Doe',
                      customer_email='john@example.com')
        inv.save()

        line = InvoiceLine(invoice_id=inv.id, description='Consulting',
                           quantity=Decimal('5'), unit_price=Decimal('50'),
                           amount=Decimal('250'))
        line.save()

        pdf_bytes = generate_invoice_pdf(inv, org)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 100
        assert pdf_bytes[:4] == b'%PDF'  # PDF magic bytes

    def test_generate_receipt_pdf(self):
        from btpay.invoicing.pdf import generate_receipt_pdf
        from btpay.invoicing.models import Invoice, Payment

        org = _make_org(slug='receipt-org')
        inv = Invoice(org_id=org.id, invoice_number='INV-RCPT', status='paid',
                      total=Decimal('100'), currency='USD')
        inv.save()

        payment = Payment(invoice_id=inv.id, method='onchain_btc',
                          txid='abc123def456', amount_btc=Decimal('0.00148'),
                          amount_fiat=Decimal('100'), confirmations=6,
                          status='confirmed')
        payment.save()

        pdf_bytes = generate_receipt_pdf(inv, payment, org)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:4] == b'%PDF'


# ======================================================================
# App Integration
# ======================================================================

class TestAppPhase4Integration:

    def test_invoicing_models_registered(self, app):
        from btpay.orm.engine import MemoryStore
        store = MemoryStore()
        assert 'Invoice' in store._tables
        assert 'InvoiceLine' in store._tables
        assert 'Payment' in store._tables
        assert 'PaymentLink' in store._tables

# EOF
