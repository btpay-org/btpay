#
# Auth views — Flask blueprint
#
# Routes: register, login, login/totp, logout, totp/setup, totp/enable,
#         totp/disable, password change
#
import logging
from flask import Blueprint, request, jsonify, g, current_app, make_response, redirect, url_for

from btpay.security.rate_limit import RateLimiter
from btpay.security.validators import validate_email, ValidationError
from btpay.security.tokens import create_secure_token, verify_secure_token
from btpay.auth.models import User, Organization, Membership, MIN_PASSWORD_LENGTH
from btpay.auth.sessions import (
    create_session, destroy_session,
    set_session_cookie, clear_session_cookie,
)
from btpay.auth.decorators import login_required

log = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# Module-level rate limiter
_limiter = RateLimiter()


# ---- Register ----

@auth_bp.route('/register', methods=['POST'])
def register():
    '''Create a new user account. Supports ?invite=<token> for org invite links.'''
    from flask import flash
    data = request.get_json(silent=True) or request.form
    is_form = request.content_type and 'form' in request.content_type

    def _error(msg):
        if is_form:
            flash(msg, 'error')
            # Preserve invite token on redirect
            invite_token = data.get('invite_token') or ''
            redir = url_for('auth.register_page')
            if invite_token:
                redir += '?invite=' + invite_token
            return redirect(redir)
        return jsonify(error=msg), 400

    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()

    # Validate email
    try:
        email = validate_email(email)
    except ValidationError as e:
        return _error(str(e))

    # Check uniqueness
    if User.get_by(email=email):
        return _error('Email already registered')

    # Validate password
    if len(password) < MIN_PASSWORD_LENGTH:
        return _error('Password must be at least %d characters' % MIN_PASSWORD_LENGTH)

    # After the first user (owner), registration requires an invite link
    invite_token = data.get('invite_token') or ''
    invite_org = None
    invite_role = 'viewer'
    has_users = User.query.count() > 0

    if not invite_token and has_users:
        return _error('Registration is invite-only. Ask an admin for an invite link.')

    if invite_token:
        jwt_secrets = current_app.config.get('JWT_SECRETS', {})
        try:
            expired, claims = verify_secure_token('invite', invite_token, jwt_secrets)
            if expired:
                return _error('Invite link has expired. Ask the admin for a new one.')
            invite_org = Organization.get(claims.get('org_id'))
            invite_role = claims.get('role', 'viewer')
            if invite_org is None:
                return _error('Invalid invite link — organization not found.')
        except ValueError:
            return _error('Invalid invite link.')

    # Create user
    user = User(email=email, first_name=first_name, last_name=last_name)
    user.set_password(password)
    user.save()

    org = None

    if invite_org:
        # Join the invited org
        org = invite_org
        Membership(
            user_id=user.id,
            org_id=org.id,
            role=invite_role,
            invited_by=0,
        ).save()
        log.info('User %s joined org %s via invite link (role=%s)', email, org.name, invite_role)
    elif User.query.count() == 1:
        # First user becomes owner with a default organization
        org = Organization(
            name='My Organization',
            slug=Organization.make_slug(first_name or email.split('@')[0]),
        )
        org.save()
        Membership(user_id=user.id, org_id=org.id, role='owner').save()

    # Create session
    token = create_session(user, org, request)

    if is_form:
        # First user → setup wizard; invited users → dashboard
        if not invite_org and User.query.count() == 1:
            resp = make_response(redirect(url_for('setup.index')))
        else:
            resp = make_response(redirect(url_for('dashboard.index')))
    else:
        resp = make_response(jsonify(
            ok=True,
            user_id=user.id,
            org_id=org.id if org else None,
        ), 201)
    set_session_cookie(resp, token)

    log.info('User registered: %s (id=%d)', email, user.id)
    return resp


# ---- Login ----

