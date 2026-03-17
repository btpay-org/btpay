#
# Bitcoin models — Wallet, BitcoinAddress, ExchangeRateSnapshot
#
import threading
from decimal import Decimal
from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import (
    Text, Integer, Boolean, DecimalColumn, DateTimeColumn, JsonColumn,
)
from btpay.chrono import NOW


# ---- Wallet ----

class Wallet(BaseMixin, MemModel):
    org_id              = Integer(index=True)
    name                = Text(required=True)
    wallet_type         = Text(default='xpub')       # 'xpub' | 'descriptor' | 'address_list'
    xpub                = Text()                      # xpub/ypub/zpub string
    descriptor          = Text()                      # output descriptor string
    derivation_path     = Text(default='m/0')         # base derivation path
    next_address_index  = Integer(default=0)
    gap_limit           = Integer(default=20)
    network             = Text(default='mainnet')
    is_active           = Boolean(default=True)

    @property
    def address_type(self):
        '''Infer address type from xpub version prefix.'''
        x = self.xpub or ''
        if x.startswith(('zpub', 'vpub')):
            return 'p2wpkh'
        elif x.startswith(('ypub', 'upub')):
            return 'p2sh_p2wpkh'
        elif x.startswith(('xpub', 'tpub')):
            return 'p2pkh'
        return 'p2wpkh'  # default

    _address_lock = threading.Lock()

    def get_next_address(self):
        '''Derive and store the next address. Returns BitcoinAddress instance.
        Thread-safe: uses a lock to prevent duplicate address assignment.'''
        with self._address_lock:
            return self._derive_next_address()

    def _derive_next_address(self):
        '''Internal address derivation (must be called under _address_lock).'''
        if self.wallet_type == 'address_list':
            return self._next_from_pool()

        from btpay.bitcoin.xpub import XPubDeriver

        if self.wallet_type == 'descriptor':
            from btpay.bitcoin.descriptors import DescriptorParser
            parser = DescriptorParser(self.descriptor)
            addr_str = parser.derive_address(self.next_address_index, self.network)
            spk = parser.derive_script_pubkey(self.next_address_index)
        else:
            deriver = XPubDeriver(self.xpub)
            # Derive along the path (e.g. m/0/<index>)
            path_parts = self.derivation_path.replace('m/', '').replace('m', '')
            if path_parts:
                deriver = deriver.derive_path(path_parts)
            child = deriver.derive_child(self.next_address_index)
            addr_str = child.address(self.network)
            spk = child.script_pubkey()

        script_hash = XPubDeriver.script_hash(spk)

        ba = BitcoinAddress(
            wallet_id=self.id,
            address=addr_str,
            derivation_index=self.next_address_index,
            script_hash=script_hash,
        )
        ba.save()

        self.next_address_index += 1
        self.save()

        return ba

    def _next_from_pool(self):
        '''Get next unused address from address_list pool.
        Marks it as reserved immediately to prevent double-assignment.'''
        ba = BitcoinAddress.query.filter(
            wallet_id=self.id,
            status='unused',
        ).order_by('derivation_index').first()
        if ba:
            ba.status = 'reserved'
            ba.save()
        return ba

    def check_gap_limit(self):
        '''Check how many unused addresses are ahead of the last used one.
           Returns (unused_count, exceeds_limit).'''
        unused = BitcoinAddress.query.filter(
            wallet_id=self.id,
            status='unused',
        ).count()
        return unused, unused >= self.gap_limit


# ---- BitcoinAddress ----

class BitcoinAddress(BaseMixin, MemModel):
    wallet_id               = Integer(index=True)
    address                 = Text(unique=True, index=True)
    derivation_index        = Integer(default=0)
    assigned_to_invoice_id  = Integer(default=0, index=True)
    first_seen_at           = DateTimeColumn(default=0)
    confirmed_at            = DateTimeColumn(default=0)
    amount_received_sat     = Integer(default=0)
    status                  = Text(default='unused')    # 'unused'|'reserved'|'assigned'|'seen'|'confirmed'|'released'
    script_hash             = Text(index=True)          # for Electrum subscriptions

    def mark_assigned(self, invoice_id):
        '''Assign this address to an invoice.'''
        self.status = 'assigned'
        self.assigned_to_invoice_id = invoice_id
        self.save()

    def mark_seen(self, amount_sat, txid=None):
        '''Mark unconfirmed payment seen on this address.'''
        self.status = 'seen'
        self.first_seen_at = NOW()
        self.amount_received_sat = amount_sat
        self.save()

    def mark_confirmed(self, amount_sat):
        '''Mark payment as confirmed.'''
        self.status = 'confirmed'
        self.confirmed_at = NOW()
        self.amount_received_sat = amount_sat
        self.save()


# ---- ExchangeRateSnapshot ----

class ExchangeRateSnapshot(BaseMixin, MemModel):
    currency    = Text(index=True)       # 'USD', 'EUR', etc.
    rate        = DecimalColumn()         # BTC price in this currency
    source      = Text()                  # 'coingecko', 'average', etc.
    fetched_at  = DateTimeColumn()

# EOF
