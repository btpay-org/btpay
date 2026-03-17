#
# Checkout views — public-facing payment flow (no auth required)
#
import io
import logging
from flask import Blueprint, render_template, jsonify, current_app, g, request

from btpay.chrono import as_time_t

log = logging.getLogger(__name__)

checkout_bp = Blueprint('checkout', __name__, url_prefix='/checkout')


def _find_invoice(ref):
    '''Look up invoice by encrypted refnum only. No invoice_number fallback.'''
    from btpay.invoicing.models import Invoice
    from btpay.security.refnums import ReferenceNumbers
    try:
        return ReferenceNumbers().unpack(ref, expect_class=Invoice)
    except (ValueError, TypeError):
        return None


def _get_checkout_methods(invoice, org):
    '''
    Build list of available payment methods for checkout display.
    Returns list of dicts with method info.
    '''
    methods = []
    enabled = list(invoice.payment_methods_enabled or [])

    # Bitcoin (on-chain)
    if 'onchain_btc' in enabled and invoice.payment_address:
        methods.append({
            'type': 'bitcoin',
            'name': 'onchain_btc',
            'display_name': 'Bitcoin',
            'icon': 'bitcoin',
        })

    # Wire transfer
    if 'wire' in enabled:
        from btpay.connectors.wire import WireConnector
        wc = WireConnector.query.filter(org_id=org.id, is_active=True).first()
        has_legacy = bool(org.wire_info)
        if wc or has_legacy:
            from btpay.invoicing.payment_methods import get_method
            wire_method = get_method('wire')
            wire_info = wire_method.get_payment_info(invoice) if wire_method else {}
            methods.append({
                'type': 'wire',
                'name': 'wire',
                'display_name': 'Wire Transfer',
                'icon': 'bank',
                'info': wire_info,
            })

    # Stablecoins — group by token
    from btpay.connectors.stablecoins import StablecoinAccount, stablecoin_payment_info

    stable_accounts = StablecoinAccount.query.filter(org_id=org.id, is_active=True).all()

    # Collect tokens that have accounts
    seen_tokens = {}
    for acct in stable_accounts:
        method_key = acct.method_name
        if method_key in enabled or 'stablecoins' in enabled:
            if acct.token not in seen_tokens:
                seen_tokens[acct.token] = []
            seen_tokens[acct.token].append({
                'chain': acct.chain,
                'chain_name': acct.chain_name,
                'address': acct.address,
                'short_address': acct.short_address,
                'explorer_url': acct.explorer_url,
                'info': stablecoin_payment_info(acct, invoice),
            })

    for token_key, accounts in seen_tokens.items():
        from btpay.connectors.stablecoins import SUPPORTED_TOKENS
        token_info = SUPPORTED_TOKENS.get(token_key, {})
        methods.append({
            'type': 'stablecoin',
            'name': 'stable_%s' % token_key,
            'display_name': token_info.get('symbol', token_key.upper()),
            'icon': 'stablecoin',
            'token': token_key,
            'token_symbol': token_info.get('symbol', token_key.upper()),
            'accounts': accounts,
        })

    # BTCPay Server
    if 'btcpay' in enabled:
        from btpay.connectors.btcpay import BTCPayConnector
        conn = BTCPayConnector.query.filter(org_id=org.id, is_active=True).first()
        if conn:
            from btpay.invoicing.payment_methods import get_method
            btcpay_method = get_method('btcpay')
            btcpay_info = btcpay_method.get_payment_info(invoice) if btcpay_method else {}
            methods.append({
                'type': 'btcpay',
                'name': 'btcpay',
                'display_name': 'BTCPay Server',
                'icon': 'bitcoin',
                'info': btcpay_info,
            })

    # LNbits (Lightning)
    if 'lnbits' in enabled:
        from btpay.connectors.lnbits import LNbitsConnector
        conn = LNbitsConnector.query.filter(org_id=org.id, is_active=True).first()
        if conn:
            from btpay.invoicing.payment_methods import get_method
            lnbits_method = get_method('lnbits')
            lnbits_info = lnbits_method.get_payment_info(invoice) if lnbits_method else {}
            methods.append({
                'type': 'lightning',
                'name': 'lnbits',
                'display_name': 'Lightning',
                'icon': 'lightning',
                'info': lnbits_info,
            })

    return methods


