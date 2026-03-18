#
# BTPay — Default Configuration
#
# Override in config.py (gitignored) or via BTPAY_* environment variables.
#
import os, enum, secrets
from btpay.dictobj import DictObj

# ---- Paths ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('BTPAY_DATA_DIR') or os.path.join(BASE_DIR, 'data')

# ---- Environment ----
import sys
DEV_MODE = ('darwin' in sys.platform)
IS_PRODUCTION = not DEV_MODE
DEMO_MODE = os.environ.get('BTPAY_DEMO', '').lower() in ('1', 'true', 'yes')

# ---- Security Keys ----
# Auto-generated at startup if not set via env vars or config.py.
# NOTE: auto-generated keys change on every restart — sessions and encrypted
# reference numbers will not survive restarts. For persistent keys, set
# BTPAY_* env vars or create config.py.
SECRET_KEY = os.environ.get('BTPAY_SECRET_KEY') or secrets.token_hex(32)

JWT_SECRETS = {
    'admin':   os.environ.get('BTPAY_JWT_ADMIN') or secrets.token_hex(32),
    'login':   os.environ.get('BTPAY_JWT_LOGIN') or secrets.token_hex(32),
    'api':     os.environ.get('BTPAY_JWT_API') or secrets.token_hex(32),
    'invite':  os.environ.get('BTPAY_JWT_INVITE') or secrets.token_hex(32),
}

# NaCl SecretBox key and nonce for reference numbers
REFNUM_KEY = os.environ.get('BTPAY_REFNUM_KEY') or secrets.token_hex(32)
REFNUM_NONCE = os.environ.get('BTPAY_REFNUM_NONCE') or secrets.token_hex(24)

# ---- Session ----
# Note: Flask uses SESSION_COOKIE_NAME for its built-in session cookie.
# Our custom auth cookie uses a different config key to avoid collision.
AUTH_COOKIE_NAME = 'btpay_session'
SESSION_COOKIE_HOURS = 720      # 30 days

# ---- Rate Limiting ----
RATE_LIMIT_LOGIN = DictObj(max_attempts=5, window_seconds=60)
RATE_LIMIT_API = DictObj(max_attempts=100, window_seconds=60)
RATE_LIMIT_CHECKOUT = DictObj(max_attempts=30, window_seconds=60)

# ---- Bitcoin ----
BTC_QUOTE_DEADLINE = 30             # minutes to lock BTC rate
BTC_MARKUP_PERCENT = 0              # markup on exchange rate (Decimal %)
MAX_UNDERPAID_GIFT = 5              # USD threshold to accept underpayment

# Confirmation thresholds (amount in satoshi -> required confirmations)
BTC_CONFIRMATION_THRESHOLDS = [
    (1_000_000, 1),       # < 0.01 BTC: 1 confirmation
    (10_000_000, 3),      # < 0.1 BTC: 3 confirmations
    (None, 6),            # >= 0.1 BTC: 6 confirmations
]

# ---- Exchange Rates ----
EXCHANGE_RATE_SOURCES = ['coingecko', 'coinbase', 'kraken']
EXCHANGE_RATE_INTERVAL = 300        # seconds between rate fetches
SUPPORTED_CURRENCIES = ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CHF']

# ---- SMTP ----
SMTP_CONFIG = DictObj(
    server=os.environ.get('BTPAY_SMTP_SERVER', ''),
    port=int(os.environ.get('BTPAY_SMTP_PORT', '587')),
    username=os.environ.get('BTPAY_SMTP_USER', ''),
    password=os.environ.get('BTPAY_SMTP_PASS', ''),
    from_address=os.environ.get('BTPAY_SMTP_FROM', 'noreply@localhost'),
    use_tls=True,
)

# ---- Webhooks ----
WEBHOOK_RETRY_DELAYS = [60, 300, 900, 3600, 7200]  # seconds between retries

# ---- Persistence ----
AUTOSAVE_INTERVAL = 60              # seconds between auto-saves
BACKUP_INTERVAL = 3600              # seconds between backups
BACKUP_KEEP = 5                     # number of backups to retain

# ---- Updates ----
UPDATE_REPO = 'btpay-org/btpay'
# Auto-disable in-app updates on managed platforms (Render, Railway, Heroku)
# where deploys are handled externally. Can be overridden via env var.
_MANAGED_PLATFORM = any(os.environ.get(v) for v in ('RENDER', 'RAILWAY_ENVIRONMENT', 'DYNO'))
UPDATE_ALLOWED = os.environ.get('BTPAY_UPDATE_ALLOWED', '' if _MANAGED_PLATFORM else '1').lower() in ('1', 'true', 'yes')

# ---- Reverse Proxy ----
# Number of trusted reverse proxies in front of the app.
# Set to 1 if behind nginx/Caddy, 2 if behind Cloudflare + nginx, etc.
# 0 = direct connection (no proxy).
NUM_PROXIES = int(os.environ.get('BTPAY_NUM_PROXIES', '0'))

# ---- Network / Privacy ----
SOCKS5_PROXY = os.environ.get('BTPAY_SOCKS5_PROXY', '')  # e.g. socks5h://127.0.0.1:9050
MEMPOOL_API_URL = os.environ.get('BTPAY_MEMPOOL_URL', 'https://mempool.space/api')

# ---- Electrum ----
ELECTRUM_SERVERS = [
    DictObj(host='electrum.blockstream.info', port=50002, ssl=True),
]

# ---- Enums ----
class OrgRole(enum.Enum):
    OWNER = 'owner'
    ADMIN = 'admin'
    VIEWER = 'viewer'

class InvoiceStatus(enum.Enum):
    DRAFT = 'draft'
    PENDING = 'pending'
    PARTIAL = 'partial'
    PAID = 'paid'
    CONFIRMED = 'confirmed'
    EXPIRED = 'expired'
    CANCELLED = 'cancelled'

class WalletType(enum.Enum):
    XPUB = 'xpub'
    DESCRIPTOR = 'descriptor'
    ADDRESS_LIST = 'address_list'

class PaymentMethodType(enum.Enum):
    ONCHAIN_BTC = 'onchain_btc'
    WIRE = 'wire'
    BTCPAY = 'btcpay'
    LNBITS = 'lnbits'
    # Stablecoin methods are dynamic: 'stable_ethereum_usdc', etc.

class ConnectorType(enum.Enum):
    BITCOIN = 'bitcoin'
    WIRE = 'wire'
    STABLECOIN = 'stablecoin'
    BTCPAY = 'btcpay'
    LNBITS = 'lnbits'

# EOF
