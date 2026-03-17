#
# Demo seed data — realistic fake invoices, users, wallets, connectors.
# All Bitcoin data is testnet. No real money.
#
import logging, random, hashlib
from decimal import Decimal
from btpay.chrono import NOW, TIME_AGO, as_time_t

log = logging.getLogger(__name__)

# BIP32 testnet xpub (tpub) — NOT real funds
DEMO_TPUB = 'tpubD6NzVbkrYhZ4XgiXtGrdW5XDAPFCL9h7we1vwNCpn8tGbBcgfVYjXyhWo4E1xkh56hjod1RhGjxbaTLV3X4FyWuejifB9jusQ46QzG87VKp'

# Well-known mainnet Bitcoin addresses with on-chain history
# Used for demo display only — BTPay never holds keys to these
DEMO_ADDRESSES = [
    'bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq',   # Binance cold wallet (segwit)
    'bc1q9d4ywgfnd8h43da5tpcxcn6ajv590cg6d3tg6axemvljvt2k76zs50tv4q',  # Kraken cold (P2WSH)
    '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa',            # Satoshi genesis coinbase
    '3M219KR5vEneNb47ewrPfWyb5jQ2DjxRP6',            # Binance hot wallet (P2SH)
    'bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h',    # Known segwit address
    '1PeizMg76Cf96nUQrYg8xuoZWLQozU5zGW',            # Early whale address
    'bc1qa5wkgaew2dkv56kxvg7ewrcmk3l5hezca03h20',    # Bitstamp cold
    '3Kzh9qAqVWQhEsfQz7zEQL1EuSx5tyNLNS',            # Gemini cold wallet
    '1FeexV6bAHb8ybZjqQMjJrcCrHGW9sb6uF',            # Mt.Gox trustee
    'bc1qx9t2l3pyny2spqpqlye8svce70nppwtaxwdrp4y',    # Bitfinex cold
]

# Famous public EVM address — Vitalik Buterin's well-known address (EIP-55 checksummed)
DEMO_EVM_ADDRESS = '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045'

# Famous Tron address — Justin Sun's well-known address
DEMO_TRON_ADDRESS = 'TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf'

# Famous Solana address — Solana Foundation's public address
DEMO_SOLANA_ADDRESS = '9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM'

# Fake txids
def _fake_txid():
    return hashlib.sha256(str(random.random()).encode()).hexdigest()

DEMO_CUSTOMERS = [
    ('Satoshi Nakamoto', 'satoshi@gmx.com', 'Bitcoin Foundation'),
    ('Hal Finney', 'hal@finney.org', 'PGP Corp'),
    ('Nick Szabo', 'nick@unenumerated.com', 'Bit Gold Labs'),
    ('Adam Back', 'adam@blockstream.com', 'Blockstream'),
    ('Wei Dai', 'wei@b-money.net', 'B-Money Research'),
    ('Len Sassaman', 'len@anonymizer.com', 'Anonymizer Inc'),
    ('Pieter Wuille', 'sipa@blockstream.com', 'Blockstream'),
    ('Gregory Maxwell', 'greg@bitcoin.org', 'Bitcoin Core'),
    ('Wladimir van der Laan', 'laanwj@protonmail.com', 'Bitcoin Core'),
    ('Luke Dashjr', 'luke@dashjr.org', 'Eligius Mining'),
]

DEMO_ITEMS = [
    ('Coldcard Mk4', Decimal('157.94')),
    ('Coldcard Q', Decimal('299.00')),
    ('COLDPOWER adapter', Decimal('39.99')),
    ('TAPSIGNER NFC card', Decimal('39.99')),
    ('SATSCARD NFC card', Decimal('29.99')),
    ('Blockclock Mini', Decimal('399.00')),
    ('OpenDime v4', Decimal('49.99')),
    ('Bitcoin Bunker setup', Decimal('500.00')),
    ('Bitcoin consulting (1 hr)', Decimal('250.00')),
    ('Node setup & maintenance', Decimal('175.00')),
    ('Security audit', Decimal('2500.00')),
    ('Multi-sig workshop', Decimal('750.00')),
    ('Hardware wallet training', Decimal('350.00')),
    ('Lightning channel management', Decimal('200.00')),
]


