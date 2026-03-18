#
# Gunicorn configuration for BTPay
#
# Usage:
#   gunicorn -c deploy/gunicorn.conf.py wsgi:app
#

import multiprocessing
import os

# Bind — check BTPAY_BIND first, then PORT (Railway/Heroku), then default
_port = os.environ.get('PORT', '5000')
bind = os.environ.get('BTPAY_BIND', '0.0.0.0:%s' % _port)

# Workers — single worker because of in-memory ORM
# Use threads for concurrency instead of multiple workers
workers = 1
threads = int(os.environ.get('BTPAY_THREADS', '4'))

# Worker class
worker_class = 'gthread'

# Timeouts
timeout = 120
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('BTPAY_LOG_LEVEL', 'info')

# Security
limit_request_line = 4094
limit_request_fields = 50
limit_request_field_size = 8190

# Process naming
proc_name = 'btpay'

# Do NOT use preload_app with this app. The in-memory ORM, background
# threads, and Python's fork-after-threads problem cause worker deadlocks.
# With workers=1 there is no memory benefit to preloading anyway.
preload_app = False

# Tell create_app() to skip background services — the master imports wsgi.py
# to validate the app reference, which runs create_app(). Background services
# must only run in the worker (post_fork sets _BTPAY_WORKER=1).
os.environ['_BTPAY_GUNICORN'] = '1'

def post_fork(server, worker):
    '''Mark this process as a worker and start background services.'''
    os.environ['_BTPAY_WORKER'] = '1'
    try:
        import wsgi
        app = wsgi.app
        if not app.config.get('TESTING'):
            from app import _start_background_services
            _start_background_services(app)
            server.log.info('Background services started in worker %s', worker.pid)
    except Exception as e:
        server.log.error('Failed to start background services: %s', e)

# EOF
