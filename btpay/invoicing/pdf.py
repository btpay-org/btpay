#
# PDF invoice/receipt generation via reportlab
#
import io
import logging
from decimal import Decimal

log = logging.getLogger(__name__)


def generate_invoice_pdf(invoice, org):
    '''
    Generate a PDF invoice.
    Returns PDF bytes.
    '''
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
    except ImportError:
        raise RuntimeError('reportlab not installed. Run: pip install reportlab')

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    elements = []

    # Header — org name
    brand_color = colors.HexColor(org.brand_color or '#F89F1B')
    title_style = styles['Title']
    elements.append(Paragraph(org.name or 'Invoice', title_style))
    elements.append(Spacer(1, 12))

    # Invoice details
    details = [
        ['Invoice Number:', invoice.invoice_number],
        ['Date:', str(invoice.created_at) if invoice.created_at else ''],
        ['Status:', invoice.status.upper()],
        ['Currency:', invoice.currency],
    ]
    if invoice.due_date:
        details.append(['Due Date:', str(invoice.due_date)])

    detail_table = Table(details, colWidths=[2*inch, 4*inch])
    detail_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(detail_table)
    elements.append(Spacer(1, 18))

    # Customer info
    if invoice.customer_name or invoice.customer_email:
        elements.append(Paragraph('Bill To:', styles['Heading3']))
        if invoice.customer_company:
            elements.append(Paragraph(invoice.customer_company, styles['Normal']))
        if invoice.customer_name:
            elements.append(Paragraph(invoice.customer_name, styles['Normal']))
        if invoice.customer_email:
            elements.append(Paragraph(invoice.customer_email, styles['Normal']))
        elements.append(Spacer(1, 12))

    # Line items table
    lines = invoice.lines
    table_data = [['Description', 'Qty', 'Unit Price', 'Amount']]
    for line in lines:
        table_data.append([
            line.description,
            _fmt_qty(line.quantity),
            _fmt_money(line.unit_price, invoice.currency),
            _fmt_money(line.amount, invoice.currency),
        ])

    line_table = Table(table_data, colWidths=[3.2*inch, 0.8*inch, 1.3*inch, 1.3*inch])
    line_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), brand_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(line_table)
    elements.append(Spacer(1, 12))

    # Totals
    totals = [
        ['Subtotal:', _fmt_money(invoice.subtotal, invoice.currency)],
    ]
    if invoice.tax_amount and invoice.tax_amount > 0:
        totals.append(['Tax (%s%%):' % invoice.tax_rate,
                       _fmt_money(invoice.tax_amount, invoice.currency)])
    if invoice.discount_amount and invoice.discount_amount > 0:
        totals.append(['Discount:', '-%s' % _fmt_money(invoice.discount_amount, invoice.currency)])
    totals.append(['Total:', _fmt_money(invoice.total, invoice.currency)])

    if invoice.btc_amount and invoice.btc_amount > 0:
        totals.append(['BTC Amount:', '%s BTC' % invoice.btc_amount])
        totals.append(['Exchange Rate:', '1 BTC = %s %s' % (
            invoice.btc_rate, invoice.currency)])

    total_table = Table(totals, colWidths=[4.7*inch, 1.9*inch])
    total_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
    ]))
    elements.append(total_table)

    # Payment address
    if invoice.payment_address and invoice.btc_amount:
        elements.append(Spacer(1, 18))
        elements.append(Paragraph('Payment Address:', styles['Heading3']))
        elements.append(Paragraph(invoice.payment_address.address, styles['Normal']))

    # Notes
    if invoice.notes:
        elements.append(Spacer(1, 18))
        elements.append(Paragraph('Notes:', styles['Heading3']))
        elements.append(Paragraph(invoice.notes, styles['Normal']))

    # Demo watermark
    from flask import current_app
    if current_app and current_app.config.get('DEMO_MODE'):
        elements.append(Spacer(1, 24))
        watermark_style = styles['Normal'].clone('watermark')
        watermark_style.textColor = colors.red
        watermark_style.fontSize = 14
        watermark_style.alignment = 1  # center
        elements.append(Paragraph('DEMO — NOT A REAL INVOICE', watermark_style))

    doc.build(elements)
    return buf.getvalue()


def generate_receipt_pdf(invoice, payment, org):
    '''
    Generate a payment receipt PDF.
    Returns PDF bytes.
    '''
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
    except ImportError:
        raise RuntimeError('reportlab not installed. Run: pip install reportlab')

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    elements = []

    brand_color = colors.HexColor(org.brand_color or '#F89F1B')

    elements.append(Paragraph('Payment Receipt', styles['Title']))
    elements.append(Spacer(1, 12))

    # Determine payment amount display based on method
    method = getattr(payment, 'method', '') or ''
    if method in ('onchain_btc', 'btcpay', 'lnbits'):
        amount_label = 'Amount (BTC):'
        amount_value = '%s BTC' % payment.amount_btc
    elif method == 'wire':
        amount_label = 'Amount:'
        amount_value = _fmt_money(payment.amount_fiat, invoice.currency)
    elif method.startswith('stable_'):
        amount_label = 'Amount:'
        amount_value = _fmt_money(payment.amount_fiat, invoice.currency)
    else:
        amount_label = 'Amount:'
        amount_value = '%s BTC' % payment.amount_btc

    details = [
        ['Invoice:', invoice.invoice_number],
        ['Date:', str(payment.created_at) if payment.created_at else ''],
        ['Payment Method:', method],
        [amount_label, amount_value],
        ['Fiat Equivalent:', _fmt_money(payment.amount_fiat, invoice.currency)],
        ['Status:', payment.status.upper()],
    ]
    if payment.txid:
        details.append(['Transaction ID:', payment.txid[:16] + '...' if len(payment.txid) > 16 else payment.txid])
    if payment.confirmations:
        details.append(['Confirmations:', str(payment.confirmations)])

    detail_table = Table(details, colWidths=[2*inch, 4.6*inch])
    detail_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(detail_table)

    elements.append(Spacer(1, 18))
    elements.append(Paragraph('Thank you for your payment.', styles['Normal']))

    doc.build(elements)
    return buf.getvalue()


def _fmt_money(amount, currency='USD'):
    '''Format a Decimal amount as currency string.'''
    if amount is None:
        return '0.00'
    symbols = {'USD': '$', 'EUR': '€', 'GBP': '£', 'CAD': 'C$', 'AUD': 'A$',
               'JPY': '¥', 'CHF': 'CHF '}
    sym = symbols.get(currency, currency + ' ')
    if currency == 'JPY':
        quantized = Decimal(str(amount)).quantize(Decimal('1'))
    else:
        quantized = Decimal(str(amount)).quantize(Decimal('0.01'))
    return '%s%s' % (sym, quantized)


def _fmt_qty(qty):
    '''Format quantity — drop decimals if whole number.'''
    if qty == int(qty):
        return str(int(qty))
    return str(qty)

# EOF
