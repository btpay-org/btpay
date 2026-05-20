#
# Background payment automation wiring.
#
import logging
from decimal import Decimal

log = logging.getLogger(__name__)


PAYABLE_STATUSES = ('pending', 'partial', 'paid')


def start_payment_automation(app):
    '''Start webhook dispatcher and payment monitors for finalized invoices.'''
    _configure_side_effect_services(app)
    _start_onchain_monitor(app)
    _start_btcpay_monitor(app)
    _start_lnbits_monitor(app)
    _start_stablecoin_monitor(app)


def watch_invoice_payments(app, invoice):
    '''Register a single finalized invoice with running monitors.'''
    if invoice.status not in PAYABLE_STATUSES:
        return

    monitor = getattr(app, '_payment_monitor', None)
    if monitor is not None and invoice.payment_address is not None:
        monitor.watch_address(invoice.payment_address)

    _watch_btcpay_invoice(app, invoice)
    _watch_lnbits_invoice(app, invoice)
    _watch_stablecoin_invoice(app, invoice)


def _configure_side_effect_services(app):
    if app.config.get('DEMO_MODE'):
        from btpay.demo.stubs import DemoWebhookDispatcher, DemoEmailService
        app._webhook_dispatcher = DemoWebhookDispatcher()
        app._email_service_factory = DemoEmailService
        return

    from btpay.api.webhooks import WebhookDispatcher
    from btpay.email.service import EmailService
    app._webhook_dispatcher = WebhookDispatcher(
        retry_delays=app.config.get('WEBHOOK_RETRY_DELAYS'))
    app._email_service_factory = EmailService


def _start_onchain_monitor(app):
    if app.config.get('PAYMENT_MONITOR_ENABLED', True) is False:
        return

    if app.config.get('DEMO_MODE'):
        from btpay.demo.stubs import DemoPaymentMonitor
        monitor = DemoPaymentMonitor()
    else:
        from btpay.bitcoin.mempool_api import MempoolAPI
        from btpay.bitcoin.monitor import PaymentMonitor

        mempool = MempoolAPI(
            base_url=app.config.get('MEMPOOL_API_URL'),
            proxy=app.config.get('SOCKS5_PROXY', ''),
        )
        monitor = PaymentMonitor(
            check_interval=app.config.get('PAYMENT_MONITOR_INTERVAL', 30),
            mempool_api=mempool,
            confirmation_thresholds=app.config.get('BTC_CONFIRMATION_THRESHOLDS'),
        )

    monitor.on_payment_seen(
        lambda btc_address, amount_sat, txid:
            _with_app(app, _handle_onchain_seen, btc_address, amount_sat, txid))
    monitor.on_payment_confirmed(
        lambda btc_address, amount_sat, confirmations:
            _with_app(app, _handle_onchain_confirmed,
                      btc_address, amount_sat, confirmations))
    monitor.load_assigned_addresses()
    monitor.start()
    app._payment_monitor = monitor


def _start_btcpay_monitor(app):
    from btpay.connectors.btcpay_monitor import BTCPayMonitor

    monitor = BTCPayMonitor(
        check_interval=app.config.get('BTCPAY_MONITOR_INTERVAL', 30))
    monitor.on_payment(
        lambda invoice_id, status, data:
            _with_app(app, _handle_btcpay_payment, invoice_id, status, data))
    app._btcpay_monitor = monitor

    for invoice in _payable_invoices():
        _watch_btcpay_invoice(app, invoice)
    monitor.start()


def _start_lnbits_monitor(app):
    from btpay.connectors.lnbits_monitor import LNbitsMonitor

    monitor = LNbitsMonitor(
        check_interval=app.config.get('LNBITS_MONITOR_INTERVAL', 15))
    monitor.on_payment(
        lambda invoice_id, data:
            _with_app(app, _handle_lnbits_payment, invoice_id, data))
    app._lnbits_monitor = monitor

    for invoice in _payable_invoices():
        _watch_lnbits_invoice(app, invoice)
    monitor.start()


def _start_stablecoin_monitor(app):
    if app.config.get('STABLECOIN_MONITOR_ENABLED', True) is False:
        return

    from btpay.connectors.evm_rpc import EvmRpcClient
    from btpay.connectors.stablecoin_monitor import StablecoinMonitor

    rpc = EvmRpcClient(
        custom_rpcs=app.config.get('STABLECOIN_RPCS', {}),
        proxy=app.config.get('SOCKS5_PROXY', ''),
    )
    monitor = StablecoinMonitor(
        rpc_client=rpc,
        check_interval=app.config.get('STABLECOIN_MONITOR_INTERVAL', 60),
    )
    monitor.on_payment(
        lambda invoice_id, chain, token, amount, address:
            _with_app(app, _handle_stablecoin_payment,
                      invoice_id, chain, token, amount, address))
    app._stablecoin_monitor = monitor

    for invoice in _payable_invoices():
        _watch_stablecoin_invoice(app, invoice)
    monitor.start()


