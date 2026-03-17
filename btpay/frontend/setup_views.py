#
# Setup wizard — first-run configuration for new installations
#
import logging
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, g, current_app,
)

from btpay.auth.decorators import login_required
from btpay.security.csrf import validate_csrf_token

log = logging.getLogger(__name__)

setup_bp = Blueprint('setup', __name__, url_prefix='/setup')


@setup_bp.before_request
def _guard_setup():
    '''Block access if not logged in, not admin+, or setup already complete.'''
    # login_required is still applied per-route, but we guard completion + role here
    if not g.get('user') or not g.get('org'):
        return  # let @login_required handle the redirect
    if g.org.setup_complete:
        return redirect(url_for('dashboard.index'))
    from btpay.auth.models import Membership
    m = Membership.query.filter(user_id=g.user.id, org_id=g.org.id).first()
    if not m or not m.has_role('admin'):
        from flask import abort
        abort(403)


CONNECTOR_TYPES = {
    'bitcoin':     'Bitcoin (on-chain)',
    'btcpay':      'BTCPay Server',
    'lnbits':      'LNbits (Lightning)',
    'wire':        'Wire Transfer',
    'stablecoins': 'Stablecoins',
}


def _csrf_check():
    token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token', '')
    cookie_name = current_app.config.get('AUTH_COOKIE_NAME', 'btpay_session')
    session_token = request.cookies.get(cookie_name, '')
    secret = current_app.config.get('SECRET_KEY', '')
    if not validate_csrf_token(session_token, token, secret):
        from flask import abort
        abort(403)


def _mark_step(step):
    '''Mark a setup step as completed.'''
    steps = g.org.setup_steps or {}
    steps[step] = True
    g.org.setup_steps = steps
    g.org.save()


def _step_done(step):
    return bool((g.org.setup_steps or {}).get(step))


# ---- Router: resume at first incomplete step ----

@setup_bp.route('/')
@login_required
def index():
    if g.org.setup_complete:
        return redirect(url_for('dashboard.index'))
    if not _step_done('org'):
        return redirect(url_for('setup.org'))
    if not _step_done('connector'):
        return redirect(url_for('setup.choose_connector'))
    return redirect(url_for('setup.done'))


# ---- Step 1: Organization ----

@setup_bp.route('/org', methods=['GET', 'POST'])
@login_required
def org():
    if request.method == 'POST':
        _csrf_check()
        org = g.org
        org.name = request.form.get('name', org.name).strip()
        org.default_currency = request.form.get('default_currency', org.default_currency)
        org.timezone = request.form.get('timezone', org.timezone)
        org.slug = org.make_slug(org.name)
        org.save()
        _mark_step('org')
        flash('Organization settings saved', 'success')
        return redirect(url_for('setup.choose_connector'))

    return render_template('setup/org.html', org=g.org, step=1)


@setup_bp.route('/org/skip')
@login_required
def skip_org():
    return redirect(url_for('setup.choose_connector'))


# ---- Step 2: Choose connector ----

@setup_bp.route('/connector')
@login_required
def choose_connector():
    return render_template('setup/choose_connector.html', step=2,
                           connector_types=CONNECTOR_TYPES)


@setup_bp.route('/connector/skip')
@login_required
def skip_connector():
    return redirect(url_for('setup.done'))


# ---- Step 3: Configure connector ----

@setup_bp.route('/connector/bitcoin', methods=['GET', 'POST'])
@login_required
def connector_bitcoin():
    if request.method == 'POST':
        _csrf_check()
        from btpay.bitcoin.models import Wallet
        from btpay.security.validators import validate_xpub, ValidationError
        from btpay.bitcoin.address_list import AddressPool

        form = request.form
        wallet = Wallet(
            org_id=g.org.id,
            name=form.get('name', '').strip() or 'My Wallet',
            wallet_type=form.get('wallet_type', 'xpub'),
            xpub=form.get('xpub', '').strip(),
            descriptor=form.get('descriptor', '').strip(),
            network=form.get('network', 'mainnet'),
        )

        if wallet.wallet_type == 'xpub' and wallet.xpub:
            try:
                wallet.xpub = validate_xpub(wallet.xpub)
            except ValidationError as e:
                flash(str(e), 'error')
                return render_template('setup/connector_bitcoin.html', step=3, form=form)

        if wallet.wallet_type == 'address_list':
            addresses_text = form.get('addresses', '')
            wallet.save()
            if addresses_text.strip():
                pool = AddressPool(wallet)
                imported, skipped, errors = pool.import_from_text(addresses_text)
                flash('Wallet created. Imported %d addresses.' % imported, 'success')
            else:
                flash('Wallet created (no addresses imported)', 'success')
        else:
            wallet.save()
            flash('Wallet added', 'success')

        _mark_step('connector')
        return redirect(url_for('setup.done'))

    return render_template('setup/connector_bitcoin.html', step=3, form={})


