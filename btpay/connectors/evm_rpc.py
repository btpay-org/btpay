#
# EVM JSON-RPC client — token balance checking via public RPCs
#
# Queries ERC-20 balanceOf() across multiple EVM chains using free public
# RPC endpoints. No API keys required for the default configuration.
# Optionally supports Alchemy/Ankr/custom RPC URLs.
#
# Also handles Tron (TronGrid API) and Solana (JSON-RPC) for stablecoin
# balance checks on those chains.
#
import json
import logging

log = logging.getLogger(__name__)


# ---- Public RPC endpoints (free, no API key) ----

PUBLIC_RPCS = {
    'ethereum':  'https://eth.llamarpc.com',
    'arbitrum':  'https://arb1.arbitrum.io/rpc',
    'base':      'https://mainnet.base.org',
    'polygon':   'https://polygon-rpc.com',
    'optimism':  'https://mainnet.optimism.io',
    'avalanche': 'https://api.avax.network/ext/bc/C/rpc',
    'tron':      'https://api.trongrid.io',
    'solana':    'https://api.mainnet-beta.solana.com',
}

# Backup RPCs (Ankr public — no key needed)
BACKUP_RPCS = {
    'ethereum':  'https://rpc.ankr.com/eth',
    'arbitrum':  'https://rpc.ankr.com/arbitrum',
    'base':      'https://rpc.ankr.com/base',
    'polygon':   'https://rpc.ankr.com/polygon',
    'optimism':  'https://rpc.ankr.com/optimism',
    'avalanche': 'https://rpc.ankr.com/avalanche',
}


# ---- Token contract addresses per chain ----
# These are the official, well-known contract addresses for major stablecoins.

TOKEN_CONTRACTS = {
    # Ethereum mainnet
    ('ethereum', 'usdt'):  '0xdAC17F958D2ee523a2206206994597C13D831ec7',
    ('ethereum', 'usdc'):  '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    ('ethereum', 'dai'):   '0x6B175474E89094C44Da98b954EedeAC495271d0F',
    ('ethereum', 'pyusd'): '0x6c3ea9036406852006290770BEdFcAbA0e23A0e8',

    # Arbitrum
    ('arbitrum', 'usdt'):  '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9',
    ('arbitrum', 'usdc'):  '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',
    ('arbitrum', 'dai'):   '0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1',

    # Base
    ('base', 'usdc'):      '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    ('base', 'dai'):       '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb',

    # Polygon
    ('polygon', 'usdt'):   '0xc2132D05D31c914a87C6611C10748AEb04B58e8F',
    ('polygon', 'usdc'):   '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',
    ('polygon', 'dai'):    '0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063',

    # Optimism
    ('optimism', 'usdt'):  '0x94b008aA00579c1307B0EF2c499aD98a8ce58e58',
    ('optimism', 'usdc'):  '0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85',
    ('optimism', 'dai'):   '0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1',

    # Avalanche C-Chain
    ('avalanche', 'usdt'): '0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7',
    ('avalanche', 'usdc'): '0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E',
    ('avalanche', 'dai'):  '0xd586E7F844cEa2F87f50152665BCbc2C279D8d70',

    # Tron — these are TRC-20 contract addresses
    ('tron', 'usdt'):      'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',
    ('tron', 'usdc'):      'TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8',

    # Solana — these are SPL token mint addresses
    ('solana', 'usdt'):    'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',
    ('solana', 'usdc'):    'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
}