@auth_bp.route('/login', methods=['POST'])
def login():
    '''Authenticate with email + password.'''
    from flask import flash
    data = request.get_json(silent=True) or request.form
    is_form = request.content_type and 'form' in request.content_type

    def _error(msg, code=401):
        if is_form:
            flash(msg, 'error')
            return redirect(url_for('auth.login_page'))
        return jsonify(error=msg), code

    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    # Rate limit by IP
    ip = request.remote_addr or '0.0.0.0'
    rl_cfg = current_app.config.get('RATE_LIMIT_LOGIN')
    max_att = getattr(rl_cfg, 'max_attempts', 5) if rl_cfg else 5
    window = getattr(rl_cfg, 'window_seconds', 60) if rl_cfg else 60

    if not current_app.config.get('DEMO_MODE') and not _limiter.check('login:' + ip, max_att, window):
        return _error('Too many login attempts. Try again later.', 429)

    user = User.get_by(email=email)
    if user is None:
        return _error('Invalid credentials')

    # Return same error for locked accounts to prevent account enumeration
    if user.is_locked:
        return _error('Invalid credentials')

    if not user.check_password(password):
        user.record_failed_login()
        return _error('Invalid credentials')

    # TOTP required?
    if user.totp_enabled and user.totp_secret:
        jwt_secrets = current_app.config.get('JWT_SECRETS', {})
        pending_token = create_secure_token(
            'login', jwt_secrets, extras={'user_id': user.id}, seconds=300
        )
        return jsonify(totp_required=True, login_token=pending_token), 200

    # Success — no TOTP
    user.record_successful_login()

    # Find user's org
    membership = Membership.query.filter(user_id=user.id).first()
    org = Organization.get(membership.org_id) if membership else None

    token = create_session(user, org, request)

    # Form submission → redirect; JSON API → return JSON
    is_form = request.content_type and 'form' in request.content_type
    if is_form:
        resp = make_response(redirect(url_for('dashboard.index')))
    else:
        resp = make_response(jsonify(ok=True, user_id=user.id))
    set_session_cookie(resp, token)

    log.info('User logged in: %s', email)
    return resp


# ---- Login TOTP Step ----

@auth_bp.route('/login/totp', methods=['POST'])
def login_totp():
    '''Complete login with TOTP code.'''
    # Rate limit TOTP login to prevent brute-force (6-digit = 1M possibilities)
    ip = request.remote_addr or '0.0.0.0'
    if not current_app.config.get('DEMO_MODE') and not _limiter.check('totp_login:' + ip, 5, 60):
        return jsonify(error='Too many TOTP attempts. Try again later.'), 429

    data = request.get_json(silent=True) or request.form

    login_token = data.get('login_token') or ''
    totp_code = data.get('totp_code') or ''

    jwt_secrets = current_app.config.get('JWT_SECRETS', {})

    try:
        expired, contents = verify_secure_token('login', login_token, jwt_secrets)
    except ValueError:
        return jsonify(error='Invalid login token'), 400

    if expired:
        return jsonify(error='Login token expired. Please log in again.'), 401

    user_id = contents.get('user_id')
    user = User.get(user_id)
    if user is None:
        return jsonify(error='User not found'), 400

    # Verify TOTP
    from btpay.auth.totp import verify_totp
    if not verify_totp(user.totp_secret, totp_code, last_used=user.last_totp_used):
        return jsonify(error='Invalid TOTP code'), 401

    # Update replay prevention
    user.last_totp_used = totp_code.strip()
    user.record_successful_login()

    membership = Membership.query.filter(user_id=user.id).first()
    org = Organization.get(membership.org_id) if membership else None

    token = create_session(user, org, request)
    resp = make_response(jsonify(ok=True, user_id=user.id))
    set_session_cookie(resp, token)

    log.info('User logged in (TOTP): %s', user.email)
    return resp


# ---- Logout ----

@auth_bp.route('/logout', methods=['POST'])
def logout():
    '''Destroy the current session.'''
    cookie_name = current_app.config.get('AUTH_COOKIE_NAME', 'btpay_session')
    token = request.cookies.get(cookie_name)
    if token:
        destroy_session(token)

    if request.content_type and 'json' in request.content_type:
        resp = make_response(jsonify(ok=True))
    else:
        resp = make_response(redirect(url_for('auth.login_page')))
    clear_session_cookie(resp)
    return resp


# ---- TOTP Setup ----

@auth_bp.route('/totp/setup', methods=['GET'])
@login_required
def totp_setup():
    '''Generate a new TOTP secret + QR code for setup.'''
    from btpay.auth.totp import generate_totp_secret, generate_totp_qr
    import base64

    secret = generate_totp_secret()
    qr_png = generate_totp_qr(secret, g.user.email)
    qr_b64 = base64.b64encode(qr_png).decode('ascii')

    # Store pending secret server-side so /totp/enable cannot substitute its own
    g.user.pending_totp_secret = secret
    g.user.save()

    return jsonify(
        secret=secret,
        qr_code='data:image/png;base64,' + qr_b64,
    )


@auth_bp.route('/totp/enable', methods=['POST'])
@login_required
def totp_enable():
    '''Enable TOTP after verifying a code against the provided secret.'''
    # Rate limit TOTP verification to prevent brute-force (6-digit = 1M possibilities)
    ip = request.remote_addr or '0.0.0.0'
    if not _limiter.check('totp_enable:' + ip, 5, 60):
        return jsonify(error='Too many attempts. Try again later.'), 429

    data = request.get_json(silent=True) or request.form
    code = data.get('totp_code') or ''

    # Retrieve the pending secret from server-side state, not from client
    secret = g.user.pending_totp_secret
    if not secret:
        return jsonify(error='No pending TOTP setup. Call /totp/setup first.'), 400

    from btpay.auth.totp import verify_totp
    if not verify_totp(secret, code):
        return jsonify(error='Invalid TOTP code'), 400

    g.user.totp_secret = secret
    g.user.totp_enabled = True
    g.user.last_totp_used = code.strip()
    g.user.pending_totp_secret = ''
    g.user.save()

    log.info('TOTP enabled for user %s', g.user.email)
    return jsonify(ok=True)


