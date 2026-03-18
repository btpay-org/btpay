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

# Graceful shutdown — handled by the worker's AutoSaver signal handler.
# Do NOT save from on_exit: with preload_app=False, the master process
# has an empty MemoryStore and would overwrite the worker's good data.

# EOF
