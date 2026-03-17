#
# Checkout flow — customer-facing payment logic
#
# Handles address assignment, QR code generation, payment status polling,
# BTC quote refresh, and under/overpayment handling.
#
import logging
from decimal import Decimal
from btpay.chrono import NOW, as_time_t
from btpay.coinmath import satoshi2coins

log = logging.getLogger(__name__)


class CheckoutService:
    '''
    Customer checkout flow.

    Usage:
        checkout = CheckoutService(invoice_service, monitor)
        info = checkout.start_checkout(invoice, wallet)
        status = checkout.check_payment_status(invoice)
    '''

    def __init__(self, invoice_service, payment_monitor=None):
        self.invoice_service = invoice_service
        self.monitor = payment_monitor

    def start_checkout(self, invoice, wallet=None):
        '''
        Begin the checkout process.
        If invoice is draft, finalizes it (assigns address, locks rate).
        Returns checkout info dict for the frontend.
        '''
        if invoice.is_draft and wallet:
            self.invoice_service.finalize_invoice(invoice, wallet)
        elif invoice.is_draft:
            raise ValueError('Wallet required to finalize invoice')

        if invoice.status not in ('pending', 'partial'):
            raise ValueError('Invoice is not payable (status: %s)' % invoice.status)

        # Check if quote expired
        if self.invoice_service.check_expiry(invoice):
            # Was expired — need to refresh
            if wallet:
                self.invoice_service.refresh_btc_quote(invoice, wallet)
            else:
                raise ValueError('BTC quote expired, wallet required to refresh')

        # Start monitoring if we have a monitor
        if self.monitor and invoice.payment_address:
            self.monitor.watch_address(invoice.payment_address)

        return self._build_checkout_info(invoice)

    def check_payment_status(self, invoice):
        '''
        Check and return current payment status for frontend polling.
        Returns status dict.
        '''
        # Refresh invoice state from store
        from btpay.invoicing.models import Invoice
        inv = Invoice.get(invoice.id)
        if inv is None:
            return {'status': 'not_found'}

        result = {
            'status': inv.status,
            'amount_due': str(inv.amount_due),
            'amount_paid': str(inv.amount_paid),
            'total': str(inv.total),
            'currency': inv.currency,
        }

        if inv.btc_amount:
            result['btc_amount'] = str(inv.btc_amount)
            result['btc_rate'] = str(inv.btc_rate)

        if inv.btc_rate_expires_at:
            exp = inv.btc_rate_expires_at
            exp_t = as_time_t(exp) if not isinstance(exp, (int, float)) else exp
            now_t = as_time_t(NOW())
            remaining = max(0, int(exp_t - now_t))
            result['quote_expires_in'] = remaining

        if inv.status in ('paid', 'confirmed'):
            result['paid_at'] = str(inv.paid_at) if inv.paid_at else ''
            result['confirmed_at'] = str(inv.confirmed_at) if inv.confirmed_at else ''

        # Payment info
        payments = inv.payments
        if payments:
            result['payments'] = [{
                'method': p.method,
                'amount_btc': str(p.amount_btc),
                'amount_fiat': str(p.amount_fiat),
                'txid': p.txid,
                'confirmations': p.confirmations,
                'status': p.status,
            } for p in payments]

        return result

    def handle_underpayment(self, invoice, received_sat, expected_sat):
        '''
        Determine how to handle an underpayment.
        Returns (accept, message).
        '''
        diff_sat = expected_sat - received_sat
        diff_btc = satoshi2coins(diff_sat)

        # Calculate fiat equivalent of the difference
        rate = invoice.btc_rate or Decimal('0')
        diff_fiat = (diff_btc * rate).quantize(Decimal('0.01')) if rate else Decimal('0')

        gift_threshold = self.invoice_service.underpaid_gift

        if diff_fiat <= gift_threshold:
            log.info('Accepting underpayment of %s %s for invoice %s (within gift threshold)',
                     diff_fiat, invoice.currency, invoice.invoice_number)
            return True, 'Accepted (within threshold)'

        return False, 'Underpaid by %s %s' % (diff_fiat, invoice.currency)

    def handle_overpayment(self, invoice, received_sat, expected_sat):
        '''
        Handle an overpayment. Log it and accept.
        '''
        excess_sat = received_sat - expected_sat
        excess_btc = satoshi2coins(excess_sat)
        rate = invoice.btc_rate or Decimal('0')
        excess_fiat = (excess_btc * rate).quantize(Decimal('0.01')) if rate else Decimal('0')

        log.info('Overpayment of %s %s for invoice %s',
                 excess_fiat, invoice.currency, invoice.invoice_number)

        return True, 'Overpaid by %s %s' % (excess_fiat, invoice.currency)

    # ---- Internal ----

    def _build_checkout_info(self, invoice):
        '''Build the checkout info dict for frontend rendering.'''
        info = {
            'invoice_number': invoice.invoice_number,
            'status': invoice.status,
            'total': str(invoice.total),
            'currency': invoice.currency,
            'amount_due': str(invoice.amount_due),
            'customer_name': invoice.customer_name,
            'customer_email': invoice.customer_email,
            'payment_methods': list(invoice.payment_methods_enabled or []),
        }

        # BTC payment details
        if invoice.btc_amount and invoice.payment_address:
            addr = invoice.payment_address
            info['btc'] = {
                'address': addr.address,
                'amount': str(invoice.btc_amount),
                'amount_sat': str(int(invoice.btc_amount * 100_000_000)),
                'rate': str(invoice.btc_rate),
                'bip21_uri': 'bitcoin:%s?amount=%s' % (addr.address, invoice.btc_amount),
            }

            # Quote expiry countdown
            if invoice.btc_rate_expires_at:
                exp = invoice.btc_rate_expires_at
                exp_t = as_time_t(exp) if not isinstance(exp, (int, float)) else exp
                now_t = as_time_t(NOW())
                info['btc']['expires_in'] = max(0, int(exp_t - now_t))

        # Wire payment details
        if 'wire' in (invoice.payment_methods_enabled or []):
            from btpay.invoicing.payment_methods import get_method
            wire = get_method('wire')
            if wire:
                info['wire'] = wire.get_payment_info(invoice)

        return info

# EOF