@auth_bp.route('/totp/disable', methods=['POST'])
@login_required
def totp_disable():
    '''Disable TOTP (requires current code to prove device access).'''
    # Rate limit TOTP verification
    ip = request.remote_addr or '0.0.0.0'
    if not _limiter.check('totp_disable:' + ip, 5, 60):
        return jsonify(error='Too many attempts. Try again later.'), 429

    data = request.get_json(silent=True) or request.form
    code = data.get('totp_code') or ''

    from btpay.auth.totp import verify_totp
    if not verify_totp(g.user.totp_secret, code, last_used=g.user.last_totp_used):
        return jsonify(error='Invalid TOTP code'), 401

    g.user.totp_secret = ''
    g.user.totp_enabled = False
    g.user.last_totp_used = ''
    g.user.save()

    log.info('TOTP disabled for user %s', g.user.email)
    return jsonify(ok=True)


# ---- Password Change ----

@auth_bp.route('/password', methods=['POST'])
@login_required
def change_password():
    '''Change password (requires current password).'''
    data = request.get_json(silent=True) or request.form

    current_pw = data.get('current_password') or ''
    new_pw = data.get('new_password') or ''

    if not g.user.check_password(current_pw):
        return jsonify(error='Current password is incorrect'), 401

    try:
        g.user.set_password(new_pw)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    g.user.save()

    # Invalidate all other sessions for this user
    from btpay.auth.models import Session as SessionModel
    cookie_name = current_app.config.get('AUTH_COOKIE_NAME', 'btpay_session')
    current_token = request.cookies.get(cookie_name)
    current_hash = None
    if current_token:
        from btpay.auth.sessions import _hash_token
        current_hash = _hash_token(current_token)

    for sess in SessionModel.query.filter(user_id=g.user.id).all():
        if current_hash and sess.token_hash == current_hash:
            continue  # keep the current session
        sess.delete()

    log.info('Password changed for user %s (other sessions invalidated)', g.user.email)
    return jsonify(ok=True)


# ---- Pages ----

@auth_bp.route('/login', methods=['GET'])
def login_page():
    '''Login page. Redirects to register if no users exist.'''
    from flask import render_template, flash
    if User.query.count() == 0:
        flash('No accounts exist yet. Create your first admin account.', 'info')
        return redirect(url_for('auth.register_page'))
    return render_template('auth/login.html', allow_register=False)


@auth_bp.route('/register', methods=['GET'])
def register_page():
    '''Registration page. Accepts ?invite=<token> for org invites.'''
    from flask import render_template
    invite_token = request.args.get('invite', '')

    # If users already exist and no invite token, redirect to login
    if not invite_token and User.query.count() > 0:
        from flask import flash
        flash('Registration is invite-only. Ask an admin for an invite link.', 'error')
        return redirect(url_for('auth.login_page'))

    # Decode invite info for display (org name, role)
    invite_org_name = ''
    invite_role = ''
    if invite_token:
        jwt_secrets = current_app.config.get('JWT_SECRETS', {})
        try:
            expired, claims = verify_secure_token('invite', invite_token, jwt_secrets)
            if not expired:
                org = Organization.get(claims.get('org_id'))
                if org:
                    invite_org_name = org.name
                    invite_role = claims.get('role', 'viewer')
        except ValueError:
            pass

    return render_template('auth/register.html',
        invite_token=invite_token,
        invite_org_name=invite_org_name,
        invite_role=invite_role)


@auth_bp.route('/totp/setup-page', methods=['GET'])
@login_required
def totp_setup_page():
    '''TOTP setup page.'''
    from flask import render_template
    from btpay.auth.totp import generate_totp_secret, generate_totp_qr
    import base64

    totp_secret = ''
    qr_data_uri = ''
    if not g.user.totp_enabled:
        totp_secret = generate_totp_secret()
        qr_png = generate_totp_qr(totp_secret, g.user.email)
        qr_data_uri = 'data:image/png;base64,' + base64.b64encode(qr_png).decode('ascii')
        # Store pending secret server-side
        g.user.pending_totp_secret = totp_secret
        g.user.save()

    return render_template('auth/totp_setup.html',
        user=g.user, totp_secret=totp_secret, qr_data_uri=qr_data_uri)

# EOF
