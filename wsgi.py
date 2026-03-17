#
# WSGI entry point for gunicorn
#
# Usage: gunicorn wsgi:app -w 1 -b 0.0.0.0:5000
#
# NOTE: Use -w 1 (single worker) since data is in-memory.
# Multiple workers would each have their own data store.
#
from app import create_app

app = create_app()

# EOF
