#
# Email templates — HTML email rendering
#
# Simple string-based templates with org branding.
# No Jinja2 dependency for email (keep it simple).
#
from decimal import Decimal


def render_invoice_created(invoice, org, checkout_url=''):
    '''Render invoice created email HTML.'''
    brand = _brand_colors(org)

    lines_html = ''
    for line in (invoice.lines or []):
        lines_html += '''
        <tr>
            <td style="padding: 8px 12px; border-bottom: 1px solid #eee;">%s</td>
            <td style="padding: 8px 12px; border-bottom: 1px solid #eee; text-align: right;">%s</td>
            <td style="padding: 8px 12px; border-bottom: 1px solid #eee; text-align: right;">%s</td>
        </tr>''' % (
            _esc(line.description),
            _fmt_qty(line.quantity),
            _fmt_money(line.amount, invoice.currency),
        )

    pay_button = ''
    if checkout_url:
        pay_button = '''
        <div style="text-align: center; margin: 24px 0;">
            <a href="%s" style="display: inline-block; padding: 12px 32px;
               background: %s; color: #ffffff; text-decoration: none;
               border-radius: 6px; font-weight: bold; font-size: 16px;">
                Pay Now
            </a>
        </div>''' % (_esc(checkout_url), brand['primary'])

    return _wrap_email(org, '''
        <h2 style="color: %s; margin: 0 0 4px;">Invoice %s</h2>
        <p style="color: #666; margin: 0 0 20px;">from %s</p>

        %s

        <table style="width: 100%%; border-collapse: collapse; margin: 20px 0;">
            <thead>
                <tr style="background: %s;">
                    <th style="padding: 10px 12px; text-align: left; color: #fff;">Description</th>
                    <th style="padding: 10px 12px; text-align: right; color: #fff;">Qty</th>
                    <th style="padding: 10px 12px; text-align: right; color: #fff;">Amount</th>
                </tr>
            </thead>
            <tbody>
                %s
            </tbody>
        </table>

        <table style="width: 100%%; margin: 12px 0;">
            <tr>
                <td style="text-align: right; padding: 4px 12px; font-weight: bold; font-size: 18px;">
                    Total: %s
                </td>
            </tr>
        </table>

        %s

        <p style="color: #888; font-size: 13px; margin-top: 24px;">
            Invoice Number: %s<br>
            Currency: %s
        </p>
    ''' % (
        brand['primary'],
        _esc(invoice.invoice_number),
        _esc(org.name),
        _customer_block(invoice),
        brand['primary'],
        lines_html,
        _fmt_money(invoice.total, invoice.currency),
        pay_button,
        _esc(invoice.invoice_number),
        _esc(invoice.currency),
    ))


def render_payment_received(invoice, payment, org):
    '''Render payment received email HTML.'''
    brand = _brand_colors(org)

    return _wrap_email(org, '''
        <h2 style="color: %s; margin: 0 0 4px;">Payment Received</h2>
        <p style="color: #666; margin: 0 0 20px;">for Invoice %s</p>

        <table style="width: 100%%; margin: 16px 0;">
            <tr>
                <td style="padding: 6px 0; color: #666;">Method:</td>
                <td style="padding: 6px 0; font-weight: bold;">%s</td>
            </tr>
            <tr>
                <td style="padding: 6px 0; color: #666;">Amount (BTC):</td>
                <td style="padding: 6px 0; font-weight: bold;">%s BTC</td>
            </tr>
            <tr>
                <td style="padding: 6px 0; color: #666;">Amount (Fiat):</td>
                <td style="padding: 6px 0; font-weight: bold;">%s</td>
            </tr>
            <tr>
                <td style="padding: 6px 0; color: #666;">Status:</td>
                <td style="padding: 6px 0; font-weight: bold;">%s</td>
            </tr>
        </table>

        <p style="color: #888; font-size: 13px;">
            Your payment is being processed. You will receive a confirmation
            once the transaction has been confirmed on the blockchain.
        </p>
    ''' % (
        brand['primary'],
        _esc(invoice.invoice_number),
        _esc(payment.method),
        payment.amount_btc,
        _fmt_money(payment.amount_fiat, invoice.currency),
        _esc(payment.status.upper()),
    ))


