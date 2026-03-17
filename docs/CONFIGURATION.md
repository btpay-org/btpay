# Configuration Reference

BTPay loads configuration in this order (later overrides earlier):

1. `config_default.py` — built-in defaults
2. `config.py` — your local overrides (git-ignored)
3. Environment variables prefixed with `BTPAY_`

## All Configuration Options

### Core

| Setting | Type | Default | Env Var | Description |
|---------|------|---------|---------|-------------|
| `SECRET_KEY` | string | `'CHANGE-ME-IN-PRODUCTION'` | `BTPAY_SECRET_KEY` | Flask secret key. **Must change in production.** |
| `DEV_MODE` | bool | `True` on macOS | — | Development mode. Enables debug toolbar, verbose logging. |
| `DATA_DIR` | path | `./data` | — | Directory for JSON data files and backups. |

### Security Keys

| Setting | Type | Default | Env Var | Description |
|---------|------|---------|---------|-------------|
| `REFNUM_KEY` | hex string | (dev default) | `BTPAY_REFNUM_KEY` | 32-byte hex key for NaCl SecretBox reference number encryption. |
| `REFNUM_NONCE` | hex string | (dev default) | `BTPAY_REFNUM_NONCE` | 24-byte hex nonce for NaCl SecretBox reference numbers. |
| `JWT_SECRETS` | dict | (dev defaults) | `BTPAY_JWT_ADMIN`, `BTPAY_JWT_LOGIN`, `BTPAY_JWT_API`, `BTPAY_JWT_INVITE` | Per-purpose JWT signing secrets. |

### Sessions

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `AUTH_COOKIE_NAME` | string | `'btpay_session'` | Name of the auth session cookie. |
| `SESSION_COOKIE_HOURS` | int | `720` (30 days) | Session lifetime in hours. |

### Rate Limiting

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `RATE_LIMIT_LOGIN` | dict | `max_attempts=5, window=60` | Login rate limit (attempts per window in seconds). |
| `RATE_LIMIT_API` | dict | `max_attempts=100, window=60` | API rate limit per key. |
| `RATE_LIMIT_CHECKOUT` | dict | `max_attempts=30, window=60` | Checkout page rate limit. |

### Bitcoin

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `BTC_QUOTE_DEADLINE` | int | `30` | Minutes to lock the BTC exchange rate for an invoice. |
| `BTC_MARKUP_PERCENT` | decimal | `0` | Percentage markup added to the exchange rate. |
| `MAX_UNDERPAID_GIFT` | decimal | `5` | USD threshold below which underpayment is accepted as paid. |
| `BTC_CONFIRMATION_THRESHOLDS` | list | `[(100,1), (1000,3), (None,6)]` | (max_usd, required_confirmations) pairs. `None` = any amount. |

### Exchange Rates

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `EXCHANGE_RATE_SOURCES` | list | `['coingecko', 'coinbase', 'kraken']` | APIs to fetch rates from. Options: `coingecko`, `coinbase`, `kraken`, `bitstamp`, `mempool`. |
| `EXCHANGE_RATE_INTERVAL` | int | `300` | Seconds between rate fetches. |
| `SUPPORTED_CURRENCIES` | list | `['USD','EUR','GBP','CAD','AUD','JPY','CHF']` | Fiat currencies to fetch rates for. |

### Network & Privacy

| Setting | Type | Default | Env Var | Description |
|---------|------|---------|---------|-------------|
| `SOCKS5_PROXY` | string | `''` | `BTPAY_SOCKS5_PROXY` | SOCKS5 proxy URL for Tor (e.g. `socks5h://127.0.0.1:9050`). |
| `MEMPOOL_API_URL` | string | `'https://mempool.space/api'` | `BTPAY_MEMPOOL_URL` | mempool.space API endpoint. Use your own instance for privacy. |
| `ELECTRUM_SERVERS` | list | `[{host: 'blockstream.info', port: 50002, ssl: True}]` | — | Electrum protocol servers for SPV verification. Configurable per-org in Settings > Electrum Server. |

### Email (SMTP)

