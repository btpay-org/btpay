# Payment Connectors — Implementation Plan

**Status**: Phase 8 (extends completed phases 1–7)

## Overview

Restructure the flat "Wallets" settings section into a **Payment Connectors** architecture. Each connector is a distinct payment method a merchant can configure. The checkout page lets the payer choose which connector to pay with.

```
Settings > Payment Connectors
├── Bitcoin Wallets          (existing — xpub/descriptor/address_list)
├── Wire Transfer            (new model — bank details displayed as text)
└── Stablecoins              (new model — per-chain token accounts)
```

---

## 8A — Payment Connectors Framework

**Goal**: Introduce a `PaymentConnector` base model and restructure the settings UI from a single "Wallets" tab into a "Payment Connectors" section with sub-tabs.

### Models

```
btpay/connectors/
├── __init__.py
├── models.py           # PaymentConnector base, connector registry
├── bitcoin.py          # BitcoinConnector (wraps existing Wallet model)
├── wire.py             # WireConnector model
└── stablecoins.py      # StablecoinConnector + StablecoinAccount models
```

**PaymentConnector** (abstract concept — NOT an ORM model; each type is its own model)

The connector types map 1:1 to `PaymentMethod` entries in the existing registry. A connector is the *merchant configuration* side; a payment method is the *checkout logic* side.

| Connector Type | PaymentMethod name | ORM Model |
|---|---|---|
| Bitcoin Wallet | `onchain_btc` | `Wallet` (existing) |
| Wire Transfer | `wire` | `WireConnector` (new) |
| Stablecoin Account | `stable_<chain>_<token>` | `StablecoinAccount` (new) |

### Settings UI

**`templates/settings/_nav.html`** — Replace `Wallets` with a `Payment Connectors` group:

```
Settings nav:
  General
  ▾ Payment Connectors    ← collapsible group header
      Bitcoin Wallets     → /settings/connectors/bitcoin
      Wire Transfer       → /settings/connectors/wire
      Stablecoins         → /settings/connectors/stablecoins
  Branding
  Team
  API Keys
  Webhooks
  Email
```

Each sub-tab is its own settings page. The existing `/settings/wallets` route becomes `/settings/connectors/bitcoin` (old URL redirects).

### Invoice & Checkout Changes

**Invoice model** — `payment_methods_enabled` TagsColumn already supports multiple methods. No schema change needed. During finalize, the invoice stores which connectors were enabled at that moment.

**Checkout page** — When an invoice has multiple payment methods enabled, the checkout page shows a **payment method selector** (tabs or dropdown) above the payment details area. Only one method's details shown at a time. The payer picks, the details render.

```
┌─────────────────────────────────┐
│ Invoice INV-0042                │
│ Amount Due: $500.00             │
├─────────────────────────────────┤
│ Pay with: [Bitcoin] [Wire] [USDC] │  ← method selector tabs
├─────────────────────────────────┤
│ (details for selected method)   │
│ QR code / address / bank info   │
└─────────────────────────────────┘
```

### Config

```python
# config_default.py additions
class ConnectorType(enum.Enum):
    BITCOIN = 'bitcoin'
    WIRE = 'wire'
    STABLECOIN = 'stablecoin'

class PaymentMethodType(enum.Enum):
    ONCHAIN_BTC = 'onchain_btc'
    WIRE = 'wire'
    # Stablecoin methods are dynamic: 'stable_ethereum_usdc', 'stable_tron_usdt', etc.
```

### Files to modify

| File | Change |
|---|---|
| `templates/settings/_nav.html` | Replace `Wallets` with connector group |
| `btpay/frontend/settings_views.py` | Add connector sub-routes, keep wallet logic |
| `config_default.py` | Add `ConnectorType` enum |
| `btpay/invoicing/payment_methods.py` | Register new methods |
| `templates/checkout/pay.html` | Add method selector tabs |
| `btpay/frontend/checkout_views.py` | Pass all enabled methods + their info |

---

## 8B — Wire Transfer Connector

**Goal**: Let the merchant configure wire/bank transfer details. At checkout, payer sees the banking info as plain text with a reference number to include in the transfer.

### Model: `WireConnector`