@checkout_bp.route('/<ref>')
def pay(ref):
    '''Public checkout page — multi-method payment.'''
    from btpay.auth.models import Organization

    invoice = _find_invoice(ref)
    if invoice is None:
        return render_template('checkout/status.html',
            invoice=type('Obj', (), {'status': 'not_found', 'invoice_number': ref})(),
            org=None), 404

    org = Organization.get(invoice.org_id)

    payment_address = None
    if invoice.payment_address:
        payment_address = invoice.payment_address.address

    # If not payable, redirect to status
    if invoice.status not in ('pending', 'partial', 'draft'):
        return render_template('checkout/status.html', invoice=invoice, org=org)

    # Rate locked timestamp for countdown
    rate_locked_at = 0
    if invoice.btc_rate_locked_at:
        val = invoice.btc_rate_locked_at
        rate_locked_at = as_time_t(val) if not isinstance(val, (int, float)) else int(val)

    quote_deadline = current_app.config.get('BTC_QUOTE_DEADLINE', 1800)

    # Build payment methods for multi-method checkout
    checkout_methods = _get_checkout_methods(invoice, org)

    return render_template('checkout/pay.html',
        invoice=invoice,
        org=org,
        payment_address=payment_address,
        rate_locked_at=rate_locked_at,
        quote_deadline=quote_deadline,
        checkout_methods=checkout_methods,
    )


@checkout_bp.route('/<ref>/status')
def status(ref):
    '''Payment status page — confirmation progress.'''
    from btpay.invoicing.models import Payment
    from btpay.auth.models import Organization

    invoice = _find_invoice(ref)
    if invoice is None:
        return render_template('checkout/status.html',
            invoice=type('Obj', (), {'status': 'not_found', 'invoice_number': ref})(),
            org=None), 404

    org = Organization.get(invoice.org_id)

    # Get confirmations from payments
    payments = Payment.query.filter(invoice_id=invoice.id).all()
    confirmations = max((p.confirmations for p in payments), default=0) if payments else 0

    # Required confirmations based on config
    required = current_app.config.get('BTC_REQUIRED_CONFIRMATIONS', 1)

    return render_template('checkout/status.html',
        invoice=invoice,
        org=org,
        confirmations=confirmations,
        required_confirmations=required,
    )


@checkout_bp.route('/<ref>/status.json')
def status_json(ref):
    '''Payment status JSON for polling.'''
    from btpay.invoicing.checkout import CheckoutService
    from btpay.invoicing.service import InvoiceService

    invoice = _find_invoice(ref)
    if invoice is None:
        return jsonify(status='not_found'), 404

    svc = InvoiceService()
    checkout = CheckoutService(svc)
    result = checkout.check_payment_status(invoice)
    return jsonify(result)


@checkout_bp.route('/<ref>/receipt')
def receipt(ref):
    '''Payment receipt page.'''
    from btpay.invoicing.models import InvoiceLine, Payment
    from btpay.auth.models import Organization

    invoice = _find_invoice(ref)
    if invoice is None:
        return 'Not found', 404

    if invoice.status not in ('paid', 'confirmed'):
        return render_template('checkout/status.html',
            invoice=invoice,
            org=Organization.get(invoice.org_id))

    org = Organization.get(invoice.org_id)
    lines = InvoiceLine.query.filter(invoice_id=invoice.id).all()
    lines.sort(key=lambda l: l.sort_order or 0)
    payments = Payment.query.filter(invoice_id=invoice.id).all()

    return render_template('checkout/receipt.html',
        invoice=invoice,
        org=org,
        lines=lines,
        payments=payments,
    )


@checkout_bp.route('/<ref>/qr')
def qr_code(ref):
    '''Generate QR code image for payment address or Lightning invoice.'''
    invoice = _find_invoice(ref)
    if invoice is None:
        return '', 404

    method = request.args.get('method', '')
    meta = invoice.metadata or {}

    if method == 'lnbits':
        bolt11 = meta.get('lnbits_bolt11', '')
        if not bolt11:
            return '', 404
        uri = bolt11.upper()  # Lightning QR codes use uppercase
    else:
        if not invoice.payment_address:
            return '', 404
        addr = invoice.payment_address.address
        amount = invoice.btc_amount or ''
        uri = 'bitcoin:%s' % addr
        if amount:
            uri += '?amount=%s' % amount

    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=6, border=2,
                            error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)

        from flask import send_file
        return send_file(buf, mimetype='image/png')
    except ImportError:
        # qrcode not installed — return placeholder
        return '', 404

# EOF
