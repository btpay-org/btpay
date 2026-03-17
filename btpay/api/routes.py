#
# REST API v1 — Flask blueprint
#
# Routes: invoices, payment-links, rates, webhooks
# Auth: API key via @api_auth decorator
#
import logging
from decimal import Decimal, InvalidOperation
from flask import Blueprint, request, jsonify, g, current_app

from btpay.auth.decorators import api_auth

log = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')


# ---- Invoices ----

@api_bp.route('/invoices', methods=['GET'])
@api_auth
def list_invoices():
    '''List invoices for the current org.'''
    from btpay.invoicing.models import Invoice
    from btpay.api.serializers import serialize_invoice

    status = request.args.get('status')
    limit = min(int(request.args.get('limit', 50)), 100)
    offset = int(request.args.get('offset', 0))

    query = Invoice.query.filter(org_id=g.org.id)
    if status:
        query = query.filter(status=status)

    all_invoices = query.order_by('-created_at').all()
    page = all_invoices[offset:offset + limit]

    return jsonify(
        invoices=[serialize_invoice(inv, include_lines=False) for inv in page],
        total=len(all_invoices),
        limit=limit,
        offset=offset,
    )


@api_bp.route('/invoices', methods=['POST'])
@api_auth
def create_invoice():
    '''Create a new invoice.'''
    from btpay.invoicing.service import InvoiceService

    data = request.get_json(silent=True) or {}

    lines = data.get('lines', [])
    if not lines:
        return jsonify(error='At least one line item required'), 400

    rate_svc = _get_rate_service()
    svc = InvoiceService(
        exchange_rate_service=rate_svc,
        quote_deadline=current_app.config.get('BTC_QUOTE_DEADLINE', 30),
        markup_percent=current_app.config.get('BTC_MARKUP_PERCENT', 0),
        underpaid_gift=current_app.config.get('MAX_UNDERPAID_GIFT', 5),
    )

    try:
        inv = svc.create_invoice(
            g.org, g.user,
            lines=lines,
            customer_email=data.get('customer_email', ''),
            customer_name=data.get('customer_name', ''),
            customer_company=data.get('customer_company', ''),
            currency=data.get('currency'),
            notes=data.get('notes', ''),
            tax_rate=data.get('tax_rate', 0),
            discount_amount=data.get('discount_amount', 0),
            payment_methods=data.get('payment_methods'),
            metadata=data.get('metadata'),
        )
    except (ValueError, InvalidOperation) as e:
        return jsonify(error=str(e)), 400

    from btpay.api.serializers import serialize_invoice
    return jsonify(serialize_invoice(inv)), 201


@api_bp.route('/invoices/<ref>', methods=['GET'])
@api_auth
def get_invoice(ref):
    '''Get invoice by reference number or invoice number.'''
    inv = _lookup_invoice(ref)
    if inv is None:
        return jsonify(error='Invoice not found'), 404

    from btpay.api.serializers import serialize_invoice
    include_payments = request.args.get('include_payments', '').lower() in ('1', 'true')
    return jsonify(serialize_invoice(inv, include_payments=include_payments))


@api_bp.route('/invoices/<ref>/finalize', methods=['POST'])
@api_auth
def finalize_invoice(ref):
    '''Finalize a draft invoice (assign address, lock rate).'''
    from btpay.invoicing.service import InvoiceService
    from btpay.bitcoin.models import Wallet

    inv = _lookup_invoice(ref)
    if inv is None:
        return jsonify(error='Invoice not found'), 404

    # Find active wallet for org
    wallet = Wallet.query.filter(org_id=g.org.id, is_active=True).first()
    if wallet is None:
        return jsonify(error='No active wallet configured'), 400

    rate_svc = _get_rate_service()
    svc = InvoiceService(
        exchange_rate_service=rate_svc,
        quote_deadline=current_app.config.get('BTC_QUOTE_DEADLINE', 30),
        markup_percent=current_app.config.get('BTC_MARKUP_PERCENT', 0),
    )

    try:
        svc.finalize_invoice(inv, wallet)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    from btpay.api.serializers import serialize_invoice
    return jsonify(serialize_invoice(inv))


@api_bp.route('/invoices/<ref>/status', methods=['GET'])
@api_auth
def invoice_status(ref):
    '''Get invoice payment status (lightweight).'''
    inv = _lookup_invoice(ref)
    if inv is None:
        return jsonify(error='Invoice not found'), 404

    return jsonify(
        invoice_number=inv.invoice_number,
        status=inv.status,
        total=str(inv.total),
        amount_paid=str(inv.amount_paid),
        amount_due=str(inv.amount_due),
        currency=inv.currency,
    )


@api_bp.route('/invoices/<ref>', methods=['DELETE'])
@api_auth
def cancel_invoice(ref):
    '''Cancel a draft or pending invoice.'''
    from btpay.invoicing.service import InvoiceService

    inv = _lookup_invoice(ref)
    if inv is None:
        return jsonify(error='Invoice not found'), 404

    svc = InvoiceService()
    try:
        svc.cancel_invoice(inv)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    return jsonify(ok=True, status=inv.status)


# ---- Payment Links ----