```python
# btpay/connectors/wire.py
class WireConnector(BaseMixin, MemModel):
    org_id          = Integer(index=True)
    name            = Text(default='Wire Transfer')
    is_active       = Boolean(default=True)

    # Bank details — all optional, merchant fills what applies
    bank_name       = Text()
    account_name    = Text()       # beneficiary name
    account_number  = Text()       # domestic account number
    routing_number  = Text()       # ABA routing (US) / Sort code (UK) / Transit+Institution (CA)
    swift_code      = Text()       # SWIFT/BIC for international
    iban            = Text()       # IBAN (EU/international)
    bank_address    = Text()       # physical address of bank
    currency        = Text(default='USD')   # currency the account expects
    notes           = Text()       # e.g. "Include invoice number in memo"
```

This replaces the existing `Organization.wire_info` JsonColumn with a proper model (migration: read org.wire_info → create WireConnector row → clear org.wire_info).

### Settings page: `/settings/connectors/wire`

```
┌─────────────────────────────────────┐
│ Wire Transfer                       │
├─────────────────────────────────────┤
│ Bank Name:        [________________]│
│ Beneficiary Name: [________________]│
│ Account Number:   [________________]│
│ Routing Number:   [________________]│
│ SWIFT/BIC:        [________________]│
│ IBAN:             [________________]│
│ Bank Address:     [________________]│
│ Currency:         [USD ▼          ] │
│ Notes to Payer:   [________________]│
│                                     │
│ [Save]                              │
└─────────────────────────────────────┘
```

Validation: at least `bank_name` + (`account_number` or `iban`) required.

### Checkout display

When payer selects "Wire Transfer" tab:

```
┌─────────────────────────────────┐
│ Wire Transfer Details           │
├─────────────────────────────────┤
│ Bank:           First National  │
│ Beneficiary:    Acme Corp       │
│ Account:        ****4567        │
│ Routing:        021000021       │
│ SWIFT:          FNBOUS33        │
│ IBAN:           —               │
│                                 │
│ Amount:         $500.00 USD     │
│ Reference:      INV-0042        │
│                                 │
│ ⓘ Include the reference number │
│   in your wire transfer memo.   │
│                                 │
│ [Copy Details]                  │
└─────────────────────────────────┘
```

No QR code, no countdown timer. Wire payments are manually marked as received by the merchant in the admin.

### Payment tracking

Wire payments are **manually reconciled**. The admin invoice detail page gets a "Record Wire Payment" button that marks the invoice as paid with method=`wire`.

---

## 8C — Stablecoin Connector

**Goal**: Let the merchant configure one or more stablecoin receiving accounts across chains. At checkout, payer selects token+chain from the enabled accounts and sees the address.

### Model: `StablecoinAccount`

```python
# btpay/connectors/stablecoins.py

# Known chains with address formats and block times
SUPPORTED_CHAINS = {
    'ethereum':  {'name': 'Ethereum',   'addr_type': 'evm',    'explorer': 'https://etherscan.io/address/'},
    'arbitrum':  {'name': 'Arbitrum',   'addr_type': 'evm',    'explorer': 'https://arbiscan.io/address/'},
    'base':      {'name': 'Base',       'addr_type': 'evm',    'explorer': 'https://basescan.org/address/'},
    'polygon':   {'name': 'Polygon',    'addr_type': 'evm',    'explorer': 'https://polygonscan.com/address/'},
    'optimism':  {'name': 'Optimism',   'addr_type': 'evm',    'explorer': 'https://optimistic.etherscan.io/address/'},
    'avalanche': {'name': 'Avalanche',  'addr_type': 'evm',    'explorer': 'https://snowtrace.io/address/'},
    'tron':      {'name': 'Tron',       'addr_type': 'base58', 'explorer': 'https://tronscan.org/#/address/'},
    'solana':    {'name': 'Solana',     'addr_type': 'base58', 'explorer': 'https://solscan.io/account/'},
}

SUPPORTED_TOKENS = {
    'usdt':  {'name': 'Tether',   'symbol': 'USDT', 'decimals': 6},
    'usdc':  {'name': 'USD Coin', 'symbol': 'USDC', 'decimals': 6},
    'dai':   {'name': 'Dai',      'symbol': 'DAI',  'decimals': 18},
    'pyusd': {'name': 'PayPal USD','symbol': 'PYUSD','decimals': 6},
}

class StablecoinAccount(BaseMixin, MemModel):
    org_id      = Integer(index=True)
    chain       = Text(required=True)       # key from SUPPORTED_CHAINS
    token       = Text(required=True)       # key from SUPPORTED_TOKENS
    address     = Text(required=True)       # receiving address (hex or base58)
    label       = Text()                    # auto-generated if blank: "USDC on Ethereum"
    is_active   = Boolean(default=True)

    @property
    def display_label(self):
        '''Human label like "USDC on Ethereum" or custom label.'''
        if self.label:
            return self.label
        token_info = SUPPORTED_TOKENS.get(self.token, {})
        chain_info = SUPPORTED_CHAINS.get(self.chain, {})
        return '%s on %s' % (
            token_info.get('symbol', self.token.upper()),
            chain_info.get('name', self.chain.title()),
        )

    @property
    def short_address(self):
        '''Truncated address for display: 0x1234...abcd'''
        if len(self.address) > 16:
            return '%s...%s' % (self.address[:6], self.address[-4:])
        return self.address

    @property
    def method_name(self):
        '''PaymentMethod registry key: stable_ethereum_usdc'''
        return 'stable_%s_%s' % (self.chain, self.token)

    @property
    def explorer_url(self):
        chain_info = SUPPORTED_CHAINS.get(self.chain, {})
        base = chain_info.get('explorer', '')
        return base + self.address if base else ''
```

