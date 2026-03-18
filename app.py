#
# BTPay — Flask Application Factory
#
import os, logging
from flask import Flask, request, g, jsonify, redirect, url_for

log = logging.getLogger(__name__)


def create_app(config_override=None):
    '''Create and configure the Flask application.'''

    import time as _time
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')

    # Load config
    _load_config(app, config_override)

    # Apply ProxyFix when behind a reverse proxy (nginx, Caddy, etc.)
    num_proxies = app.config.get('NUM_PROXIES', 0)
    if num_proxies:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=num_proxies,
            x_proto=num_proxies,
            x_host=num_proxies,
            x_prefix=num_proxies,
        )

    # Request size limit (16 MB) — override Flask's default None
    if app.config.get('MAX_CONTENT_LENGTH') is None:
        app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
    app.config.setdefault('START_TIME', _time.time())

    # Refuse to start with default secrets in production
    if not app.config.get('TESTING'):
        _check_secrets(app)

    # Setup logging
    _setup_logging(app)

    # Initialize ORM and load persisted data
    _init_orm(app)

    # Wire refnum encryption keys from config (singleton is already created by model imports)
    from btpay.security.refnums import ReferenceNumbers
    ReferenceNumbers().reconfigure(
        app.config['REFNUM_KEY'],
        app.config['REFNUM_NONCE'],
    )

    # Demo mode: seed data instead of loading from disk
    if app.config.get('DEMO_MODE') and not app.config.get('TESTING'):
        _init_demo(app)

    # Register middleware
    _register_middleware(app)

    # Register blueprints (added in later phases)
    _register_blueprints(app)

    # Register CLI commands
    _register_cli(app)

    # Start background services (skipped when gunicorn preloads in master;
    # post_fork hook in gunicorn.conf.py starts them in the worker instead)
    if not app.config.get('TESTING') and not os.environ.get('_GUNICORN_PRELOAD'):
        _start_background_services(app)

    return app


def _load_config(app, config_override):
    '''Load configuration from config_default.py, config.py, and env vars.'''
    import config_default

    # Load all uppercase attributes from config_default
    for key in dir(config_default):
        if key.isupper():
            app.config[key] = getattr(config_default, key)

    app.config['SECRET_KEY'] = config_default.SECRET_KEY

    # Try to load user overrides from config.py
    try:
        import config as user_config
        for key in dir(user_config):
            if key.isupper():
                app.config[key] = getattr(user_config, key)
    except ImportError:
        if not config_default.DEV_MODE:
            log.warning("No config.py found. Using defaults. Create config.py for production!")

    # Apply any test overrides
    if config_override:
        app.config.update(config_override)


# Env vars that indicate secrets were explicitly configured
_SECRET_ENV_VARS = [
    'BTPAY_SECRET_KEY', 'BTPAY_JWT_ADMIN', 'BTPAY_JWT_LOGIN',
    'BTPAY_JWT_API', 'BTPAY_JWT_INVITE', 'BTPAY_REFNUM_KEY', 'BTPAY_REFNUM_NONCE',
]


def _check_secrets(app):
    '''Warn if secrets were auto-generated (ephemeral, won't survive restarts).'''
    import os
    missing = [v for v in _SECRET_ENV_VARS if not os.environ.get(v)]

    if missing and not app.config.get('DEV_MODE') and not app.config.get('DEMO_MODE'):
        log.warning(
            'AUTO-GENERATED EPHEMERAL SECRETS: %s. '
            'Sessions and reference numbers will reset on restart. '
            'Set these via BTPAY_* env vars for persistence.',
            ', '.join(missing)
        )


def _setup_logging(app):
    '''Configure structured logging.'''
    from btpay.logging_config import setup_logging
    setup_logging(app)


def _init_orm(app):
    '''Initialize ORM engine and load data from disk.'''
    data_dir = app.config.get('DATA_DIR', 'data')
    os.makedirs(data_dir, exist_ok=True)

    # Import all models so they register with the engine
    _import_all_models()

    # Skip loading persisted data in demo mode (seed will populate)
    if not app.config.get('DEMO_MODE'):
        from btpay.orm.persistence import load_from_disk
        load_from_disk(data_dir)