@api_bp.route('/payment-links', methods=['GET'])
@api_auth
def list_payment_links():
    '''List payment links for the current org.'''
    from btpay.invoicing.models import PaymentLink
    from btpay.api.serializers import serialize_payment_link

    links = PaymentLink.query.filter(org_id=g.org.id).all()
    return jsonify(payment_links=[serialize_payment_link(pl) for pl in links])


@api_bp.route('/payment-links', methods=['POST'])
@api_auth
def create_payment_link():
    '''Create a new payment link.'''
    from btpay.invoicing.models import PaymentLink
    from btpay.api.serializers import serialize_payment_link

    data = request.get_json(silent=True) or {}

    title = data.get('title', '').strip()
    if not title:
        return jsonify(error='Title is required'), 400

    slug = data.get('slug', '').strip()
    if not slug:
        # Generate slug from title
        import re
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')

    # Check uniqueness
    if PaymentLink.get_by(slug=slug):
        return jsonify(error='Slug already exists'), 400

    amount = None
    if data.get('amount'):
        try:
            amount = Decimal(str(data['amount']))
        except InvalidOperation:
            return jsonify(error='Invalid amount'), 400

    pl = PaymentLink(
        org_id=g.org.id,
        slug=slug,
        title=title,
        description=data.get('description', ''),
        amount=amount,
        currency=data.get('currency', g.org.default_currency or 'USD'),
        payment_methods_enabled=data.get('payment_methods', ['onchain_btc']),
        redirect_url=data.get('redirect_url', ''),
        metadata=data.get('metadata'),
    )
    pl.save()

    return jsonify(serialize_payment_link(pl)), 201


@api_bp.route('/payment-links/<slug>', methods=['DELETE'])
@api_auth
def delete_payment_link(slug):
    '''Deactivate a payment link.'''
    from btpay.invoicing.models import PaymentLink

    pl = PaymentLink.get_by(slug=slug)
    if pl is None or pl.org_id != g.org.id:
        return jsonify(error='Payment link not found'), 404

    pl.is_active = False
    pl.save()
    return jsonify(ok=True)


# ---- Rates ----

@api_bp.route('/rates', methods=['GET'])
@api_auth
def get_rates():
    '''Get current exchange rates.'''
    from btpay.api.serializers import serialize_rate

    rate_svc = _get_rate_service()
    if rate_svc is None:
        return jsonify(error='Exchange rate service not available'), 503

    rates = rate_svc.get_rates()
    return jsonify(
        rates=[serialize_rate(cur, rate) for cur, rate in sorted(rates.items())]
    )


# ---- Webhooks ----

@api_bp.route('/webhooks', methods=['GET'])
@api_auth
def list_webhooks():
    '''List webhook endpoints for the current org.'''
    from btpay.api.webhook_models import WebhookEndpoint

    endpoints = WebhookEndpoint.query.filter(org_id=g.org.id).all()
    return jsonify(webhooks=[{
        'id': ep.id,
        'url': ep.url,
        'events': list(ep.events or []),
        'is_active': ep.is_active,
        'description': ep.description,
    } for ep in endpoints])


@api_bp.route('/webhooks', methods=['POST'])
@api_auth
def create_webhook():
    '''Register a new webhook endpoint.'''
    from btpay.api.webhook_models import WebhookEndpoint
    from btpay.security.hashing import generate_random_token

    data = request.get_json(silent=True) or {}

    from btpay.security.validators import validate_external_url, ValidationError

    url = data.get('url', '').strip()
    if not url:
        return jsonify(error='URL is required'), 400

    try:
        url = validate_external_url(url)
    except ValidationError as e:
        return jsonify(error=str(e)), 400

    events = data.get('events', ['*'])
    secret = generate_random_token(32)

    ep = WebhookEndpoint(
        org_id=g.org.id,
        url=url,
        secret=secret,
        events=events,
        description=data.get('description', ''),
    )
    ep.save()

    return jsonify(
        id=ep.id,
        url=ep.url,
        secret=secret,  # show once
        events=list(ep.events),
        is_active=ep.is_active,
    ), 201


@api_bp.route('/webhooks/<int:webhook_id>', methods=['DELETE'])
@api_auth
def delete_webhook(webhook_id):
    '''Delete a webhook endpoint.'''
    from btpay.api.webhook_models import WebhookEndpoint

    ep = WebhookEndpoint.get(webhook_id)
    if ep is None or ep.org_id != g.org.id:
        return jsonify(error='Webhook not found'), 404

    ep.is_active = False
    ep.save()
    return jsonify(ok=True)


# ---- Helpers ----

def _lookup_invoice(ref):
    '''Look up invoice by refnum or invoice number.'''
    from btpay.invoicing.models import Invoice

    # Try by invoice number first
    inv = Invoice.get_by(invoice_number=ref)
    if inv and inv.org_id == g.org.id:
        return inv

    # Try by refnum
    try:
        from btpay.security.refnums import ReferenceNumbers
        obj = ReferenceNumbers().unpack(ref, expect_class=Invoice)
        if obj and obj.org_id == g.org.id:
            return obj
    except (ValueError, TypeError):
        pass

    return None


def _get_rate_service():
    '''Get the exchange rate service from the app.'''
    if hasattr(current_app, '_exchange_rate_service'):
        return current_app._exchange_rate_service
    return None

# EOF