def render_payment_confirmed(invoice, payment, org):
    '''Render payment confirmed email HTML.'''
    brand = _brand_colors(org)

    return _wrap_email(org, '''
        <h2 style="color: %s; margin: 0 0 4px;">Payment Confirmed</h2>
        <p style="color: #666; margin: 0 0 20px;">for Invoice %s</p>

        <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px;
                    padding: 16px; text-align: center; margin: 20px 0;">
            <p style="font-size: 24px; margin: 0; color: #16a34a;">&#10003;</p>
            <p style="font-size: 16px; font-weight: bold; margin: 8px 0 0; color: #166534;">
                Payment Complete
            </p>
        </div>

        <table style="width: 100%%; margin: 16px 0;">
            <tr>
                <td style="padding: 6px 0; color: #666;">Invoice:</td>
                <td style="padding: 6px 0; font-weight: bold;">%s</td>
            </tr>
            <tr>
                <td style="padding: 6px 0; color: #666;">Amount:</td>
                <td style="padding: 6px 0; font-weight: bold;">%s BTC (%s)</td>
            </tr>
            <tr>
                <td style="padding: 6px 0; color: #666;">Confirmations:</td>
                <td style="padding: 6px 0; font-weight: bold;">%s</td>
            </tr>
        </table>

        <p style="color: #888; font-size: 13px;">
            Thank you for your payment. This email serves as your receipt.
        </p>
    ''' % (
        brand['primary'],
        _esc(invoice.invoice_number),
        _esc(invoice.invoice_number),
        payment.amount_btc,
        _fmt_money(payment.amount_fiat, invoice.currency),
        payment.confirmations or 0,
    ))


# ---- Internal helpers ----

def _wrap_email(org, body_html):
    '''Wrap body in a standard email layout with org branding.'''
    brand = _brand_colors(org)
    org_name = _esc(getattr(org, 'name', 'BTPay'))

    return '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin: 0; padding: 0; background: #f4f4f5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
    <table width="100%%" cellpadding="0" cellspacing="0" style="background: #f4f4f5; padding: 24px 0;">
        <tr><td align="center">
            <table width="600" cellpadding="0" cellspacing="0" style="background: #ffffff; border-radius: 8px; overflow: hidden;">
                <!-- Header -->
                <tr>
                    <td style="background: %s; padding: 20px 24px;">
                        <h1 style="margin: 0; color: #ffffff; font-size: 20px;">%s</h1>
                    </td>
                </tr>
                <!-- Body -->
                <tr>
                    <td style="padding: 24px;">
                        %s
                    </td>
                </tr>
                <!-- Footer -->
                <tr>
                    <td style="padding: 16px 24px; background: #f9fafb; border-top: 1px solid #e5e7eb;">
                        <p style="margin: 0; color: #9ca3af; font-size: 12px; text-align: center;">
                            Sent by %s &mdash; Powered by BTPay
                        </p>
                    </td>
                </tr>
            </table>
        </td></tr>
    </table>
</body>
</html>''' % (brand['primary'], org_name, body_html, org_name)


def _customer_block(invoice):
    '''Build customer info block if we have customer details.'''
    parts = []
    if getattr(invoice, 'customer_name', ''):
        parts.append(_esc(invoice.customer_name))
    if getattr(invoice, 'customer_company', ''):
        parts.append(_esc(invoice.customer_company))
    if getattr(invoice, 'customer_email', ''):
        parts.append(_esc(invoice.customer_email))

    if not parts:
        return ''

    return '<p style="color: #666; margin: 0 0 8px;">Bill To: %s</p>' % '<br>'.join(parts)


def _brand_colors(org):
    '''Extract brand colors from org, with defaults.'''
    return {
        'primary': getattr(org, 'brand_color', None) or '#F89F1B',
        'accent': getattr(org, 'brand_accent_color', None) or '#3B3A3C',
    }


def _esc(s):
    '''HTML-escape a string.'''
    if s is None:
        return ''
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _fmt_money(amount, currency='USD'):
    '''Format amount as currency string.'''
    if amount is None:
        return '0.00'
    symbols = {'USD': '$', 'EUR': '\u20ac', 'GBP': '\u00a3', 'CAD': 'C$', 'AUD': 'A$',
               'JPY': '\u00a5', 'CHF': 'CHF '}
    sym = symbols.get(currency, currency + ' ')
    if currency == 'JPY':
        quantized = Decimal(str(amount)).quantize(Decimal('1'))
    else:
        quantized = Decimal(str(amount)).quantize(Decimal('0.01'))
    return '%s%s' % (sym, quantized)


def _fmt_qty(qty):
    '''Format quantity.'''
    if qty is None:
        return '1'
    if qty == int(qty):
        return str(int(qty))
    return str(qty)

# EOF
