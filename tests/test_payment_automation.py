#
# Tests for background payment automation wiring.
#
from decimal import Decimal


def _make_org():
    from btpay.auth.models import Organization
    org = Organization(name='Automation Org', slug='automation-org')
    org.save()
    return org


def _make_user():
    from btpay.auth.models import User
    user = User(email='automation@test.com')
    user.set_password('securepass123')
    user.save()
    return user


def _make_invoice(org, user, **kw):
    from btpay.invoicing.models import Invoice
    defaults = dict(
        org_id=org.id,
        invoice_number='AUTO-001',
        status='pending',
        currency='USD',
        total=Decimal('100.00'),
        amount_paid=Decimal('0'),
        btc_rate=Decimal('100000'),
        created_by_user_id=user.id,
        payment_methods_enabled=['onchain_btc'],
    )
    defaults.update(kw)
    inv = Invoice(**defaults)
    inv.save()
    return inv


def test_payment_events_dispatch_webhooks_and_email(app):
    from btpay.invoicing.service import InvoiceService

    class FakeDispatcher:
        def __init__(self):
            self.events = []

        def dispatch(self, event, data, org_id):
            self.events.append((event, data, org_id))

    class FakeEmailService:
        received = []
        confirmed = []

        @classmethod
        def for_org(cls, org, app_config):
            return cls()

        def is_configured(self):
            return True

        def send_payment_received(self, invoice, payment, org):
            self.received.append((invoice.id, payment.id, org.id))
            return True

        def send_payment_confirmed(self, invoice, payment, org):
            self.confirmed.append((invoice.id, payment.id, org.id))
            return True

    with app.app_context():
        org = _make_org()
        user = _make_user()
        invoice = _make_invoice(org, user, payment_methods_enabled=['lnbits'])

        dispatcher = FakeDispatcher()
        app._webhook_dispatcher = dispatcher
        app._email_service_factory = FakeEmailService

        svc = InvoiceService(data_dir=app.config['DATA_DIR'])
        payment = svc.record_fiat_payment(
            invoice, Decimal('100.00'), method='lnbits', txid='lnbits:abc')
        svc.confirm_payment(invoice, payment, 1)

        events = [event for event, _data, _org_id in dispatcher.events]
        assert 'payment.received' in events
        assert 'invoice.paid' in events
        assert 'payment.confirmed' in events
        assert 'invoice.confirmed' in events
        assert FakeEmailService.received == [(invoice.id, payment.id, org.id)]
        assert FakeEmailService.confirmed == [(invoice.id, payment.id, org.id)]


def test_start_payment_automation_loads_existing_watches(app, monkeypatch):
    from btpay.payment_automation import start_payment_automation

    class FakeOnchainMonitor:
        def __init__(self, *args, **kwargs):
            self.watched = []
            self.seen_callbacks = []
            self.confirmed_callbacks = []
            self.started = False

        def on_payment_seen(self, callback):
            self.seen_callbacks.append(callback)

        def on_payment_confirmed(self, callback):
            self.confirmed_callbacks.append(callback)

        def load_assigned_addresses(self):
            from btpay.bitcoin.models import BitcoinAddress
            self.watched.extend(BitcoinAddress.query.filter(status='assigned').all())

        def start(self):
            self.started = True

    class FakeProcessorMonitor:
        def __init__(self, *args, **kwargs):
            self.watches = []
            self.callbacks = []
            self.started = False

        def on_payment(self, callback):
            self.callbacks.append(callback)

        def watch(self, *args):
            self.watches.append(args)

        def start(self):
            self.started = True

    monkeypatch.setattr('btpay.bitcoin.monitor.PaymentMonitor', FakeOnchainMonitor)
    monkeypatch.setattr('btpay.bitcoin.mempool_api.MempoolAPI', lambda **kw: object())
    monkeypatch.setattr('btpay.connectors.btcpay_monitor.BTCPayMonitor',
                        FakeProcessorMonitor)
    monkeypatch.setattr('btpay.connectors.lnbits_monitor.LNbitsMonitor',
                        FakeProcessorMonitor)

    app.config['STABLECOIN_MONITOR_ENABLED'] = False

    with app.app_context():
        from btpay.bitcoin.models import BitcoinAddress
        from btpay.connectors.btcpay import BTCPayConnector
        from btpay.connectors.lnbits import LNbitsConnector

        org = _make_org()
        user = _make_user()
        address = BitcoinAddress(
            wallet_id=1,
            address='tb1qautomationaddress',
            status='assigned',
            assigned_to_invoice_id=0,
        )
        address.save()
        btcpay = BTCPayConnector(
            org_id=org.id, server_url='https://btcpay.test',
            api_key='key', store_id='store', is_active=True)
        btcpay.save()
        lnbits = LNbitsConnector(
            org_id=org.id, server_url='https://lnbits.test',
            api_key='key', is_active=True)
        lnbits.save()

        invoice = _make_invoice(
            org, user,
            payment_address_id=address.id,
            payment_methods_enabled=['onchain_btc', 'btcpay', 'lnbits'],
            metadata={
                'btcpay_invoice_id': 'bp-123',
                'btcpay_connector_id': btcpay.id,
                'lnbits_payment_hash': 'ln-123',
                'lnbits_connector_id': lnbits.id,
            },
        )
        address.assigned_to_invoice_id = invoice.id
        address.save()

        start_payment_automation(app)

        assert app._payment_monitor.started is True
        assert app._payment_monitor.watched[0].id == address.id
        assert len(app._payment_monitor.seen_callbacks) == 1
        assert len(app._payment_monitor.confirmed_callbacks) == 1
        assert len(app._btcpay_monitor.watches) == 1
        assert app._btcpay_monitor.watches[0][0:2] == (invoice.id, 'bp-123')
        assert app._btcpay_monitor.watches[0][2].id == btcpay.id
        assert len(app._lnbits_monitor.watches) == 1
        assert app._lnbits_monitor.watches[0][0:2] == (invoice.id, 'ln-123')
        assert app._lnbits_monitor.watches[0][2].id == lnbits.id


def test_onchain_seen_callback_records_payment(app):
    from btpay.payment_automation import _handle_onchain_seen

    with app.app_context():
        from btpay.bitcoin.models import BitcoinAddress
        from btpay.invoicing.models import Payment

        org = _make_org()
        user = _make_user()
        invoice = _make_invoice(org, user)
        address = BitcoinAddress(
            wallet_id=1,
            address='tb1qseenautomation',
            status='assigned',
            assigned_to_invoice_id=invoice.id,
        )
        address.save()
        invoice.payment_address_id = address.id
        invoice.save()

        _handle_onchain_seen(app, address, 100000, 'tx-seen')

        payments = Payment.query.filter(invoice_id=invoice.id).all()
        assert len(payments) == 1
        assert payments[0].txid == 'tx-seen'
        assert payments[0].method == 'onchain_btc'
        invoice.reload()
        assert invoice.status == 'paid'
