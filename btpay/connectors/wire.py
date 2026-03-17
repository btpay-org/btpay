#
# Wire transfer connector — bank details for wire/ACH/SEPA payments
#
from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import Text, Integer, Boolean


class WireConnector(BaseMixin, MemModel):
    '''
    Stores wire transfer banking details for an organization.
    At checkout, these fields are displayed as text to the payer.
    '''
    org_id          = Integer(index=True)
    name            = Text(default='Wire Transfer')
    is_active       = Boolean(default=True)

    # Bank details — merchant fills what applies
    bank_name       = Text()
    account_name    = Text()        # beneficiary name
    account_number  = Text()        # domestic account number
    routing_number  = Text()        # ABA (US) / Sort code (UK) / Transit (CA)
    swift_code      = Text()        # SWIFT/BIC for international wires
    iban            = Text()        # IBAN (EU/international)
    bank_address    = Text()        # physical address of bank branch
    currency        = Text(default='USD')
    notes           = Text()        # displayed to payer (e.g. "Include invoice # in memo")


def validate_wire_connector(wc):
    '''
    Validate a WireConnector has minimum required fields.
    Returns (valid, errors_list).
    '''
    errors = []
    if not wc.bank_name:
        errors.append('Bank name is required')
    if not wc.account_name:
        errors.append('Beneficiary / account name is required')
    if not wc.account_number and not wc.iban:
        errors.append('Account number or IBAN is required')
    return len(errors) == 0, errors


def wire_payment_info(wc, invoice):
    '''
    Build payment info dict for checkout display.
    '''
    return {
        'bank_name': wc.bank_name or '',
        'account_name': wc.account_name or '',
        'account_number': wc.account_number or '',
        'routing_number': wc.routing_number or '',
        'swift_code': wc.swift_code or '',
        'iban': wc.iban or '',
        'bank_address': wc.bank_address or '',
        'currency': wc.currency or invoice.currency,
        'notes': wc.notes or '',
        'reference': invoice.invoice_number,
        'amount': str(invoice.total),
        'invoice_currency': invoice.currency,
    }

# EOF