### Address validation

```python
# btpay/connectors/stablecoins.py

def validate_stablecoin_address(address, chain):
    '''Validate address format for the given chain. Returns (valid, error).'''
    chain_info = SUPPORTED_CHAINS.get(chain)
    if not chain_info:
        return False, 'Unsupported chain: %s' % chain

    addr_type = chain_info['addr_type']

    if addr_type == 'evm':
        # Must be 0x + 40 hex chars
        if not re.match(r'^0x[0-9a-fA-F]{40}$', address):
            return False, 'Invalid EVM address format (expected 0x + 40 hex characters)'
        # EIP-55 checksum validation (warn, don't reject — some wallets don't checksum)
        if address != address.lower() and address != address.upper():
            if not _eip55_valid(address):
                return False, 'Invalid EIP-55 checksum'
        return True, ''

    elif addr_type == 'base58':
        if chain == 'tron':
            # Tron: starts with T, 34 chars, base58check
            if not address.startswith('T') or len(address) != 34:
                return False, 'Invalid Tron address (expected T + 33 base58 characters)'
            # base58check validation
            try:
                _base58check_decode(address)
                return True, ''
            except ValueError as e:
                return False, str(e)
        elif chain == 'solana':
            # Solana: base58, 32-44 chars, decodes to 32 bytes
            if not re.match(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$', address):
                return False, 'Invalid Solana address format'
            return True, ''

    return False, 'Unknown address type for chain %s' % chain


def _eip55_valid(address):
    '''Validate EIP-55 mixed-case checksum.'''
    import hashlib
    addr = address[2:]  # strip 0x
    addr_lower = addr.lower()
    h = hashlib.sha3_256(addr_lower.encode()).hexdigest()  # keccak-256
    for i, c in enumerate(addr):
        if c in '0123456789':
            continue
        expected_upper = int(h[i], 16) >= 8
        if expected_upper and c != c.upper():
            return False
        if not expected_upper and c != c.lower():
            return False
    return True
```

### Settings page: `/settings/connectors/stablecoins`

```
┌──────────────────────────────────────────────────────────────┐
│ Stablecoin Accounts                                         │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ ● USDC on Ethereum                          [Active]  │  │
│  │   0x1234...abcd                                       │  │
│  └────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ ● USDT on Tron                              [Active]  │  │
│  │   TJYk...x9Wp                                         │  │
│  └────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ ● USDC on Arbitrum                          [Active]  │  │
│  │   0x1234...abcd  (same EVM address)                   │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ── Add Account ─────────────────────────────────────────    │
│  Token:   [USDC ▼]                                           │
│  Chain:   [Ethereum ▼]                                       │
│  Address: [0x_____________________________________]          │
│  Label:   [__________________] (optional)                    │
│                                                              │
│  [Add Account]                                               │
└──────────────────────────────────────────────────────────────┘
```

**UX notes:**
- Token dropdown populated from `SUPPORTED_TOKENS`
- Chain dropdown populated from `SUPPORTED_CHAINS`, filtered to chains supporting selected token (future: for now show all)
- Address field validates on submit based on selected chain's `addr_type`
- Label auto-generates as "USDC on Ethereum" if left blank
- Each account card has toggle active/deactivate and delete

### Checkout display

When payer selects a stablecoin tab (e.g. "USDC"):

