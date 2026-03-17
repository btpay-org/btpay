#
# Bitcoin math helpers
#
from decimal import Decimal

ONE_BTC = 100_000_000
DEC_ONE_BTC = Decimal('100000000')
ONE_SATOSHI = Decimal('0.00000001')

def satoshi2coins(n):
    "Convert satoshis (int) to BTC (Decimal)"
    return Decimal(int(n)) / DEC_ONE_BTC

def coins2satoshi(c):
    "Convert BTC (Decimal or float) to satoshis (int)"
    return int(Decimal(str(c)) * DEC_ONE_BTC)

def round_satoshi(a):
    "Round a Decimal BTC amount to satoshi precision"
    return Decimal(str(a)).quantize(ONE_SATOSHI)

# EOF