@setup_bp.route('/connector/btcpay', methods=['GET', 'POST'])
@login_required
def connector_btcpay():
    if request.method == 'POST':
        _csrf_check()
        from btpay.connectors.btcpay import BTCPayConnector, validate_btcpay_connector

        conn = BTCPayConnector(org_id=g.org.id)
        conn.name = request.form.get('name', 'BTCPay Server').strip() or 'BTCPay Server'
        conn.server_url = request.form.get('server_url', '').strip().rstrip('/')
        conn.api_key = request.form.get('api_key', '').strip()
        conn.store_id = request.form.get('store_id', '').strip()
        conn.is_active = True

        valid, errors = validate_btcpay_connector(conn)
        if not valid:
            for err in errors:
                flash(err, 'error')
            return render_template('setup/connector_btcpay.html', step=3, form=request.form)

        conn.save()
        _mark_step('connector')
        flash('BTCPay Server connected', 'success')
        return redirect(url_for('setup.done'))

    return render_template('setup/connector_btcpay.html', step=3, form={})


@setup_bp.route('/connector/lnbits', methods=['GET', 'POST'])
@login_required
def connector_lnbits():
    if request.method == 'POST':
        _csrf_check()
        from btpay.connectors.lnbits import LNbitsConnector, validate_lnbits_connector

        conn = LNbitsConnector(org_id=g.org.id)
        conn.name = request.form.get('name', 'LNbits').strip() or 'LNbits'
        conn.server_url = request.form.get('server_url', '').strip().rstrip('/')
        conn.api_key = request.form.get('api_key', '').strip()
        conn.is_active = True

        valid, errors = validate_lnbits_connector(conn)
        if not valid:
            for err in errors:
                flash(err, 'error')
            return render_template('setup/connector_lnbits.html', step=3, form=request.form)

        conn.save()
        _mark_step('connector')
        flash('LNbits connected', 'success')
        return redirect(url_for('setup.done'))

    return render_template('setup/connector_lnbits.html', step=3, form={})


@setup_bp.route('/connector/wire', methods=['GET', 'POST'])
@login_required
def connector_wire():
    if request.method == 'POST':
        _csrf_check()
        from btpay.connectors.wire import WireConnector, validate_wire_connector

        wc = WireConnector(org_id=g.org.id)
        form = request.form
        wc.name = form.get('name', 'Wire Transfer').strip() or 'Wire Transfer'
        wc.bank_name = form.get('bank_name', '').strip()
        wc.account_name = form.get('account_name', '').strip()
        wc.account_number = form.get('account_number', '').strip()
        wc.routing_number = form.get('routing_number', '').strip()
        wc.swift_code = form.get('swift_code', '').strip()
        wc.iban = form.get('iban', '').strip()
        wc.bank_address = form.get('bank_address', '').strip()
        wc.currency = form.get('currency', 'USD').strip()
        wc.notes = form.get('notes', '').strip()
        wc.is_active = True

        valid, errors = validate_wire_connector(wc)
        if not valid:
            for err in errors:
                flash(err, 'error')
            return render_template('setup/connector_wire.html', step=3, form=form)

        wc.save()
        _mark_step('connector')
        flash('Wire transfer details saved', 'success')
        return redirect(url_for('setup.done'))

    return render_template('setup/connector_wire.html', step=3, form={})


@setup_bp.route('/connector/stablecoins', methods=['GET', 'POST'])
@login_required
def connector_stablecoins():
    from btpay.connectors.stablecoins import (
        SUPPORTED_CHAINS, SUPPORTED_TOKENS,
    )

    if request.method == 'POST':
        _csrf_check()
        from btpay.connectors.stablecoins import (
            StablecoinAccount, validate_stablecoin_address,
        )

        form = request.form
        chain = form.get('chain', '').strip()
        token = form.get('token', '').strip()
        address = form.get('address', '').strip()
        label = form.get('label', '').strip()

        errors = []
        if chain not in SUPPORTED_CHAINS:
            errors.append('Unsupported chain: %s' % chain)
        if token not in SUPPORTED_TOKENS:
            errors.append('Unsupported token: %s' % token)
        if not errors:
            valid, err = validate_stablecoin_address(address, chain)
            if not valid:
                errors.append(err)

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('setup/connector_stablecoins.html', step=3,
                                   form=form, chains=SUPPORTED_CHAINS, tokens=SUPPORTED_TOKENS)

        acct = StablecoinAccount(
            org_id=g.org.id, chain=chain, token=token,
            address=address, label=label or '',
        )
        acct.save()
        _mark_step('connector')
        flash('Stablecoin account added', 'success')
        return redirect(url_for('setup.done'))

    return render_template('setup/connector_stablecoins.html', step=3,
                           form={}, chains=SUPPORTED_CHAINS, tokens=SUPPORTED_TOKENS)


# ---- Step 4: Done ----

@setup_bp.route('/done')
@login_required
def done():
    return render_template('setup/done.html', step=4,
                           steps=g.org.setup_steps or {})


@setup_bp.route('/finish', methods=['POST'])
@login_required
def finish():
    _csrf_check()
    g.org.setup_complete = True
    g.org.save()
    return redirect(url_for('dashboard.index'))

# EOF
