#
# Jinja2 template filters
#
from decimal import Decimal
import datetime
from markupsafe import Markup
from btpay.chrono import NOW, as_time_t, from_time_t


def btc_format(value, precision=8):
    '''Format a BTC amount. btc_format(0.00123456) -> "0.00123456"'''
    if value is None or value == 0:
        return '0'
    d = Decimal(str(value))
    # Remove trailing zeros but keep at least 1 decimal
    fmt = d.quantize(Decimal(10) ** -precision).normalize()
    s = str(fmt)
    if '.' not in s:
        s += '.0'
    return s


def currency_format(value, currency='USD'):
    '''Format a fiat amount with currency symbol.'''
    if value is None:
        return '0.00'
    symbols = {
        'USD': '$', 'EUR': '\u20ac', 'GBP': '\u00a3',
        'CAD': 'C$', 'AUD': 'A$', 'JPY': '\u00a5', 'CHF': 'CHF ',
    }
    sym = symbols.get(currency, currency + ' ')
    return '%s%s' % (sym, Decimal(str(value)).quantize(Decimal('0.01')))


def time_ago(value):
    '''Format a timestamp as a human-readable time ago string.'''
    if not value or value == 0:
        return ''

    if isinstance(value, (int, float)):
        if value <= 0:
            return ''
        ts = value
    elif isinstance(value, datetime.datetime):
        ts = as_time_t(value)
    else:
        return str(value)

    now_ts = as_time_t(NOW())
    diff = int(now_ts - ts)

    if diff < 0:
        return 'just now'
    if diff < 60:
        return '%ds ago' % diff
    if diff < 3600:
        m = diff // 60
        return '%dm ago' % m
    if diff < 86400:
        h = diff // 3600
        return '%dh ago' % h
    if diff < 604800:
        d = diff // 86400
        return '%dd ago' % d
    if diff < 2592000:
        w = diff // 604800
        return '%dw ago' % w

    dt = from_time_t(ts)
    return dt.strftime('%b %d, %Y')


def status_badge(status):
    '''Return HTML for a colored status badge.'''
    colors = {
        'draft':     'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300',
        'pending':   'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300',
        'partial':   'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-300',
        'paid':      'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300',
        'confirmed': 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300',
        'expired':   'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300',
        'cancelled': 'bg-red-100 text-red-600 dark:bg-red-900 dark:text-red-400',
        # Payment statuses
        'active':    'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300',
        'inactive':  'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
    }
    css = colors.get(status, 'bg-gray-100 text-gray-700')
    return Markup('<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium %s">%s</span>' % (css, status.capitalize()))


def truncate_address(address, start=8, end=6):
    '''Truncate a BTC address for display.'''
    if not address:
        return ''
    if len(address) <= start + end + 3:
        return address
    return '%s...%s' % (address[:start], address[-end:])


def satoshi_format(sats):
    '''Format satoshis with comma separators.'''
    if sats is None:
        return '0'
    return '{:,}'.format(int(sats))


METHOD_LABELS = {
    'onchain_btc': 'Bitcoin',
    'wire': 'Wire',
    'btcpay': 'BTCPay',
    'lnbits': 'Lightning',
    'stablecoins': 'Stablecoin',
    'stable_ethereum_usdc': 'USDC (ETH)',
    'stable_arbitrum_usdc': 'USDC (Arb)',
    'stable_base_usdc': 'USDC (Base)',
    'stable_tron_usdt': 'USDT (Tron)',
    'stable_ethereum_usdt': 'USDT (ETH)',
    'stable_ethereum_dai': 'DAI (ETH)',
    'stable_solana_usdc': 'USDC (Sol)',
}


def method_label(value):
    '''Convert a payment method key to a human-readable label.'''
    if not value:
        return '—'
    return METHOD_LABELS.get(value, value)


# Preferred display order for payment methods
_METHOD_PRIORITY = list(METHOD_LABELS.keys())


def primary_method(methods):
    '''Pick the most relevant method from a set/list for display.'''
    if not methods:
        return ''
    for m in _METHOD_PRIORITY:
        if m in methods:
            return m
    return next(iter(methods))


def register_filters(app):
    '''Register all custom Jinja2 filters with the Flask app.'''
    app.jinja_env.filters['btc'] = btc_format
    app.jinja_env.filters['currency'] = currency_format
    app.jinja_env.filters['time_ago'] = time_ago
    app.jinja_env.filters['status_badge'] = status_badge
    app.jinja_env.filters['truncate_address'] = truncate_address
    app.jinja_env.filters['satoshi'] = satoshi_format
    app.jinja_env.filters['method_label'] = method_label
    app.jinja_env.filters['primary_method'] = primary_method

# EOF