def _with_app(app, func, *args):
    with app.app_context():
        return func(app, *args)


def _payable_invoices():
    from btpay.invoicing.models import Invoice

    invoices = []
    for status in PAYABLE_STATUSES:
        invoices.extend(Invoice.query.filter(status=status).all())
    return invoices


def _invoice_service(app):
    from btpay.invoicing.service import InvoiceService

    return InvoiceService(
        exchange_rate_service=getattr(app, '_exchange_rate_service', None),
        quote_deadline=app.config.get('BTC_QUOTE_DEADLINE', 30),
        markup_percent=app.config.get('BTC_MARKUP_PERCENT', 0),
        underpaid_gift=app.config.get('MAX_UNDERPAID_GIFT', 5),
        data_dir=app.config.get('DATA_DIR', 'data'),
    )


def _find_payment(invoice, method, txid='', address=''):
    from btpay.invoicing.models import Payment

    payments = Payment.query.filter(invoice_id=invoice.id).all()
    candidates = [p for p in payments if p.method == method]
    if txid:
        for payment in candidates:
            if payment.txid == txid:
                return payment
    if address:
        address_matches = [p for p in candidates if p.address == address]
        if address_matches:
            return sorted(address_matches, key=lambda p: p.id or 0)[-1]
    return sorted(candidates, key=lambda p: p.id or 0)[-1] if candidates else None


def _handle_onchain_seen(app, btc_address, amount_sat, txid):
    from btpay.invoicing.models import Invoice

    invoice = Invoice.get(btc_address.assigned_to_invoice_id)
    if invoice is None or invoice.status not in PAYABLE_STATUSES:
        return
    if txid and _find_payment(invoice, 'onchain_btc', txid=txid):
        return

    _invoice_service(app).record_payment(
        invoice,
        amount_sat,
        txid=txid or '',
        address=btc_address.address,
        confirmations=0,
        method='onchain_btc',
        raw_data={'source': 'payment_monitor'},
    )


def _handle_onchain_confirmed(app, btc_address, amount_sat, confirmations):
    from btpay.invoicing.models import Invoice

    invoice = Invoice.get(btc_address.assigned_to_invoice_id)
    if invoice is None or invoice.status not in PAYABLE_STATUSES:
        return

    svc = _invoice_service(app)
    payment = _find_payment(invoice, 'onchain_btc', address=btc_address.address)
    if payment is None:
        payment = svc.record_payment(
            invoice,
            amount_sat,
            address=btc_address.address,
            confirmations=0,
            method='onchain_btc',
            raw_data={'source': 'payment_monitor', 'confirmed_without_seen': True},
        )
    if payment.status != 'confirmed':
        svc.confirm_payment(invoice, payment, confirmations)


def _watch_btcpay_invoice(app, invoice):
    monitor = getattr(app, '_btcpay_monitor', None)
    if monitor is None:
        return
    meta = invoice.metadata or {}
    btcpay_invoice_id = meta.get('btcpay_invoice_id')
    connector_id = meta.get('btcpay_connector_id')
    if not btcpay_invoice_id or not connector_id:
        return

    from btpay.connectors.btcpay import BTCPayConnector
    conn = BTCPayConnector.get(connector_id)
    if conn is not None and conn.is_active:
        monitor.watch(invoice.id, btcpay_invoice_id, conn)


def _handle_btcpay_payment(app, invoice_id, status, data):
    from btpay.invoicing.models import Invoice

    invoice = Invoice.get(invoice_id)
    if invoice is None or invoice.status not in PAYABLE_STATUSES:
        return

    btcpay_id = (invoice.metadata or {}).get('btcpay_invoice_id', '')
    txid = 'btcpay:%s' % btcpay_id if btcpay_id else ''
    svc = _invoice_service(app)
    payment = _find_payment(invoice, 'btcpay', txid=txid)

    if status == 'Processing' and payment is None:
        svc.record_fiat_payment(
            invoice, invoice.amount_due, txid=txid,
            confirmations=0, method='btcpay', raw_data=data)
    elif status == 'Settled':
        if payment is None:
            payment = svc.record_fiat_payment(
                invoice, invoice.amount_due, txid=txid,
                confirmations=0, method='btcpay', raw_data=data)
        if payment.status != 'confirmed':
            svc.confirm_payment(invoice, payment, 1)


