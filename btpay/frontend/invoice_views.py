#
# Invoice views - list, create, detail, finalize, cancel
#
import logging
from decimal import Decimal, InvalidOperation
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, g, current_app, send_file,
)

from btpay.auth.decorators import login_required, role_required, csrf_protect

log = logging.getLogger(__name__)

invoices_bp = Blueprint('invoices', __name__, url_prefix='/invoices')


@invoices_bp.route('/')
@login_required
def list_invoices():
    '''Invoice list with search, date range, and status filter.'''
    from btpay.invoicing.models import Invoice
    import pendulum

    org_id = g.org.id if g.org else 0
    status_filter = request.args.get('status', '')
    search_q = request.args.get('q', '').strip().lower()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    all_inv = Invoice.query.filter(org_id=org_id).all()
    if status_filter:
        all_inv = [i for i in all_inv if i.status == status_filter]

    # Search filter
    if search_q:
        def _matches(inv):
            fields = [
                getattr(inv, 'customer_name', '') or '',
                getattr(inv, 'customer_email', '') or '',
                getattr(inv, 'invoice_number', '') or '',
                getattr(inv, 'customer_company', '') or '',
            ]
            return any(search_q in f.lower() for f in fields)
        all_inv = [i for i in all_inv if _matches(i)]

    # Date range filter (datetimes are directly comparable)
    if date_from:
        try:
            dt_from = pendulum.parse(date_from, tz='UTC')
            all_inv = [i for i in all_inv if i.created_at and i.created_at >= dt_from]
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = pendulum.parse(date_to, tz='UTC').add(days=1)
            all_inv = [i for i in all_inv if i.created_at and i.created_at < dt_to]
        except ValueError:
            pass

    # Sort by created_at descending (datetimes are ordered)
    all_inv.sort(key=lambda i: i.created_at or pendulum.datetime(1970, 1, 1), reverse=True)

    # Pagination
    limit = 20
    offset = int(request.args.get('offset', 0))
    total = len(all_inv)
    invoices = all_inv[offset:offset + limit]

    # Build base_url preserving all active filters for pagination links
    base_params = {}
    if status_filter:
        base_params['status'] = status_filter
    if search_q:
        base_params['q'] = search_q
    if date_from:
        base_params['date_from'] = date_from
    if date_to:
        base_params['date_to'] = date_to

    return render_template('invoices/list.html',
        invoices=invoices,
        total=total,
        limit=limit,
        offset=offset,
        status_filter=status_filter,
        base_url=url_for('invoices.list_invoices', **base_params),
    )


@invoices_bp.route('/export.csv')
@login_required
def export_csv():
    '''Export filtered invoices as CSV.'''
    import csv
    import io
    import pendulum
    from btpay.invoicing.models import Invoice

    org_id = g.org.id if g.org else 0
    status_filter = request.args.get('status', '')
    search_q = request.args.get('q', '').strip().lower()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    all_inv = Invoice.query.filter(org_id=org_id).all()
    if status_filter:
        all_inv = [i for i in all_inv if i.status == status_filter]
    if search_q:
        def _matches(inv):
            fields = [
                getattr(inv, 'customer_name', '') or '',
                getattr(inv, 'customer_email', '') or '',
                getattr(inv, 'invoice_number', '') or '',
                getattr(inv, 'customer_company', '') or '',
            ]
            return any(search_q in f.lower() for f in fields)
        all_inv = [i for i in all_inv if _matches(i)]
    if date_from:
        try:
            dt_from = pendulum.parse(date_from, tz='UTC')
            all_inv = [i for i in all_inv if i.created_at and i.created_at >= dt_from]
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = pendulum.parse(date_to, tz='UTC').add(days=1)
            all_inv = [i for i in all_inv if i.created_at and i.created_at < dt_to]
        except ValueError:
            pass

    all_inv.sort(key=lambda i: i.created_at or pendulum.datetime(1970, 1, 1), reverse=True)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Invoice #', 'Status', 'Customer Name', 'Customer Email',
                     'Currency', 'Subtotal', 'Tax', 'Discount', 'Total',
                     'Amount Paid', 'BTC Amount', 'Created'])
    for inv in all_inv:
        created = str(inv.created_at) if inv.created_at else ''
        writer.writerow([
            inv.invoice_number, inv.status,
            inv.customer_name or '', inv.customer_email or '',
            inv.currency, inv.subtotal or 0, inv.tax_amount or 0,
            inv.discount_amount or 0, inv.total or 0,
            inv.amount_paid or 0, inv.btc_amount or 0, created,
        ])

    from flask import Response
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=invoices.csv'},
    )


