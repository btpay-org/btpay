#
# Recurring invoices — model, scheduling, and generation.
#
import logging
import threading
import calendar
from decimal import Decimal

from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import (
    Text, Integer, Boolean, DecimalColumn, DateTimeColumn, JsonColumn, TagsColumn,
)
from btpay.chrono import NOW
from btpay.orm.persistence import save_to_disk

log = logging.getLogger(__name__)


# ---- RecurringInvoice Model ----

class RecurringInvoice(BaseMixin, MemModel):
    org_id                  = Integer(index=True)
    name                    = Text()                    # template name
    status                  = Text(default='active')    # active/paused/completed/cancelled

    # Customer info
    customer_email          = Text()
    customer_name           = Text()
    customer_company        = Text()

    # Billing
    currency                = Text(default='USD')
    lines                   = JsonColumn()              # [{description, quantity, unit_price}]
    tax_rate                = DecimalColumn(default=Decimal('0'))
    discount_amount         = DecimalColumn(default=Decimal('0'))
    payment_methods_enabled = TagsColumn()
    notes                   = Text()

    # Schedule
    frequency               = Text()                    # weekly/biweekly/monthly/quarterly/yearly/custom
    custom_interval_days    = Integer(default=0)
    anchor_day              = Integer(default=1)         # day of month for monthly (1-31)
    start_date              = DateTimeColumn()
    max_occurrences         = Integer(default=0)         # 0 = unlimited
    occurrences_generated   = Integer(default=0)
    next_run_at             = DateTimeColumn()
    last_run_at             = DateTimeColumn(default=0)

    # Automation
    auto_finalize           = Boolean(default=False)
    auto_send_email         = Boolean(default=False)

    created_by_user_id      = Integer(default=0)


# ---- Schedule computation ----

def compute_next_run(template, from_date):
    """
    Compute the next run date from `from_date` based on the template's
    frequency and anchor_day settings.

    Returns a pendulum DateTime (UTC).
    """
    import pendulum

    freq = template.frequency
    anchor = max(template.anchor_day or 1, 1)  # clamp to at least day 1

    if freq == 'weekly':
        return from_date.add(weeks=1)

    elif freq == 'biweekly':
        return from_date.add(weeks=2)

    elif freq == 'monthly':
        return _advance_months(from_date, 1, anchor)

    elif freq == 'quarterly':
        return _advance_months(from_date, 3, anchor)

    elif freq == 'yearly':
        return _advance_years(from_date, 1, anchor)

    elif freq == 'custom':
        days = template.custom_interval_days or 1
        if days < 1:
            days = 1  # prevent backward/zero advancement
        return from_date.add(days=days)

    else:
        # Fallback: monthly
        log.warning('Unknown frequency %r, defaulting to monthly', freq)
        return _advance_months(from_date, 1, anchor)


def _advance_months(from_date, months, anchor_day):
    """Advance by `months` months, clamping day to anchor_day or end-of-month."""
    import pendulum

    new_month = from_date.month + months
    new_year = from_date.year + (new_month - 1) // 12
    new_month = ((new_month - 1) % 12) + 1
    max_day = calendar.monthrange(new_year, new_month)[1]
    day = min(anchor_day, max_day)
    return pendulum.datetime(new_year, new_month, day,
                             from_date.hour, from_date.minute, from_date.second,
                             tz='UTC')


def _advance_years(from_date, years, anchor_day):
    """Advance by `years` years, handling Feb 29 -> Feb 28 for non-leap years."""
    import pendulum

    new_year = from_date.year + years
    month = from_date.month
    max_day = calendar.monthrange(new_year, month)[1]
    day = min(from_date.day, max_day)
    return pendulum.datetime(new_year, month, day,
                             from_date.hour, from_date.minute, from_date.second,
                             tz='UTC')


# ---- Scheduler ----

