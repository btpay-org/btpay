#
# Exchange rate service — multi-source BTC price fetcher
#
# Daemon thread that periodically fetches BTC rates from multiple sources,
# averages them (discarding outliers), and caches the result.
#
import logging
import threading
import time
from decimal import Decimal, InvalidOperation

log = logging.getLogger(__name__)


class ExchangeRateService:
    '''
    Multi-source BTC rate fetcher with averaging and outlier detection.

    Usage:
        svc = ExchangeRateService(sources=['coingecko', 'coinbase'])
        svc.start()
        rate = svc.get_rate('USD')
        svc.stop()
    '''

    # Source name -> fetch method name
    _SOURCE_METHODS = {
        'coingecko': '_fetch_coingecko',
        'coinbase':  '_fetch_coinbase',
        'kraken':    '_fetch_kraken',
        'bitstamp':  '_fetch_bitstamp',
        'mempool':   '_fetch_mempool',
    }

    def __init__(self, sources=None, interval=300, proxy=None,
                 currencies=None, mempool_url=None):
        self.sources = sources or ['coingecko', 'coinbase', 'kraken']
        self.interval = interval
        self.proxy = proxy
        self.currencies = currencies or ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CHF']
        self.mempool_url = mempool_url or 'https://mempool.space/api'

        # Cached rates: {currency: Decimal}
        self._rates = {}
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()
        self._last_fetch_at = 0
        self._last_errors = []

    def start(self):
        '''Start background rate fetching thread.'''
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name='exchange-rate-service',
            daemon=True,
        )
        self._thread.start()
        log.info('Exchange rate service started (interval=%ds, sources=%s)',
                 self.interval, self.sources)

    def stop(self):
        '''Stop background thread.'''
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        log.info('Exchange rate service stopped')

    def get_rate(self, currency='USD'):
        '''Get current BTC rate in given currency. Returns Decimal or None.'''
        with self._lock:
            return self._rates.get(currency.upper())

    def get_rates(self):
        '''Get all cached rates: {currency: Decimal}.'''
        with self._lock:
            return dict(self._rates)

    def fetch_now(self):
        '''Force an immediate rate fetch. Returns True if any rates updated.'''
        return self._do_fetch()

    # ---- Internal ----

    def _run_loop(self):
        '''Main loop: fetch rates at interval.'''
        # Fetch immediately on start
        self._do_fetch()

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self.interval)
            if self._stop_event.is_set():
                break
            self._do_fetch()

    def _do_fetch(self):
        '''Fetch from all configured sources, average, store.'''
        import requests as req_lib

        # {currency: [rate1, rate2, ...]}
        all_rates = {}
        errors = []

        for source in self.sources:
            method_name = self._SOURCE_METHODS.get(source)
            if not method_name:
                log.warning('Unknown exchange rate source: %s', source)
                continue

            method = getattr(self, method_name)
            try:
                rates = method(req_lib)
                if rates:
                    for cur, rate in rates.items():
                        cur = cur.upper()
                        if cur in self.currencies:
                            validated = self._validate_rate(rate, cur)
                            if validated is not None:
                                all_rates.setdefault(cur, []).append(validated)
            except Exception as e:
                errors.append('%s: %s' % (source, str(e)))
                log.debug('Rate fetch error from %s: %s', source, e)

        self._last_errors = errors

        if not all_rates:
            log.warning('No exchange rates fetched from any source')
            return False

        # Average rates per currency
        with self._lock:
            for cur, rates_list in all_rates.items():
                avg = self._average_rates(rates_list)
                if avg is not None:
                    self._rates[cur] = avg

        self._last_fetch_at = time.time()
        log.debug('Exchange rates updated: %s',
                  {k: str(v) for k, v in self._rates.items()})
        return True

    def _get_session(self, req_lib):
        '''Create a requests session with optional proxy.'''
        session = req_lib.Session()
        session.headers['User-Agent'] = 'BTPay/1.0'
        if self.proxy:
            session.proxies = {
                'http': self.proxy,
                'https': self.proxy,
            }
        return session

    def _fetch_coingecko(self, req_lib):
        '''Fetch rates from CoinGecko free API.'''
        session = self._get_session(req_lib)
        vs = ','.join(c.lower() for c in self.currencies)
        url = 'https://api.coingecko.com/api/v3/simple/price'
        resp = session.get(url, params={
            'ids': 'bitcoin',
            'vs_currencies': vs,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        btc = data.get('bitcoin', {})
        rates = {}
        for cur in self.currencies:
            val = btc.get(cur.lower())
            if val is not None:
                rates[cur] = Decimal(str(val))
        return rates

    def _fetch_coinbase(self, req_lib):
        '''Fetch rates from Coinbase exchange rates API.'''
        session = self._get_session(req_lib)
        url = 'https://api.coinbase.com/v2/exchange-rates'
        resp = session.get(url, params={'currency': 'BTC'}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        raw_rates = data.get('data', {}).get('rates', {})
        rates = {}
        for cur in self.currencies:
            val = raw_rates.get(cur)
            if val is not None:
                rates[cur] = Decimal(str(val))
        return rates

    def _fetch_kraken(self, req_lib):
        '''Fetch rates from Kraken public ticker.'''
        session = self._get_session(req_lib)

        # Kraken pair names
        pair_map = {
            'USD': 'XXBTZUSD', 'EUR': 'XXBTZEUR', 'GBP': 'XXBTZGBP',
            'CAD': 'XXBTZCAD', 'AUD': 'XXBTZAUD', 'JPY': 'XXBTZJPY',
            'CHF': 'XXBTZCHF',
        }

        pairs = []
        for cur in self.currencies:
            if cur in pair_map:
                pairs.append(pair_map[cur])

        if not pairs:
            return {}

        url = 'https://api.kraken.com/0/public/Ticker'
        resp = session.get(url, params={'pair': ','.join(pairs)}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        result_data = data.get('result', {})
        rates = {}
        for cur, pair_name in pair_map.items():
            if cur not in self.currencies:
                continue
            ticker = result_data.get(pair_name)
            if ticker:
                # 'c' is the last trade closed: [price, lot-volume]
                last_price = ticker.get('c', [None])[0]
                if last_price:
                    rates[cur] = Decimal(str(last_price))
        return rates

    def _fetch_bitstamp(self, req_lib):
        '''Fetch rates from Bitstamp ticker.'''
        session = self._get_session(req_lib)

        pair_map = {
            'USD': 'btcusd', 'EUR': 'btceur', 'GBP': 'btcgbp',
        }

        rates = {}
        for cur in self.currencies:
            pair = pair_map.get(cur)
            if not pair:
                continue
            url = 'https://www.bitstamp.net/api/v2/ticker/%s/' % pair
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                last = data.get('last')
                if last:
                    rates[cur] = Decimal(str(last))
        return rates

    def _fetch_mempool(self, req_lib):
        '''Fetch rates from mempool.space prices API.'''
        session = self._get_session(req_lib)
        url = '%s/v1/prices' % self.mempool_url.rstrip('/')
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        rates = {}
        for cur in self.currencies:
            val = data.get(cur)
            if val is not None:
                rates[cur] = Decimal(str(val))
        return rates

    def _validate_rate(self, rate, currency='USD'):
        '''
        Reasonableness check for BTC price.
        Returns Decimal rate if valid, None if suspicious.
        '''
        try:
            rate = Decimal(str(rate))
        except (InvalidOperation, TypeError, ValueError):
            return None

        if rate <= 0:
            return None

        # USD sanity bounds — tightened to prevent manipulation
        # Lower: $10K (BTC hasn't been below this since 2020)
        # Upper: $2M (allows 20x growth from current levels)
        if currency == 'USD':
            if rate < 10_000 or rate > 2_000_000:
                log.warning('Suspicious BTC/USD rate: %s (bounds: $10K-$2M)', rate)
                return None

        # For other currencies, bound relative to USD-equivalent range
        if rate > 500_000_000:
            log.warning('Suspicious BTC/%s rate: %s', currency, rate)
            return None

        return rate

    def _average_rates(self, rates):
        '''
        Average rates from multiple sources, discarding outliers.
        If 3+ rates, discard any that deviate > 5% from median.
        '''
        if not rates:
            return None

        if len(rates) == 1:
            return rates[0]

        if len(rates) == 2:
            return (rates[0] + rates[1]) / 2

        # 3+ rates: compute median, discard outliers
        sorted_rates = sorted(rates)
        mid = len(sorted_rates) // 2
        median = sorted_rates[mid]

        threshold = median * Decimal('0.05')
        filtered = [r for r in sorted_rates if abs(r - median) <= threshold]

        if not filtered:
            # All are outliers? Just return median
            return median

        total = sum(filtered)
        return total / len(filtered)

    def save_snapshot(self):
        '''Save current rates as ExchangeRateSnapshot rows.'''
        from btpay.bitcoin.models import ExchangeRateSnapshot
        from btpay.chrono import NOW

        now = NOW()
        with self._lock:
            for currency, rate in self._rates.items():
                snap = ExchangeRateSnapshot(
                    currency=currency,
                    rate=rate,
                    source='average',
                    fetched_at=now,
                )
                snap.save()

# EOF
