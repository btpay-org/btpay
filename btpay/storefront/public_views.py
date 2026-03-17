#
# Public storefront views - no authentication required
#
# Handles browsing, item purchase, donation submission, and cart checkout.
# Creates invoices via InvoiceService and redirects to existing checkout flow.
#
import logging
from decimal import Decimal, InvalidOperation
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, current_app, jsonify, session,
)

log = logging.getLogger(__name__)

public_storefront_bp = Blueprint('public_storefront', __name__, url_prefix='/s')


def _get_storefront(slug):
    '''Look up an active storefront by slug.'''
    from btpay.storefront.models import Storefront
    sf = Storefront.get_by(slug=slug)
    if sf is None or not sf.is_active:
        return None
    return sf


def _get_org(sf):
    from btpay.auth.models import Organization
    return Organization.get(sf.org_id)


def _get_org_owner(org):
    '''Get the org owner user for attributing storefront invoices.'''
    from btpay.auth.models import Membership, User
    membership = Membership.query.filter(org_id=org.id, role='owner').first()
    if membership:
        return User.get(membership.user_id)
    # Fallback: any admin
    membership = Membership.query.filter(org_id=org.id, role='admin').first()
    if membership:
        return User.get(membership.user_id)
    log.warning('No owner or admin found for org %s - storefront invoice will have no creator', org.id)
    return None


def _get_invoice_service():
    '''Build an InvoiceService with the current app's exchange rate service.'''
    from btpay.invoicing.service import InvoiceService
    rate_svc = getattr(current_app, '_exchange_rate_service', None)
    return InvoiceService(
        exchange_rate_service=rate_svc,
        quote_deadline=current_app.config.get('BTC_QUOTE_DEADLINE', 30),
        markup_percent=current_app.config.get('BTC_MARKUP_PERCENT', 0),
        underpaid_gift=current_app.config.get('MAX_UNDERPAID_GIFT', 5),
        data_dir=current_app.config.get('DATA_DIR', 'data'),
    )


def _get_payment_methods(sf, org):
    '''Determine payment methods for this storefront.'''
    if sf.payment_methods_enabled:
        return sf.payment_methods_enabled
    # Fall back to org's available methods
    methods = []
    from btpay.bitcoin.models import Wallet
    if Wallet.query.filter(org_id=org.id).first():
        methods.append('onchain_btc')
    from btpay.connectors.wire import WireConnector
    if WireConnector.query.filter(org_id=org.id, is_active=True).first():
        methods.append('wire')
    from btpay.connectors.btcpay import BTCPayConnector
    if BTCPayConnector.query.filter(org_id=org.id, is_active=True).first():
        methods.append('btcpay')
    from btpay.connectors.lnbits import LNbitsConnector
    if LNbitsConnector.query.filter(org_id=org.id, is_active=True).first():
        methods.append('lnbits')
    from btpay.connectors.stablecoins import StablecoinAccount
    for acct in StablecoinAccount.query.filter(org_id=org.id, is_active=True).all():
        key = acct.method_name
        if key not in methods:
            methods.append(key)
    return methods or ['onchain_btc']


# ---- Public storefront page ----

@public_storefront_bp.route('/<slug>')
def view_storefront(slug):
    sf = _get_storefront(slug)
    if sf is None:
        return render_template('storefronts/public/not_found.html'), 404

    org = _get_org(sf)
    items = sf.active_items

    if sf.is_donation:
        return render_template('storefronts/public/donation.html',
            sf=sf, org=org)

    # Group items by category
    categories = {}
    for item in items:
        cat = item.category or 'Items'
        categories.setdefault(cat, []).append(item)

    return render_template('storefronts/public/store.html',
        sf=sf, org=org, items=items, categories=categories)


# ---- Buy / checkout an item ----

@public_storefront_bp.route('/<slug>/buy/<int:item_id>', methods=['POST'])
def buy_item(slug, item_id):
    '''Create an invoice for a single item and redirect to checkout.'''
    from btpay.storefront.models import StorefrontItem

    sf = _get_storefront(slug)
    if sf is None:
        return 'Storefront not found', 404

    item = StorefrontItem.get(item_id)
    if item is None or item.storefront_id != sf.id or not item.is_active:
        flash('Item not available', 'error')
        return redirect(url_for('public_storefront.view_storefront', slug=slug))

    if not item.in_stock:
        flash('Item is out of stock', 'error')
        return redirect(url_for('public_storefront.view_storefront', slug=slug))

    # Custom amount for pay-what-you-want items
    if item.is_pay_what_you_want:
        amount_str = request.form.get('amount', '').strip()
        try:
            amount = Decimal(amount_str)
            if amount <= 0:
                raise ValueError()
        except (InvalidOperation, ValueError):
            flash('Please enter a valid amount', 'error')
            return redirect(url_for('public_storefront.view_storefront', slug=slug))
    else:
        amount = item.price

    # Get optional buyer info
    customer_email = request.form.get('email', '').strip() if sf.require_email else ''
    customer_name = request.form.get('name', '').strip() if sf.require_name else ''

    org = _get_org(sf)
    svc = _get_invoice_service()
    methods = _get_payment_methods(sf, org)

    # Use the org owner as invoice creator for storefront purchases
    system_user = _get_org_owner(org)

    invoice = svc.create_invoice(
        org=org,
        user=system_user,
        lines=[{
            'description': '%s - %s' % (sf.title, item.title),
            'quantity': 1,
            'unit_price': amount,
        }],
        customer_email=customer_email,
        customer_name=customer_name,
        currency=sf.currency,
        payment_methods=methods,
        metadata={
            'storefront_id': sf.id,
            'storefront_slug': sf.slug,
            'item_id': item.id,
            'item_title': item.title,
            'source': 'storefront',
        },
    )

    # Finalize immediately - assign address and lock rate
    wallet = _get_wallet(org)
    if wallet or 'onchain_btc' not in methods:
        try:
            svc.finalize_invoice(invoice, wallet)
        except ValueError as e:
            log.warning('Storefront invoice finalize failed: %s', e)
            # Still redirect to checkout; it will show as draft

    # NOTE: stats (total_orders, total_revenue) and inventory are NOT updated
    # here because the buyer has not yet paid.  They should be updated by the
    # payment-confirmation callback (webhook / monitor) once the invoice is
    # marked as paid.  Decrementing inventory at invoice-creation time would
    # allow abandoned checkouts to deplete stock.

    return redirect(url_for('checkout.pay', ref=invoice.ref_number))