class RecurringInvoiceScheduler:
    """
    Background daemon thread that periodically checks for recurring invoice
    templates that are due and generates invoices from them.

    Same pattern as AutoSaver.
    """

    def __init__(self, check_interval=60):
        self.check_interval = check_interval
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(
            target=self._check_loop, daemon=True, name='recurring-invoices')
        self._thread.start()
        log.info('RecurringInvoiceScheduler started (interval=%ds)', self.check_interval)

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _check_loop(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(self.check_interval)
            if self._stop_event.is_set():
                break
            try:
                self._process_due_templates()
            except Exception:
                log.exception('RecurringInvoiceScheduler error')

    def _process_due_templates(self):
        """Find active templates where next_run_at <= NOW() and generate invoices."""
        now = NOW()
        templates = RecurringInvoice.query.filter(status='active').all()

        for tmpl in templates:
            if tmpl.next_run_at is None:
                continue
            if tmpl.next_run_at > now:
                continue

            try:
                self._generate_invoice(tmpl, now)
            except Exception:
                log.exception('Failed to generate invoice for recurring template id=%s',
                              tmpl.id)

    def _generate_invoice(self, tmpl, now):
        """Generate a single invoice from the recurring template."""
        from btpay.invoicing.models import Invoice
        from btpay.invoicing.service import InvoiceService
        from btpay.auth.models import Organization, User

        occurrence_number = tmpl.occurrences_generated + 1

        # ANTI-DOUBLE: Check if an invoice with this recurring_id + occurrence already exists
        existing = Invoice.query.filter(org_id=tmpl.org_id).all()
        for inv in existing:
            meta = inv.metadata or {}
            if (meta.get('recurring_id') == tmpl.id and
                    meta.get('recurring_occurrence') == occurrence_number):
                log.info('Skipping duplicate: recurring_id=%d, occurrence=%d already exists',
                         tmpl.id, occurrence_number)
                # Advance the template anyway (crash recovery case)
                tmpl.occurrences_generated = occurrence_number
                tmpl.last_run_at = now
                tmpl.next_run_at = compute_next_run(tmpl, now)
                if tmpl.max_occurrences > 0 and tmpl.occurrences_generated >= tmpl.max_occurrences:
                    tmpl.status = 'completed'
                tmpl.save()
                return

        org = Organization.get(tmpl.org_id)
        if org is None:
            log.error('Organization %d not found for recurring template %d',
                      tmpl.org_id, tmpl.id)
            return

        user = User.get(tmpl.created_by_user_id) if tmpl.created_by_user_id else None

        # Build InvoiceService — get exchange rate service from Flask app context if available
        exchange_rate_service = None
        try:
            from flask import current_app
            exchange_rate_service = getattr(current_app, '_exchange_rate_service', None)
        except RuntimeError:
            pass

        svc = InvoiceService(exchange_rate_service=exchange_rate_service)

        lines = tmpl.lines or []
        payment_methods = list(tmpl.payment_methods_enabled) if tmpl.payment_methods_enabled else None

        invoice = svc.create_invoice(
            org=org,
            user=user,
            lines=lines,
            customer_email=tmpl.customer_email or '',
            customer_name=tmpl.customer_name or '',
            customer_company=tmpl.customer_company or '',
            currency=tmpl.currency or 'USD',
            tax_rate=tmpl.tax_rate or 0,
            discount_amount=tmpl.discount_amount or 0,
            notes=tmpl.notes or '',
            payment_methods=payment_methods,
            metadata={
                'recurring_id': tmpl.id,
                'recurring_occurrence': occurrence_number,
            },
        )

        log.info('Generated invoice %s from recurring template %d (occurrence %d)',
                 invoice.invoice_number, tmpl.id, occurrence_number)

        # Auto-finalize if configured and a wallet exists
        if tmpl.auto_finalize:
            try:
                from btpay.bitcoin.models import Wallet
                wallet = Wallet.query.filter(org_id=tmpl.org_id, is_active=True).first()
                svc.finalize_invoice(invoice, wallet)
                log.info('Auto-finalized invoice %s', invoice.invoice_number)
            except Exception:
                log.exception('Auto-finalize failed for invoice %s', invoice.invoice_number)

        # Advance template
        tmpl.occurrences_generated = occurrence_number
        tmpl.last_run_at = now
        tmpl.next_run_at = compute_next_run(tmpl, now)

        if tmpl.max_occurrences > 0 and tmpl.occurrences_generated >= tmpl.max_occurrences:
            tmpl.status = 'completed'
            log.info('Recurring template %d completed (%d/%d occurrences)',
                     tmpl.id, tmpl.occurrences_generated, tmpl.max_occurrences)

        tmpl.save()

        # Flush to disk
        try:
            from flask import current_app
            data_dir = current_app.config.get('DATA_DIR', 'data')
            save_to_disk(data_dir)
        except RuntimeError:
            pass

# EOF
