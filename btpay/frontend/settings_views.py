#
# Settings views — general, connectors (bitcoin/wire/stablecoins), branding, team, api_keys, webhooks, email
#
import hashlib
import logging
import os
from flask import (
    Blueprint, render_template, render_template_string, request, redirect,
    url_for, flash, g, current_app, jsonify,
)

from btpay.auth.decorators import login_required, role_required, csrf_protect
from btpay.security.hashing import generate_random_token

log = logging.getLogger(__name__)

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')


# ---- General ----

@settings_bp.route('/general', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def general():
    if request.method == 'POST':
        _csrf_check()
        org = g.org
        org.name = request.form.get('name', org.name).strip()
        org.default_currency = request.form.get('default_currency', org.default_currency)
        org.invoice_prefix = request.form.get('invoice_prefix', org.invoice_prefix).strip()
        org.timezone = request.form.get('timezone', org.timezone)
        org.custom_domain = request.form.get('custom_domain', '').strip()
        org.base_url = request.form.get('base_url', '').strip().rstrip('/')
        org.support_email = request.form.get('support_email', '').strip()
        org.terms_url = request.form.get('terms_url', '').strip()
        org.privacy_url = request.form.get('privacy_url', '').strip()
        org.save()
        flash('Settings saved', 'success')
        return redirect(url_for('settings.general'))

    return render_template('settings/general.html', org=g.org)


# ---- Connectors: Bitcoin Wallets ----

@settings_bp.route('/wallets')
@login_required
@role_required('admin')
def wallets():
    '''Legacy URL — redirect to connectors/bitcoin.'''
    return redirect(url_for('settings.connectors_bitcoin'), code=301)


@settings_bp.route('/connectors/bitcoin', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def connectors_bitcoin():
    from btpay.bitcoin.models import Wallet

    if request.method == 'POST':
        _csrf_check()
        form = request.form
        wallet = Wallet(
            org_id=g.org.id,
            name=form.get('name', '').strip(),
            wallet_type=form.get('wallet_type', 'xpub'),
            xpub=form.get('xpub', '').strip(),
            descriptor=form.get('descriptor', '').strip(),
            network=form.get('network', 'mainnet'),
        )

        # Validate xpub before saving
        if wallet.wallet_type == 'xpub' and wallet.xpub:
            from btpay.security.validators import validate_xpub, ValidationError
            try:
                wallet.xpub = validate_xpub(wallet.xpub)
            except ValidationError as e:
                flash(str(e), 'error')
                return redirect(url_for('settings.connectors_bitcoin'))

        # Handle address list import
        if wallet.wallet_type == 'address_list':
            addresses_text = form.get('addresses', '')
            wallet.save()
            if addresses_text.strip():
                from btpay.bitcoin.address_list import AddressPool
                pool = AddressPool(wallet)
                imported, skipped, errors = pool.import_from_text(addresses_text)
                flash('Wallet created. Imported %d addresses.' % imported, 'success')
            else:
                flash('Wallet created (no addresses imported)', 'success')
        else:
            wallet.save()
            flash('Wallet added', 'success')

        return redirect(url_for('settings.connectors_bitcoin'))

    wallet_list = Wallet.query.filter(org_id=g.org.id).all()
    return render_template('settings/connectors_bitcoin.html', wallets=wallet_list, org=g.org)


# ---- Connectors: Wire Transfer ----

@settings_bp.route('/connectors/wire', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def connectors_wire():
    from btpay.connectors.wire import WireConnector, validate_wire_connector

    # Get existing connector for this org (at most one)
    wc = WireConnector.query.filter(org_id=g.org.id).first()

    if request.method == 'POST':
        _csrf_check()
        form = request.form

        if not wc:
            wc = WireConnector(org_id=g.org.id)

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
        wc.is_active = form.get('is_active') == '1'

        valid, errors = validate_wire_connector(wc)
        if not valid:
            for err in errors:
                flash(err, 'error')
            return render_template('settings/connectors_wire.html', wc=wc, org=g.org)

        wc.save()
        flash('Wire transfer settings saved', 'success')
        return redirect(url_for('settings.connectors_wire'))

    return render_template('settings/connectors_wire.html', wc=wc, org=g.org)


# ---- Connectors: Stablecoins ----

@settings_bp.route('/connectors/stablecoins', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def connectors_stablecoins():
    from btpay.connectors.stablecoins import (
        StablecoinAccount, SUPPORTED_CHAINS, SUPPORTED_TOKENS,
        validate_stablecoin_address,
    )

    if request.method == 'POST':
        _csrf_check()
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
        else:
            acct = StablecoinAccount(
                org_id=g.org.id,
                chain=chain,
                token=token,
                address=address,
                label=label or '',
            )
            acct.save()
            flash('Stablecoin account added: %s' % acct.display_label, 'success')

        return redirect(url_for('settings.connectors_stablecoins'))

    accounts = StablecoinAccount.query.filter(org_id=g.org.id).all()
    return render_template('settings/connectors_stablecoins.html',
        accounts=accounts, org=g.org,
        chains=SUPPORTED_CHAINS, tokens=SUPPORTED_TOKENS)


@settings_bp.route('/connectors/stablecoins/<int:account_id>/toggle', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def toggle_stablecoin(account_id):
    from btpay.connectors.stablecoins import StablecoinAccount
    acct = StablecoinAccount.get(account_id)
    if acct is None or acct.org_id != g.org.id:
        flash('Account not found', 'error')
        return redirect(url_for('settings.connectors_stablecoins'))

    acct.is_active = not acct.is_active
    acct.save()
    flash('%s %s' % (acct.display_label, 'enabled' if acct.is_active else 'disabled'), 'success')
    return redirect(url_for('settings.connectors_stablecoins'))


@settings_bp.route('/connectors/stablecoins/<int:account_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def delete_stablecoin(account_id):
    from btpay.connectors.stablecoins import StablecoinAccount
    acct = StablecoinAccount.get(account_id)
    if acct is None or acct.org_id != g.org.id:
        flash('Account not found', 'error')
        return redirect(url_for('settings.connectors_stablecoins'))

    label = acct.display_label
    acct.delete()
    flash('Removed: %s' % label, 'success')
    return redirect(url_for('settings.connectors_stablecoins'))


@settings_bp.route('/connectors/stablecoins/rpc', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def stablecoin_rpc():
    from btpay.connectors.evm_rpc import PUBLIC_RPCS

    org = g.org
    rpc = org.stablecoin_rpc or {}

    if request.method == 'POST':
        _csrf_check()
        form = request.form

        new_rpc = {
            'provider': form.get('provider', 'public'),  # 'public', 'alchemy', 'ankr', 'custom'
            'monitoring_enabled': form.get('monitoring_enabled') == '1',
            'check_interval': int(form.get('check_interval') or 60),
        }

        provider = new_rpc['provider']

        if provider == 'alchemy':
            key = form.get('alchemy_key', '').strip()
            if key:
                new_rpc['alchemy_key'] = key
            elif rpc.get('alchemy_key'):
                new_rpc['alchemy_key'] = rpc['alchemy_key']

        elif provider == 'ankr':
            key = form.get('ankr_key', '').strip()
            if key:
                new_rpc['ankr_key'] = key
            elif rpc.get('ankr_key'):
                new_rpc['ankr_key'] = rpc['ankr_key']

        elif provider == 'custom':
            # Save custom RPC URLs per chain (validate against SSRF)
            from btpay.security.validators import validate_external_url, ValidationError
            custom_rpcs = {}
            for chain in PUBLIC_RPCS:
                url = form.get('rpc_%s' % chain, '').strip()
                if url:
                    try:
                        url = validate_external_url(url)
                    except ValidationError:
                        flash('Invalid RPC URL for %s — private/internal URLs are not allowed' % chain, 'error')
                        return redirect(url_for('settings.stablecoin_rpc'))
                    custom_rpcs[chain] = url
            new_rpc['custom_rpcs'] = custom_rpcs

        org.stablecoin_rpc = new_rpc
        org.save()
        flash('Stablecoin RPC settings saved', 'success')
        return redirect(url_for('settings.stablecoin_rpc'))

    return render_template('settings/stablecoin_rpc.html',
        org=org, rpc=rpc, public_rpcs=PUBLIC_RPCS)


@settings_bp.route('/connectors/stablecoins/rpc/test', methods=['POST'])
@login_required
@role_required('admin')
def test_stablecoin_rpc():
    '''Test RPC connection to a chain. Returns JSON.'''
    from btpay.connectors.evm_rpc import EvmRpcClient, EvmRpcError

    chain = request.json.get('chain', '') if request.is_json else ''
    custom_rpc = request.json.get('rpc_url', '') if request.is_json else ''

    if not chain:
        return jsonify(error='Chain is required'), 400

    custom_rpcs = {chain: custom_rpc} if custom_rpc else {}
    client = EvmRpcClient(custom_rpcs=custom_rpcs, timeout=10)

    try:
        success, result = client.check_chain_connection(chain)
        if success:
            return jsonify(success=True, chain=chain, block_height=result)
        else:
            return jsonify(error=result), 502
    except Exception as e:
        return jsonify(error=str(e)), 502


@settings_bp.route('/connectors/stablecoins/rpc/balance', methods=['POST'])
@login_required
@role_required('admin')
def check_stablecoin_balance():
    '''Check token balance for a stablecoin account. Returns JSON.'''
    from btpay.connectors.evm_rpc import EvmRpcClient, EvmRpcError
    from btpay.connectors.stablecoins import StablecoinAccount

    account_id = request.json.get('account_id') if request.is_json else None
    if not account_id:
        return jsonify(error='Account ID required'), 400

    acct = StablecoinAccount.get(int(account_id))
    if not acct or acct.org_id != g.org.id:
        return jsonify(error='Account not found'), 404

    org = g.org
    rpc_config = org.stablecoin_rpc or {}
    custom_rpcs = _build_rpc_urls(rpc_config)
    client = EvmRpcClient(custom_rpcs=custom_rpcs, timeout=15)

    try:
        balance = client.get_token_balance_human(acct.chain, acct.token, acct.address)
        return jsonify(
            success=True,
            balance=str(balance),
            token=acct.token_symbol,
            chain=acct.chain_name,
            address=acct.short_address,
        )
    except EvmRpcError as e:
        return jsonify(error=str(e)), 502
    except Exception as e:
        return jsonify(error='Balance check failed: %s' % str(e)), 502


def _build_rpc_urls(rpc_config):
    '''Build custom RPC URL dict from org's stablecoin_rpc config.'''
    from btpay.connectors.evm_rpc import PUBLIC_RPCS

    provider = rpc_config.get('provider', 'public')

    if provider == 'public':
        return {}  # Use defaults

    if provider == 'alchemy':
        key = rpc_config.get('alchemy_key', '')
        if not key:
            return {}
        return {
            'ethereum':  'https://eth-mainnet.g.alchemy.com/v2/%s' % key,
            'arbitrum':  'https://arb-mainnet.g.alchemy.com/v2/%s' % key,
            'base':      'https://base-mainnet.g.alchemy.com/v2/%s' % key,
            'polygon':   'https://polygon-mainnet.g.alchemy.com/v2/%s' % key,
            'optimism':  'https://opt-mainnet.g.alchemy.com/v2/%s' % key,
        }

    if provider == 'ankr':
        key = rpc_config.get('ankr_key', '')
        prefix = 'https://rpc.ankr.com'
        suffix = '/%s' % key if key else ''
        return {
            'ethereum':  '%s/eth%s' % (prefix, suffix),
            'arbitrum':  '%s/arbitrum%s' % (prefix, suffix),
            'base':      '%s/base%s' % (prefix, suffix),
            'polygon':   '%s/polygon%s' % (prefix, suffix),
            'optimism':  '%s/optimism%s' % (prefix, suffix),
            'avalanche': '%s/avalanche%s' % (prefix, suffix),
        }

    if provider == 'custom':
        return rpc_config.get('custom_rpcs', {})

    return {}


# ---- Connectors: BTCPay Server ----

@settings_bp.route('/connectors/btcpay', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def connectors_btcpay():
    from btpay.connectors.btcpay import BTCPayConnector, validate_btcpay_connector

    conn = BTCPayConnector.query.filter(org_id=g.org.id).first()

    if request.method == 'POST':
        _csrf_check()
        form = request.form

        if not conn:
            conn = BTCPayConnector(org_id=g.org.id)

        conn.name = form.get('name', 'BTCPay Server').strip() or 'BTCPay Server'
        conn.server_url = form.get('server_url', '').strip().rstrip('/')
        conn.api_key = form.get('api_key', '').strip()
        conn.store_id = form.get('store_id', '').strip()
        conn.is_active = form.get('is_active') == '1'

        valid, errors = validate_btcpay_connector(conn)
        if not valid:
            for err in errors:
                flash(err, 'error')
            return render_template('settings/connectors_btcpay.html', conn=conn, org=g.org)

        conn.save()
        flash('BTCPay Server settings saved', 'success')
        return redirect(url_for('settings.connectors_btcpay'))

    return render_template('settings/connectors_btcpay.html', conn=conn, org=g.org)


@settings_bp.route('/connectors/btcpay/test', methods=['POST'])
@login_required
@role_required('admin')
def test_btcpay():
    '''Test BTCPay Server connection. Returns JSON.'''
    from btpay.connectors.btcpay import BTCPayClient

    server_url = request.json.get('server_url', '').strip() if request.is_json else ''
    api_key = request.json.get('api_key', '').strip() if request.is_json else ''
    store_id = request.json.get('store_id', '').strip() if request.is_json else ''

    if not server_url or not api_key or not store_id:
        return jsonify(error='All fields are required'), 400

    client = BTCPayClient(server_url, api_key, store_id, timeout=10)
    ok, result = client.test_connection()
    if ok:
        return jsonify(success=True, store_name=result.get('name', ''))
    return jsonify(error=result), 502


# ---- Connectors: LNbits ----

@settings_bp.route('/connectors/lnbits', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def connectors_lnbits():
    from btpay.connectors.lnbits import LNbitsConnector, validate_lnbits_connector

    conn = LNbitsConnector.query.filter(org_id=g.org.id).first()

    if request.method == 'POST':
        _csrf_check()
        form = request.form

        if not conn:
            conn = LNbitsConnector(org_id=g.org.id)

        conn.name = form.get('name', 'LNbits').strip() or 'LNbits'
        conn.server_url = form.get('server_url', '').strip().rstrip('/')
        conn.api_key = form.get('api_key', '').strip()
        conn.is_active = form.get('is_active') == '1'

        valid, errors = validate_lnbits_connector(conn)
        if not valid:
            for err in errors:
                flash(err, 'error')
            return render_template('settings/connectors_lnbits.html', conn=conn, org=g.org)

        conn.save()
        flash('LNbits settings saved', 'success')
        return redirect(url_for('settings.connectors_lnbits'))

    return render_template('settings/connectors_lnbits.html', conn=conn, org=g.org)


@settings_bp.route('/connectors/lnbits/test', methods=['POST'])
@login_required
@role_required('admin')
def test_lnbits():
    '''Test LNbits connection. Returns JSON.'''
    from btpay.connectors.lnbits import LNbitsClient

    server_url = request.json.get('server_url', '').strip() if request.is_json else ''
    api_key = request.json.get('api_key', '').strip() if request.is_json else ''

    if not server_url or not api_key:
        return jsonify(error='All fields are required'), 400

    client = LNbitsClient(server_url, api_key, timeout=10)
    ok, result = client.test_connection()
    if ok:
        balance_sat = result.get('balance_msat', 0) // 1000
        return jsonify(success=True, wallet_name=result.get('name', ''),
                      balance_sat=balance_sat)
    return jsonify(error=result), 502


# ---- Branding ----

@settings_bp.route('/branding', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def branding():
    if request.method == 'POST':
        _csrf_check()
        org = g.org
        org.logo_url = request.form.get('logo_url', '').strip()
        org.brand_color = request.form.get('brand_color', '#F89F1B').strip()
        org.brand_accent_color = request.form.get('brand_accent_color', '#3B3A3C').strip()
        org.custom_checkout_css = request.form.get('custom_checkout_css', '').strip()
        org.receipt_footer = request.form.get('receipt_footer', '').strip()
        org.save()
        flash('Branding updated', 'success')
        return redirect(url_for('settings.branding'))

    return render_template('settings/branding.html', org=g.org)


# ---- Team ----

@settings_bp.route('/team')
@login_required
@role_required('admin')
def team():
    return render_template('settings/team.html',
        members=_get_team_members(), org=g.org,
        is_owner=_is_owner(), is_admin=True,
        invite_url=None)


@settings_bp.route('/team/invite-link', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def generate_invite_link():
    '''Generate a shareable invite link (JWT token with org + role).'''
    from btpay.security.tokens import create_secure_token

    role = request.form.get('link_role', 'viewer')
    if role not in ('viewer', 'admin'):
        role = 'viewer'

    hours_str = request.form.get('link_hours', '72')
    try:
        hours = int(hours_str)
    except (ValueError, TypeError):
        hours = 72
    hours = max(1, min(hours, 720))  # 1h - 30 days

    jwt_secrets = current_app.config.get('JWT_SECRETS', {})
    token = create_secure_token(
        'invite', jwt_secrets,
        extras={'org_id': g.org.id, 'role': role, 'invited_by': g.user.id},
        hours=hours,
    )

    invite_url = request.host_url.rstrip('/') + url_for('auth.register_page') + '?invite=' + token
    flash('Invite link generated — copy it below', 'success')
    return render_template('settings/team.html',
        members=_get_team_members(),
        org=g.org,
        is_owner=_is_owner(),
        is_admin=True,
        invite_url=invite_url,
        invite_role=role,
        invite_hours=hours,
    )


@settings_bp.route('/team/invite', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def invite_member():
    from btpay.auth.models import User, Membership
    from btpay.security.validators import validate_email, ValidationError

    email = request.form.get('email', '').strip().lower()
    role = request.form.get('role', 'viewer')

    if role not in ('viewer', 'admin'):
        flash('Invalid role', 'error')
        return redirect(url_for('settings.team'))

    try:
        email = validate_email(email)
    except ValidationError as e:
        flash(str(e), 'error')
        return redirect(url_for('settings.team'))

    user = User.get_by(email=email)
    if user is None:
        flash('User not found. They must register first.', 'error')
        return redirect(url_for('settings.team'))

    # Check not already member
    existing = Membership.query.filter(user_id=user.id, org_id=g.org.id).first()
    if existing:
        flash('User is already a member', 'error')
        return redirect(url_for('settings.team'))

    Membership(
        user_id=user.id,
        org_id=g.org.id,
        role=role,
        invited_by=g.user.id,
    ).save()

    flash('Member added: %s' % email, 'success')
    return redirect(url_for('settings.team'))


@settings_bp.route('/team/remove/<int:member_id>', methods=['POST'])
@login_required
@role_required('owner')
@csrf_protect
def remove_member(member_id):
    from btpay.auth.models import Membership

    membership = Membership.get(member_id)
    if membership is None or membership.org_id != g.org.id:
        flash('Member not found', 'error')
        return redirect(url_for('settings.team'))

    if membership.role == 'owner':
        flash('Cannot remove the owner', 'error')
        return redirect(url_for('settings.team'))

    membership.delete()
    flash('Member removed', 'success')
    return redirect(url_for('settings.team'))


# ---- API Keys ----

@settings_bp.route('/api-keys', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def api_keys():
    from btpay.auth.models import ApiKey

    new_key = None

    if request.method == 'POST':
        _csrf_check()
        label = request.form.get('label', '').strip()
        permissions = request.form.getlist('permissions')

        raw_key = generate_random_token(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        api_key = ApiKey(
            org_id=g.org.id,
            user_id=g.user.id,
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            label=label,
            permissions=permissions,
        )
        api_key.save()
        new_key = raw_key
        flash('API key created', 'success')

    keys = ApiKey.query.filter(org_id=g.org.id).all()
    return render_template('settings/api_keys.html',
        api_keys=keys, org=g.org, new_key=new_key)


@settings_bp.route('/api-keys/<int:key_id>/revoke', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def revoke_api_key(key_id):
    from btpay.auth.models import ApiKey

    key = ApiKey.get(key_id)
    if key is None or key.org_id != g.org.id:
        flash('API key not found', 'error')
        return redirect(url_for('settings.api_keys'))

    key.is_active = False
    key.save()
    flash('API key revoked', 'success')
    return redirect(url_for('settings.api_keys'))


# ---- Webhooks ----

@settings_bp.route('/webhooks', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def webhooks():
    from btpay.api.webhook_models import WebhookEndpoint

    if request.method == 'POST':
        _csrf_check()
        form = request.form
        ep = WebhookEndpoint(
            org_id=g.org.id,
            url=form.get('url', '').strip(),
            secret=form.get('secret', '').strip(),
            description=form.get('description', '').strip(),
            events=form.getlist('events'),
        )
        ep.save()
        flash('Webhook added', 'success')
        return redirect(url_for('settings.webhooks'))

    endpoints = WebhookEndpoint.query.filter(org_id=g.org.id).all()
    return render_template('settings/webhooks.html',
        endpoints=endpoints, org=g.org)


@settings_bp.route('/webhooks/<int:endpoint_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def delete_webhook(endpoint_id):
    from btpay.api.webhook_models import WebhookEndpoint

    ep = WebhookEndpoint.get(endpoint_id)
    if ep is None or ep.org_id != g.org.id:
        flash('Webhook not found', 'error')
        return redirect(url_for('settings.webhooks'))

    ep.delete()
    flash('Webhook deleted', 'success')
    return redirect(url_for('settings.webhooks'))


# ---- Server Info ----

@settings_bp.route('/server')
@login_required
@role_required('admin')
def server_info():
    import sys
    import platform
    import time

    from btpay.bitcoin.models import Wallet
    from btpay.invoicing.models import Invoice
    from btpay.auth.models import User, Organization

    uptime_seconds = time.time() - current_app.config.get('START_TIME', time.time())

    from btpay.version import get_full_version_string

    info = {
        'version': get_full_version_string(),
        'python_version': sys.version.split()[0],
        'platform': platform.platform(),
        'flask_env': current_app.config.get('ENV', 'production'),
        'debug': current_app.debug,
        'demo_mode': current_app.config.get('DEMO_MODE', False),
        'uptime_seconds': int(uptime_seconds),
        'uptime_display': _format_uptime(uptime_seconds),
        'total_users': len(User.query.all()),
        'total_orgs': len(Organization.query.all()),
        'total_invoices': len(Invoice.query.all()),
        'total_wallets': len(Wallet.query.filter(org_id=g.org.id).all()),
    }

    # Check Electrum connectivity
    electrum_config = current_app.config.get('ELECTRUM_CONFIG', {})
    info['electrum_configured'] = bool(electrum_config.get('host'))
    info['electrum_host'] = electrum_config.get('host', 'Not configured')

    return render_template('settings/server_info.html', org=g.org, info=info)


def _format_uptime(seconds):
    '''Format seconds as human-readable uptime.'''
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if days:
        parts.append('%dd' % days)
    if hours:
        parts.append('%dh' % hours)
    parts.append('%dm' % minutes)
    return ' '.join(parts)


# ---- Notifications ----

@settings_bp.route('/notifications', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def notifications():
    org = g.org
    prefs = org.notification_prefs or {}

    if request.method == 'POST':
        _csrf_check()
        org.notification_prefs = {
            'invoice_created': 'invoice_created' in request.form,
            'payment_received': 'payment_received' in request.form,
            'payment_confirmed': 'payment_confirmed' in request.form,
            'invoice_expired': 'invoice_expired' in request.form,
        }
        org.save()
        flash('Notification preferences saved', 'success')
        return redirect(url_for('settings.notifications'))

    return render_template('settings/notifications.html', org=org, prefs=prefs)


# ---- Electrum Server ----

# Well-known public Electrum servers (curated like Sparrow's list)
KNOWN_ELECTRUM_SERVERS = [
    {'host': 'electrum.blockstream.info', 'port': 50002, 'ssl': True, 'source': 'Blockstream'},
    {'host': 'electrum.emzy.de', 'port': 50002, 'ssl': True, 'source': 'emzy.de'},
    {'host': 'electrum.bitaroo.net', 'port': 50002, 'ssl': True, 'source': 'Bitaroo'},
    {'host': 'bitcoin.lu.ke', 'port': 50002, 'ssl': True, 'source': 'luke.dashjr'},
    {'host': 'electrum.hodlister.co', 'port': 50002, 'ssl': True, 'source': 'Hodlister'},
    {'host': 'e2.kze.me', 'port': 50002, 'ssl': True, 'source': 'kze.me'},
    {'host': 'electrum3.hodlister.co', 'port': 50002, 'ssl': True, 'source': 'Hodlister'},
    {'host': 'fortress.qtornern.com', 'port': 443, 'ssl': True, 'source': 'Fortress'},
    {'host': 'electrum.aantonop.com', 'port': 50002, 'ssl': True, 'source': 'aantonop'},
]


@settings_bp.route('/connectors/electrum', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def connectors_electrum():
    org = g.org
    ec = org.electrum_config or {}

    if request.method == 'POST':
        _csrf_check()
        form = request.form
        mode = form.get('mode', 'public')  # 'public' or 'private'

        new_config = {'mode': mode}

        if mode == 'public':
            new_config['host'] = form.get('public_host', '').strip()
            # Look up port/ssl from known list
            for s in KNOWN_ELECTRUM_SERVERS:
                if s['host'] == new_config['host']:
                    new_config['port'] = s['port']
                    new_config['ssl'] = s['ssl']
                    break
            else:
                # Fallback defaults
                new_config['port'] = 50002
                new_config['ssl'] = True
        else:
            new_config['host'] = form.get('host', '').strip()
            new_config['port'] = int(form.get('port') or 50002)
            new_config['ssl'] = form.get('ssl') == '1'
            new_config['verify_ssl'] = not (form.get('skip_verify_ssl') == '1')

        new_config['proxy'] = form.get('proxy', '').strip()

        org.electrum_config = new_config
        org.save()
        flash('Electrum server settings saved', 'success')
        return redirect(url_for('settings.connectors_electrum'))

    return render_template('settings/connectors_electrum.html',
        org=org, ec=ec, known_servers=KNOWN_ELECTRUM_SERVERS)


@settings_bp.route('/connectors/electrum/test', methods=['POST'])
@login_required
@role_required('admin')
def test_electrum():
    '''Test connection to an Electrum server. Returns JSON.'''
    from btpay.bitcoin.electrum import ElectrumClient, ElectrumError

    host = request.json.get('host', '').strip() if request.is_json else ''
    port = int(request.json.get('port', 50002)) if request.is_json else 50002
    use_ssl = request.json.get('ssl', True) if request.is_json else True
    verify_ssl = request.json.get('verify_ssl', True) if request.is_json else True
    proxy = request.json.get('proxy', '').strip() if request.is_json else ''

    if not host:
        return jsonify(error='Host is required'), 400

    client = ElectrumClient(host, port, use_ssl=use_ssl,
                            verify_ssl=verify_ssl,
                            proxy=proxy or None, timeout=10)
    try:
        client.connect()
        version_info = client.server_version()
        banner = ''
        try:
            banner = client.server_banner() or ''
        except Exception:
            pass

        header = None
        try:
            header = client.headers_subscribe()
        except Exception:
            pass

        result = {
            'success': True,
            'server_software': version_info[0] if version_info else 'Unknown',
            'protocol_version': version_info[1] if version_info and len(version_info) > 1 else '?',
            'banner': banner[:500],
        }
        if header:
            result['block_height'] = header.get('height', 0)

        client.disconnect()
        return jsonify(result)
    except ElectrumError as e:
        return jsonify(error=str(e)), 502
    except Exception as e:
        return jsonify(error='Connection failed: %s' % str(e)), 502
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


@settings_bp.route('/connectors/electrum/discover', methods=['POST'])
@login_required
@role_required('admin')
def discover_electrum_servers():
    '''Discover Electrum servers by querying a connected server for peers.'''
    from btpay.bitcoin.electrum import ElectrumClient, ElectrumError

    # Try connecting to a known server and asking for peers
    peers = []
    for server in KNOWN_ELECTRUM_SERVERS[:3]:
        client = ElectrumClient(server['host'], server['port'],
                                use_ssl=server['ssl'], timeout=10)
        try:
            client.connect()
            client.server_version()
            raw_peers = client._call('server.peers.subscribe')
            client.disconnect()
            if raw_peers:
                for peer in raw_peers:
                    if isinstance(peer, list) and len(peer) >= 3:
                        ip = peer[0]
                        hostname = peer[1]
                        features = peer[2] if len(peer) > 2 else []
                        # Parse features for port/ssl info
                        ssl_port = None
                        tcp_port = None
                        for f in features:
                            if isinstance(f, str):
                                if f.startswith('s'):
                                    ssl_port = int(f[1:]) if len(f) > 1 else 50002
                                elif f.startswith('t'):
                                    tcp_port = int(f[1:]) if len(f) > 1 else 50001
                        if hostname and ssl_port:
                            peers.append({
                                'host': hostname,
                                'port': ssl_port,
                                'ssl': True,
                                'source': 'Discovered',
                            })
                        elif hostname and tcp_port:
                            peers.append({
                                'host': hostname,
                                'port': tcp_port,
                                'ssl': False,
                                'source': 'Discovered',
                            })
                break  # Got peers from one server, done
        except (ElectrumError, Exception):
            try:
                client.disconnect()
            except Exception:
                pass
            continue

    # Deduplicate by host
    seen = set(s['host'] for s in KNOWN_ELECTRUM_SERVERS)
    unique_peers = []
    for p in peers:
        if p['host'] not in seen:
            seen.add(p['host'])
            unique_peers.append(p)

    return jsonify(peers=unique_peers[:20])


# ---- Email ----

@settings_bp.route('/email', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def email():
    from btpay.dictobj import DictObj

    org = g.org
    smtp = DictObj(org.smtp_config or {})

    if request.method == 'POST':
        _csrf_check()
        form = request.form
        provider = form.get('email_provider', 'smtp')

        smtp_config = {
            'email_provider': provider,
            'from_addr': form.get('smtp_from', '').strip(),
            'from_name': form.get('smtp_from_name', '').strip(),
        }

        if provider == 'mailgun':
            smtp_config['mailgun_domain'] = form.get('mailgun_domain', '').strip()
            smtp_config['mailgun_region'] = form.get('mailgun_region', 'us').strip()
            # Only update API key if provided
            new_key = form.get('mailgun_api_key', '').strip()
            if new_key:
                smtp_config['mailgun_api_key'] = new_key
            elif smtp.get('mailgun_api_key'):
                smtp_config['mailgun_api_key'] = smtp.mailgun_api_key
        else:
            smtp_config['host'] = form.get('smtp_host', '').strip()
            smtp_config['port'] = int(form.get('smtp_port') or 0)
            smtp_config['user'] = form.get('smtp_user', '').strip()
            # Only update password if provided
            new_pass = form.get('smtp_pass', '').strip()
            if new_pass:
                smtp_config['password'] = new_pass
            elif smtp.get('password'):
                smtp_config['password'] = smtp.password

        org.smtp_config = smtp_config
        org.save()
        flash('Email settings saved', 'success')
        return redirect(url_for('settings.email'))

    return render_template('settings/email.html', org=org, smtp=smtp)


@settings_bp.route('/email/test', methods=['POST'])
@login_required
@role_required('admin')
def test_email():
    '''Send a test email to the logged-in user.'''
    from btpay.email.service import EmailService

    try:
        svc = EmailService.for_org(g.org, current_app.config)
        svc.send(
            to_email=g.user.email,
            subject='BTPay Test Email',
            html_body='<h2>Test Email</h2><p>Your email configuration is working.</p>',
        )
        return jsonify(message='Test email sent to %s' % g.user.email)
    except Exception as e:
        return jsonify(error=str(e)), 500


# ---- Helpers ----

def _csrf_check():
    '''Manual CSRF check for form posts without the decorator.'''
    from btpay.security.csrf import validate_csrf_token
    token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token', '')
    cookie_name = current_app.config.get('AUTH_COOKIE_NAME', 'btpay_session')
    session_token = request.cookies.get(cookie_name, '')
    secret = current_app.config.get('SECRET_KEY', '')
    if not validate_csrf_token(session_token, token, secret):
        from flask import abort
        abort(403)


def _get_team_members():
    '''Get all memberships for the current org.'''
    from btpay.auth.models import Membership
    return Membership.query.filter(org_id=g.org.id).all()


def _is_owner():
    '''Check if the current user is the org owner.'''
    from btpay.auth.models import Membership
    m = Membership.query.filter(org_id=g.org.id, user_id=g.user.id).first()
    return m and m.role == 'owner'

# ---- Backup & Restore ----

@settings_bp.route('/backup')
@login_required
@role_required('admin')
def backup():
    '''Backup & Restore page.'''
    import os
    data_dir = current_app.config.get('DATA_DIR', 'data')
    backup_dir = os.path.join(data_dir, 'backups')

    # Gather info about current data files
    data_files = []
    total_size = 0
    if os.path.isdir(data_dir):
        db_path = os.path.join(data_dir, 'btpay.db')
        if os.path.isfile(db_path):
            size = os.path.getsize(db_path)
            total_size += size
            data_files.append({'name': 'btpay.db', 'size': size})

    # List existing automatic backups
    auto_backups = []
    if os.path.isdir(backup_dir):
        for fname in sorted(os.listdir(backup_dir), reverse=True):
            fpath = os.path.join(backup_dir, fname)
            if os.path.isfile(fpath) and fname.endswith('.db'):
                bsize = os.path.getsize(fpath)
                auto_backups.append({'name': fname, 'files': 1, 'size': bsize})

    return render_template('settings/backup.html', org=g.org,
        data_files=data_files, total_size=total_size,
        auto_backups=auto_backups)


@settings_bp.route('/backup/download')
@login_required
@role_required('admin')
def backup_download():
    '''Download a ZIP archive of the pickle database.'''
    import io, zipfile, datetime, os
    from flask import send_file
    from btpay.orm.persistence import save_to_disk

    data_dir = current_app.config.get('DATA_DIR', 'data')

    # Force a save so the download is up-to-date
    save_to_disk(data_dir)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        db_path = os.path.join(data_dir, 'btpay.db')
        if os.path.isfile(db_path):
            zf.write(db_path, 'btpay.db')

    buf.seek(0)
    timestamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True,
                     download_name='btpay_backup_%s.zip' % timestamp)


@settings_bp.route('/backup/restore', methods=['POST'])
@login_required
@role_required('owner')
def backup_restore():
    '''Restore data from an uploaded ZIP archive.'''
    import io, zipfile, pickle, os
    from btpay.orm.persistence import (
        save_to_disk, backup_rotation, load_from_disk,
    )
    from btpay.orm.engine import MemoryStore

    _csrf_check()

    uploaded = request.files.get('backup_file')
    if not uploaded or not uploaded.filename:
        flash('No file selected', 'error')
        return redirect(url_for('settings.backup'))

    if not uploaded.filename.lower().endswith('.zip'):
        flash('Only .zip files are accepted', 'error')
        return redirect(url_for('settings.backup'))

    try:
        file_bytes = uploaded.read()
        if len(file_bytes) > 50 * 1024 * 1024:  # 50 MB limit
            flash('File too large (max 50 MB)', 'error')
            return redirect(url_for('settings.backup'))

        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile:
        flash('Invalid ZIP file', 'error')
        return redirect(url_for('settings.backup'))

    # Validate: must contain btpay.db
    names = zf.namelist()
    if 'btpay.db' not in names:
        flash('Invalid backup: missing btpay.db', 'error')
        return redirect(url_for('settings.backup'))

    for name in names:
        # Reject path traversal
        if '..' in name or name.startswith('/'):
            flash('Invalid backup: suspicious filename "%s"' % name, 'error')
            return redirect(url_for('settings.backup'))

    # Validate btpay.db is a valid pickle
    try:
        db_bytes = zf.read('btpay.db')
        snapshot = pickle.loads(db_bytes)
        if not isinstance(snapshot, dict) or 'models' not in snapshot:
            flash('Invalid backup: corrupt btpay.db', 'error')
            return redirect(url_for('settings.backup'))
    except Exception:
        flash('Invalid backup: corrupt btpay.db', 'error')
        return redirect(url_for('settings.backup'))

    # All checks passed — create a safety backup before restoring
    data_dir = current_app.config.get('DATA_DIR', 'data')
    try:
        backup_rotation(data_dir, keep=10)
    except Exception:
        log.exception("Pre-restore backup failed")

    # Write the pickle file atomically
    db_dest = os.path.join(data_dir, 'btpay.db')
    tmp_path = db_dest + '.tmp'
    try:
        with open(tmp_path, 'wb') as f:
            f.write(db_bytes)
        os.replace(tmp_path, db_dest)
    except Exception:
        log.exception("Failed to write restored backup")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        flash('Restore failed: could not write data file', 'error')
        return redirect(url_for('settings.backup'))

    # Reload data into memory
    store = MemoryStore()
    store.clear()

    load_from_disk(data_dir)

    flash('Backup restored successfully. You may need to log in again.', 'success')
    log.info("Data restored from uploaded backup by user %s", g.user.email)
    return redirect(url_for('settings.backup'))


# ---- Software Updates ----

@settings_bp.route('/updates')
@login_required
@role_required('owner')
def updates():
    """Software update page."""
    from btpay.version import get_version, get_git_info
    from btpay.updater.git_updater import is_git_available, is_git_repo
    from btpay.updater.backup import get_update_history

    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = current_app.config.get('DATA_DIR', 'data')

    return render_template('settings/updates.html',
        current_version=get_version(),
        git_info=get_git_info(),
        git_available=is_git_available() and is_git_repo(app_root),
        update_history=get_update_history(data_dir),
        update_allowed=current_app.config.get('UPDATE_ALLOWED', True),
    )


@settings_bp.route('/updates/check', methods=['POST'])
@login_required
@role_required('owner')
def updates_check():
    """HTMX endpoint: fetch available versions from GitHub."""
    _csrf_check()
    from btpay.updater.github import GitHubReleaseFetcher
    from btpay.version import get_version

    proxy = current_app.config.get('SOCKS5_PROXY')
    repo = current_app.config.get('UPDATE_REPO', 'btpay-org/btpay')
    fetcher = GitHubReleaseFetcher(repo=repo, proxy=proxy)

    try:
        releases = fetcher.fetch_releases()
        tags = fetcher.fetch_tags()
    except Exception as e:
        log.exception('Failed to check for updates')
        msg = str(e)
        if 'circular import' in msg or 'partially initialized' in msg:
            msg = 'requests library failed to load — try again in a few seconds'
        return render_template_string(
            '<div class="text-red-400 text-sm mt-2">Failed to check for updates: {{ error }}</div>',
            error=msg
        )

    return render_template('settings/_updates_versions.html',
        releases=releases,
        tags=tags,
        current_version=get_version(),
    )


@settings_bp.route('/updates/apply', methods=['POST'])
@login_required
@role_required('owner')
def updates_apply():
    """Apply an update (git tag or uploaded ZIP)."""
    _csrf_check()

    from btpay.version import get_version
    from btpay.updater.checks import pre_update_checks
    from btpay.updater.backup import create_code_backup, create_data_backup, record_update
    from btpay.updater.restart import pip_install, trigger_restart

    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = current_app.config.get('DATA_DIR', 'data')
    backup_dir = os.path.join(data_dir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    from_version = get_version()
    method = request.form.get('method', 'git')  # 'git' or 'zip'
    target_version = request.form.get('version', '')
    force = request.form.get('force') == '1'

    # Pre-flight checks
    if not force:
        issues = pre_update_checks(app_root, data_dir)
        blockers = [i for i in issues if i['level'] == 'blocker']
        if blockers:
            flash(' '.join(i['message'] for i in blockers), 'error')
            return redirect(url_for('settings.updates'))

    # Backup
    try:
        code_backup = create_code_backup(app_root, backup_dir, from_version)
        data_backup = create_data_backup(data_dir, from_version)
    except Exception as e:
        flash('Backup failed: %s' % str(e), 'error')
        return redirect(url_for('settings.updates'))

    # Apply update
    try:
        if method == 'zip':
            from btpay.updater.zip_updater import validate_zip, apply_zip
            f = request.files.get('zipfile')
            if not f:
                flash('No file uploaded', 'error')
                return redirect(url_for('settings.updates'))
            file_bytes = f.read()
            validation = validate_zip(file_bytes)
            if not validation['valid']:
                flash('Invalid ZIP: %s' % validation['error'], 'error')
                return redirect(url_for('settings.updates'))
            target_version = validation['version']
            result = apply_zip(file_bytes, app_root)
            if not result['success']:
                flash('Update failed: %s' % result['error'], 'error')
                return redirect(url_for('settings.updates'))
        else:
            from btpay.updater.git_updater import fetch_tags, checkout_tag
            proxy = current_app.config.get('SOCKS5_PROXY')
            fetch_tags(app_root, proxy=proxy)
            checkout_tag(app_root, target_version)
    except Exception as e:
        flash('Update failed: %s' % str(e), 'error')
        return redirect(url_for('settings.updates'))

    # pip install
    success, output = pip_install(app_root)
    if not success:
        flash('pip install failed: %s' % output, 'error')
        return redirect(url_for('settings.updates'))

    # Record update
    record_update(data_dir, from_version, target_version, method, code_backup, data_backup)

    flash('Updated to %s. Restarting...' % target_version, 'success')

    # Trigger restart after response
    import threading
    def _delayed_restart():
        import time
        time.sleep(2)
        trigger_restart()
    threading.Thread(target=_delayed_restart, daemon=True).start()

    return redirect(url_for('settings.updates'))


@settings_bp.route('/updates/rollback', methods=['POST'])
@login_required
@role_required('owner')
def updates_rollback():
    """Rollback to a previous version."""
    _csrf_check()

    from btpay.updater.backup import restore_code_backup, restore_data_backup, get_update_history
    from btpay.updater.restart import pip_install, trigger_restart

    data_dir = current_app.config.get('DATA_DIR', 'data')
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    code_backup = request.form.get('code_backup', '')
    data_backup = request.form.get('data_backup', '')

    if not code_backup:
        flash('No backup specified', 'error')
        return redirect(url_for('settings.updates'))

    try:
        restore_code_backup(code_backup, app_root)
        if data_backup:
            restore_data_backup(data_backup, data_dir)
        pip_install(app_root)
        flash('Rolled back successfully. Restarting...', 'success')
        import threading
        def _delayed_restart():
            import time
            time.sleep(2)
            trigger_restart()
        threading.Thread(target=_delayed_restart, daemon=True).start()
    except Exception as e:
        flash('Rollback failed: %s' % str(e), 'error')

    return redirect(url_for('settings.updates'))


# ---- Account Security ----

@settings_bp.route('/account', methods=['GET'])
@login_required
def account():
    '''Account security page — change password, manage 2FA.'''
    return render_template('settings/account.html', user=g.user)


# EOF
