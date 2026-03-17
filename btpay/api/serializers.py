#
# Model-to-JSON serializers
#
# Converts ORM model instances to JSON-safe dicts.
# Handles Decimal→string, datetime→ISO format.
#
from decimal import Decimal
import datetime


def _serialize_value(v):
    '''Convert a single value to JSON-safe type.'''
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime.datetime):
        return v.isoformat() if v.year > 1970 else None
    if isinstance(v, (list, tuple, set)):
        return list(v)
    if isinstance(v, dict):
        return {k: _serialize_value(val) for k, val in v.items()}
    return v


def serialize_invoice(invoice, include_lines=True, include_payments=False):
    '''Serialize an Invoice to a JSON-safe dict.'''
    data = {
        'id': invoice.id,
        'ref': _get_ref(invoice),
        'invoice_number': invoice.invoice_number,
        'status': invoice.status,
        'customer_email': invoice.customer_email,
        'customer_name': invoice.customer_name,
        'customer_company': invoice.customer_company,
        'currency': invoice.currency,
        'subtotal': str(invoice.subtotal),
        'tax_amount': str(invoice.tax_amount),
        'tax_rate': str(invoice.tax_rate),
        'discount_amount': str(invoice.discount_amount),
        'total': str(invoice.total),
        'amount_paid': str(invoice.amount_paid),
        'amount_due': str(invoice.amount_due),
        'btc_rate': str(invoice.btc_rate) if invoice.btc_rate else None,
        'btc_amount': str(invoice.btc_amount) if invoice.btc_amount else None,
        'payment_methods': list(invoice.payment_methods_enabled or []),
        'ref_number': invoice.ref_number,
        'notes': invoice.notes,
        'created_at': _serialize_value(invoice.created_at),
        'due_date': _serialize_value(invoice.due_date) if invoice.due_date else None,
        'paid_at': _serialize_value(invoice.paid_at) if invoice.paid_at else None,
        'confirmed_at': _serialize_value(invoice.confirmed_at) if invoice.confirmed_at else None,
    }

    if invoice.payment_address:
        data['payment_address'] = invoice.payment_address.address
    else:
        data['payment_address'] = None

    if include_lines:
        data['lines'] = [serialize_invoice_line(l) for l in invoice.lines]

    if include_payments:
        data['payments'] = [serialize_payment(p) for p in invoice.payments]

    return data


def serialize_invoice_line(line):
    '''Serialize an InvoiceLine.'''
    return {
        'id': line.id,
        'description': line.description,
        'quantity': str(line.quantity),
        'unit_price': str(line.unit_price),
        'amount': str(line.amount),
        'sort_order': line.sort_order,
    }


def serialize_payment(payment):
    '''Serialize a Payment.'''
    return {
        'id': payment.id,
        'method': payment.method,
        'txid': payment.txid,
        'address': payment.address,
        'amount_btc': str(payment.amount_btc),
        'amount_fiat': str(payment.amount_fiat),
        'exchange_rate': str(payment.exchange_rate),
        'confirmations': payment.confirmations,
        'status': payment.status,
        'created_at': _serialize_value(payment.created_at),
    }


def serialize_payment_link(pl):
    '''Serialize a PaymentLink.'''
    return {
        'id': pl.id,
        'ref': _get_ref(pl),
        'slug': pl.slug,
        'title': pl.title,
        'description': pl.description,
        'amount': str(pl.amount) if pl.amount else None,
        'currency': pl.currency,
        'is_active': pl.is_active,
        'payment_methods': list(pl.payment_methods_enabled or []),
        'redirect_url': pl.redirect_url,
        'created_at': _serialize_value(pl.created_at),
    }


def serialize_rate(currency, rate):
    '''Serialize an exchange rate.'''
    return {
        'currency': currency,
        'rate': str(rate),
    }


def _get_ref(instance):
    '''Get reference number for a model instance, or None.'''
    try:
        from btpay.security.refnums import ReferenceNumbers
        return ReferenceNumbers().pack(instance)
    except Exception:
        return None

# EOF
