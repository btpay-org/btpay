#
# Dashboard views
#
import logging
from decimal import Decimal
from flask import Blueprint, render_template, g, current_app

from btpay.auth.decorators import login_required
from btpay.chrono import NOW, TIME_AGO, as_time_t

log = logging.getLogger(__name__)


def _to_ts(val):
    '''Convert a datetime or timestamp to float. Safe for sorting.'''
    import datetime
    if isinstance(val, datetime.datetime):
        return as_time_t(val)
    if isinstance(val, (int, float)):
        return float(val)
    return 0.0

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard')
@login_required
def index():
    '''Main dashboard with stats and recent invoices.'''
    from btpay.invoicing.models import Invoice, Payment

    org_id = g.org.id if g.org else 0

    # All invoices for this org
    all_invoices = Invoice.query.filter(org_id=org_id).all()

    # Revenue — sum of paid/confirmed invoices
    paid_statuses = ('paid', 'confirmed')
    total_revenue = sum(
        (inv.total or Decimal('0'))
        for inv in all_invoices
        if inv.status in paid_statuses
    )

    # Pending count
    pending_count = sum(1 for inv in all_invoices if inv.status in ('pending', 'partial'))

    # Paid in last 30 days
    thirty_days_ago_ts = as_time_t(TIME_AGO(days=30))
    paid_30d = sum(
        (inv.total or Decimal('0'))
        for inv in all_invoices
        if inv.status in paid_statuses and inv.paid_at and _to_ts(inv.paid_at) > thirty_days_ago_ts
    )

    # BTC rate
    btc_rate = None
    currency = g.org.default_currency if g.org else 'USD'
    if hasattr(current_app, '_exchange_rate_service'):
        btc_rate = current_app._exchange_rate_service.get_rate(currency)

    # Recent invoices (last 10)
    recent = sorted(all_invoices, key=lambda i: _to_ts(i.created_at), reverse=True)[:10]

    return render_template('dashboard/index.html',
        total_revenue=total_revenue,
        pending_count=pending_count,
        paid_30d=paid_30d,
        btc_rate=btc_rate,
        currency=currency,
        recent_invoices=recent,
    )

# EOF
