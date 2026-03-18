#
# Tests for recurring invoices — model, scheduling, and generation.
#
import pytest
import pendulum
from decimal import Decimal
from unittest.mock import MagicMock, patch


# ======================================================================
# Helpers
# ======================================================================

def _make_org(**kw):
    from btpay.auth.models import Organization
    defaults = dict(name='Test Org', slug='test-org', default_currency='USD')
    defaults.update(kw)
    org = Organization(**defaults)
    org.save()
    return org


def _make_user(**kw):
    from btpay.auth.models import User
    defaults = dict(email='recur@example.com', first_name='Test', last_name='User')
    defaults.update(kw)
    user = User(**defaults)
    user.set_password('testpass123')
    user.save()
    return user


def _make_wallet(org, xpub=None):
    from btpay.bitcoin.models import Wallet
    xpub = xpub or 'xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8'
    w = Wallet(org_id=org.id, name='Test Wallet', wallet_type='xpub',
               xpub=xpub, derivation_path='m/0')
    w.save()
    return w


def _make_recurring(org, user, **kw):
    """Create a recurring invoice template with sensible defaults."""
    from btpay.invoicing.recurring import RecurringInvoice, compute_next_run

    defaults = dict(
        org_id=org.id,
        name='Monthly Hosting',
        status='active',
        customer_email='customer@example.com',
        customer_name='Jane Doe',
        currency='USD',
        lines=[{'description': 'Hosting', 'quantity': '1', 'unit_price': '100'}],
        frequency='monthly',
        anchor_day=1,
        start_date=pendulum.datetime(2025, 1, 1, tz='UTC'),
        created_by_user_id=user.id,
    )
    defaults.update(kw)

    tmpl = RecurringInvoice(**defaults)
    if tmpl.next_run_at is None:
        tmpl.next_run_at = compute_next_run(tmpl, tmpl.start_date)
    tmpl.save()
    return tmpl


# ======================================================================
# Model Creation & Fields
# ======================================================================

class TestRecurringInvoiceModel:

    def test_create_recurring(self):
        from btpay.invoicing.recurring import RecurringInvoice
        org = _make_org()
        tmpl = RecurringInvoice(
            org_id=org.id,
            name='Test Recurring',
            status='active',
            frequency='monthly',
        )
        tmpl.save()
        assert tmpl.id > 0
        assert tmpl.status == 'active'
        assert tmpl.frequency == 'monthly'

    def test_default_values(self):
        from btpay.invoicing.recurring import RecurringInvoice
        tmpl = RecurringInvoice(org_id=1, name='Defaults')
        tmpl.save()
        assert tmpl.status == 'active'
        assert tmpl.currency == 'USD'
        assert tmpl.tax_rate == Decimal('0')
        assert tmpl.discount_amount == Decimal('0')
        assert tmpl.max_occurrences == 0
        assert tmpl.occurrences_generated == 0
        assert tmpl.custom_interval_days == 0
        assert tmpl.anchor_day == 1
        assert tmpl.auto_finalize is False
        assert tmpl.auto_send_email is False

    def test_lines_json_storage(self):
        from btpay.invoicing.recurring import RecurringInvoice
        lines = [
            {'description': 'Widget A', 'quantity': '2', 'unit_price': '50'},
            {'description': 'Widget B', 'quantity': '1', 'unit_price': '75'},
        ]
        tmpl = RecurringInvoice(org_id=1, name='Lines Test', lines=lines)
        tmpl.save()
        loaded = RecurringInvoice.get(tmpl.id)
        assert len(loaded.lines) == 2
        assert loaded.lines[0]['description'] == 'Widget A'

    def test_payment_methods_tags(self):
        from btpay.invoicing.recurring import RecurringInvoice
        tmpl = RecurringInvoice(
            org_id=1, name='Tags Test',
            payment_methods_enabled={'onchain_btc', 'wire'},
        )
        tmpl.save()
        loaded = RecurringInvoice.get(tmpl.id)
        assert 'onchain_btc' in loaded.payment_methods_enabled
        assert 'wire' in loaded.payment_methods_enabled


# ======================================================================
# compute_next_run
# ======================================================================

