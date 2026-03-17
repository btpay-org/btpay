#
# Invoice lifecycle service
#
# Business logic for creating, finalizing, paying, and expiring invoices.
#
import logging
import threading
from decimal import Decimal
from btpay.chrono import NOW, TIME_FUTURE, as_time_t
from btpay.coinmath import coins2satoshi, satoshi2coins, round_satoshi
from btpay.orm.persistence import save_to_disk

log = logging.getLogger(__name__)


class InvoiceService:
    '''
    Invoice lifecycle management.

    Usage:
        svc = InvoiceService(exchange_rate_service)
        inv = svc.create_invoice(org, user, lines=[...])
        svc.finalize_invoice(inv, wallet)
        svc.check_expiry(inv)
        svc.record_payment(inv, amount_btc, txid)
    '''

    def __init__(self, exchange_rate_service=None, quote_deadline=30,
                 markup_percent=0, underpaid_gift=5, data_dir=None):
        self.rate_service = exchange_rate_service
        self.quote_deadline = quote_deadline         # minutes
        self.markup_percent = Decimal(str(markup_percent))
        self.underpaid_gift = Decimal(str(underpaid_gift))  # USD threshold
        self._data_dir = data_dir

    def _flush_to_disk(self):
        '''Write-through persistence after payment-critical operations.'''
        data_dir = self._data_dir
        if not data_dir:
            try:
                from flask import current_app
                data_dir = current_app.config.get('DATA_DIR', 'data')
            except RuntimeError:
                log.error('Cannot flush to disk: no data_dir and no app context')
                return
        try:
            save_to_disk(data_dir)
        except Exception:
            log.error('Failed to flush to disk after critical operation', exc_info=True)

    def create_invoice(self, org, user, lines=None, customer_email='',
                       customer_name='', customer_company='', currency=None,
                       notes='', due_date=0, payment_methods=None,
                       tax_rate=0, discount_amount=0, metadata=None):
        '''
        Create a new draft invoice.
        lines: list of dicts [{description, quantity, unit_price}, ...]
        '''
        from btpay.invoicing.models import Invoice, InvoiceLine

        inv_number = self.generate_invoice_number(org)

        inv = Invoice(
            org_id=org.id,
            invoice_number=inv_number,
            status='draft',
            customer_email=customer_email,
            customer_name=customer_name,
            customer_company=customer_company,
            currency=currency or org.default_currency or 'USD',
            tax_rate=Decimal(str(tax_rate)),
            discount_amount=Decimal(str(discount_amount)),
            notes=notes,
            due_date=due_date,
            payment_methods_enabled=payment_methods or ['onchain_btc'],
            created_by_user_id=user.id if user else 0,
            metadata=metadata or {},
        )
        inv.save()

        # Add line items
        if lines:
            for i, line_data in enumerate(lines):
                line = InvoiceLine(
                    invoice_id=inv.id,
                    description=line_data.get('description', ''),
                    quantity=Decimal(str(line_data.get('quantity', 1))),
                    unit_price=Decimal(str(line_data.get('unit_price', 0))),
                    sort_order=i,
                )
                line.calculate_amount()
                line.save()

        inv.recalculate_totals()

        log.info('Invoice created: %s (id=%d, org=%d)', inv_number, inv.id, org.id)
        return inv

    def finalize_invoice(self, invoice, wallet=None):
        '''
        Move invoice from draft to pending.
        Assigns a payment address and locks the BTC rate.
        '''
        if invoice.status != 'draft':
            raise ValueError('Cannot finalize invoice in status: %s' % invoice.status)

        if invoice.total <= 0:
            raise ValueError('Invoice total must be positive')

        methods = invoice.payment_methods_enabled or []

        # Assign BTC address if onchain_btc enabled
        if 'onchain_btc' in methods:
            if wallet is None:
                raise ValueError('Wallet required for BTC payment')
            self._assign_address(invoice, wallet)
            self._lock_btc_rate(invoice)

        # Create BTCPay invoice if btcpay enabled
        if 'btcpay' in methods:
            self._create_btcpay_invoice(invoice)

        # Create LNbits invoice if lnbits enabled
        if 'lnbits' in methods:
            self._create_lnbits_invoice(invoice)

        invoice.status = 'pending'
        invoice.save()
        self._flush_to_disk()

        log.info('Invoice finalized: %s -> pending', invoice.invoice_number)
        return invoice

    def check_expiry(self, invoice):
        '''
        Check if the BTC quote has expired.
        If expired and not yet paid, refresh or expire the invoice.
        Returns True if invoice expired.
        '''
        if invoice.status not in ('pending', 'partial'):
            return False

        if not invoice.btc_rate_expires_at:
            return False

        now_t = as_time_t(NOW())
        exp_t = as_time_t(invoice.btc_rate_expires_at) if not isinstance(
            invoice.btc_rate_expires_at, (int, float)) else invoice.btc_rate_expires_at

        if now_t < exp_t:
            return False

        # Quote expired
        if invoice.amount_paid > 0:
            # Partial payment — don't expire, just log
            log.warning('Quote expired for partially paid invoice %s',
                        invoice.invoice_number)
            return False

        invoice.status = 'expired'
        invoice.expired_at = NOW()
        invoice.save()

        log.info('Invoice expired: %s', invoice.invoice_number)
        return True

    def refresh_btc_quote(self, invoice, wallet=None):
        '''
        Refresh the BTC rate for a pending/expired invoice.
        Optionally assigns a new address.
        '''
        if invoice.status not in ('pending', 'expired'):
            raise ValueError('Cannot refresh quote for invoice in status: %s' % invoice.status)

        self._lock_btc_rate(invoice)

        if invoice.status == 'expired':
            invoice.status = 'pending'
            invoice.expired_at = 0

        invoice.save()
        log.info('BTC quote refreshed for invoice %s: %s BTC at %s %s/BTC',
                 invoice.invoice_number, invoice.btc_amount,
                 invoice.btc_rate, invoice.currency)
        return invoice

    _payment_lock = threading.Lock()

    def record_payment(self, invoice, amount_sat, txid='', address='',
                       confirmations=0, method='onchain_btc', raw_data=None):
        '''
        Record a payment against an invoice.
        Updates invoice status based on payment amount.
        Thread-safe: uses a lock to prevent race conditions on payment state.
        '''
        with self._payment_lock:
            return self._record_payment_locked(invoice, amount_sat, txid,
                                               address, confirmations, method, raw_data)

    def _record_payment_locked(self, invoice, amount_sat, txid='', address='',
                               confirmations=0, method='onchain_btc', raw_data=None):
        '''Internal payment recording (must be called under _payment_lock).'''
        from btpay.invoicing.models import Payment

        amount_btc = satoshi2coins(amount_sat)

        # Calculate fiat equivalent
        rate = invoice.btc_rate or Decimal('0')
        amount_fiat = (amount_btc * rate).quantize(Decimal('0.01')) if rate else Decimal('0')

        payment = Payment(
            invoice_id=invoice.id,
            method=method,
            txid=txid,
            address=address,
            amount_btc=amount_btc,
            amount_fiat=amount_fiat,
            exchange_rate=rate,
            confirmations=confirmations,
            status='pending' if confirmations == 0 else 'confirmed',
            raw_data=raw_data or {},
        )
        payment.save()

        # Update invoice paid amount
        invoice.amount_paid = invoice.amount_paid + amount_fiat

        # Determine new status
        if invoice.amount_paid >= invoice.total:
            invoice.status = 'paid'
            invoice.paid_at = NOW()
        elif invoice.amount_paid > 0:
            # Check if close enough (underpaid gift threshold)
            remaining = invoice.total - invoice.amount_paid
            if remaining <= self.underpaid_gift:
                invoice.status = 'paid'
                invoice.paid_at = NOW()
            else:
                invoice.status = 'partial'

        invoice.save()
        self._flush_to_disk()

        # Trigger storefront fulfillment on paid transition
        if invoice.status == 'paid':
            try:
                from btpay.storefront.fulfillment import fulfill_storefront_invoice
                fulfill_storefront_invoice(invoice)
            except Exception:
                log.exception('Storefront fulfillment failed for %s', invoice.invoice_number)

        log.info('Payment recorded for %s: %s BTC (%s %s), status=%s',
                 invoice.invoice_number, amount_btc, amount_fiat,
                 invoice.currency, invoice.status)
        return payment

    def confirm_payment(self, invoice, payment, confirmations):
        '''Mark a payment as confirmed and update invoice status.'''
        payment.mark_confirmed(confirmations)

        if invoice.status == 'paid':
            invoice.status = 'confirmed'
            invoice.confirmed_at = NOW()
            invoice.save()
            self._flush_to_disk()
            log.info('Invoice confirmed: %s (%d confirmations)',
                     invoice.invoice_number, confirmations)

        return invoice

    def cancel_invoice(self, invoice):
        '''Cancel a draft or pending invoice.'''
        if invoice.status not in ('draft', 'pending'):
            raise ValueError('Cannot cancel invoice in status: %s' % invoice.status)

        # Release assigned address — mark as 'released', never return to pool.
        # Returning to 'unused' would risk cross-customer payment attribution.
        if invoice.payment_address_id:
            from btpay.bitcoin.models import BitcoinAddress
            addr = BitcoinAddress.get(invoice.payment_address_id)
            if addr and addr.status == 'assigned':
                addr.status = 'released'
                addr.assigned_to_invoice_id = 0
                addr.save()

        invoice.status = 'cancelled'
        invoice.cancelled_at = NOW()
        invoice.save()
        self._flush_to_disk()

        log.info('Invoice cancelled: %s', invoice.invoice_number)
        return invoice

    def generate_invoice_number(self, org):
        '''Generate the next invoice number for the org.'''
        prefix = org.invoice_prefix or 'INV'
        number = org.invoice_next_number or 1
        inv_number = '%s-%04d' % (prefix, number)

        org.invoice_next_number = number + 1
        org.save()

        return inv_number

    # ---- Internal helpers ----

    def _assign_address(self, invoice, wallet):
        '''Assign a fresh BTC address to the invoice.'''
        ba = wallet.get_next_address()
        if ba is None:
            raise ValueError('No available addresses in wallet')

        ba.mark_assigned(invoice.id)
        invoice.payment_address_id = ba.id

    def _lock_btc_rate(self, invoice):
        '''Lock the BTC exchange rate for this invoice.'''
        if self.rate_service is None:
            raise ValueError('Exchange rate service not available')

        rate = self.rate_service.get_rate(invoice.currency)
        if rate is None:
            raise ValueError('No exchange rate available for %s' % invoice.currency)

        # Apply markup
        if self.markup_percent:
            markup_factor = 1 + self.markup_percent / 100
            rate = rate * markup_factor

        # Calculate BTC amount
        btc_amount = round_satoshi(invoice.total / rate)

        invoice.btc_rate = rate
        invoice.btc_amount = btc_amount
        invoice.btc_rate_locked_at = NOW()
        invoice.btc_rate_expires_at = TIME_FUTURE(minutes=self.quote_deadline)

    def _create_btcpay_invoice(self, invoice):
        '''Create a corresponding invoice on BTCPay Server.'''
        from btpay.connectors.btcpay import BTCPayConnector, BTCPayClient, BTCPayError

        conn = BTCPayConnector.query.filter(
            org_id=invoice.org_id, is_active=True).first()
        if not conn:
            raise ValueError('No active BTCPay connector for this organization')

        client = BTCPayClient.from_connector(conn)
        try:
            result = client.create_invoice(
                amount=float(invoice.total),
                currency=invoice.currency,
                order_id=invoice.invoice_number,
                metadata={'btpay_invoice_id': invoice.id},
            )
        except BTCPayError as e:
            raise ValueError('BTCPay invoice creation failed: %s' % e)

        meta = dict(invoice.metadata or {})
        meta['btcpay_invoice_id'] = result.get('id', '')
        meta['btcpay_checkout_url'] = result.get('checkoutLink', '')
        meta['btcpay_connector_id'] = conn.id
        invoice.metadata = meta

    def _create_lnbits_invoice(self, invoice):
        '''Create a Lightning invoice on LNbits.'''
        from btpay.connectors.lnbits import LNbitsConnector, LNbitsClient, LNbitsError

        conn = LNbitsConnector.query.filter(
            org_id=invoice.org_id, is_active=True).first()
        if not conn:
            raise ValueError('No active LNbits connector for this organization')

        # Convert fiat to sats
        if self.rate_service is None:
            raise ValueError('Exchange rate service required for Lightning invoices')
        rate = self.rate_service.get_rate(invoice.currency)
        if rate is None:
            raise ValueError('No exchange rate available for %s' % invoice.currency)

        btc_amount = round_satoshi(invoice.total / rate)
        amount_sat = int(btc_amount * 100_000_000)

        client = LNbitsClient.from_connector(conn)
        try:
            result = client.create_invoice(
                amount_sat=amount_sat,
                memo='Invoice %s' % invoice.invoice_number,
            )
        except LNbitsError as e:
            raise ValueError('LNbits invoice creation failed: %s' % e)

        meta = dict(invoice.metadata or {})
        meta['lnbits_payment_hash'] = result.get('payment_hash', '')
        meta['lnbits_bolt11'] = result.get('payment_request', '')
        meta['lnbits_amount_sat'] = amount_sat
        meta['lnbits_connector_id'] = conn.id
        invoice.metadata = meta

# EOF
