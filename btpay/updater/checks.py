#
# Pre-update safety checks.
#
import logging
import os
import shutil

log = logging.getLogger(__name__)


def pre_update_checks(app_root, data_dir):
    '''
    Run pre-update safety checks.

    Returns list of {'level': 'warning'|'blocker', 'message': str}.
    '''
    issues = []

    # Check data_dir exists
    if not os.path.isdir(data_dir):
        issues.append({
            'level': 'warning',
            'message': 'Data directory does not exist: %s' % data_dir,
        })

    # Check write permissions on app_root
    if not os.access(app_root, os.W_OK):
        issues.append({
            'level': 'blocker',
            'message': 'No write permission on application directory: %s' % app_root,
        })

    # Check disk space: need 3x data dir size free
    if os.path.isdir(data_dir):
        try:
            data_size = _dir_size(data_dir)
            usage = shutil.disk_usage(data_dir)
            required = data_size * 3
            if usage.free < required:
                issues.append({
                    'level': 'blocker',
                    'message': 'Insufficient disk space: need %d MB free, have %d MB' % (
                        required // (1024 * 1024),
                        usage.free // (1024 * 1024),
                    ),
                })
        except Exception as e:
            issues.append({
                'level': 'warning',
                'message': 'Could not check disk space: %s' % str(e),
            })

    # Check for pending/partial invoices
    try:
        from btpay.invoicing.models import Invoice
        pending = Invoice.filter(status='pending')
        partial = Invoice.filter(status='partial')
        active_count = len(pending) + len(partial)
        if active_count > 0:
            issues.append({
                'level': 'warning',
                'message': '%d invoice(s) are pending or partially paid — '
                           'update may interrupt payment monitoring' % active_count,
            })
    except Exception as e:
        log.debug('Could not check invoices: %s', e)

    return issues


def _dir_size(path):
    '''Calculate total size of all files in a directory tree.'''
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                total += os.path.getsize(fpath)
            except OSError:
                pass
    return total


# EOF