class TestComputeNextRun:

    def _make_tmpl(self, frequency='monthly', anchor_day=1, custom_interval_days=0):
        """Helper to create an in-memory template stub."""
        from btpay.invoicing.recurring import RecurringInvoice
        return RecurringInvoice(
            org_id=1,
            name='test',
            frequency=frequency,
            anchor_day=anchor_day,
            custom_interval_days=custom_interval_days,
        )

    def test_weekly(self):
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('weekly')
        base = pendulum.datetime(2025, 3, 10, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result == pendulum.datetime(2025, 3, 17, tz='UTC')

    def test_biweekly(self):
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('biweekly')
        base = pendulum.datetime(2025, 3, 10, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result == pendulum.datetime(2025, 3, 24, tz='UTC')

    def test_monthly(self):
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('monthly', anchor_day=15)
        base = pendulum.datetime(2025, 1, 15, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result == pendulum.datetime(2025, 2, 15, tz='UTC')

    def test_monthly_anchor_day_31_feb(self):
        """anchor_day=31 in February should clamp to 28."""
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('monthly', anchor_day=31)
        base = pendulum.datetime(2025, 1, 31, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result.day == 28  # Feb 2025 has 28 days
        assert result.month == 2

    def test_monthly_anchor_day_31_apr(self):
        """anchor_day=31 in April should clamp to 30."""
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('monthly', anchor_day=31)
        base = pendulum.datetime(2025, 3, 31, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result.day == 30  # April has 30 days
        assert result.month == 4

    def test_monthly_anchor_day_31_jun(self):
        """anchor_day=31 in June should clamp to 30."""
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('monthly', anchor_day=31)
        base = pendulum.datetime(2025, 5, 31, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result.day == 30  # June has 30 days
        assert result.month == 6

    def test_monthly_year_rollover(self):
        """December -> January next year."""
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('monthly', anchor_day=15)
        base = pendulum.datetime(2025, 12, 15, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result == pendulum.datetime(2026, 1, 15, tz='UTC')

    def test_quarterly(self):
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('quarterly', anchor_day=1)
        base = pendulum.datetime(2025, 1, 1, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result == pendulum.datetime(2025, 4, 1, tz='UTC')

    def test_quarterly_clamping(self):
        """Quarterly with anchor_day=31, Jan -> Apr (30 days)."""
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('quarterly', anchor_day=31)
        base = pendulum.datetime(2025, 1, 31, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result.month == 4
        assert result.day == 30

    def test_yearly(self):
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('yearly')
        base = pendulum.datetime(2025, 6, 15, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result == pendulum.datetime(2026, 6, 15, tz='UTC')

    def test_yearly_leap_year_feb29(self):
        """Feb 29 yearly advance to non-leap year should go to Feb 28."""
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('yearly')
        base = pendulum.datetime(2024, 2, 29, tz='UTC')  # 2024 is leap year
        result = compute_next_run(tmpl, base)
        assert result == pendulum.datetime(2025, 2, 28, tz='UTC')

    def test_yearly_to_leap_year(self):
        """Feb 28 yearly advance to leap year stays Feb 28."""
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('yearly')
        base = pendulum.datetime(2023, 2, 28, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result == pendulum.datetime(2024, 2, 28, tz='UTC')

    def test_custom_interval(self):
        from btpay.invoicing.recurring import compute_next_run
        tmpl = self._make_tmpl('custom', custom_interval_days=10)
        base = pendulum.datetime(2025, 3, 1, tz='UTC')
        result = compute_next_run(tmpl, base)
        assert result == pendulum.datetime(2025, 3, 11, tz='UTC')


# ======================================================================
# Scheduler
# ======================================================================

class TestRecurringScheduler:

    def test_scheduler_generates_invoice_when_due(self):
        """Scheduler should generate an invoice for a due template."""
        from btpay.invoicing.recurring import RecurringInvoiceScheduler
        from btpay.invoicing.models import Invoice

        org = _make_org(slug='sched-org')
        user = _make_user(email='sched@example.com')

        # Create template with next_run_at in the past
        tmpl = _make_recurring(org, user,
            next_run_at=pendulum.datetime(2025, 1, 1, tz='UTC'),
        )

        scheduler = RecurringInvoiceScheduler()
        scheduler._process_due_templates()

        # Should have generated 1 invoice
        invoices = Invoice.query.filter(org_id=org.id).all()
        recurring_invoices = [i for i in invoices
                              if (i.metadata or {}).get('recurring_id') == tmpl.id]
        assert len(recurring_invoices) == 1

        inv = recurring_invoices[0]
        assert inv.customer_name == 'Jane Doe'
        assert inv.metadata['recurring_occurrence'] == 1

        # Template should be advanced
        tmpl.reload()
        assert tmpl.occurrences_generated == 1
        assert tmpl.next_run_at > pendulum.datetime(2025, 1, 1, tz='UTC')

    def test_double_invoice_prevention(self):
        """CRITICAL: Running scheduler twice should NOT create duplicate invoices."""
        from btpay.invoicing.recurring import RecurringInvoiceScheduler
        from btpay.invoicing.models import Invoice

        org = _make_org(slug='double-org')
        user = _make_user(email='double@example.com')

        tmpl = _make_recurring(org, user,
            next_run_at=pendulum.datetime(2025, 1, 1, tz='UTC'),
        )

        scheduler = RecurringInvoiceScheduler()

        # Run scheduler once
        scheduler._process_due_templates()

        # Force next_run_at back into the past to simulate re-run
        tmpl.reload()
        tmpl.next_run_at = pendulum.datetime(2025, 1, 1, tz='UTC')
        tmpl.occurrences_generated = 0  # Simulate "not advanced" crash state
        tmpl.save()

        # Run scheduler again
        scheduler._process_due_templates()

        # Should still only have 1 invoice
        invoices = Invoice.query.filter(org_id=org.id).all()
        recurring_invoices = [i for i in invoices
                              if (i.metadata or {}).get('recurring_id') == tmpl.id]
        assert len(recurring_invoices) == 1

    def test_pause_prevents_generation(self):
        """Paused templates should not generate invoices."""
        from btpay.invoicing.recurring import RecurringInvoiceScheduler
        from btpay.invoicing.models import Invoice

        org = _make_org(slug='pause-org')
        user = _make_user(email='pause@example.com')

        tmpl = _make_recurring(org, user,
            status='paused',
            next_run_at=pendulum.datetime(2025, 1, 1, tz='UTC'),
        )

        scheduler = RecurringInvoiceScheduler()
        scheduler._process_due_templates()

        invoices = Invoice.query.filter(org_id=org.id).all()
        recurring_invoices = [i for i in invoices
                              if (i.metadata or {}).get('recurring_id') == tmpl.id]
        assert len(recurring_invoices) == 0

    def test_cancel_prevents_generation(self):
        """Cancelled templates should not generate invoices."""
        from btpay.invoicing.recurring import RecurringInvoiceScheduler
        from btpay.invoicing.models import Invoice

        org = _make_org(slug='cancel-org')
        user = _make_user(email='cancel@example.com')

        tmpl = _make_recurring(org, user,
            status='cancelled',
            next_run_at=pendulum.datetime(2025, 1, 1, tz='UTC'),
        )

        scheduler = RecurringInvoiceScheduler()
        scheduler._process_due_templates()

        invoices = Invoice.query.filter(org_id=org.id).all()
        recurring_invoices = [i for i in invoices
                              if (i.metadata or {}).get('recurring_id') == tmpl.id]
        assert len(recurring_invoices) == 0

    def test_max_occurrences_completed(self):
        """Template should transition to 'completed' after max occurrences."""
        from btpay.invoicing.recurring import RecurringInvoiceScheduler
        from btpay.invoicing.models import Invoice

        org = _make_org(slug='max-org')
        user = _make_user(email='max@example.com')

        tmpl = _make_recurring(org, user,
            max_occurrences=2,
            occurrences_generated=1,
            next_run_at=pendulum.datetime(2025, 1, 1, tz='UTC'),
        )

        scheduler = RecurringInvoiceScheduler()
        scheduler._process_due_templates()

        tmpl.reload()
        assert tmpl.occurrences_generated == 2
        assert tmpl.status == 'completed'

    def test_future_next_run_not_generated(self):
        """Templates with future next_run_at should not generate."""
        from btpay.invoicing.recurring import RecurringInvoiceScheduler
        from btpay.invoicing.models import Invoice

        org = _make_org(slug='future-org')
        user = _make_user(email='future@example.com')

        tmpl = _make_recurring(org, user,
            next_run_at=pendulum.now('UTC').add(days=30),
        )

        scheduler = RecurringInvoiceScheduler()
        scheduler._process_due_templates()

        invoices = Invoice.query.filter(org_id=org.id).all()
        recurring_invoices = [i for i in invoices
                              if (i.metadata or {}).get('recurring_id') == tmpl.id]
        assert len(recurring_invoices) == 0

    def test_crash_recovery_invoice_exists_template_not_advanced(self):
        """
        Crash recovery: if an invoice exists for occurrence N but template
        wasn't advanced, the scheduler should skip creation and advance.
        """
        from btpay.invoicing.recurring import RecurringInvoiceScheduler
        from btpay.invoicing.models import Invoice
        from btpay.invoicing.service import InvoiceService

        org = _make_org(slug='crash-org')
        user = _make_user(email='crash@example.com')

        tmpl = _make_recurring(org, user,
            next_run_at=pendulum.datetime(2025, 1, 1, tz='UTC'),
        )

        # Manually create the invoice that "would have been created"
        svc = InvoiceService()
        svc.create_invoice(
            org=org, user=user,
            lines=[{'description': 'Hosting', 'quantity': 1, 'unit_price': 100}],
            metadata={'recurring_id': tmpl.id, 'recurring_occurrence': 1},
        )

        # Template is NOT advanced (simulating crash after invoice creation)
        assert tmpl.occurrences_generated == 0

        scheduler = RecurringInvoiceScheduler()
        scheduler._process_due_templates()

        # Should not create a second invoice
        invoices = Invoice.query.filter(org_id=org.id).all()
        recurring_invoices = [i for i in invoices
                              if (i.metadata or {}).get('recurring_id') == tmpl.id]
        assert len(recurring_invoices) == 1

        # But template should be advanced
        tmpl.reload()
        assert tmpl.occurrences_generated == 1


# ======================================================================
# Resume / Manual Generate
# ======================================================================

class TestRecurringActions:

    def test_resume_recalculates_next_run(self):
        """Resuming a paused template should recalculate next_run_at from now."""
        from btpay.invoicing.recurring import RecurringInvoice, compute_next_run

        org = _make_org(slug='resume-org')
        user = _make_user(email='resume@example.com')

        old_next = pendulum.datetime(2024, 1, 1, tz='UTC')
        tmpl = _make_recurring(org, user, status='paused', next_run_at=old_next)

        # Simulate resume
        tmpl.status = 'active'
        now = pendulum.now('UTC')
        tmpl.next_run_at = compute_next_run(tmpl, now)
        tmpl.save()

        assert tmpl.status == 'active'
        assert tmpl.next_run_at > now

    def test_manual_generate_now(self):
        """Manual generation should create an invoice and advance the counter."""
        from btpay.invoicing.recurring import RecurringInvoiceScheduler
        from btpay.invoicing.models import Invoice

        org = _make_org(slug='manual-org')
        user = _make_user(email='manual@example.com')

        # next_run_at is in the future — manual should still work
        tmpl = _make_recurring(org, user,
            next_run_at=pendulum.now('UTC').add(days=30),
        )

        scheduler = RecurringInvoiceScheduler()
        now = pendulum.now('UTC')
        scheduler._generate_invoice(tmpl, now)

        # Should have created 1 invoice
        invoices = Invoice.query.filter(org_id=org.id).all()
        recurring_invoices = [i for i in invoices
                              if (i.metadata or {}).get('recurring_id') == tmpl.id]
        assert len(recurring_invoices) == 1

        tmpl.reload()
        assert tmpl.occurrences_generated == 1


# ======================================================================
# App Integration
# ======================================================================

class TestRecurringModelRegistered:

    def test_model_registered(self, app):
        from btpay.orm.engine import MemoryStore
        store = MemoryStore()
        assert 'RecurringInvoice' in store._tables

# EOF
