#
# LNbits payment monitor — polls invoice payment status
#
# Watches LNbits invoices for payment confirmation.
# Lightning payments are instant, so a short poll interval is used.
# Mirrors the StablecoinMonitor pattern for consistency.
#
import logging
import threading
import time

log = logging.getLogger(__name__)


class LNbitsMonitor:
    '''
    Background watcher for LNbits Lightning invoice payments.

    Polls LNbits payment status and fires callbacks when
    payment is detected (paid: true).

    Usage:
        monitor = LNbitsMonitor()
        monitor.on_payment(callback)
        monitor.start()
        monitor.watch(invoice_id, payment_hash, connector)
        monitor.stop()
    '''

    def __init__(self, check_interval=15):
        self.check_interval = check_interval

        # Watched entries: {invoice_id: WatchEntry}
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
            name='lnbits-monitor',
            daemon=True,
        )
        self._thread.start()
        log.info('LNbits monitor started (interval=%ds)', self.check_interval)

    def stop(self):
        '''Stop monitoring thread.'''
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        log.info('LNbits monitor stopped')

    def watch(self, invoice_id, payment_hash, connector):
        '''
        Add a Lightning invoice to the watch list.

        Args:
            invoice_id: BTPay invoice ID to credit
            payment_hash: LNbits payment hash
            connector: LNbitsConnector model instance
        '''
        entry = LNbitsWatchEntry(
            invoice_id=invoice_id,
            payment_hash=payment_hash,
            connector=connector,
        )
        with self._lock:
            self._watched[invoice_id] = entry
        log.debug('Watching LNbits payment %s for BTPay invoice %s',
                  payment_hash[:16], invoice_id)

    def unwatch(self, invoice_id):
        '''Remove an invoice from the watch list.'''
        with self._lock:
            self._watched.pop(invoice_id, None)

    def on_payment(self, callback):
        '''
        Register callback for when payment is detected.
        callback(invoice_id, payment_data)
        '''
        self._on_payment_callbacks.append(callback)

    @property
    def watched_count(self):
        with self._lock:
            return len(self._watched)

    # ---- Internal ----

    def _check_loop(self):
        '''Main loop: iterate watched entries, check payment status.'''
        while not self._stop_event.is_set():
            with self._lock:
                entries = list(self._watched.items())

            for invoice_id, entry in entries:
                if self._stop_event.is_set():
                    break
                try:
                    self._check_entry(invoice_id, entry)
                except Exception as e:
                    log.error('Error checking LNbits payment %s: %s',
                              entry.payment_hash[:16], e)

            self._stop_event.wait(timeout=self.check_interval)

    def _check_entry(self, invoice_id, entry):
        '''Check a single LNbits payment for completion.'''
        if entry.paid:
            return

        from btpay.connectors.lnbits import LNbitsClient, LNbitsError
        client = LNbitsClient.from_connector(entry.connector)

        try:
            data = client.check_payment(entry.payment_hash)
        except LNbitsError as e:
            log.debug('LNbits payment check failed for %s: %s',
                      entry.payment_hash[:16], e)
            return

        is_paid = data.get('paid', False)

        if is_paid and not entry.paid:
            entry.paid = True
            entry.last_checked = time.time()
            log.info('LNbits payment confirmed: hash=%s (BTPay invoice %s)',
                     entry.payment_hash[:16], invoice_id)
            self._fire_payment(invoice_id, data)

            # Remove from watch list — Lightning is final
            with self._lock:
                self._watched.pop(invoice_id, None)

    def _fire_payment(self, invoice_id, payment_data):
        '''Fire payment callbacks.'''
        for cb in self._on_payment_callbacks:
            try:
                cb(invoice_id, payment_data)
            except Exception as e:
                log.error('LNbits payment callback error: %s', e)


class LNbitsWatchEntry:
    '''A single LNbits payment being watched.'''

    __slots__ = ('invoice_id', 'payment_hash', 'connector',
                 'last_checked', 'paid')

    def __init__(self, invoice_id, payment_hash, connector):
        self.invoice_id = invoice_id
        self.payment_hash = payment_hash
        self.connector = connector
        self.last_checked = 0
        self.paid = False

# EOF