```
┌─────────────────────────────────┐
│ Pay with USDC                   │
├─────────────────────────────────┤
│ Select network:                 │
│ ○ Ethereum                      │
│ ● Arbitrum                      │
│ ○ Base                          │
│                                 │
│ Send exactly:                   │
│ ┌─────────────────────────────┐ │
│ │ 500.00 USDC                 │ │ ← copy button
│ └─────────────────────────────┘ │
│                                 │
│ To address:                     │
│ ┌─────────────────────────────┐ │
│ │ 0x1234567890abcdef1234...   │ │ ← copy button
│ └─────────────────────────────┘ │
│                                 │
│ ⚠ Only send USDC on the        │
│   Arbitrum network. Sending on  │
│   the wrong network will result │
│   in permanent loss of funds.   │
│                                 │
│ No countdown — stablecoin       │
│ amount is fixed (1:1 with USD). │
└─────────────────────────────────┘
```

**Checkout logic:**
- No BTC rate lock needed — stablecoins are 1:1 with fiat (amount = invoice total)
- If invoice currency is not USD, convert at current rate (or merchant-configured rate)
- No QR code for stablecoins (wallets don't have a universal URI scheme like BIP21)
- Group stablecoin accounts by token in the method selector: one tab per token, then chain selector inside
- Show chain-specific warning about sending on correct network
- Stablecoin payments are **manually reconciled** (like wire), merchant confirms receipt in admin

### Payment method registration

```python
# Dynamic registration in payment_methods.py
class StablecoinPaymentMethod(PaymentMethod):
    '''Dynamically registered for each active StablecoinAccount.'''

    def __init__(self, account):
        self.account = account
        self.name = account.method_name          # 'stable_ethereum_usdc'
        self.display_name = account.display_label # 'USDC on Ethereum'
        self.icon = 'stablecoin'

    def is_available(self, org):
        return self.account.is_active and self.account.org_id == org.id

    def get_payment_info(self, invoice):
        return {
            'token': self.account.token,
            'chain': self.account.chain,
            'address': self.account.address,
            'label': self.account.display_label,
            'amount': str(invoice.total),  # 1:1 with fiat
            'currency': invoice.currency,
            'explorer_url': self.account.explorer_url,
            'warning': 'Only send %s on the %s network.' % (
                SUPPORTED_TOKENS[self.account.token]['symbol'],
                SUPPORTED_CHAINS[self.account.chain]['name'],
            ),
        }
```

---

## 8D — Demo Mode Seed Data

```python
# btpay/demo/seed.py additions

# Wire connector
wire = WireConnector(
    org_id=org.id,
    name='Demo Bank Wire',
    bank_name='First National Bank',
    account_name='Linen Avenue Co',
    account_number='****4567',
    routing_number='021000021',
    swift_code='FNBOUS33',
    currency='USD',
    notes='Include invoice number in wire memo.',
)

# Stablecoin accounts
StablecoinAccount(org_id=org.id, chain='ethereum', token='usdc',
    address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045')
StablecoinAccount(org_id=org.id, chain='tron', token='usdt',
    address='TJYkDrBnPwB9rD9tGNqsXaKLP1vBSNkefX')
StablecoinAccount(org_id=org.id, chain='arbitrum', token='usdc',
    address='0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045')
```

---

## Implementation Order

```
1. Connector framework       (8A: models, settings nav, config)
2. Wire connector            (8B: model, settings page, checkout tab)
3. Stablecoin connector      (8C: model, validation, settings page, checkout tab)
4. Checkout multi-method UI  (8A: method selector tabs in pay.html)
5. Demo seed data            (8D: wire + stablecoin demo accounts)
6. Tests                     (all connectors: models, views, checkout, validation)
```

### Migration from existing code

- `Organization.wire_info` JsonColumn → `WireConnector` model (one-time migration helper)
- `/settings/wallets` → `/settings/connectors/bitcoin` (redirect old URL)
- `Wallet` model stays in `btpay/bitcoin/models.py` (no move needed, just wrapped conceptually)
- Existing `WireTransfer` payment method in `payment_methods.py` → delegates to `WireConnector` model
- `btpay/invoicing/wire_info.py` → absorbed into `btpay/connectors/wire.py`

### NOT in scope

- **Automatic stablecoin payment detection** — requires RPC node access per chain; out of scope for self-hosted v1. Manual reconciliation only.
- **Lightning Network** — separate future phase.
- **Token contract addresses** — hardcoded reference only, no on-chain interaction.
- **Multi-currency stablecoin conversion** — v1 assumes 1 USDC = 1 USD. Non-USD invoices show the fiat total and leave conversion to the payer.
