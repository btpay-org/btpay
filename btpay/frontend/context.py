#
# Template context processors
#
# Inject common data into all templates.
#
from flask import g, request, current_app
from btpay.chrono import NOW
from btpay.version import get_version


def register_context_processors(app):
    '''Register template context processors.'''

    @app.context_processor
    def inject_globals():
        '''Inject common variables into all templates.'''
        ctx = {
            'current_user': getattr(g, 'user', None),
            'current_org': getattr(g, 'org', None),
            'now': NOW(),
            'dev_mode': current_app.config.get('DEV_MODE', False),
            'btpay_version': get_version(),
        }

        # CSRF token for forms
        if hasattr(g, 'session_token') and g.session_token:
            from btpay.security.csrf import generate_csrf_token
            secret = current_app.config.get('SECRET_KEY', '')
            ctx['csrf_token'] = generate_csrf_token(g.session_token, secret)
        elif hasattr(g, 'user') and g.user:
            from btpay.security.csrf import generate_csrf_token
            secret = current_app.config.get('SECRET_KEY', '')
            ctx['csrf_token'] = generate_csrf_token(str(g.user.id), secret)
        else:
            ctx['csrf_token'] = ''

        return ctx

# EOF