def _init_demo(app):
    '''Initialize demo mode: seed data, disable persistence.'''
    from btpay.demo.seed import seed_demo_data
    summary = seed_demo_data()
    log.info("DEMO MODE: Seeded %d invoices, %d users. Login: demo / demo" % (
        summary['invoices'], summary['users']))
    app.config['_DEMO_SUMMARY'] = summary


def _import_all_models():
    '''Import all model modules so they register with the ORM engine.'''
    import btpay.auth.models          # User, Organization, Membership, Session, ApiKey
    import btpay.bitcoin.models       # Wallet, BitcoinAddress, ExchangeRateSnapshot
    import btpay.invoicing.models     # Invoice, InvoiceLine, Payment, PaymentLink
    import btpay.api.webhook_models   # WebhookEndpoint, WebhookDelivery
    import btpay.connectors.wire      # WireConnector
    import btpay.connectors.stablecoins  # StablecoinAccount
    import btpay.storefront.models    # Storefront, StorefrontItem


def _register_middleware(app):
    '''Register request/response middleware.'''
    from btpay.security.hack_detect import is_hacking_request

    @app.before_request
    def check_hacking():
        if is_hacking_request(request.path, request.method, request.content_length or 0):
            return '', 404

    @app.before_request
    def load_session():
        '''Auto-load session for every request.'''
        from btpay.auth.sessions import validate_session
        cookie_name = app.config.get('AUTH_COOKIE_NAME', 'btpay_session')
        token = request.cookies.get(cookie_name)
        g.user = None
        g.org = None
        if token:
            result = validate_session(token)
            if result:
                g.user, g.org = result

    @app.after_request
    def security_headers(response):
        # Privacy and security headers
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '0'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=()'

        if not app.config.get('DEV_MODE'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

        return response

    # Phase 7: Per-route rate limiting, CSP, request logging
    from btpay.security.middleware import register_security_middleware
    register_security_middleware(app)


def _register_blueprints(app):
    '''Register Flask blueprints. Added incrementally as phases are built.'''
    from btpay.auth.views import auth_bp
    app.register_blueprint(auth_bp)

    from btpay.api.routes import api_bp
    app.register_blueprint(api_bp)

    # Phase 6: Frontend view blueprints
    from btpay.frontend.dashboard import dashboard_bp
    from btpay.frontend.invoice_views import invoices_bp
    from btpay.frontend.checkout_views import checkout_bp
    from btpay.frontend.settings_views import settings_bp
    from btpay.frontend.setup_views import setup_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(invoices_bp)
    app.register_blueprint(checkout_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(setup_bp)

    # Storefront blueprints (admin + public)
    from btpay.storefront.views import storefront_bp
    from btpay.storefront.public_views import public_storefront_bp
    app.register_blueprint(storefront_bp)
    app.register_blueprint(public_storefront_bp)

    # Register Jinja2 filters and context processors
    from btpay.frontend.filters import register_filters
    from btpay.frontend.context import register_context_processors
    register_filters(app)
    register_context_processors(app)

    @app.route('/')
    def index():
        if g.get('user'):
            return redirect(url_for('dashboard.index'))
        return redirect(url_for('auth.login_page'))

    @app.route('/health')
    def health():
        return jsonify(status='ok')

    # Demo mode: reset route and context variable
    if app.config.get('DEMO_MODE'):
        @app.route('/demo/reset', methods=['POST'])
        def demo_reset():
            from btpay.demo.seed import reset_demo_data
            reset_demo_data()
            from flask import flash
            flash('Demo data has been reset.', 'success')
            return redirect(url_for('auth.login_page'))

        @app.context_processor
        def inject_demo_mode():
            return dict(demo_mode=True)


def _register_cli(app):
    '''Register Flask CLI commands.'''
    import click

    @app.cli.command('db-export')
    @click.argument('output_dir', default='data')
    def db_export(output_dir):
        '''Export all data to JSON files.'''
        from btpay.orm.persistence import save_to_disk
        save_to_disk(output_dir)
        click.echo('Exported to %s' % output_dir)

    @app.cli.command('db-backup')
    def db_backup():
        '''Create a timestamped backup.'''
        from btpay.orm.persistence import backup_rotation
        data_dir = app.config.get('DATA_DIR', 'data')
        backup_rotation(data_dir)
        click.echo('Backup created')

    @app.cli.command('user-create')
    @click.option('--email', prompt=True)
    @click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option('--first-name', prompt='First name', default='')
    @click.option('--last-name', prompt='Last name', default='')
    def user_create(email, password, first_name, last_name):
        '''Create an admin user with a default organization.'''
        from btpay.auth.models import User, Organization, Membership
        from btpay.security.validators import validate_email, ValidationError

        try:
            email = validate_email(email)
        except ValidationError as e:
            click.echo('Error: %s' % e)
            return

        if User.get_by(email=email):
            click.echo('Error: Email already registered')
            return

        user = User(email=email, first_name=first_name, last_name=last_name)
        try:
            user.set_password(password)
        except ValueError as e:
            click.echo('Error: %s' % e)
            return
        user.save()

        # Create default org if none exists
        org = Organization.query.first()
        if org is None:
            slug = Organization.make_slug(first_name or email.split('@')[0])
            org = Organization(name='My Organization', slug=slug)
            org.save()

        Membership(user_id=user.id, org_id=org.id, role='owner').save()

        # Persist immediately
        from btpay.orm.persistence import save_to_disk
        data_dir = app.config.get('DATA_DIR', 'data')
        save_to_disk(data_dir)

        click.echo('Admin user created: %s (id=%d)' % (email, user.id))

    @app.cli.command('wallet-create')
    @click.option('--org-id', type=int, prompt='Organization ID')
    @click.option('--name', prompt='Wallet name')
    @click.option('--type', 'wallet_type', type=click.Choice(['xpub', 'descriptor', 'address_list']),
                  default='xpub', prompt='Wallet type')
    @click.option('--xpub', default='', help='Extended public key (xpub/ypub/zpub)')
    @click.option('--descriptor', default='', help='Output descriptor')
    @click.option('--network', type=click.Choice(['mainnet', 'testnet']),
                  default='mainnet', prompt='Network')
    def wallet_create(org_id, name, wallet_type, xpub, descriptor, network):
        '''Create a new wallet.'''
        from btpay.bitcoin.models import Wallet
        from btpay.auth.models import Organization

        org = Organization.get(org_id)
        if org is None:
            click.echo('Error: Organization %d not found' % org_id)
            return

        if wallet_type == 'xpub' and not xpub:
            xpub = click.prompt('Enter xpub/ypub/zpub')

        if wallet_type == 'descriptor' and not descriptor:
            descriptor = click.prompt('Enter output descriptor')

        wallet = Wallet(
            org_id=org_id,
            name=name,
            wallet_type=wallet_type,
            xpub=xpub,
            descriptor=descriptor,
            network=network,
        )
        wallet.save()

        from btpay.orm.persistence import save_to_disk
        data_dir = app.config.get('DATA_DIR', 'data')
        save_to_disk(data_dir)

        click.echo('Wallet created: %s (id=%d, type=%s)' % (name, wallet.id, wallet_type))

    @app.cli.command('wallet-import')
    @click.option('--wallet-id', type=int, prompt='Wallet ID')
    @click.option('--file', 'filepath', type=click.Path(exists=True),
                  help='Text file with one address per line')
    def wallet_import(wallet_id, filepath):
        '''Import addresses into an address_list wallet.'''
        from btpay.bitcoin.models import Wallet
        from btpay.bitcoin.address_list import AddressPool

        wallet = Wallet.get(wallet_id)
        if wallet is None:
            click.echo('Error: Wallet %d not found' % wallet_id)
            return

        if wallet.wallet_type != 'address_list':
            click.echo('Error: Wallet type must be address_list (is %s)' % wallet.wallet_type)
            return

        with open(filepath, 'r') as f:
            text = f.read()

        pool = AddressPool(wallet)
        imported, skipped, errors = pool.import_from_text(text)

        click.echo('Imported: %d, Skipped: %d, Errors: %d' % (imported, skipped, len(errors)))
        for err in errors:
            click.echo('  %s' % err)

        from btpay.orm.persistence import save_to_disk
        data_dir = app.config.get('DATA_DIR', 'data')
        save_to_disk(data_dir)

    @app.cli.command('rates')
    def show_rates():
        '''Show current exchange rates.'''
        if hasattr(app, '_exchange_rate_service'):
            rates = app._exchange_rate_service.get_rates()
            if rates:
                for cur, rate in sorted(rates.items()):
                    click.echo('BTC/%s: %s' % (cur, rate))
            else:
                click.echo('No rates available yet. Fetching...')
                app._exchange_rate_service.fetch_now()
                rates = app._exchange_rate_service.get_rates()
                for cur, rate in sorted(rates.items()):
                    click.echo('BTC/%s: %s' % (cur, rate))
        else:
            click.echo('Exchange rate service not running')

    @app.cli.command('user-reset-totp')
    @click.option('--email', prompt=True)
    def user_reset_totp(email):
        '''Reset TOTP 2FA for a user (emergency recovery).'''
        from btpay.auth.models import User
        user = User.get_by(email=email.strip().lower())
        if user is None:
            click.echo('Error: User not found')
            return
        user.totp_secret = ''
        user.totp_enabled = False
        user.last_totp_used = ''
        user.save()
        from btpay.orm.persistence import save_to_disk
        save_to_disk(app.config.get('DATA_DIR', 'data'))
        click.echo('TOTP reset for %s' % email)

    @app.cli.command('user-list')
    def user_list():
        '''List all users.'''
        from btpay.auth.models import User
        users = User.query.all()
        if not users:
            click.echo('No users')
            return
        for u in users:
            click.echo('  id=%d email=%s name="%s" active=%s totp=%s' % (
                u.id, u.email, u.full_name, u.is_active, u.totp_enabled))

    @app.cli.command('db-stats')
    def db_stats():
        '''Show ORM storage statistics.'''
        from btpay.orm.engine import MemoryStore
        store = MemoryStore()
        click.echo('Tables:')
        for table_name, rows in store._tables.items():
            click.echo('  %-25s %d rows' % (table_name, len(rows)))

    @app.cli.command('db-import')
    @click.argument('input_dir', default='data')
    def db_import(input_dir):
        '''Import data from JSON files.'''
        from btpay.orm.persistence import load_from_disk
        load_from_disk(input_dir)
        click.echo('Imported from %s' % input_dir)

    @app.cli.command('demo-seed')
    def demo_seed():
        '''Seed demo data (for DEMO_MODE).'''
        from btpay.demo.seed import seed_demo_data
        summary = seed_demo_data()
        click.echo('Demo data seeded:')
        for k, v in summary.items():
            click.echo('  %s: %s' % (k, v))
        click.echo('\nLogin: demo / demo')

    @app.cli.command('check-updates')
    def check_updates_cmd():
        """Check for available BTPay updates."""
        from btpay.version import get_version, get_git_info
        from btpay.updater.github import GitHubReleaseFetcher
        from btpay.updater.version_compare import is_newer

        current = get_version()
        git_info = get_git_info()
        click.echo('BTPay %s' % current)
        if git_info:
            click.echo('  commit: %s  branch: %s%s' % (
                git_info['commit'], git_info['branch'],
                ' (dirty)' if git_info['dirty'] else ''))

        proxy = app.config.get('SOCKS5_PROXY')
        repo = app.config.get('UPDATE_REPO', 'btpay-org/btpay')
        click.echo('\nChecking %s ...' % repo)

        try:
            fetcher = GitHubReleaseFetcher(repo=repo, proxy=proxy)
            releases = fetcher.fetch_releases()
        except Exception as e:
            click.echo('Error: %s' % e, err=True)
            return

        if not releases:
            click.echo('No releases found.')
            return

        click.echo('\nAvailable versions:')
        for r in releases[:10]:
            marker = ' <-- current' if r['tag'].lstrip('v') == current else ''
            pre = ' (pre-release)' if r.get('prerelease') else ''
            click.echo('  %-12s %s%s%s' % (r['tag'], r.get('date', ''), pre, marker))

    @app.cli.command('update')
    @click.option('--version', 'target_version', default=None, help='Target version tag (e.g. v0.2.0)')
    @click.option('--zip', 'zip_path', default=None, type=click.Path(exists=True), help='Path to release ZIP file')
    @click.option('--force', is_flag=True, help='Skip safety checks')
    @click.option('--skip-backup', is_flag=True, help='Skip pre-update backup (dangerous)')
    def update_cmd(target_version, zip_path, force, skip_backup):
        """Update BTPay to a new version."""
        import os
        from btpay.version import get_version
        from btpay.updater.checks import pre_update_checks
        from btpay.updater.backup import create_code_backup, create_data_backup, record_update
        from btpay.updater.restart import pip_install

        if not target_version and not zip_path:
            click.echo('Error: specify --version or --zip', err=True)
            return

        if not app.config.get('UPDATE_ALLOWED', True):
            click.echo('Error: updates are disabled (UPDATE_ALLOWED=False)', err=True)
            return

        app_root = os.path.dirname(os.path.abspath(__file__))
        data_dir = app.config.get('DATA_DIR', 'data')
        backup_dir = os.path.join(data_dir, 'backups')
        from_version = get_version()

        click.echo('BTPay %s' % from_version)

        # Pre-flight
        if not force:
            issues = pre_update_checks(app_root, data_dir)
            for issue in issues:
                prefix = 'BLOCKER' if issue['level'] == 'blocker' else 'WARNING'
                click.echo('  [%s] %s' % (prefix, issue['message']))
            blockers = [i for i in issues if i['level'] == 'blocker']
            if blockers:
                click.echo('Aborting. Use --force to override.', err=True)
                return

        # Confirm
        if zip_path:
            click.echo('Will update from ZIP: %s' % zip_path)
        else:
            click.echo('Will update to: %s' % target_version)
        if not click.confirm('Continue?'):
            return

        # Backup
        if not skip_backup:
            click.echo('Creating backups...')
            os.makedirs(backup_dir, exist_ok=True)
            code_backup = create_code_backup(app_root, backup_dir, from_version)
            data_backup = create_data_backup(data_dir, from_version)
            click.echo('  Code: %s' % code_backup)
            click.echo('  Data: %s' % data_backup)
        else:
            code_backup = None
            data_backup = None

        # Apply
        click.echo('Applying update...')
        method = 'zip' if zip_path else 'git'
        try:
            if zip_path:
                from btpay.updater.zip_updater import validate_zip, apply_zip
                with open(zip_path, 'rb') as f:
                    file_bytes = f.read()
                validation = validate_zip(file_bytes)
                if not validation['valid']:
                    click.echo('Invalid ZIP: %s' % validation['error'], err=True)
                    return
                target_version = validation['version']
                result = apply_zip(file_bytes, app_root)
                if not result['success']:
                    click.echo('Failed: %s' % result['error'], err=True)
                    return
                click.echo('  Extracted %d files' % result['files_updated'])
            else:
                from btpay.updater.git_updater import fetch_tags, checkout_tag
                proxy = app.config.get('SOCKS5_PROXY')
                click.echo('  Fetching tags...')
                fetch_tags(app_root, proxy=proxy)
                click.echo('  Checking out %s...' % target_version)
                checkout_tag(app_root, target_version)
        except Exception as e:
            click.echo('Update failed: %s' % e, err=True)
            return

        # pip install
        click.echo('Installing dependencies...')
        success, output = pip_install(app_root)
        if not success:
            click.echo('pip install failed:\n%s' % output, err=True)
            return

        # Record
        record_update(data_dir, from_version, target_version or 'unknown', method, code_backup, data_backup)

        click.echo('\nUpdated to %s. Restart the service to apply:' % target_version)
        click.echo('  systemctl restart btpay')

    @app.cli.command('update-rollback')
    @click.option('--version', 'target', default=None, help='Version to rollback to')
    def update_rollback_cmd(target):
        """Rollback to a previous BTPay version."""
        import os
        from btpay.updater.backup import get_update_history, restore_code_backup, restore_data_backup
        from btpay.updater.restart import pip_install

        data_dir = app.config.get('DATA_DIR', 'data')
        app_root = os.path.dirname(os.path.abspath(__file__))

        history = get_update_history(data_dir)
        if not history:
            click.echo('No update history found.')
            return

        click.echo('Update history:')
        for i, entry in enumerate(reversed(history)):
            click.echo('  [%d] %s -> %s (%s, %s)' % (
                i, entry.get('from_version'), entry.get('to_version'),
                entry.get('method'), entry.get('timestamp', '')))

        if target is None:
            idx = click.prompt('Select entry to rollback', type=int, default=0)
            entry = list(reversed(history))[idx]
        else:
            entry = next((e for e in history if e.get('from_version') == target), None)
            if not entry:
                click.echo('Version %s not found in history' % target, err=True)
                return

        code_backup = entry.get('code_backup')
        data_backup = entry.get('data_backup')

        if not code_backup or not os.path.exists(code_backup):
            click.echo('Code backup not found: %s' % code_backup, err=True)
            return

        click.echo('Will rollback to %s' % entry.get('from_version'))
        click.echo('  Code backup: %s' % code_backup)
        click.echo('  Data backup: %s' % (data_backup or 'none'))
        if not click.confirm('Continue?'):
            return

        try:
            click.echo('Restoring code...')
            restore_code_backup(code_backup, app_root)
            if data_backup and os.path.exists(data_backup):
                click.echo('Restoring data...')
                restore_data_backup(data_backup, data_dir)
            click.echo('Installing dependencies...')
            pip_install(app_root)
            click.echo('\nRollback complete. Restart the service:')
            click.echo('  systemctl restart btpay')
        except Exception as e:
            click.echo('Rollback failed: %s' % e, err=True)


def _start_background_services(app):
    '''Start background threads for auto-save, etc.'''
    is_demo = app.config.get('DEMO_MODE')

    # AutoSaver — disabled in demo mode (no persistence)
    if not is_demo:
        from btpay.orm.persistence import AutoSaver
        data_dir = app.config.get('DATA_DIR', 'data')
        interval = app.config.get('AUTOSAVE_INTERVAL', 60)
        backup_interval = app.config.get('BACKUP_INTERVAL', 3600)
        backup_keep = app.config.get('BACKUP_KEEP', 5)
        saver = AutoSaver(data_dir, interval, backup_interval, backup_keep)
        saver.start()
        app._autosaver = saver
    else:
        log.info("DEMO: AutoSaver disabled — data resets on restart")

    # Exchange rate service — stub in demo mode
    if is_demo:
        from btpay.demo.stubs import DemoExchangeRateService
        rate_svc = DemoExchangeRateService()
    else:
        from btpay.bitcoin.exchange import ExchangeRateService
        rate_svc = ExchangeRateService(
            sources=app.config.get('EXCHANGE_RATE_SOURCES', ['coingecko', 'coinbase', 'kraken']),
            interval=app.config.get('EXCHANGE_RATE_INTERVAL', 300),
            proxy=app.config.get('SOCKS5_PROXY', ''),
            currencies=app.config.get('SUPPORTED_CURRENCIES', ['USD']),
            mempool_url=app.config.get('MEMPOOL_API_URL', 'https://mempool.space/api'),
        )
    rate_svc.start()
    app._exchange_rate_service = rate_svc


# Entry point for development
if __name__ == '__main__':
    import os
    app = create_app()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)

# EOF
