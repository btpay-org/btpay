#
# BTCPay Server connector — self-hosted Bitcoin payment processor
#
# Integrates with a BTCPay Server instance via the Greenfield API.
# Supports both on-chain and Lightning payments (delegated to BTCPay).
#
import logging
import requests

from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import Text, Integer, Boolean, JsonColumn

log = logging.getLogger(__name__)


class BTCPayConnector(BaseMixin, MemModel):
    '''
    Stores BTCPay Server connection details for an organization.
    Each org can have one active BTCPay connector.
    '''
    org_id      = Integer(index=True)
    name        = Text(default='BTCPay Server')
    is_active   = Boolean(default=True)
    server_url  = Text()        # e.g. https://btcpay.example.com
    api_key     = Text()        # Greenfield API key
    store_id    = Text()        # BTCPay store ID


def validate_btcpay_connector(conn):
    '''
    Validate a BTCPayConnector has minimum required fields.
    Returns (valid, errors_list).
    '''
    errors = []
    if not conn.server_url:
        errors.append('Server URL is required')
    elif not conn.server_url.startswith('http'):
        errors.append('Server URL must start with http:// or https://')
    if not conn.api_key:
        errors.append('API key is required')
    if not conn.store_id:
        errors.append('Store ID is required')
    return len(errors) == 0, errors


class BTCPayClient:
    '''
    Minimal client for the BTCPay Server Greenfield API.

    Usage:
        client = BTCPayClient('https://btcpay.example.com', 'api-key', 'store-id')
        ok, info = client.test_connection()
        inv = client.create_invoice(100.00, 'USD', order_id='INV-001')
        status = client.get_invoice(inv['id'])
    '''

    def __init__(self, server_url, api_key, store_id, timeout=30):
        self.server_url = server_url.rstrip('/')
        self.api_key = api_key
        self.store_id = store_id
        self.timeout = timeout

    @classmethod
    def from_connector(cls, conn):
        '''Create client from a BTCPayConnector model instance.'''
        return cls(conn.server_url, conn.api_key, conn.store_id)

    def _headers(self):
        return {
            'Authorization': 'token %s' % self.api_key,
            'Content-Type': 'application/json',
        }

    def _url(self, path):
        return '%s/api/v1/stores/%s%s' % (self.server_url, self.store_id, path)

    def test_connection(self):
        '''
        Test the connection by fetching store info.
        Returns (success, info_dict_or_error_string).
        '''
        try:
            resp = requests.get(
                self._url(''),
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, {'name': data.get('name', ''), 'id': data.get('id', '')}
            return False, 'HTTP %d: %s' % (resp.status_code, resp.text[:200])
        except requests.RequestException as e:
            return False, str(e)

    def create_invoice(self, amount, currency, order_id='', metadata=None):
        '''
        Create an invoice on BTCPay Server.

        Returns dict with keys: id, checkoutLink, status, ...
        Raises BTCPayError on failure.
        '''
        payload = {
            'amount': str(amount),
            'currency': currency,
        }
        if order_id:
            payload['orderId'] = order_id
        if metadata:
            payload['metadata'] = metadata

        try:
            resp = requests.post(
                self._url('/invoices'),
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            raise BTCPayError('Create invoice failed: HTTP %d: %s' % (
                resp.status_code, resp.text[:300]))
        except requests.RequestException as e:
            raise BTCPayError('Create invoice request failed: %s' % e)

    def get_invoice(self, btcpay_invoice_id):
        '''
        Get invoice status from BTCPay Server.

        Returns dict with keys: id, status, additionalStatus, ...
        BTCPay statuses: New, Processing, Expired, Invalid, Settled
        Raises BTCPayError on failure.
        '''
        try:
            resp = requests.get(
                self._url('/invoices/%s' % btcpay_invoice_id),
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            raise BTCPayError('Get invoice failed: HTTP %d: %s' % (
                resp.status_code, resp.text[:300]))
        except requests.RequestException as e:
            raise BTCPayError('Get invoice request failed: %s' % e)

    def get_invoice_payment_methods(self, btcpay_invoice_id):
        '''
        Get payment methods for an invoice (addresses, amounts, etc.).
        Returns list of dicts with destination, amount, paymentLink, etc.
        '''
        try:
            resp = requests.get(
                self._url('/invoices/%s/payment-methods' % btcpay_invoice_id),
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            raise BTCPayError('Get payment methods failed: HTTP %d: %s' % (
                resp.status_code, resp.text[:300]))
        except requests.RequestException as e:
            raise BTCPayError('Get payment methods request failed: %s' % e)


class BTCPayError(Exception):
    '''Error communicating with BTCPay Server.'''
    pass


def btcpay_payment_info(conn, invoice):
    '''
    Build payment info dict for checkout display.
    '''
    meta = invoice.metadata or {}
    btcpay_id = meta.get('btcpay_invoice_id', '')
    checkout_url = meta.get('btcpay_checkout_url', '')

    return {
        'btcpay_invoice_id': btcpay_id,
        'checkout_url': checkout_url,
        'server_url': conn.server_url,
        'amount': str(invoice.total),
        'currency': invoice.currency,
    }

# EOF
