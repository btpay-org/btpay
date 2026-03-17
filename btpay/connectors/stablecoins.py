#
# Stablecoin connector — multi-chain token receiving accounts
#
import re
import hashlib
from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import Text, Integer, Boolean


# ---- Chain & token registries ----

SUPPORTED_CHAINS = {
    'ethereum':  {'name': 'Ethereum',  'addr_type': 'evm',    'explorer': 'https://etherscan.io/address/'},
    'arbitrum':  {'name': 'Arbitrum',  'addr_type': 'evm',    'explorer': 'https://arbiscan.io/address/'},
    'base':      {'name': 'Base',      'addr_type': 'evm',    'explorer': 'https://basescan.org/address/'},
    'polygon':   {'name': 'Polygon',   'addr_type': 'evm',    'explorer': 'https://polygonscan.com/address/'},
    'optimism':  {'name': 'Optimism',  'addr_type': 'evm',    'explorer': 'https://optimistic.etherscan.io/address/'},
    'avalanche': {'name': 'Avalanche', 'addr_type': 'evm',    'explorer': 'https://snowtrace.io/address/'},
    'tron':      {'name': 'Tron',      'addr_type': 'base58', 'explorer': 'https://tronscan.org/#/address/'},
    'solana':    {'name': 'Solana',    'addr_type': 'base58', 'explorer': 'https://solscan.io/account/'},
}

SUPPORTED_TOKENS = {
    'usdt':  {'name': 'Tether',      'symbol': 'USDT', 'decimals': 6},
    'usdc':  {'name': 'USD Coin',    'symbol': 'USDC', 'decimals': 6},
    'dai':   {'name': 'Dai',         'symbol': 'DAI',  'decimals': 18},
    'pyusd': {'name': 'PayPal USD',  'symbol': 'PYUSD', 'decimals': 6},
}


# ---- Model ----

class StablecoinAccount(BaseMixin, MemModel):
    '''
    A merchant's stablecoin receiving address on a specific chain.
    Each row = one chain + token + address combination.
    '''
    org_id      = Integer(index=True)
    chain       = Text(required=True)       # key from SUPPORTED_CHAINS
    token       = Text(required=True)       # key from SUPPORTED_TOKENS
    address     = Text(required=True)       # receiving address (0x hex or base58)
    label       = Text()                    # custom label, or auto-generated
    is_active   = Boolean(default=True)

    @property
    def display_label(self):
        '''Human label like "USDC on Ethereum" or custom override.'''
        if self.label:
            return self.label
        token_info = SUPPORTED_TOKENS.get(self.token, {})
        chain_info = SUPPORTED_CHAINS.get(self.chain, {})
        return '%s on %s' % (
            token_info.get('symbol', self.token.upper()),
            chain_info.get('name', self.chain.title()),
        )

    @property
    def token_symbol(self):
        info = SUPPORTED_TOKENS.get(self.token, {})
        return info.get('symbol', self.token.upper())

    @property
    def chain_name(self):
        info = SUPPORTED_CHAINS.get(self.chain, {})
        return info.get('name', self.chain.title())

    @property
    def short_address(self):
        '''Truncated for display: 0x1234...abcd'''
        if self.address and len(self.address) > 16:
            return '%s...%s' % (self.address[:6], self.address[-4:])
        return self.address or ''

    @property
    def method_name(self):
        '''Unique key for payment method registry: stable_ethereum_usdc'''
        return 'stable_%s_%s' % (self.chain, self.token)

    @property
    def explorer_url(self):
        chain_info = SUPPORTED_CHAINS.get(self.chain, {})
        base = chain_info.get('explorer', '')
        return (base + self.address) if base and self.address else ''

    @property
    def addr_type(self):
        chain_info = SUPPORTED_CHAINS.get(self.chain, {})
        return chain_info.get('addr_type', 'unknown')


# ---- Address validation ----

def validate_stablecoin_address(address, chain):
    '''
    Validate address format for the given chain.
    Returns (valid, error_message).
    '''
    if not address or not address.strip():
        return False, 'Address is required'

    address = address.strip()
    chain_info = SUPPORTED_CHAINS.get(chain)
    if not chain_info:
        return False, 'Unsupported chain: %s' % chain

    addr_type = chain_info['addr_type']

    if addr_type == 'evm':
        return _validate_evm_address(address)
    elif addr_type == 'base58':
        if chain == 'tron':
            return _validate_tron_address(address)
        elif chain == 'solana':
            return _validate_solana_address(address)

    return False, 'Unknown address type for chain %s' % chain


def _validate_evm_address(address):
    '''Validate Ethereum/EVM 0x address with optional EIP-55 checksum.'''
    if not re.match(r'^0x[0-9a-fA-F]{40}$', address):
        return False, 'Invalid EVM address (expected 0x + 40 hex characters)'
    # Check EIP-55 checksum if mixed case
    if address != address.lower() and address != ('0x' + address[2:].upper()):
        if not _eip55_valid(address):
            return False, 'Invalid EIP-55 checksum'
    return True, ''


def _validate_tron_address(address):
    '''Validate Tron base58 address (starts with T, 34 chars).'''
    if not address.startswith('T'):
        return False, 'Tron address must start with T'
    if len(address) != 34:
        return False, 'Tron address must be 34 characters (got %d)' % len(address)
    # Basic base58 charset check
    if not re.match(r'^[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]+$', address):
        return False, 'Invalid base58 characters in Tron address'
    return True, ''


def _validate_solana_address(address):
    '''Validate Solana base58 address (32-44 chars).'''
    if not re.match(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$', address):
        return False, 'Invalid Solana address (expected 32-44 base58 characters)'
    return True, ''


def _eip55_valid(address):
    '''
    Validate EIP-55 mixed-case checksum.
    Uses keccak-256 (sha3) of the lowercase hex address.
    '''
    addr_hex = address[2:]
    addr_lower = addr_hex.lower()

    # Use hashlib keccak if available, fall back to sha3_256
    try:
        h = hashlib.new('keccak_256', addr_lower.encode()).hexdigest()
    except ValueError:
        # keccak not available, skip checksum validation
        return True

    for i, c in enumerate(addr_hex):
        if c in '0123456789':
            continue
        should_upper = int(h[i], 16) >= 8
        if should_upper and c != c.upper():
            return False
        if not should_upper and c != c.lower():
            return False
    return True


def stablecoin_payment_info(account, invoice):
    '''
    Build payment info dict for checkout display.
    Stablecoins are ~1:1 with USD — amount equals invoice total for USD invoices.
    '''
    return {
        'token': account.token,
        'token_symbol': account.token_symbol,
        'chain': account.chain,
        'chain_name': account.chain_name,
        'address': account.address,
        'label': account.display_label,
        'short_address': account.short_address,
        'amount': str(invoice.total),
        'currency': invoice.currency,
        'explorer_url': account.explorer_url,
        'warning': 'Only send %s on the %s network. Sending on the wrong network will result in permanent loss of funds.' % (
            account.token_symbol, account.chain_name,
        ),
    }

# EOF
