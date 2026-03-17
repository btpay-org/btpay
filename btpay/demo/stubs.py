#
# Stubbed services for demo mode.
# No outbound network connections. Everything is simulated.
#
import logging
from decimal import Decimal

log = logging.getLogger(__name__)

# Static demo rates (no HTTP calls)
DEMO_RATES = {
    'USD': Decimal('71250.00'),
    'EUR': Decimal('62100.00'),
    'GBP': Decimal('53800.00'),
    'CAD': Decimal('98200.00'),
    'AUD': Decimal('101500.00'),
    'JPY': Decimal('10680000'),
    'CHF': Decimal('56500.00'),
}


class DemoExchangeRateService:
    '''Returns static rates. No network calls.'''

    def __init__(self, **kwargs):
        self._rates = dict(DEMO_RATES)

    def start(self):
        log.info("DEMO: Exchange rate service started (static rates)")

    def stop(self):
        pass

    def get_rate(self, currency='USD'):
        return self._rates.get(currency)

    def get_rates(self):
        return dict(self._rates)

    def fetch_now(self):
        pass

    def save_snapshot(self):
        pass


class DemoPaymentMonitor:
    '''Does not monitor anything. Logs watch requests.'''

    def __init__(self, **kwargs):
        self._callbacks_seen = []
        self._callbacks_confirmed = []

    def start(self):
        log.info("DEMO: Payment monitor started (no-op)")

    def stop(self):
        pass

    def watch_address(self, address):
        log.info("DEMO: Would watch address %s" % address)

    def unwatch_address(self, address):
        pass

    def on_payment_seen(self, callback):
        self._callbacks_seen.append(callback)

    def on_payment_confirmed(self, callback):
        self._callbacks_confirmed.append(callback)

    def load_assigned_addresses(self):
        pass


class DemoWebhookDispatcher:
    '''Logs webhook events instead of POSTing.'''

    def __init__(self, **kwargs):
        pass

    def dispatch(self, event, data, org_id=None):
        log.info("DEMO: Webhook event=%s org=%s data=%s" % (event, org_id, data))


class DemoEmailService:
    '''Logs emails instead of sending via SMTP.'''

    def __init__(self, **kwargs):
        pass

    @classmethod
    def for_org(cls, org, app_config):
        return cls()

    def is_configured(self):
        return True

    def send(self, to, subject, html, **kwargs):
        log.info("DEMO: Email to=%s subject=%s" % (to, subject))
        return True

    def send_invoice_created(self, invoice, org):
        log.info("DEMO: Would email invoice %s to %s" % (
            invoice.invoice_number, invoice.customer_email))

    def send_payment_received(self, invoice, payment, org):
        log.info("DEMO: Would email payment received for %s" % invoice.invoice_number)

    def send_payment_confirmed(self, invoice, payment, org):
        log.info("DEMO: Would email payment confirmed for %s" % invoice.invoice_number)

# EOF