class EvmRpcClient:
    '''
    Multi-chain ERC-20 balance checker using JSON-RPC.

    Usage:
        client = EvmRpcClient()
        balance = client.get_token_balance('ethereum', 'usdc', '0x1234...')
        # balance is in token's smallest unit (e.g. 1000000 = 1.0 USDC)
    '''

    def __init__(self, custom_rpcs=None, proxy=None, timeout=15):
        self.custom_rpcs = custom_rpcs or {}
        self.proxy = proxy
        self.timeout = timeout

    def _get_rpc_url(self, chain):
        '''Get RPC URL for chain: custom > public > backup.'''
        if chain in self.custom_rpcs:
            return self.custom_rpcs[chain]
        return PUBLIC_RPCS.get(chain)

    def _rpc_call(self, rpc_url, method, params):
        '''Make a JSON-RPC call.'''
        import requests

        payload = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': method,
            'params': params,
        }

        proxies = None
        if self.proxy:
            proxies = {'http': self.proxy, 'https': self.proxy}

        resp = requests.post(
            rpc_url,
            json=payload,
            timeout=self.timeout,
            proxies=proxies,
            headers={'Content-Type': 'application/json', 'User-Agent': 'BTPay/1.0'},
        )
        resp.raise_for_status()
        data = resp.json()

        if 'error' in data and data['error']:
            raise EvmRpcError('RPC error: %s' % data['error'].get('message', str(data['error'])))

        return data.get('result')

    def _http_get(self, url):
        '''Simple HTTP GET returning JSON.'''
        import requests
        proxies = None
        if self.proxy:
            proxies = {'http': self.proxy, 'https': self.proxy}
        resp = requests.get(url, timeout=self.timeout, proxies=proxies,
                            headers={'User-Agent': 'BTPay/1.0'})
        resp.raise_for_status()
        return resp.json()

    def _http_post(self, url, payload):
        '''Simple HTTP POST returning JSON.'''
        import requests
        proxies = None
        if self.proxy:
            proxies = {'http': self.proxy, 'https': self.proxy}
        resp = requests.post(url, json=payload, timeout=self.timeout, proxies=proxies,
                             headers={'Content-Type': 'application/json', 'User-Agent': 'BTPay/1.0'})
        resp.raise_for_status()
        return resp.json()

    # ---- EVM chains (Ethereum, Arbitrum, Base, Polygon, Optimism, Avalanche) ----

    def _evm_balance_of(self, chain, contract_address, wallet_address):
        '''
        Call ERC-20 balanceOf(address) via eth_call.
        Returns balance as int (in token's smallest unit).
        '''
        rpc_url = self._get_rpc_url(chain)
        if not rpc_url:
            raise EvmRpcError('No RPC URL for chain: %s' % chain)

        # ERC-20 balanceOf function selector: 0x70a08231
        # Pad address to 32 bytes
        addr_clean = wallet_address.lower().replace('0x', '')
        data = '0x70a08231' + addr_clean.rjust(64, '0')

        try:
            result = self._rpc_call(rpc_url, 'eth_call', [
                {'to': contract_address, 'data': data},
                'latest',
            ])
        except Exception:
            # Try backup RPC
            backup = BACKUP_RPCS.get(chain)
            if backup and backup != rpc_url:
                result = self._rpc_call(backup, 'eth_call', [
                    {'to': contract_address, 'data': data},
                    'latest',
                ])
            else:
                raise

        if result and result != '0x':
            return int(result, 16)
        return 0

    # ---- Tron (TRC-20) ----

    def _tron_balance_of(self, contract_address, wallet_address):
        '''
        Query TRC-20 token balance via TronGrid API.
        Uses /wallet/triggerconstantcontract to call balanceOf.
        '''
        rpc_url = self._get_rpc_url('tron') or PUBLIC_RPCS['tron']

        # Convert base58 address to hex for the function parameter
        addr_hex = _tron_base58_to_hex(wallet_address)
        if not addr_hex:
            raise EvmRpcError('Invalid Tron address: %s' % wallet_address)

        url = rpc_url.rstrip('/') + '/wallet/triggerconstantcontract'
        payload = {
            'owner_address': wallet_address,
            'contract_address': contract_address,
            'function_selector': 'balanceOf(address)',
            'parameter': addr_hex.rjust(64, '0'),
            'visible': True,
        }

        data = self._http_post(url, payload)

        if data.get('result', {}).get('result'):
            results = data.get('constant_result', [])
            if results:
                return int(results[0], 16)

        return 0

    # ---- Solana (SPL tokens) ----

    def _solana_balance_of(self, mint_address, wallet_address):
        '''
        Query SPL token balance via Solana RPC getTokenAccountsByOwner.
        '''
        rpc_url = self._get_rpc_url('solana') or PUBLIC_RPCS['solana']

        result = self._rpc_call(rpc_url, 'getTokenAccountsByOwner', [
            wallet_address,
            {'mint': mint_address},
            {'encoding': 'jsonParsed'},
        ])

        if result and result.get('value'):
            total = 0
            for account in result['value']:
                parsed = account.get('account', {}).get('data', {}).get('parsed', {})
                info = parsed.get('info', {})
                token_amount = info.get('tokenAmount', {})
                amount = int(token_amount.get('amount', '0'))
                total += amount
            return total
        return 0

    # ---- Unified interface ----

    def get_token_balance(self, chain, token, wallet_address):
        '''
        Get token balance for any supported chain.
        Returns balance in the token's smallest unit (e.g. 10^6 for USDC).

        Args:
            chain: 'ethereum', 'arbitrum', 'tron', 'solana', etc.
            token: 'usdt', 'usdc', 'dai', 'pyusd'
            wallet_address: The merchant's receiving address
        '''
        contract = TOKEN_CONTRACTS.get((chain, token))
        if not contract:
            raise EvmRpcError('No contract address for %s/%s' % (chain, token))

        if chain == 'tron':
            return self._tron_balance_of(contract, wallet_address)
        elif chain == 'solana':
            return self._solana_balance_of(contract, wallet_address)
        else:
            # All EVM chains use the same balanceOf interface
            return self._evm_balance_of(chain, contract, wallet_address)

    def get_token_balance_human(self, chain, token, wallet_address):
        '''
        Get token balance as a human-readable Decimal.
        e.g. 1000000 raw USDC (6 decimals) -> Decimal('1.0')
        '''
        from decimal import Decimal
        from btpay.connectors.stablecoins import SUPPORTED_TOKENS

        raw = self.get_token_balance(chain, token, wallet_address)
        token_info = SUPPORTED_TOKENS.get(token, {})
        decimals = token_info.get('decimals', 6)
        return Decimal(raw) / Decimal(10 ** decimals)

    def check_chain_connection(self, chain):
        '''
        Test if we can reach the RPC for a chain.
        Returns (success, block_number_or_error).
        '''
        rpc_url = self._get_rpc_url(chain)
        if not rpc_url:
            return False, 'No RPC URL configured'

        if chain == 'tron':
            try:
                data = self._http_get(rpc_url.rstrip('/') + '/wallet/getnowblock')
                height = data.get('block_header', {}).get('raw_data', {}).get('number', 0)
                return True, height
            except Exception as e:
                return False, str(e)

        if chain == 'solana':
            try:
                result = self._rpc_call(rpc_url, 'getSlot', [])
                return True, result
            except Exception as e:
                return False, str(e)

        # EVM chains
        try:
            result = self._rpc_call(rpc_url, 'eth_blockNumber', [])
            return True, int(result, 16) if result else 0
        except Exception as e:
            return False, str(e)


class EvmRpcError(Exception):
    '''Error from EVM RPC call.'''
    pass


# ---- Helpers ----

def _tron_base58_to_hex(address):
    '''Convert Tron base58 address to hex (without 41 prefix).'''
    try:
        import base58
        raw = base58.b58decode_check(address)
        # Tron addresses start with 0x41, strip it for the parameter
        return raw[1:].hex()
    except Exception:
        # Fallback: if base58 module not available, try manual decode
        # For the balanceOf call we need the 20-byte address in hex
        try:
            alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
            n = 0
            for c in address:
                n = n * 58 + alphabet.index(c)
            raw = n.to_bytes(25, 'big')
            return raw[1:21].hex()
        except Exception:
            return None

# EOF
