#
# mempool.space REST API client
#
# Configurable base URL for self-hosted instances.
# Supports SOCKS5 proxy for Tor privacy.
#
import logging

log = logging.getLogger(__name__)


class MempoolAPI:
    '''
    REST client for mempool.space/api.

    Usage:
        api = MempoolAPI()
        utxos = api.get_address_utxos('bc1q...')
        height = api.get_block_height()
    '''

    def __init__(self, base_url=None, proxy=None, timeout=30):
        self.base_url = (base_url or 'https://mempool.space/api').rstrip('/')
        self.proxy = proxy
        self.timeout = timeout

    def _get(self, path):
        '''HTTP GET with optional proxy.'''
        import requests
        url = '%s%s' % (self.base_url, path)

        proxies = None
        if self.proxy:
            proxies = {'http': self.proxy, 'https': self.proxy}

        resp = requests.get(
            url,
            timeout=self.timeout,
            proxies=proxies,
            headers={'User-Agent': 'BTPay/1.0'},
        )
        resp.raise_for_status()
        return resp.json()

    def _get_text(self, path):
        '''HTTP GET returning plain text.'''
        import requests
        url = '%s%s' % (self.base_url, path)

        proxies = None
        if self.proxy:
            proxies = {'http': self.proxy, 'https': self.proxy}

        resp = requests.get(
            url,
            timeout=self.timeout,
            proxies=proxies,
            headers={'User-Agent': 'BTPay/1.0'},
        )
        resp.raise_for_status()
        return resp.text

    # ---- Address endpoints ----

    def get_address_utxos(self, address):
        '''
        GET /address/{addr}/utxo
        Returns list of UTXOs: [{txid, vout, status, value}, ...]
        '''
        return self._get('/address/%s/utxo' % address)

    def get_address_txs(self, address):
        '''
        GET /address/{addr}/txs
        Returns list of transactions for this address.
        '''
        return self._get('/address/%s/txs' % address)

    def get_address_info(self, address):
        '''
        GET /address/{addr}
        Returns address info: {address, chain_stats, mempool_stats}.
        chain_stats/mempool_stats have: funded_txo_count, funded_txo_sum,
        spent_txo_count, spent_txo_sum, tx_count.
        '''
        return self._get('/address/%s' % address)

    # ---- Transaction endpoints ----

    def get_tx(self, txid):
        '''
        GET /tx/{txid}
        Returns full transaction details.
        '''
        return self._get('/tx/%s' % txid)

    def get_tx_status(self, txid):
        '''
        GET /tx/{txid}/status
        Returns {confirmed: bool, block_height, block_hash, block_time}.
        '''
        return self._get('/tx/%s/status' % txid)

    def get_tx_hex(self, txid):
        '''
        GET /tx/{txid}/hex
        Returns raw transaction hex.
        '''
        return self._get_text('/tx/%s/hex' % txid)

    # ---- Block endpoints ----

    def get_block_height(self):
        '''
        GET /blocks/tip/height
        Returns current block height as integer.
        '''
        return int(self._get_text('/blocks/tip/height'))

    def get_block_hash(self, height=None):
        '''
        GET /blocks/tip/hash or /block-height/{height}
        Returns block hash string.
        '''
        if height is not None:
            return self._get_text('/block-height/%d' % height)
        return self._get_text('/blocks/tip/hash')

    # ---- Fee endpoints ----

    def get_fee_estimates(self):
        '''
        GET /v1/fees/recommended
        Returns {fastestFee, halfHourFee, hourFee, economyFee, minimumFee}.
        Values are in sat/vB.
        '''
        return self._get('/v1/fees/recommended')

    # ---- Price endpoint ----

    def get_prices(self):
        '''
        GET /v1/prices
        Returns BTC price in various currencies: {USD: ..., EUR: ..., ...}
        '''
        return self._get('/v1/prices')

    # ---- Convenience methods ----

    def get_address_balance(self, address):
        '''
        Get confirmed + unconfirmed balance for an address.
        Returns (confirmed_sat, unconfirmed_sat).
        '''
        info = self.get_address_info(address)
        chain = info.get('chain_stats', {})
        mempool = info.get('mempool_stats', {})

        confirmed = chain.get('funded_txo_sum', 0) - chain.get('spent_txo_sum', 0)
        unconfirmed = mempool.get('funded_txo_sum', 0) - mempool.get('spent_txo_sum', 0)
        return (confirmed, unconfirmed)

    def get_confirmations(self, txid):
        '''
        Get number of confirmations for a transaction.
        Returns 0 if unconfirmed, or (current_height - block_height + 1).
        '''
        status = self.get_tx_status(txid)
        if not status.get('confirmed'):
            return 0

        block_height = status.get('block_height', 0)
        if not block_height:
            return 0

        current_height = self.get_block_height()
        return max(0, current_height - block_height + 1)

# EOF
