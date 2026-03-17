#
# LNbits connector — self-hosted Lightning wallet API
#
# Integrates with an LNbits instance for Lightning Network payments.
# Uses the simple REST API: create invoice, check payment status.
#
import logging
import requests

from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import Text, Integer, Boolean

log = logging.getLogger(__name__)


class LNbitsConnector(BaseMixin, MemModel):
    '''
    Stores LNbits connection details for an organization.
    Each org can have one active LNbits connector.
    '''
    org_id      = Integer(index=True)
    name        = Text(default='LNbits')
    is_active   = Boolean(default=True)
    server_url  = Text()        # e.g. https://lnbits.example.com
    api_key     = Text()        # Invoice/read key (NOT admin key)


def validate_lnbits_connector(conn):
    '''
    Validate an LNbitsConnector has minimum required fields.
    Returns (valid, errors_list).
    '''
    errors = []
    if not conn.server_url:
        errors.append('Server URL is required')
    elif not conn.server_url.startswith('http'):
        errors.append('Server URL must start with http:// or https://')
    if not conn.api_key:
        errors.append('API key (invoice key) is required')
    return len(errors) == 0, errors


class LNbitsClient:
    '''
    Minimal client for the LNbits API.

    Usage:
        client = LNbitsClient('https://lnbits.example.com', 'invoice-key')
        ok, info = client.test_connection()
        inv = client.create_invoice(50000, memo='Invoice INV-001')
        status = client.check_payment(inv['payment_hash'])
    '''

    def __init__(self, server_url, api_key, timeout=30):
        self.server_url = server_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_connector(cls, conn):
        '''Create client from an LNbitsConnector model instance.'''
        return cls(conn.server_url, conn.api_key)

    def _headers(self):
        return {
            'X-Api-Key': self.api_key,
            'Content-Type': 'application/json',
        }

    def test_connection(self):
        '''
        Test the connection by fetching wallet info.
        Returns (success, info_dict_or_error_string).
        '''
        try:
            resp = requests.get(
                '%s/api/v1/wallet' % self.server_url,
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, {
                    'name': data.get('name', ''),
                    'balance_msat': data.get('balance', 0),
                }
            return False, 'HTTP %d: %s' % (resp.status_code, resp.text[:200])
        except requests.RequestException as e:
            return False, str(e)

    def create_invoice(self, amount_sat, memo=''):
        '''
        Create a Lightning invoice.

        Args:
            amount_sat: Amount in satoshis
            memo: Invoice description

        Returns dict with keys: payment_hash, payment_request (bolt11), ...
        Raises LNbitsError on failure.
        '''
        payload = {
            'out': False,
            'amount': amount_sat,
        }
        if memo:
            payload['memo'] = memo

        try:
            resp = requests.post(
                '%s/api/v1/payments' % self.server_url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            raise LNbitsError('Create invoice failed: HTTP %d: %s' % (
                resp.status_code, resp.text[:300]))
        except requests.RequestException as e:
            raise LNbitsError('Create invoice request failed: %s' % e)

    def check_payment(self, payment_hash):
        '''
        Check payment status by payment hash.

        Returns dict with key 'paid' (bool) and payment details.
        Raises LNbitsError on failure.
        '''
        try:
            resp = requests.get(
                '%s/api/v1/payments/%s' % (self.server_url, payment_hash),
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            raise LNbitsError('Check payment failed: HTTP %d: %s' % (
                resp.status_code, resp.text[:300]))
        except requests.RequestException as e:
            raise LNbitsError('Check payment request failed: %s' % e)


class LNbitsError(Exception):
    '''Error communicating with LNbits.'''
    pass


def lnbits_payment_info(conn, invoice):
    '''
    Build payment info dict for checkout display.
    '''
    meta = invoice.metadata or {}
    return {
        'bolt11': meta.get('lnbits_bolt11', ''),
        'payment_hash': meta.get('lnbits_payment_hash', ''),
        'amount_sat': meta.get('lnbits_amount_sat', 0),
        'server_url': conn.server_url,
        'amount': str(invoice.total),
        'currency': invoice.currency,
    }

# EOF