@invoices_bp.route('/create', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def create():
    '''Create a new invoice.'''
    from btpay.invoicing.models import Invoice, InvoiceLine
    from btpay.invoicing.service import InvoiceService

    if request.method == 'GET':
        currency = g.org.default_currency if g.org else 'USD'
        available_methods = _available_payment_methods(g.org)
        return render_template('invoices/create.html', currency=currency,
                               available_methods=available_methods)

    # POST - create invoice
    form = request.form

    svc = InvoiceService(
        exchange_rate_service=getattr(current_app, '_exchange_rate_service', None),
        quote_deadline=current_app.config.get('BTC_QUOTE_DEADLINE', 30),
        markup_percent=current_app.config.get('BTC_MARKUP_PERCENT', 0),
    )

    # Build line items from form
    descriptions = request.form.getlist('line_description[]')
    quantities = request.form.getlist('line_qty[]')
    prices = request.form.getlist('line_price[]')

    lines = []
    for i, desc in enumerate(descriptions):
        if not desc.strip():
            continue
        qty = _dec(quantities[i]) if i < len(quantities) else Decimal('1')
        price = _dec(prices[i]) if i < len(prices) else Decimal('0')
        lines.append({
            'description': desc.strip(),
            'quantity': qty,
            'unit_price': price,
        })

    # Collect selected payment methods
    selected_methods = request.form.getlist('payment_methods')
    if not selected_methods:
        selected_methods = ['onchain_btc']

    try:
        invoice = svc.create_invoice(
            org=g.org,
            user=g.user,
            lines=lines,
            customer_name=form.get('customer_name', '').strip(),
            customer_email=form.get('customer_email', '').strip(),
            customer_company=form.get('customer_company', '').strip(),
            currency=form.get('currency', 'USD'),
            tax_rate=_dec(form.get('tax_rate', '0')),
            discount_amount=_dec(form.get('discount_amount', '0')),
            notes=form.get('notes', '').strip(),
            payment_methods=selected_methods,
        )
    except Exception as e:
        flash(str(e), 'error')
        return redirect(url_for('invoices.create'))

    flash('Invoice created: %s' % invoice.invoice_number, 'success')
    return redirect(url_for('invoices.detail', ref=invoice.invoice_number))


@invoices_bp.route('/<ref>')
@login_required
def detail(ref):
    '''Invoice detail page.'''
    from btpay.invoicing.models import Invoice, InvoiceLine, Payment

    invoice = Invoice.get_by(invoice_number=ref)
    if invoice is None or invoice.org_id != (g.org.id if g.org else 0):
        flash('Invoice not found', 'error')
        return redirect(url_for('invoices.list_invoices'))

    lines = InvoiceLine.query.filter(invoice_id=invoice.id).all()
    lines.sort(key=lambda l: l.sort_order or 0)

    payments = Payment.query.filter(invoice_id=invoice.id).all()

    return render_template('invoices/detail.html',
        invoice=invoice,
        lines=lines,
        payments=payments,
    )


@invoices_bp.route('/<ref>/pdf')
@login_required
def invoice_pdf(ref):
    '''Download invoice PDF.'''
    from btpay.invoicing.models import Invoice, InvoiceLine
    from btpay.invoicing.pdf import generate_invoice_pdf

    invoice = Invoice.get_by(invoice_number=ref)
    if invoice is None or invoice.org_id != (g.org.id if g.org else 0):
        flash('Invoice not found', 'error')
        return redirect(url_for('invoices.list_invoices'))

    lines = InvoiceLine.query.filter(invoice_id=invoice.id).all()
    lines.sort(key=lambda l: l.sort_order or 0)

    org = g.org
    pdf_bytes = generate_invoice_pdf(invoice, org)

    from io import BytesIO
    buf = BytesIO(pdf_bytes)
    return send_file(buf, mimetype='application/pdf',
                     download_name='%s.pdf' % invoice.invoice_number)


@invoices_bp.route('/<ref>/finalize', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def finalize(ref):
    '''Finalize a draft invoice.'''
    from btpay.invoicing.models import Invoice
    from btpay.invoicing.service import InvoiceService
    from btpay.bitcoin.models import Wallet

    invoice = Invoice.get_by(invoice_number=ref)
    if invoice is None or invoice.org_id != (g.org.id if g.org else 0):
        flash('Invoice not found', 'error')
        return redirect(url_for('invoices.list_invoices'))

    if not invoice.is_draft:
        flash('Invoice is not a draft', 'error')
        return redirect(url_for('invoices.detail', ref=ref))

    # Find active wallet
    wallet = Wallet.query.filter(org_id=g.org.id, is_active=True).first()

    svc = InvoiceService(
        exchange_rate_service=getattr(current_app, '_exchange_rate_service', None),
        quote_deadline=current_app.config.get('BTC_QUOTE_DEADLINE', 30),
    )

    try:
        svc.finalize_invoice(invoice, wallet)
        flash('Invoice finalized', 'success')
    except Exception as e:
        flash(str(e), 'error')

    return redirect(url_for('invoices.detail', ref=ref))


@invoices_bp.route('/<ref>/cancel', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def cancel(ref):
    '''Cancel an invoice.'''
    from btpay.invoicing.models import Invoice
    from btpay.invoicing.service import InvoiceService

    invoice = Invoice.get_by(invoice_number=ref)
    if invoice is None or invoice.org_id != (g.org.id if g.org else 0):
        flash('Invoice not found', 'error')
        return redirect(url_for('invoices.list_invoices'))

    svc = InvoiceService()
    try:
        svc.cancel_invoice(invoice)
        flash('Invoice cancelled', 'success')
    except Exception as e:
        flash(str(e), 'error')

    return redirect(url_for('invoices.detail', ref=ref))


def _dec(s):
    '''Parse a string to Decimal. Rejects floats to prevent silent precision loss.'''
    if isinstance(s, float):
        raise TypeError("_dec() requires a string, not float (got %r)" % s)
    if isinstance(s, Decimal):
        return s
    try:
        return Decimal(str(s) if s else '0')
    except (InvalidOperation, ValueError):
        return Decimal('0')


def _available_payment_methods(org):
    '''Return list of available payment methods for this org.'''
    from btpay.bitcoin.models import Wallet

    methods = []

    # On-chain Bitcoin - available if org has an active wallet
    wallet = Wallet.query.filter(org_id=org.id, is_active=True).first()
    if wallet:
        methods.append(('onchain_btc', 'Bitcoin (on-chain)', True))

    # Wire Transfer
    from btpay.connectors.wire import WireConnector
    wc = WireConnector.query.filter(org_id=org.id, is_active=True).first()
    if wc:
        methods.append(('wire', 'Wire Transfer', False))

    # BTCPay Server
    from btpay.connectors.btcpay import BTCPayConnector
    bp = BTCPayConnector.query.filter(org_id=org.id, is_active=True).first()
    if bp:
        methods.append(('btcpay', 'BTCPay Server', False))

    # LNbits (Lightning)
    from btpay.connectors.lnbits import LNbitsConnector
    ln = LNbitsConnector.query.filter(org_id=org.id, is_active=True).first()
    if ln:
        methods.append(('lnbits', 'Lightning (LNbits)', False))

    # Stablecoins
    from btpay.connectors.stablecoins import StablecoinAccount
    accts = StablecoinAccount.query.filter(org_id=org.id, is_active=True).all()
    if accts:
        methods.append(('stablecoins', 'Stablecoins', False))

    return methods

# EOF
