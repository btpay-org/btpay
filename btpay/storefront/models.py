#
# Storefront models — Storefront, StorefrontItem
#
# Inspired by BTCPay Server's "Apps" feature (Point of Sale + Crowdfunding).
# Supports two modes:
#   - "store" : product catalog with fixed prices (like BTCPay PoS)
#   - "donation" : donation/tip page with preset + custom amounts (like BTCPay Crowdfunding)
#
import re
from decimal import Decimal
from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import (
    Text, Integer, Boolean, DecimalColumn, DateTimeColumn, JsonColumn,
)

_HEX_COLOR_RE = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


class Storefront(BaseMixin, MemModel):
    org_id          = Integer(index=True)
    slug            = Text(unique=True, index=True)     # URL-safe identifier
    title           = Text(required=True)
    description     = Text()                            # markdown or plain text
    storefront_type = Text(default='store')             # 'store' | 'donation'
    currency        = Text(default='USD')
    is_active       = Boolean(default=True)

    # Appearance
    hero_image_url  = Text()                            # optional banner image
    button_text     = Text(default='Buy Now')           # CTA button label
    brand_color     = Text(default='')                  # override org brand color

    # Donation-mode settings
    donation_presets = JsonColumn()                      # e.g. [5, 10, 25, 50, 100]
    donation_allow_custom = Boolean(default=True)       # allow free-form amounts
    donation_goal_amount = DecimalColumn(default=Decimal('0'))  # 0 = no goal
    donation_goal_label = Text(default='')              # e.g. "Server costs"

    # Checkout options
    payment_methods_enabled = JsonColumn()              # list of enabled methods, or None = org defaults
    require_email   = Boolean(default=False)            # ask buyer for email
    require_name    = Boolean(default=False)
    success_message = Text(default='Thank you for your purchase!')
    redirect_url    = Text()                            # optional post-payment redirect

    # Stats (denormalized for display)
    total_orders    = Integer(default=0)
    total_revenue   = DecimalColumn(default=Decimal('0'))

    @staticmethod
    def sanitize_color(val):
        '''Strip anything that isn't a valid hex color.'''
        if not val:
            return ''
        val = val.strip()
        if _HEX_COLOR_RE.match(val):
            return val
        return ''

    @property
    def is_donation(self):
        return self.storefront_type == 'donation'

    @property
    def is_store(self):
        return self.storefront_type == 'store'

    @property
    def items(self):
        return StorefrontItem.query.filter(
            storefront_id=self.id,
        ).order_by('sort_order').all()

    @property
    def active_items(self):
        return [i for i in self.items if i.is_active]

    @property
    def public_url(self):
        return '/s/%s' % self.slug

    @property
    def goal_percent(self):
        if not self.donation_goal_amount or self.donation_goal_amount <= 0:
            return 0
        pct = (self.total_revenue / self.donation_goal_amount * 100)
        return min(int(pct), 100)

    @classmethod
    def make_slug(cls, title):
        '''Generate a URL-safe slug from a title.'''
        import re
        slug = title.lower().strip()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        if not slug:
            slug = 'store'
        # Ensure uniqueness
        base = slug
        counter = 1
        while cls.get_by(slug=slug):
            slug = '%s-%d' % (base, counter)
            counter += 1
        return slug


class StorefrontItem(BaseMixin, MemModel):
    storefront_id   = Integer(index=True)
    title           = Text(required=True)
    description     = Text()
    price           = DecimalColumn(default=Decimal('0'))   # 0 = pay-what-you-want
    image_url       = Text()
    sort_order      = Integer(default=0)
    is_active       = Boolean(default=True)
    inventory       = Integer(default=-1)                   # -1 = unlimited

    # Item categories / tags
    category        = Text(default='')

    @property
    def is_pay_what_you_want(self):
        '''Price is 0 or None — buyer names their own price.'''
        return self.price is None or self.price <= 0

    @property
    def in_stock(self):
        return self.inventory < 0 or self.inventory > 0

    def decrement_inventory(self):
        if self.inventory > 0:
            self.inventory -= 1
            self.save()

# EOF
