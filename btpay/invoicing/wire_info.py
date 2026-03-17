#
# Wire transfer details module
#
# Helpers for displaying and validating wire transfer info.
#


def format_wire_info(wire_info, invoice_number=''):
    '''
    Format wire transfer details for display.
    wire_info: dict from Organization.wire_info JsonColumn.
    Returns formatted string.
    '''
    if not wire_info:
        return ''

    lines = []
    if wire_info.get('bank_name'):
        lines.append('Bank: %s' % wire_info['bank_name'])
    if wire_info.get('account_name'):
        lines.append('Account Name: %s' % wire_info['account_name'])
    if wire_info.get('account_number'):
        lines.append('Account Number: %s' % wire_info['account_number'])
    if wire_info.get('routing_number'):
        lines.append('Routing Number: %s' % wire_info['routing_number'])
    if wire_info.get('swift_code'):
        lines.append('SWIFT/BIC: %s' % wire_info['swift_code'])
    if wire_info.get('iban'):
        lines.append('IBAN: %s' % wire_info['iban'])
    if wire_info.get('bank_address'):
        lines.append('Bank Address: %s' % wire_info['bank_address'])
    if invoice_number:
        lines.append('Reference: %s' % invoice_number)

    return '\n'.join(lines)


def validate_wire_info(wire_info):
    '''
    Validate wire transfer details.
    Returns (valid, errors) tuple.
    '''
    errors = []

    if not wire_info:
        errors.append('Wire transfer info is empty')
        return False, errors

    if not wire_info.get('bank_name'):
        errors.append('Bank name is required')
    if not wire_info.get('account_name'):
        errors.append('Account name is required')
    if not wire_info.get('account_number') and not wire_info.get('iban'):
        errors.append('Account number or IBAN is required')

    return len(errors) == 0, errors

# EOF
