#
# TOTP 2FA — Time-based One-Time Password
#
import io
import pyotp
import qrcode


def generate_totp_secret():
    '''Generate a new TOTP secret (base32 encoded).'''
    return pyotp.random_base32()


def generate_totp_uri(secret, email, issuer='BTPay'):
    '''Generate the otpauth:// URI for QR code provisioning.'''
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def generate_totp_qr(secret, email, issuer='BTPay'):
    '''Generate a QR code image (PNG bytes) for TOTP setup.'''
    uri = generate_totp_uri(secret, email, issuer)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=6,
        border=4,
    )
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def verify_totp(secret, code, last_used=None):
    '''
    Verify a 6-digit TOTP code.
    Rejects replays if last_used matches the current code.
    Returns True if valid.
    '''
    if not secret or not code:
        return False

    code = str(code).strip()
    if len(code) != 6 or not code.isdigit():
        return False

    # Reject replay
    if last_used and last_used == code:
        return False

    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)

# EOF