def _watch_lnbits_invoice(app, invoice):
    monitor = getattr(app, '_lnbits_monitor', None)
    if monitor is None:
        return
    meta = invoice.metadata or {}
    payment_hash = meta.get('lnbits_payment_hash')
    connector_id = meta.get('lnbits_connector_id')
    if not payment_hash or not connector_id:
        return

    from btpay.connectors.lnbits import LNbitsConnector
    conn = LNbitsConnector.get(connector_id)
    if conn is not None and conn.is_active:
        monitor.watch(invoice.id, payment_hash, conn)


def _handle_lnbits_payment(app, invoice_id, data):
    from btpay.invoicing.models import Invoice

    invoice = Invoice.get(invoice_id)
    if invoice is None or invoice.status not in PAYABLE_STATUSES:
        return

    payment_hash = (invoice.metadata or {}).get('lnbits_payment_hash', '')
    txid = 'lnbits:%s' % payment_hash if payment_hash else ''
    svc = _invoice_service(app)
    payment = _find_payment(invoice, 'lnbits', txid=txid)
    if payment is None:
        payment = svc.record_fiat_payment(
            invoice, invoice.amount_due, txid=txid,
            confirmations=0, method='lnbits', raw_data=data)
    if payment.status != 'confirmed':
        svc.confirm_payment(invoice, payment, 1)


def _watch_stablecoin_invoice(app, invoice):
    monitor = getattr(app, '_stablecoin_monitor', None)
    if monitor is None:
        return

    methods = invoice.payment_methods_enabled or []
    stable_methods = [m for m in methods if m.startswith('stable_')]
    for method in stable_methods:
        parts = method.split('_', 2)
        if len(parts) != 3:
            continue
        _watch_stablecoin_method(app, monitor, invoice, method, parts[1], parts[2])


def _watch_stablecoin_method(app, monitor, invoice, method, chain, token):
    from btpay.connectors.stablecoins import StablecoinAccount, SUPPORTED_TOKENS

    accounts = StablecoinAccount.query.filter(
        org_id=invoice.org_id, chain=chain, token=token, is_active=True).all()
    if len(accounts) != 1:
        log.warning('Stablecoin monitor skipped %s for invoice %s: expected one '
                    'active account, found %d',
                    method, invoice.invoice_number, len(accounts))
        return
    if _stablecoin_has_conflict(invoice, method):
        log.warning('Stablecoin monitor skipped %s for invoice %s: another '
                    'payable invoice uses the same shared account',
                    method, invoice.invoice_number)
        return

    account = accounts[0]
    token_info = SUPPORTED_TOKENS.get(token, {})
    decimals = token_info.get('decimals', 6)
    expected = int((invoice.total * (Decimal(10) ** decimals)).quantize(Decimal('1')))

    meta = dict(invoice.metadata or {})
    baselines = dict(meta.get('stablecoin_baselines') or {})
    if method not in baselines:
        baselines[method] = monitor.snapshot_balance(chain, token, account.address)
        meta['stablecoin_baselines'] = baselines
        invoice.metadata = meta
        invoice.save()

    monitor.watch(
        invoice.id, chain, token, account.address, expected,
        baseline_balance=baselines[method],
    )


def _stablecoin_has_conflict(invoice, method):
    for other in _payable_invoices():
        if other.id == invoice.id or other.org_id != invoice.org_id:
            continue
        if method in (other.payment_methods_enabled or []):
            return True
    return False


def _handle_stablecoin_payment(app, invoice_id, chain, token, amount_received, address):
    from btpay.connectors.stablecoins import SUPPORTED_TOKENS
    from btpay.invoicing.models import Invoice

    invoice = Invoice.get(invoice_id)
    if invoice is None or invoice.status not in PAYABLE_STATUSES:
        return

    method = 'stable_%s_%s' % (chain, token)
    txid = '%s:%s' % (method, amount_received)
    if _find_payment(invoice, method, txid=txid, address=address):
        return

    decimals = SUPPORTED_TOKENS.get(token, {}).get('decimals', 6)
    amount_fiat = (Decimal(amount_received) / (Decimal(10) ** decimals)).quantize(
        Decimal('0.01'))

    svc = _invoice_service(app)
    payment = svc.record_fiat_payment(
        invoice,
        amount_fiat,
        txid=txid,
        address=address,
        confirmations=0,
        method=method,
        raw_data={
            'chain': chain,
            'token': token,
            'amount_received': amount_received,
            'source': 'stablecoin_monitor',
        },
    )
    if payment.status != 'confirmed':
        svc.confirm_payment(invoice, payment, 1)

