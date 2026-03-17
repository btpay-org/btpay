#
# BTCPay Server payment monitor — polls invoice status
#
# Watches BTCPay invoices for status changes (Processing, Settled).
# Mirrors the StablecoinMonitor pattern for consistency.
#
import logging
import threading
import time

log = logging.getLogger(__name__)


class BTCPayMonitor:
    '''
    Background watcher for BTCPay Server invoice payments.

    Polls BTCPay invoice status and fires callbacks when payment
    is detected (Processing) or confirmed (Settled).

    Usage:
        monitor = BTCPayMonitor()
        monitor.on_payment(callback)
        monitor.start()
        monitor.watch(invoice_id, btcpay_invoice_id, connector)
        monitor.stop()
    '''

    def __init__(self, check_interval=30):
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
            name='btcpay-monitor',
            daemon=True,
        )
        self._thread.start()
        log.info('BTCPay monitor started (interval=%ds)', self.check_interval)

    def stop(self):
        '''Stop monitoring thread.'''
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        log.info('BTCPay monitor stopped')

    def watch(self, invoice_id, btcpay_invoice_id, connector):
        '''
        Add a BTCPay invoice to the watch list.

        Args:
            invoice_id: BTPay invoice ID to credit
            btcpay_invoice_id: BTCPay Server invoice ID
            connector: BTCPayConnector model instance
        '''
        entry = BTCPayWatchEntry(
            invoice_id=invoice_id,
            btcpay_invoice_id=btcpay_invoice_id,
            connector=connector,
        )
        with self._lock:
            self._watched[invoice_id] = entry
        log.debug('Watching BTCPay invoice %s for BTPay invoice %s',
                  btcpay_invoice_id, invoice_id)

    def unwatch(self, invoice_id):
        '''Remove an invoice from the watch list.'''
        with self._lock:
            self._watched.pop(invoice_id, None)

    def on_payment(self, callback):
        '''
        Register callback for when payment status changes.
        callback(invoice_id, btcpay_status, btcpay_data)

        btcpay_status is one of: 'Processing', 'Settled'
        '''
        self._on_payment_callbacks.append(callback)

    @property
    def watched_count(self):
        with self._lock:
            return len(self._watched)

    # ---- Internal ----

    def _check_loop(self):
        '''Main loop: iterate watched entries, check statuses.'''
        while not self._stop_event.is_set():
            with self._lock:
                entries = list(self._watched.items())

            for invoice_id, entry in entries:
                if self._stop_event.is_set():
                    break
                try:
                    self._check_entry(invoice_id, entry)
                except Exception as e:
                    log.error('Error checking BTCPay invoice %s: %s',
                              entry.btcpay_invoice_id, e)

            self._stop_event.wait(timeout=self.check_interval)

    def _check_entry(self, invoice_id, entry):
        '''Check a single BTCPay invoice for status change.'''
        if entry.settled:
            return

        from btpay.connectors.btcpay import BTCPayClient, BTCPayError
        client = BTCPayClient.from_connector(entry.connector)

        try:
            data = client.get_invoice(entry.btcpay_invoice_id)
        except BTCPayError as e:
            log.debug('BTCPay status check failed for %s: %s',
                      entry.btcpay_invoice_id, e)
            return

        status = data.get('status', '')

        # Track status transitions
        if status == entry.last_status:
            return

        old_status = entry.last_status
        entry.last_status = status
        entry.last_checked = time.time()

        if status == 'Processing' and old_status != 'Processing':
            log.info('BTCPay invoice %s processing (BTPay invoice %s)',
                     entry.btcpay_invoice_id, invoice_id)
            self._fire_payment(invoice_id, status, data)

        elif status == 'Settled':
            entry.settled = True
            log.info('BTCPay invoice %s settled (BTPay invoice %s)',
                     entry.btcpay_invoice_id, invoice_id)
            self._fire_payment(invoice_id, status, data)

            # Remove from watch list
            with self._lock:
                self._watched.pop(invoice_id, None)

        elif status in ('Expired', 'Invalid'):
            log.warning('BTCPay invoice %s %s (BTPay invoice %s)',
                        entry.btcpay_invoice_id, status, invoice_id)
            # Remove from watch list
            with self._lock:
                self._watched.pop(invoice_id, None)

    def _fire_payment(self, invoice_id, btcpay_status, btcpay_data):
        '''Fire payment callbacks.'''
        for cb in self._on_payment_callbacks:
            try:
                cb(invoice_id, btcpay_status, btcpay_data)
            except Exception as e:
                log.error('BTCPay payment callback error: %s', e)


class BTCPayWatchEntry:
    '''A single BTCPay invoice being watched.'''

    __slots__ = ('invoice_id', 'btcpay_invoice_id', 'connector',
                 'last_status', 'last_checked', 'settled')

    def __init__(self, invoice_id, btcpay_invoice_id, connector):
        self.invoice_id = invoice_id
        self.btcpay_invoice_id = btcpay_invoice_id
        self.connector = connector
        self.last_status = 'New'
        self.last_checked = 0
        self.settled = False

# EOF
