#
# Structured logging configuration
#
# Provides JSON-formatted logging for production and human-readable for dev.
#
import json
import logging
import sys
import time


class JsonFormatter(logging.Formatter):
    '''JSON log formatter for structured logging in production.'''

    def format(self, record):
        log_entry = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(record.created)),
            'level': record.levelname.lower(),
            'logger': record.name,
            'msg': record.getMessage(),
        }

        if record.exc_info and record.exc_info[0] is not None:
            log_entry['exception'] = self.formatException(record.exc_info)

        # Add any extra fields
        for key in ('method', 'path', 'status', 'duration_ms', 'ip',
                     'user_id', 'org_id', 'event', 'invoice_id'):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry, default=str)


class DevFormatter(logging.Formatter):
    '''Human-readable formatter for development.'''

    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[41m',  # Red background
    }
    RESET = '\033[0m'

    def format(self, record):
        color = self.COLORS.get(record.levelname, '')
        ts = time.strftime('%H:%M:%S', time.localtime(record.created))
        name = record.name.split('.')[-1] if '.' in record.name else record.name
        msg = '%s%s%s %s: %s' % (color, record.levelname[0], self.RESET, name, record.getMessage())

        if record.exc_info and record.exc_info[0] is not None:
            msg += '\n' + self.formatException(record.exc_info)

        return '%s %s' % (ts, msg)


def setup_logging(app):
    '''Configure logging based on app config.'''
    dev_mode = app.config.get('DEV_MODE', False)
    log_level = app.config.get('LOG_LEVEL', 'DEBUG' if dev_mode else 'INFO')

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)

    if dev_mode:
        handler.setFormatter(DevFormatter())
    else:
        handler.setFormatter(JsonFormatter())

    root.addHandler(handler)

    # Quiet down noisy libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

# EOF
