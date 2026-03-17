#
# Payment monitor — background payment watcher
#
# Daemon thread that watches assigned Bitcoin addresses for incoming payments.
# Prefers Electrum if configured, falls back to mempool.space API.
#
import logging
import threading
import time

log = logging.getLogger(__name__)


class PaymentMonitor:
    '''
    Background payment watcher.

    Monitors assigned BitcoinAddresses for incoming payments.
    Updates address status and triggers callbacks when payments are
    detected or confirmed.

    Usage:
        monitor = PaymentMonitor()
        monitor.start()
        monitor.watch_address(btc_address)
        # ... later
        monitor.stop()
    '''

    def __init__(self, check_interval=30, electrum_client=None,
                 mempool_api=None, confirmation_thresholds=None):
        self.check_interval = check_interval
        self.electrum = electrum_client
        self.mempool = mempool_api

        # Confirmation thresholds: (satoshi_amount, required_confs)
        self.confirmation_thresholds = confirmation_thresholds or [
            (1_000_000, 1),       # < 0.01 BTC: 1 confirmation
            (10_000_000, 3),      # < 0.1 BTC: 3 confirmations
            (None, 6),            # >= 0.1 BTC: 6 confirmations
        ]

        # Watched addresses: {address_str: BitcoinAddress}
        self._watched = {}
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()

        # Callbacks
        self._on_seen_callbacks = []
        self._on_confirmed_callbacks = []

    def start(self):
        '''Start monitoring thread.'''
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._check_loop,
            name='payment-monitor',
            daemon=True,
        )
        self._thread.start()
        log.info('Payment monitor started (interval=%ds)', self.check_interval)

    def stop(self):
        '''Stop monitoring thread.'''
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        log.info('Payment monitor stopped')

    def watch_address(self, btc_address):
        '''Add a BitcoinAddress to the watch list.'''
        with self._lock:
            self._watched[btc_address.address] = btc_address
        log.debug('Watching address: %s', btc_address.address)

    def unwatch_address(self, btc_address):
        '''Remove from watch list.'''
        with self._lock:
            self._watched.pop(btc_address.address, None)
        log.debug('Unwatched address: %s', btc_address.address)

    def on_payment_seen(self, callback):
        '''Register callback for when unconfirmed payment is detected.
        callback(btc_address, amount_sat, txid)'''
        self._on_seen_callbacks.append(callback)

    def on_payment_confirmed(self, callback):
        '''Register callback for when payment reaches required confirmations.
        callback(btc_address, amount_sat, confirmations)'''
        self._on_confirmed_callbacks.append(callback)

    @property
    def watched_count(self):
        with self._lock:
            return len(self._watched)

    # ---- Internal ----

    def _check_loop(self):
        '''Main loop: iterate watched addresses, check for payments.'''
        while not self._stop_event.is_set():
            with self._lock:
                addresses = list(self._watched.values())

            for addr in addresses:
                if self._stop_event.is_set():
                    break
                try:
                    self._check_address(addr)
                except Exception as e:
                    log.error('Error checking address %s: %s', addr.address, e)

            self._stop_event.wait(timeout=self.check_interval)

    def _check_address(self, btc_address):
        '''
        Check single address for payment activity.
        Updates BitcoinAddress status as needed.
        Returns True if state changed.
        '''
        if btc_address.status == 'confirmed':
            # Already confirmed — remove from watch
            self.unwatch_address(btc_address)
            return False

        # Try Electrum first, fall back to mempool.space
        if self.electrum and self.electrum.is_connected:
            return self._check_via_electrum(btc_address)
        elif self.mempool:
            if self.electrum:
                log.warning('Electrum unavailable, falling back to mempool.space '
                            'for address %s — this exposes addresses to a public API',
                            btc_address.address)
            return self._check_via_mempool(btc_address)
        else:
            log.warning('No payment checking backend available')
            return False

    def _check_via_electrum(self, btc_address):
        '''Check address via Electrum protocol.'''
        script_hash = btc_address.script_hash
        if not script_hash:
            log.warning('No script_hash for address %s', btc_address.address)
            return False

        balance = self.electrum.scripthash_get_balance(script_hash)
        confirmed = balance.get('confirmed', 0)
        unconfirmed = balance.get('unconfirmed', 0)

        total = confirmed + unconfirmed

        if total <= 0:
            return False

        if btc_address.status == 'assigned' or btc_address.status == 'unused':
            # First time seeing funds
            txid = self._get_latest_txid_electrum(script_hash)
            btc_address.mark_seen(total)
            self._fire_seen(btc_address, total, txid)

            if confirmed > 0:
                # Already has confirmations
                confs = self._get_confirmations_electrum(script_hash, txid)
                required = self._required_confirmations(btc_address)
                if confs >= required:
                    btc_address.mark_confirmed(confirmed)
                    self._fire_confirmed(btc_address, confirmed, confs)
            return True

        elif btc_address.status == 'seen':
            # Check if now confirmed
            if confirmed > 0:
                txid = self._get_latest_txid_electrum(script_hash)
                confs = self._get_confirmations_electrum(script_hash, txid)
                required = self._required_confirmations(btc_address)
                if confs >= required:
                    btc_address.mark_confirmed(confirmed)
                    self._fire_confirmed(btc_address, confirmed, confs)
                    return True
            # Update amount if changed
            if total != btc_address.amount_received_sat:
                btc_address.amount_received_sat = total
                btc_address.save()
                return True

        return False

    def _check_via_mempool(self, btc_address):
        '''Check address via mempool.space API.'''
        address = btc_address.address

        confirmed_sat, unconfirmed_sat = self.mempool.get_address_balance(address)
        total = confirmed_sat + unconfirmed_sat

        if total <= 0:
            return False

        if btc_address.status in ('assigned', 'unused'):
            # First time seeing funds — get txid
            txid = self._get_latest_txid_mempool(address)
            btc_address.mark_seen(total)
            self._fire_seen(btc_address, total, txid)

            if confirmed_sat > 0 and txid:
                confs = self.mempool.get_confirmations(txid)
                required = self._required_confirmations(btc_address)
                if confs >= required:
                    btc_address.mark_confirmed(confirmed_sat)
                    self._fire_confirmed(btc_address, confirmed_sat, confs)
            return True

        elif btc_address.status == 'seen':
            if confirmed_sat > 0:
                txid = self._get_latest_txid_mempool(address)
                if txid:
                    confs = self.mempool.get_confirmations(txid)
                    required = self._required_confirmations(btc_address)
                    if confs >= required:
                        btc_address.mark_confirmed(confirmed_sat)
                        self._fire_confirmed(btc_address, confirmed_sat, confs)
                        return True
            if total != btc_address.amount_received_sat:
                btc_address.amount_received_sat = total
                btc_address.save()
                return True

        return False

    def _get_latest_txid_electrum(self, script_hash):
        '''Get the most recent txid for a script hash via Electrum.'''
        try:
            history = self.electrum.scripthash_get_history(script_hash)
            if history:
                return history[-1].get('tx_hash', '')
        except Exception:
            pass
        return ''

    def _get_confirmations_electrum(self, script_hash, txid):
        '''Get confirmation count via Electrum.'''
        try:
            history = self.electrum.scripthash_get_history(script_hash)
            header = self.electrum.headers_subscribe()
            current_height = header.get('height', 0) if header else 0

            for entry in history:
                if entry.get('tx_hash') == txid:
                    height = entry.get('height', 0)
                    if height > 0 and current_height > 0:
                        return current_height - height + 1
                    return 0
        except Exception:
            pass
        return 0

    def _get_latest_txid_mempool(self, address):
        '''Get the most recent txid for an address via mempool.space.'''
        try:
            txs = self.mempool.get_address_txs(address)
            if txs:
                return txs[0].get('txid', '')
        except Exception:
            pass
        return ''

    def _required_confirmations(self, btc_address):
        '''
        Determine required confirmations based on received satoshi amount.
        Uses configured thresholds (satoshi_amount, required_confs).
        '''
        amount_sat = btc_address.amount_received_sat or 0

        for threshold_sat, confs in self.confirmation_thresholds:
            if threshold_sat is None:
                return confs
            if amount_sat < threshold_sat:
                return confs

        return 6  # fallback

    def _fire_seen(self, btc_address, amount_sat, txid):
        '''Fire payment-seen callbacks.'''
        log.info('Payment seen: %s, %d sat, txid=%s',
                 btc_address.address, amount_sat, txid or 'unknown')
        for cb in self._on_seen_callbacks:
            try:
                cb(btc_address, amount_sat, txid)
            except Exception as e:
                log.error('Payment seen callback error: %s', e)

    def _fire_confirmed(self, btc_address, amount_sat, confirmations):
        '''Fire payment-confirmed callbacks.'''
        log.info('Payment confirmed: %s, %d sat, %d confs',
                 btc_address.address, amount_sat, confirmations)
        for cb in self._on_confirmed_callbacks:
            try:
                cb(btc_address, amount_sat, confirmations)
            except Exception as e:
                log.error('Payment confirmed callback error: %s', e)

    def load_assigned_addresses(self):
        '''
        Load all currently assigned addresses into the watch list.
        Call at startup to resume monitoring.
        '''
        from btpay.bitcoin.models import BitcoinAddress

        addresses = BitcoinAddress.query.filter(status='assigned').all()
        addresses += BitcoinAddress.query.filter(status='seen').all()

        with self._lock:
            for addr in addresses:
                self._watched[addr.address] = addr

        log.info('Loaded %d addresses for monitoring', len(addresses))

# EOF
