#
# Storefront fulfillment — post-payment side-effects
#
# Called when an invoice transitions to 'paid'.  Updates storefront stats
# and decrements inventory.  Idempotent: uses a metadata flag to prevent
# double-counting from webhook replays or monitor re-fires.
#
import logging
from decimal import Decimal

log = logging.getLogger(__name__)


def fulfill_storefront_invoice(invoice):
    '''
    If *invoice* originated from a storefront purchase, update the
    storefront's stats and decrement item inventory.

    Safe to call multiple times — a ``storefront_fulfilled`` flag in the
    invoice metadata guards against double-application.
    '''
    meta = invoice.metadata
    if not meta or not isinstance(meta, dict):
        return
    if meta.get('source') not in ('storefront', 'donation', 'storefront_cart'):
        return

    # Idempotency guard
    if meta.get('storefront_fulfilled'):
        return

    from btpay.storefront.models import Storefront, StorefrontItem

    sf = Storefront.get(meta.get('storefront_id'))
    if sf is None:
        log.warning('Storefront %s not found for invoice %s',
                    meta.get('storefront_id'), invoice.invoice_number)
        return

    # --- Update storefront counters ---
    # Use amount_paid (actual collected) rather than invoice.total because
    # InvoiceService can accept small underpayments via the underpaid_gift
    # threshold — invoice.total would overstate actual revenue.
    sf.total_orders = sf.total_orders + 1
    sf.total_revenue = sf.total_revenue + (invoice.amount_paid or Decimal('0'))
    sf.save()

    # --- Decrement inventory ---
    source = meta['source']

    if source in ('storefront',):
        # Single-item purchase
        item_id = meta.get('item_id')
        if item_id:
            item = StorefrontItem.get(item_id)
            if item:
                item.decrement_inventory()

    elif source == 'storefront_cart':
        # Cart purchase — per-item quantities stored in metadata
        cart_items = meta.get('cart_items', [])
        for ci in cart_items:
            item = StorefrontItem.get(ci.get('item_id'))
            if item is None:
                continue
            qty = int(ci.get('quantity', 1))
            for _ in range(qty):
                item.decrement_inventory()

    # source == 'donation' has no inventory to decrement

    # --- Mark as fulfilled ---
    meta['storefront_fulfilled'] = True
    invoice.metadata = meta
    invoice.save()

    log.info('Storefront fulfillment complete for invoice %s (sf=%s, source=%s)',
             invoice.invoice_number, sf.slug, source)

# EOF