def seed_demo_data():
    '''Populate the ORM with realistic demo data. Returns summary dict.'''
    from btpay.auth.models import User, Organization, Membership, ApiKey
    from btpay.bitcoin.models import Wallet, BitcoinAddress, ExchangeRateSnapshot
    from btpay.invoicing.models import Invoice, InvoiceLine, Payment, PaymentLink
    from btpay.api.webhook_models import WebhookEndpoint
    from btpay.connectors.wire import WireConnector
    from btpay.connectors.stablecoins import StablecoinAccount
    from btpay.security.hashing import generate_random_token

    log.info("DEMO: Seeding demo data...")

    # --- Users ---
    from btpay.security.hashing import hash_password
    demo_hash = hash_password('demo')

    admin = User(email='demo', first_name='Demo', last_name='Admin')
    admin.password_hash = demo_hash
    admin.save()

    viewer = User(email='viewer@demo.btpay', first_name='Demo', last_name='Viewer')
    viewer.password_hash = demo_hash
    viewer.save()

    # --- Organization ---
    org = Organization(
        name='Linen Avenue Demo',
        slug='linen-avenue-demo',
        brand_color='#F89F1B',
        brand_accent_color='#3B3A3C',
        default_currency='USD',
        invoice_prefix='DEMO',
        invoice_next_number=16,
        wire_info={
            'bank_name': 'Demo Bank International',
            'account_name': 'Linen Avenue Co',
            'account_number': '****4321',
            'routing': '****5678',
            'swift': 'DEMOXXXX',
            'reference': 'Linen Avenue',
        },
    )
    org.save()

    # --- Memberships ---
    Membership(user_id=admin.id, org_id=org.id, role='owner').save()
    Membership(user_id=viewer.id, org_id=org.id, role='viewer').save()

    # --- Wallet (testnet xpub) ---
    wallet = Wallet(
        org_id=org.id,
        name='Demo Testnet Wallet',
        wallet_type='xpub',
        xpub=DEMO_TPUB,
        network='mainnet',
        is_active=True,
        next_address_index=len(DEMO_ADDRESSES),
    )
    wallet.save()

    for i, addr in enumerate(DEMO_ADDRESSES):
        ba = BitcoinAddress(
            wallet_id=wallet.id,
            address=addr,
            derivation_index=i,
            status='unused',
        )
        ba.save()

    # --- Wire Transfer Connector ---
    wc = WireConnector(
        org_id=org.id,
        name='Demo Bank Wire',
        is_active=True,
        bank_name='First National Bank',
        account_name='Linen Avenue Co',
        account_number='****4567',
        routing_number='021000021',
        swift_code='FNBOUS33',
        iban='',
        bank_address='100 Wall Street, New York, NY 10005',
        currency='USD',
        notes='Include the invoice number in the wire transfer memo.',
    )
    wc.save()

    # --- BTCPay Server Connector (demo) ---
    from btpay.connectors.btcpay import BTCPayConnector
    BTCPayConnector(
        org_id=org.id,
        name='Demo BTCPay Server',
        is_active=True,
        server_url='https://btcpay.demo.btpay.local',
        api_key='demo-btcpay-api-key-not-real',
        store_id='demo-store-id',
    ).save()

    # --- LNbits Connector (demo) ---
    from btpay.connectors.lnbits import LNbitsConnector
    LNbitsConnector(
        org_id=org.id,
        name='Demo LNbits',
        is_active=True,
        server_url='https://lnbits.demo.btpay.local',
        api_key='demo-lnbits-invoice-key-not-real',
    ).save()

    # --- Stablecoin Accounts (famous public addresses) ---
    StablecoinAccount(org_id=org.id, chain='ethereum', token='usdc',
        address=DEMO_EVM_ADDRESS).save()
    StablecoinAccount(org_id=org.id, chain='arbitrum', token='usdc',
        address=DEMO_EVM_ADDRESS).save()
    StablecoinAccount(org_id=org.id, chain='base', token='usdc',
        address=DEMO_EVM_ADDRESS).save()
    StablecoinAccount(org_id=org.id, chain='tron', token='usdt',
        address=DEMO_TRON_ADDRESS).save()
    StablecoinAccount(org_id=org.id, chain='ethereum', token='usdt',
        address=DEMO_EVM_ADDRESS).save()
    StablecoinAccount(org_id=org.id, chain='ethereum', token='dai',
        address=DEMO_EVM_ADDRESS).save()

    # --- Exchange Rate Snapshots ---
    from btpay.demo.stubs import DEMO_RATES
    for currency, rate in DEMO_RATES.items():
        ExchangeRateSnapshot(
            currency=currency,
            rate=rate,
            source='demo',
            fetched_at=as_time_t(NOW()),
        ).save()

    # --- Invoices ---
    # (status, days_ago, cust_idx, item_count, paid_frac, pay_method)
    invoices_spec = [
        ('confirmed', 45, 0, 2, 1.0, 'onchain_btc'),
        ('confirmed', 38, 1, 1, 1.0, 'wire'),
        ('confirmed', 30, 3, 3, 1.0, 'lnbits'),
        ('confirmed', 22, 2, 1, 1.0, 'stable_ethereum_usdc'),
        ('paid',      14, 4, 2, 1.0, 'onchain_btc'),
        ('paid',       7, 5, 1, 1.0, 'btcpay'),
        ('paid',       3, 6, 2, 1.0, 'wire'),
        ('partial',    2, 7, 1, 0.6, 'stable_tron_usdt'),
        ('pending',    1, 8, 2, 0.0, 'onchain_btc'),
        ('pending',    0, 9, 1, 0.0, 'lnbits'),
        ('expired',   10, 3, 1, 0.0, 'onchain_btc'),
        ('cancelled', 20, 1, 1, 0.0, 'wire'),
        ('draft',      0, 0, 3, 0.0, 'onchain_btc'),
        ('draft',      0, 4, 1, 0.0, 'stable_arbitrum_usdc'),
        ('draft',      0, 6, 2, 0.0, 'lnbits'),
    ]

    addr_idx = 0
    created_invoices = []

    all_methods = ['onchain_btc', 'wire', 'btcpay', 'lnbits', 'stablecoins',
                   'stable_ethereum_usdc', 'stable_arbitrum_usdc', 'stable_base_usdc',
                   'stable_tron_usdt', 'stable_ethereum_usdt', 'stable_ethereum_dai']

    for inv_num, (status, days_ago, cust_idx, item_count, paid_frac, pay_method) in enumerate(invoices_spec, 1):
        cname, cemail, ccompany = DEMO_CUSTOMERS[cust_idx]
        created_at = as_time_t(TIME_AGO(days=days_ago, hours=random.randint(0, 12)))

        inv = Invoice(
            org_id=org.id,
            invoice_number='DEMO-%04d' % inv_num,
            status=status,
            customer_email=cemail,
            customer_name=cname,
            customer_company=ccompany,
            currency='USD',
            created_by_user_id=admin.id,
            payment_methods_enabled=[pay_method] + [m for m in all_methods if m != pay_method],
            notes='Demo invoice — not real' if inv_num == 1 else '',
        )
        inv.save()

        items = random.sample(DEMO_ITEMS, min(item_count, len(DEMO_ITEMS)))
        subtotal = Decimal('0')
        for sort_idx, (desc, price) in enumerate(items):
            qty = Decimal(random.choice([1, 1, 1, 2, 3, 5]))
            amount = (price * qty).quantize(Decimal('0.01'))
            subtotal += amount
            InvoiceLine(
                invoice_id=inv.id,
                description=desc,
                quantity=qty,
                unit_price=price,
                amount=amount,
                sort_order=sort_idx,
            ).save()

        tax_rate = Decimal('13') if inv_num % 3 == 0 else Decimal('0')
        tax_amount = (subtotal * tax_rate / 100).quantize(Decimal('0.01'))
        total = subtotal + tax_amount

        inv.subtotal = str(subtotal)
        inv.tax_rate = str(tax_rate)
        inv.tax_amount = str(tax_amount)
        inv.total = str(total)
        inv.amount_paid = str(Decimal('0'))

        if status != 'draft':
            rate = DEMO_RATES['USD']
            btc_amount = (total / rate).quantize(Decimal('0.00000001'))
            inv.btc_rate = str(rate)
            inv.btc_amount = str(btc_amount)
            inv.btc_rate_locked_at = created_at

            if addr_idx < len(DEMO_ADDRESSES):
                addrs = BitcoinAddress.query.filter(address=DEMO_ADDRESSES[addr_idx]).all()
                if addrs:
                    ba = addrs[0]
                    ba.status = 'assigned'
                    ba.assigned_to_invoice_id = inv.id
                    ba.save()
                    inv.payment_address_id = ba.id
                addr_idx += 1

        if paid_frac > 0:
            paid_total = total * Decimal(str(paid_frac))
            btc_paid = (paid_total / DEMO_RATES['USD']).quantize(Decimal('0.00000001'))
            p = Payment(
                invoice_id=inv.id,
                method=pay_method,
                txid=_fake_txid(),
                address=DEMO_ADDRESSES[min(addr_idx - 1, len(DEMO_ADDRESSES) - 1)],
                amount_btc=str(btc_paid),
                amount_fiat=str(paid_total.quantize(Decimal('0.01'))),
                exchange_rate=str(DEMO_RATES['USD']),
                confirmations=6 if status == 'confirmed' else (3 if status == 'paid' else 1),
                status='confirmed' if status == 'confirmed' else 'pending',
            )
            p.save()

            inv.amount_paid = str(paid_total.quantize(Decimal('0.01')))
            if status in ('paid', 'confirmed'):
                inv.paid_at = as_time_t(TIME_AGO(days=max(0, days_ago - 1)))
            if status == 'confirmed':
                inv.confirmed_at = as_time_t(TIME_AGO(days=max(0, days_ago - 2)))

            if inv.payment_address_id:
                ba = BitcoinAddress.get(inv.payment_address_id)
                if ba:
                    ba.status = 'confirmed' if status == 'confirmed' else 'seen'
                    ba.amount_received_sat = int(btc_paid * 100_000_000)
                    ba.save()

        if status == 'expired':
            inv.expired_at = as_time_t(TIME_AGO(days=max(0, days_ago - 1)))
        if status == 'cancelled':
            inv.cancelled_at = as_time_t(TIME_AGO(days=max(0, days_ago - 1)))

        inv.created_at = created_at
        inv.save()
        created_invoices.append(inv)

    org.invoice_next_number = len(invoices_spec) + 1
    org.save()

    # --- Payment Links ---
    PaymentLink(
        org_id=org.id, slug='donate',
        title='Donate to the Project',
        description='Support open-source Bitcoin development',
        currency='USD', is_active=True,
        payment_methods_enabled=['onchain_btc'],
    ).save()

    PaymentLink(
        org_id=org.id, slug='coldcard-mk4',
        title='Buy Coldcard Mk4',
        amount=str(Decimal('157.94')),
        currency='USD', is_active=True,
        payment_methods_enabled=['onchain_btc', 'wire', 'stablecoins',
                                  'stable_ethereum_usdc', 'stable_arbitrum_usdc'],
    ).save()

    PaymentLink(
        org_id=org.id, slug='consulting',
        title='Book a Consulting Session',
        amount=str(Decimal('250.00')),
        currency='USD', is_active=True,
        payment_methods_enabled=['onchain_btc', 'stable_ethereum_usdc'],
    ).save()

    # --- Webhook Endpoints ---
    WebhookEndpoint(
        org_id=org.id,
        url='https://example.com/webhooks/btpay',
        secret='whsec_demo_' + generate_random_token(16),
        events=['invoice.paid', 'invoice.confirmed'],
        is_active=True,
        description='Demo store webhook',
    ).save()

    # --- API Key ---
    raw_key = 'demo_' + generate_random_token(24)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    ApiKey(
        org_id=org.id, user_id=admin.id,
        key_hash=key_hash, key_prefix=raw_key[:12],
        label='Demo API Key',
        permissions=['invoices:read', 'invoices:write', 'rates:read'],
        is_active=True,
    ).save()

    summary = {
        'users': 2,
        'invoices': len(invoices_spec),
        'payments': sum(1 for _, _, _, _, pf, _ in invoices_spec if pf > 0),
        'payment_links': 3,
        'addresses': len(DEMO_ADDRESSES),
        'wire_connectors': 1,
        'btcpay_connectors': 1,
        'lnbits_connectors': 1,
        'stablecoin_accounts': 6,
        'webhook_endpoints': 1,
        'api_key_prefix': raw_key[:12],
    }

    log.info("DEMO: Seed complete — %d users, %d invoices, %d stablecoin accounts" % (
        summary['users'], summary['invoices'], summary['stablecoin_accounts']))

    return summary


def reset_demo_data():
    '''Clear all ORM data and re-seed.'''
    from btpay.orm.engine import MemoryStore
    store = MemoryStore()
    for table_name in list(store._tables.keys()):
        store._tables[table_name].clear()
        store._sequences[table_name] = 1
        for idx_name, idx in list(store._indexes.get(table_name, {}).items()):
            idx.clear()

    return seed_demo_data()

# EOF
