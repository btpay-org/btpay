#
# Stablecoin payment monitor — background balance watcher
#
# Polls ERC-20/TRC-20/SPL token balances for merchant addresses
# using public RPC endpoints. Detects payments by comparing current
# balance to the snapshot taken when the invoice was created.
#
# Mirrors the PaymentMonitor pattern for consistency.
#
import logging
import threading
import time

log = logging.getLogger(__name__)


class StablecoinMonitor:
    '''
    Background watcher for stablecoin payments.

    Watches merchant addresses for incoming token transfers by polling
    balanceOf() via public RPC endpoints.

    Usage:
        monitor = StablecoinMonitor()
        monitor.start()
        monitor.watch(invoice_id, chain, token, address, expected_amount)
        monitor.stop()
    '''

    def __init__(self, rpc_client=None, check_interval=60):
        self.rpc = rpc_client
        self.check_interval = check_interval

        # Watched entries: {key: WatchEntry}
        # key = "invoice_id:chain:token"
        self._watched = {}
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()

        # Callbacks
        self._on_payment_callbacks = []

    def start(self):
        '''Start monitoring thread.'''
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._check_loop,
            name='stablecoin-monitor',
            daemon=True,
        )
        self._thread.start()
        log.info('Stablecoin monitor started (interval=%ds)', self.check_interval)

    def stop(self):
        '''Stop monitoring thread.'''
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        log.info('Stablecoin monitor stopped')

    def watch(self, invoice_id, chain, token, address, expected_amount,
              baseline_balance=None):
        '''
        Add an address to watch for stablecoin payment.

        Args:
            invoice_id: Invoice to credit when payment arrives
            chain: 'ethereum', 'arbitrum', etc.
            token: 'usdc', 'usdt', etc.
            address: Merchant's receiving address
            expected_amount: Expected payment in token's smallest unit
            baseline_balance: Balance at time of invoice creation (for diff)
        '''
        key = '%s:%s:%s' % (invoice_id, chain, token)
        entry = WatchEntry(
            invoice_id=invoice_id,
            chain=chain,
            token=token,
            address=address,
            expected_amount=expected_amount,
            baseline_balance=baseline_balance,
        )
        with self._lock:
            self._watched[key] = entry
        log.debug('Watching stablecoin: %s %s on %s for invoice %s',
                  token.upper(), address[:10], chain, invoice_id)

    def unwatch(self, invoice_id, chain=None, token=None):
        '''Remove an invoice from the watch list.'''
        with self._lock:
            if chain and token:
                key = '%s:%s:%s' % (invoice_id, chain, token)
                self._watched.pop(key, None)
            else:
                # Remove all entries for this invoice
                keys = [k for k in self._watched if k.startswith('%s:' % invoice_id)]
                for k in keys:
                    del self._watched[k]

    def on_payment(self, callback):
        '''
        Register callback for when payment is detected.
        callback(invoice_id, chain, token, amount_received, address)
        '''
        self._on_payment_callbacks.append(callback)

    @property
    def watched_count(self):
        with self._lock:
            return len(self._watched)

    # ---- Internal ----

    def _check_loop(self):
        '''Main loop: iterate watched entries, check balances.'''
        while not self._stop_event.is_set():
            with self._lock:
                entries = list(self._watched.items())

            for key, entry in entries:
                if self._stop_event.is_set():
                    break
                try:
                    self._check_entry(key, entry)
                except Exception as e:
                    log.error('Error checking stablecoin %s: %s', key, e)

            self._stop_event.wait(timeout=self.check_interval)

    def _check_entry(self, key, entry):
        '''Check a single watch entry for payment.'''
        if not self.rpc:
            log.warning('No RPC client configured for stablecoin monitor')
            return

        if entry.confirmed:
            return

        try:
            current_balance = self.rpc.get_token_balance(
                entry.chain, entry.token, entry.address)
        except Exception as e:
            log.debug('Balance check failed for %s: %s', key, e)
            return

        # Determine received amount
        baseline = entry.baseline_balance or 0
        received = current_balance - baseline

        if received <= 0:
            return

        # Check if we've already seen this amount
        if received == entry.last_seen_amount:
            return

        entry.last_seen_amount = received
        entry.last_checked = time.time()

        # Check if payment meets expected amount (with small tolerance)
        # Allow 0.5% tolerance for rounding
        threshold = int(entry.expected_amount * 0.995) if entry.expected_amount else 0

        if received >= threshold:
            entry.confirmed = True
            log.info('Stablecoin payment confirmed: invoice=%s chain=%s token=%s '
                     'received=%d expected=%d',
                     entry.invoice_id, entry.chain, entry.token,
                     received, entry.expected_amount)
            self._fire_payment(entry, received)

            # Remove from watch list
            with self._lock:
                self._watched.pop(key, None)
        else:
            log.info('Stablecoin partial payment: invoice=%s chain=%s token=%s '
                     'received=%d expected=%d',
                     entry.invoice_id, entry.chain, entry.token,
                     received, entry.expected_amount)

    def _fire_payment(self, entry, amount_received):
        '''Fire payment callbacks.'''
        for cb in self._on_payment_callbacks:
            try:
                cb(entry.invoice_id, entry.chain, entry.token,
                   amount_received, entry.address)
            except Exception as e:
                log.error('Stablecoin payment callback error: %s', e)

    def snapshot_balance(self, chain, token, address):
        '''
        Take a balance snapshot for baseline comparison.
        Call this when creating an invoice to know the starting balance.
        Returns balance in token's smallest unit, or 0 on error.
        '''
        if not self.rpc:
            return 0
        try:
            return self.rpc.get_token_balance(chain, token, address)
        except Exception as e:
            log.debug('Snapshot balance failed for %s/%s/%s: %s',
                      chain, token, address[:10], e)
            return 0


class WatchEntry:
    '''A single address being watched for stablecoin payment.'''

    __slots__ = ('invoice_id', 'chain', 'token', 'address',
                 'expected_amount', 'baseline_balance',
                 'last_seen_amount', 'last_checked', 'confirmed')

    def __init__(self, invoice_id, chain, token, address,
                 expected_amount=0, baseline_balance=None):
        self.invoice_id = invoice_id
        self.chain = chain
        self.token = token
        self.address = address
        self.expected_amount = expected_amount
        self.baseline_balance = baseline_balance
        self.last_seen_amount = 0
        self.last_checked = 0
        self.confirmed = False

# EOF
