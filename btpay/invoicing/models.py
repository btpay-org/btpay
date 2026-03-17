#
# Invoicing models — Invoice, InvoiceLine, Payment, PaymentLink
#
from decimal import Decimal
from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import (
    Text, Integer, Boolean, DecimalColumn, DateTimeColumn, JsonColumn, TagsColumn,
)
from btpay.chrono import NOW


# ---- Invoice ----

class Invoice(BaseMixin, MemModel):
    org_id              = Integer(index=True)
    invoice_number      = Text(unique=True, index=True)   # e.g. INV-0001
    status              = Text(default='draft', index=True)
    # Statuses: draft | pending | partial | paid | confirmed | expired | cancelled

    # Customer info
    customer_email      = Text()
    customer_name       = Text()
    customer_company    = Text()

    # Currency / amounts
    currency            = Text(default='USD')
    subtotal            = DecimalColumn(default=Decimal('0'))
    tax_amount          = DecimalColumn(default=Decimal('0'))
    tax_rate            = DecimalColumn(default=Decimal('0'))     # percentage
    discount_amount     = DecimalColumn(default=Decimal('0'))
    total               = DecimalColumn(default=Decimal('0'))
    amount_paid         = DecimalColumn(default=Decimal('0'))

    # BTC pricing
    btc_rate            = DecimalColumn(default=Decimal('0'))     # BTC/currency at lock time
    btc_amount          = DecimalColumn(default=Decimal('0'))     # total in BTC
    btc_rate_locked_at  = DateTimeColumn(default=0)               # when rate was locked
    btc_rate_expires_at = DateTimeColumn(default=0)               # when quote expires

    # Payment
    payment_address_id  = Integer(default=0, index=True)
    payment_methods_enabled = TagsColumn()                       # 'onchain_btc', 'wire', etc.

    # Metadata
    metadata            = JsonColumn()
    notes               = Text()
    due_date            = DateTimeColumn(default=0)
    paid_at             = DateTimeColumn(default=0)
    confirmed_at        = DateTimeColumn(default=0)
    expired_at          = DateTimeColumn(default=0)
    cancelled_at        = DateTimeColumn(default=0)
    created_by_user_id  = Integer(default=0)

    @property
    def ref_number(self):
        '''Encrypted reference number for public URLs.'''
        from btpay.security.refnums import ReferenceNumbers
        return ReferenceNumbers().pack(self)

    # ---- Properties ----

    @property
    def is_draft(self):
        return self.status == 'draft'

    @property
    def is_pending(self):
        return self.status == 'pending'

    @property
    def is_paid(self):
        return self.status in ('paid', 'confirmed')

    @property
    def is_expired(self):
        return self.status == 'expired'

    @property
    def amount_due(self):
        '''Amount still owed.'''
        return max(Decimal('0'), self.total - self.amount_paid)

    @property
    def lines(self):
        '''Get invoice line items.'''
        return InvoiceLine.query.filter(
            invoice_id=self.id,
        ).order_by('sort_order').all()

    @property
    def payments(self):
        '''Get payments for this invoice.'''
        return Payment.query.filter(invoice_id=self.id).all()

    @property
    def payment_address(self):
        '''Get the assigned BitcoinAddress.'''
        if not self.payment_address_id:
            return None
        from btpay.bitcoin.models import BitcoinAddress
        return BitcoinAddress.get(self.payment_address_id)

    def recalculate_totals(self):
        '''Recalculate subtotal, tax, discount, and total from line items.'''
        items = self.lines
        self.subtotal = sum((item.amount for item in items), Decimal('0'))

        if self.tax_rate:
            self.tax_amount = (self.subtotal * self.tax_rate / 100).quantize(Decimal('0.01'))
        else:
            self.tax_amount = Decimal('0')

        self.total = self.subtotal + self.tax_amount - self.discount_amount
        if self.total < 0:
            self.total = Decimal('0')
        self.save()


# ---- InvoiceLine ----

class InvoiceLine(BaseMixin, MemModel):
    invoice_id      = Integer(index=True)
    description     = Text(required=True)
    quantity        = DecimalColumn(default=Decimal('1'))
    unit_price      = DecimalColumn(default=Decimal('0'))
    amount          = DecimalColumn(default=Decimal('0'))
    sort_order      = Integer(default=0)

    def calculate_amount(self):
        '''Set amount = quantity * unit_price.'''
        self.amount = (self.quantity * self.unit_price).quantize(Decimal('0.01'))
        return self.amount


# ---- Payment ----

class Payment(BaseMixin, MemModel):
    invoice_id      = Integer(index=True)
    method          = Text()                    # 'onchain_btc', 'wire', etc.
    txid            = Text()                    # blockchain transaction ID
    address         = Text()                    # payment address used
    amount_btc      = DecimalColumn(default=Decimal('0'))
    amount_fiat     = DecimalColumn(default=Decimal('0'))
    exchange_rate   = DecimalColumn(default=Decimal('0'))
    confirmations   = Integer(default=0)
    status          = Text(default='pending')   # 'pending' | 'confirmed' | 'failed'
    raw_data        = JsonColumn()

    def mark_confirmed(self, confirmations):
        self.status = 'confirmed'
        self.confirmations = confirmations
        self.save()


# ---- PaymentLink ----

class PaymentLink(BaseMixin, MemModel):
    org_id          = Integer(index=True)
    slug            = Text(unique=True, index=True)   # URL slug
    title           = Text(required=True)
    description     = Text()
    amount          = DecimalColumn()                  # None = user chooses
    currency        = Text(default='USD')
    is_active       = Boolean(default=True)
    payment_methods_enabled = TagsColumn()
    redirect_url    = Text()
    metadata        = JsonColumn()

    @property
    def is_fixed_amount(self):
        return self.amount is not None and self.amount > 0

# EOF