| Setting | Type | Default | Env Var | Description |
|---------|------|---------|---------|-------------|
| `SMTP_CONFIG.host` | string | `''` | `BTPAY_SMTP_HOST` | SMTP server hostname. |
| `SMTP_CONFIG.port` | int | `587` | `BTPAY_SMTP_PORT` | SMTP port. Use 587 for STARTTLS, 465 for SSL. |
| `SMTP_CONFIG.username` | string | `''` | `BTPAY_SMTP_USER` | SMTP authentication username. |
| `SMTP_CONFIG.password` | string | `''` | `BTPAY_SMTP_PASS` | SMTP authentication password. |
| `SMTP_CONFIG.from_email` | string | `''` | `BTPAY_SMTP_FROM` | Sender email address. |
| `SMTP_CONFIG.from_name` | string | `'BTPay'` | `BTPAY_SMTP_FROM_NAME` | Sender display name. |

### Stablecoin RPC

Stablecoin payment monitoring uses public RPC endpoints by default (no API keys needed). Configure per-org in **Settings > Stablecoin RPC**.

| Setting | Description |
|---------|-------------|
| Provider | `public` (default), `alchemy`, `ankr`, or `custom` |
| Monitoring | Enable/disable automatic balance polling |
| Check interval | Seconds between balance checks (default: 60, min: 15) |

Supported chains: Ethereum, Arbitrum, Base, Polygon, Optimism, Avalanche, Tron, Solana.
Supported tokens: USDC, USDT, DAI, PYUSD.

### Webhooks

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `WEBHOOK_RETRY_DELAYS` | list | `[60, 300, 900, 3600, 7200]` | Seconds between retry attempts for failed webhook deliveries. |

### Data Persistence

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `AUTOSAVE_INTERVAL` | int | `60` | Seconds between automatic data saves to disk. |
| `BACKUP_INTERVAL` | int | `3600` | Seconds between automatic backup rotations. |
| `BACKUP_KEEP` | int | `5` | Number of backup copies to retain. |

### Software Updates

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `UPDATE_REPO` | string | `'btpay-org/btpay'` | GitHub repository for update checks. |
| `UPDATE_ALLOWED` | bool | `True` | Set to `False` to disable self-update from UI and CLI. |

## Example config.py

```python
# Production configuration
SECRET_KEY = 'a1b2c3d4e5f6...'  # 64 hex chars
DEV_MODE = False

REFNUM_KEY = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4'
REFNUM_NONCE = 'f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1'

JWT_SECRETS = {
    'admin':  'secret-admin-key-here',
    'login':  'secret-login-key-here',
    'api':    'secret-api-key-here',
    'invite': 'secret-invite-key-here',
}

# Bitcoin
BTC_QUOTE_DEADLINE = 15          # Lock rate for 15 minutes
BTC_MARKUP_PERCENT = 1           # 1% markup on exchange rate
EXCHANGE_RATE_SOURCES = ['coingecko', 'coinbase', 'kraken', 'bitstamp']

# Privacy — route through Tor
SOCKS5_PROXY = 'socks5h://127.0.0.1:9050'
MEMPOOL_API_URL = 'http://your-mempool-instance.onion/api'

# Email
SMTP_CONFIG = {
    'host': 'smtp.mailgun.org',
    'port': 587,
    'username': 'postmaster@mg.yourdomain.com',
    'password': 'your-mailgun-password',
    'from_email': 'payments@yourdomain.com',
    'from_name': 'Your Business Name',
}

# Data
AUTOSAVE_INTERVAL = 30           # Save more frequently
BACKUP_KEEP = 10                 # Keep more backups
```

## Environment Variables

Every setting can be overridden via environment variables prefixed with `BTPAY_`. Examples:

```bash
export BTPAY_SECRET_KEY="your-secret-key"
export BTPAY_SOCKS5_PROXY="socks5h://127.0.0.1:9050"
export BTPAY_MEMPOOL_URL="https://your-mempool.com/api"
export BTPAY_SMTP_HOST="smtp.example.com"
export BTPAY_SMTP_PORT="587"
export BTPAY_SMTP_USER="user"
export BTPAY_SMTP_PASS="password"
export BTPAY_SMTP_FROM="payments@example.com"
```
