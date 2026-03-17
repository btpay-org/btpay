#
# Payment method registry — pluggable payment types
#
import logging

log = logging.getLogger(__name__)

# Global registry
PAYMENT_METHODS = {}


def register_method(cls):
    '''Class decorator to register a payment method.'''
    instance = cls()
    PAYMENT_METHODS[instance.name] = instance
    return cls


def register_instance(instance):
    '''Register a payment method instance (for dynamic methods like stablecoins).'''
    PAYMENT_METHODS[instance.name] = instance


def get_method(name):
    '''Get a registered payment method by name.'''
    return PAYMENT_METHODS.get(name)


def available_methods(org):
    '''Get list of available payment methods for an org.'''
    # Ensure stablecoin methods are registered for this org
    _refresh_stablecoin_methods(org)
    return [m for m in PAYMENT_METHODS.values() if m.is_available(org)]


class PaymentMethod:
    '''
    Base class for payment methods.
    Subclass and register to add new payment types.
    '''
    name = ''
    display_name = ''
    icon = ''
    method_type = ''    # 'bitcoin', 'wire', 'stablecoin'

    def is_available(self, org):
        '''Check if this method is configured and available for the org.'''
        return False

    def get_payment_info(self, invoice):
        '''Get payment details to display to customer.
        Returns a dict of display info.'''
        return {}

    def validate_payment(self, invoice, data):
        '''Validate incoming payment data. Returns (valid, error_msg).'''
        return True, ''


@register_method
class OnchainBTC(PaymentMethod):
    '''On-chain Bitcoin payment.'''
    name = 'onchain_btc'
    display_name = 'Bitcoin (On-Chain)'
    icon = 'bitcoin'
    method_type = 'bitcoin'

    def is_available(self, org):
        from btpay.bitcoin.models import Wallet
        wallets = Wallet.query.filter(org_id=org.id, is_active=True).all()
        return len(wallets) > 0

    def get_payment_info(self, invoice):
        addr = invoice.payment_address
        if not addr:
            return {}
        return {
            'address': addr.address,
            'amount_btc': str(invoice.btc_amount),
            'amount_sat': str(int(invoice.btc_amount * 100_000_000)) if invoice.btc_amount else '0',
            'rate': str(invoice.btc_rate),
            'currency': invoice.currency,
            'bip21_uri': 'bitcoin:%s?amount=%s' % (
                addr.address, invoice.btc_amount
            ) if invoice.btc_amount else '',
        }


@register_method
class WireTransfer(PaymentMethod):
    '''Wire / bank transfer payment.'''
    name = 'wire'
    display_name = 'Wire Transfer'
    icon = 'bank'
    method_type = 'wire'

    def is_available(self, org):
        from btpay.connectors.wire import WireConnector
        wc = WireConnector.query.filter(org_id=org.id, is_active=True).first()
        if wc:
            return True
        # Fallback: legacy org.wire_info
        return bool(org.wire_info)

    def get_payment_info(self, invoice):
        from btpay.connectors.wire import WireConnector, wire_payment_info
        from btpay.auth.models import Organization

        org = Organization.get(invoice.org_id)
        if not org:
            return {}

        # Prefer WireConnector model
        wc = WireConnector.query.filter(org_id=org.id, is_active=True).first()
        if wc:
            return wire_payment_info(wc, invoice)

        # Fallback: legacy org.wire_info
        if org.wire_info:
            info = org.wire_info
            return {
                'bank_name': info.get('bank_name', ''),
                'account_name': info.get('account_name', ''),
                'account_number': info.get('account_number', ''),
                'routing_number': info.get('routing_number', ''),
                'swift_code': info.get('swift_code', ''),
                'reference': invoice.invoice_number,
                'amount': str(invoice.total),
                'currency': invoice.currency,
            }
        return {}


class StablecoinPaymentMethod(PaymentMethod):
    '''
    Dynamically created for each active StablecoinAccount.
    One instance per chain+token combination.
    '''
    icon = 'stablecoin'
    method_type = 'stablecoin'

    def __init__(self, account=None):
        if account:
            self.account = account
            self.name = account.method_name
            self.display_name = account.display_label

    def is_available(self, org):
        return (self.account and
                self.account.is_active and
                self.account.org_id == org.id)

    def get_payment_info(self, invoice):
        from btpay.connectors.stablecoins import stablecoin_payment_info
        return stablecoin_payment_info(self.account, invoice)


@register_method
class BTCPayPayment(PaymentMethod):
    '''BTCPay Server payment (on-chain + Lightning).'''
    name = 'btcpay'
    display_name = 'BTCPay Server'
    icon = 'bitcoin'
    method_type = 'btcpay'

    def is_available(self, org):
        from btpay.connectors.btcpay import BTCPayConnector
        return BTCPayConnector.query.filter(
            org_id=org.id, is_active=True).first() is not None

    def get_payment_info(self, invoice):
        from btpay.connectors.btcpay import BTCPayConnector, btcpay_payment_info
        from btpay.auth.models import Organization
        org = Organization.get(invoice.org_id)
        if not org:
            return {}
        conn = BTCPayConnector.query.filter(org_id=org.id, is_active=True).first()
        if not conn:
            return {}
        return btcpay_payment_info(conn, invoice)


@register_method
class LNbitsPayment(PaymentMethod):
    '''Lightning payment via LNbits.'''
    name = 'lnbits'
    display_name = 'Lightning (LNbits)'
    icon = 'lightning'
    method_type = 'lightning'

    def is_available(self, org):
        from btpay.connectors.lnbits import LNbitsConnector
        return LNbitsConnector.query.filter(
            org_id=org.id, is_active=True).first() is not None

    def get_payment_info(self, invoice):
        from btpay.connectors.lnbits import LNbitsConnector, lnbits_payment_info
        from btpay.auth.models import Organization
        org = Organization.get(invoice.org_id)
        if not org:
            return {}
        conn = LNbitsConnector.query.filter(org_id=org.id, is_active=True).first()
        if not conn:
            return {}
        return lnbits_payment_info(conn, invoice)


def _refresh_stablecoin_methods(org):
    '''Ensure all active StablecoinAccounts for this org are registered.'''
    from btpay.connectors.stablecoins import StablecoinAccount
    accounts = StablecoinAccount.query.filter(org_id=org.id, is_active=True).all()
    for acct in accounts:
        if acct.method_name not in PAYMENT_METHODS:
            register_instance(StablecoinPaymentMethod(acct))

# EOF
