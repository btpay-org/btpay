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

# Preload app to share memory (important for single-worker setup)
preload_app = True

# Graceful shutdown — save data before exit
def on_exit(server):
    '''Save ORM data on shutdown.'''
    try:
        from btpay.orm.persistence import save_to_disk
        import config_default
        data_dir = os.environ.get('BTPAY_DATA_DIR', getattr(config_default, 'DATA_DIR', 'data'))
        save_to_disk(data_dir)
        server.log.info('Data saved on shutdown')
    except Exception as e:
        server.log.error('Failed to save data on shutdown: %s', e)

# EOF
