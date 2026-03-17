#
# Static address pool management
#
# For wallets that use pre-imported address lists instead of xpub derivation.
#
import logging
from btpay.security.validators import validate_btc_address, ValidationError

log = logging.getLogger(__name__)


class AddressPool:
    '''
    Manage a static pool of pre-imported Bitcoin addresses.

    Usage:
        pool = AddressPool(wallet)
        pool.import_from_text("bc1q...\nbc1q...\n...")
        addr = pool.get_next_unused()
    '''

    def __init__(self, wallet):
        self.wallet = wallet

    def import_from_text(self, text):
        '''
        Import addresses from newline-separated text.
        Validates each address and creates BitcoinAddress rows.
        Returns (imported_count, skipped_count, errors).
        '''
        from btpay.bitcoin.models import BitcoinAddress
        from btpay.bitcoin.xpub import XPubDeriver, _hash160

        imported = 0
        skipped = 0
        errors = []

        for i, line in enumerate(text.strip().splitlines()):
            addr = line.strip()
            if not addr or addr.startswith('#'):
                continue

            try:
                addr = validate_btc_address(addr)
            except ValidationError as e:
                errors.append('Line %d: %s' % (i + 1, str(e)))
                continue

            # Check for duplicates
            existing = BitcoinAddress.get_by(address=addr)
            if existing:
                skipped += 1
                continue

            ba = BitcoinAddress(
                wallet_id=self.wallet.id,
                address=addr,
                derivation_index=i,
                status='unused',
                script_hash='',     # can't easily compute without pubkey
            )
            ba.save()
            imported += 1

        log.info("Imported %d addresses, skipped %d, errors %d",
                 imported, skipped, len(errors))
        return imported, skipped, errors

    def get_next_unused(self):
        '''Return the next unused BitcoinAddress or None if pool is empty.'''
        from btpay.bitcoin.models import BitcoinAddress
        return BitcoinAddress.query.filter(
            wallet_id=self.wallet.id,
            status='unused',
        ).order_by('derivation_index').first()

    def unused_count(self):
        '''How many addresses are still available.'''
        from btpay.bitcoin.models import BitcoinAddress
        return BitcoinAddress.query.filter(
            wallet_id=self.wallet.id,
            status='unused',
        ).count()

    def is_low(self, threshold=5):
        '''Check if the pool is running low.'''
        return self.unused_count() < threshold

# EOF