# ---- Donation submission ----

@public_storefront_bp.route('/<slug>/donate', methods=['POST'])
def donate(slug):
    '''Create an invoice for a donation and redirect to checkout.'''
    sf = _get_storefront(slug)
    if sf is None:
        return 'Storefront not found', 404

    if not sf.is_donation:
        return 'Not a donation page', 400

    amount_str = request.form.get('amount', '').strip()
    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise ValueError()
    except (InvalidOperation, ValueError):
        flash('Please enter a valid donation amount', 'error')
        return redirect(url_for('public_storefront.view_storefront', slug=slug))

    customer_email = request.form.get('email', '').strip() if sf.require_email else ''
    customer_name = request.form.get('name', '').strip() if sf.require_name else ''
    message = request.form.get('message', '').strip()

    org = _get_org(sf)
    svc = _get_invoice_service()
    methods = _get_payment_methods(sf, org)

    system_user = _get_org_owner(org)

    invoice = svc.create_invoice(
        org=org,
        user=system_user,
        lines=[{
            'description': 'Donation - %s' % sf.title,
            'quantity': 1,
            'unit_price': amount,
        }],
        customer_email=customer_email,
        customer_name=customer_name,
        currency=sf.currency,
        payment_methods=methods,
        metadata={
            'storefront_id': sf.id,
            'storefront_slug': sf.slug,
            'source': 'donation',
            'donor_message': message,
        },
    )

    wallet = _get_wallet(org)
    if wallet or 'onchain_btc' not in methods:
        try:
            svc.finalize_invoice(invoice, wallet)
        except ValueError as e:
            log.warning('Donation invoice finalize failed: %s', e)

    # Stats updated on payment confirmation, not at invoice creation.

    return redirect(url_for('checkout.pay', ref=invoice.ref_number))


# ---- Cart checkout (multi-item) ----

@public_storefront_bp.route('/<slug>/cart/checkout', methods=['POST'])
def cart_checkout(slug):
    '''Create an invoice from a cart (multiple items) and redirect to checkout.'''
    from btpay.storefront.models import StorefrontItem

    sf = _get_storefront(slug)
    if sf is None:
        return 'Storefront not found', 404

    # Cart is sent as JSON: [{item_id, quantity}]
    cart_data = request.get_json(silent=True)
    if not cart_data or not isinstance(cart_data, list):
        # Fallback: form-encoded cart
        cart_data = []
        for key in request.form:
            if key.startswith('qty_'):
                item_id = int(key[4:])
                qty = int(request.form[key])
                if qty > 0:
                    cart_data.append({'item_id': item_id, 'quantity': qty})

    if not cart_data:
        flash('Cart is empty', 'error')
        return redirect(url_for('public_storefront.view_storefront', slug=slug))

    lines = []
    cart_items_meta = []   # for post-payment inventory reconciliation
    total = Decimal('0')
    for entry in cart_data:
        item = StorefrontItem.get(int(entry.get('item_id', 0)))
        if item is None or item.storefront_id != sf.id or not item.is_active:
            continue
        if not item.in_stock:
            continue

        qty = int(entry.get('quantity', 1))
        if qty < 1:
            qty = 1

        price = item.price or Decimal('0')
        lines.append({
            'description': item.title,
            'quantity': qty,
            'unit_price': price,
        })
        cart_items_meta.append({
            'item_id': item.id,
            'quantity': qty,
            'unit_price': str(price),
        })
        total += price * qty

    if not lines:
        flash('No valid items in cart', 'error')
        return redirect(url_for('public_storefront.view_storefront', slug=slug))

    customer_email = request.form.get('email', '') if sf.require_email else ''
    customer_name = request.form.get('name', '') if sf.require_name else ''

    org = _get_org(sf)
    svc = _get_invoice_service()
    methods = _get_payment_methods(sf, org)

    system_user = _get_org_owner(org)

    invoice = svc.create_invoice(
        org=org,
        user=system_user,
        lines=lines,
        customer_email=customer_email,
        customer_name=customer_name,
        currency=sf.currency,
        payment_methods=methods,
        metadata={
            'storefront_id': sf.id,
            'storefront_slug': sf.slug,
            'source': 'storefront_cart',
            'cart_items': cart_items_meta,
        },
    )

    wallet = _get_wallet(org)
    if wallet or 'onchain_btc' not in methods:
        try:
            svc.finalize_invoice(invoice, wallet)
        except ValueError as e:
            log.warning('Cart checkout finalize failed: %s', e)

    # Stats updated on payment confirmation, not at invoice creation.

    return redirect(url_for('checkout.pay', ref=invoice.ref_number))


# ---- Helpers ----

def _get_wallet(org):
    '''Get the first available wallet for the org.'''
    from btpay.bitcoin.models import Wallet
    return Wallet.query.filter(org_id=org.id).first()

# EOF
