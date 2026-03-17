#
# Auth models — User, Organization, Membership, Session, ApiKey
#
import re
from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import (
    Text, Integer, Boolean, DateTimeColumn, JsonColumn, TagsColumn,
)
from btpay.chrono import NOW, as_time_t
from btpay.security.hashing import hash_password, verify_password


# Role hierarchy for permission checks
_ROLE_RANK = {'owner': 3, 'admin': 2, 'viewer': 1}

# Minimum password requirements
MIN_PASSWORD_LENGTH = 8


class User(BaseMixin, MemModel):
    email               = Text(unique=True, index=True)
    password_hash       = Text()
    first_name          = Text()
    last_name           = Text()
    totp_secret         = Text()              # base32 secret, blank = not set up
    totp_enabled        = Boolean(default=False)
    last_totp_used      = Text()              # prevents replay
    pending_totp_secret = Text()              # temporary: set during setup, cleared on enable
    is_active           = Boolean(default=True)
    last_login_at       = DateTimeColumn(default=0)
    failed_login_count  = Integer(default=0)
    locked_until        = DateTimeColumn(default=0)

    @property
    def full_name(self):
        parts = [self.first_name, self.last_name]
        return ' '.join(p for p in parts if p).strip() or self.email

    @property
    def is_locked(self):
        if not self.locked_until:
            return False
        if isinstance(self.locked_until, (int, float)):
            return self.locked_until > as_time_t(NOW())
        return as_time_t(self.locked_until) > as_time_t(NOW())

    # Exponential lockout durations (in seconds) by failure count bracket
    _LOCKOUT_DURATIONS = {
        5: 60,       # 5 failures: 1 minute
        10: 300,     # 10 failures: 5 minutes
        15: 900,     # 15 failures: 15 minutes
        20: 3600,    # 20+ failures: 1 hour
    }

    def record_failed_login(self):
        self.failed_login_count += 1
        if self.failed_login_count >= 5:
            from btpay.chrono import TIME_FUTURE
            # Exponential backoff based on failure count
            duration = 60  # default 1 minute
            for threshold, secs in sorted(self._LOCKOUT_DURATIONS.items()):
                if self.failed_login_count >= threshold:
                    duration = secs
            self.locked_until = TIME_FUTURE(seconds=duration)
        self.save()

    def record_successful_login(self):
        self.failed_login_count = 0
        self.locked_until = 0
        self.last_login_at = NOW()
        self.save()

    def set_password(self, password):
        '''Validate strength and hash.'''
        if not password or len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError("Password must be at least %d characters" % MIN_PASSWORD_LENGTH)
        self.password_hash = hash_password(password)

    def check_password(self, password):
        '''Verify password against stored hash.'''
        if not self.password_hash:
            return False
        return verify_password(password, self.password_hash)


class Organization(BaseMixin, MemModel):
    name                = Text(required=True)
    slug                = Text(unique=True, index=True)
    logo_url            = Text()
    brand_color         = Text(default='#F89F1B')
    brand_accent_color  = Text(default='#3B3A3C')
    default_currency    = Text(default='USD')
    timezone            = Text(default='UTC')
    invoice_prefix      = Text(default='INV')
    invoice_next_number = Integer(default=1)
    custom_domain       = Text()                 # e.g. pay.example.com
    base_url            = Text()                 # full URL override, e.g. https://pay.example.com
    support_email       = Text()                 # customer-facing support email
    terms_url           = Text()                 # link to terms of service
    privacy_url         = Text()                 # link to privacy policy
    custom_checkout_css = Text()                 # custom CSS injected into checkout pages
    receipt_footer      = Text()                 # custom footer text on receipts
    notification_prefs  = JsonColumn()           # per-org email notification toggles
    electrum_config     = JsonColumn()           # per-org Electrum server selection
    stablecoin_rpc      = JsonColumn()           # per-org RPC config for stablecoin monitoring
    smtp_config         = JsonColumn()
    wire_info           = JsonColumn()
    setup_steps         = JsonColumn()           # tracks wizard progress, e.g. {"org": true}
    setup_complete      = Boolean(default=False)  # true once wizard is finished or dismissed

    _SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$')

    def next_invoice_number(self):
        '''Return formatted invoice number and increment counter.'''
        num = self.invoice_next_number or 1
        result = '%s-%04d' % (self.invoice_prefix, num)
        self.invoice_next_number = num + 1
        self.save()
        return result

    @staticmethod
    def make_slug(name):
        '''Generate a URL-safe slug from a name.'''
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        return slug or 'org'


class Membership(BaseMixin, MemModel):
    user_id     = Integer(index=True)
    org_id      = Integer(index=True)
    role        = Text(default='viewer')    # 'owner' | 'admin' | 'viewer'
    invited_by  = Integer(default=0)
    accepted_at = DateTimeColumn(default=0)

    @property
    def user(self):
        return User.get(self.user_id)

    @property
    def org(self):
        return Organization.get(self.org_id)

    def has_role(self, min_role):
        '''Check if this membership's role meets or exceeds min_role.'''
        my_rank = _ROLE_RANK.get(self.role, 0)
        required_rank = _ROLE_RANK.get(min_role, 0)
        return my_rank >= required_rank


class Session(BaseMixin, MemModel):
    user_id     = Integer(index=True)
    token_hash  = Text(unique=True, index=True)
    ip_address  = Text()
    user_agent  = Text()
    expires_at  = DateTimeColumn()
    org_id      = Integer()


class ApiKey(BaseMixin, MemModel):
    org_id      = Integer(index=True)
    user_id     = Integer()
    key_hash    = Text(unique=True, index=True)
    key_prefix  = Text()
    label       = Text()
    permissions = TagsColumn()          # 'invoices:read', 'invoices:write', etc.
    last_used_at = DateTimeColumn(default=0)
    is_active   = Boolean(default=True)

# EOF
