#
# Backup and restore for the update system.
#
# Code backups use tar.gz archives; data backups use the same JSON dump
# pattern as btpay.orm.persistence.backup_rotation.
#
import datetime
import json
import logging
import os
import tarfile
import tempfile

log = logging.getLogger(__name__)

CODE_DIRS = ['btpay', 'templates', 'static', 'deploy', 'docs']
CODE_FILES = ['app.py', 'wsgi.py', 'pyproject.toml', 'Makefile', 'Procfile']


def create_code_backup(app_root, backup_dir, version):
    '''
    Create a tarball of code directories and files.

    Returns the path to the backup file, or None on failure.
    '''
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = 'code_%s_%s.tar.gz' % (version, timestamp)
    dest = os.path.join(backup_dir, filename)
    tmp_path = dest + '.tmp'

    try:
        with tarfile.open(tmp_path, 'w:gz') as tar:
            for dirname in CODE_DIRS:
                full = os.path.join(app_root, dirname)
                if os.path.isdir(full):
                    tar.add(full, arcname=dirname)

            for fname in CODE_FILES:
                full = os.path.join(app_root, fname)
                if os.path.isfile(full):
                    tar.add(full, arcname=fname)

        os.replace(tmp_path, dest)
        log.info('Code backup created: %s', dest)
        return dest
    except Exception:
        log.exception('Failed to create code backup')
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return None


def create_data_backup(data_dir, version):
    '''
    Create a JSON snapshot of all data files (same pattern as persistence.backup_rotation).

    Returns the path to the backup file, or None on failure.
    '''
    backup_dir = os.path.join(data_dir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = 'data_%s_%s.json' % (version, timestamp)
    dest = os.path.join(backup_dir, filename)
    tmp_path = dest + '.tmp'

    try:
        # Collect all model data into a single snapshot
        snapshot = {}
        for fname in os.listdir(data_dir):
            if fname.endswith('.json'):
                fpath = os.path.join(data_dir, fname)
                with open(fpath, 'r') as f:
                    snapshot[fname] = json.load(f)

        with open(tmp_path, 'w') as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp_path, dest)
        log.info('Data backup created: %s', dest)
        return dest
    except Exception:
        log.exception('Failed to create data backup')
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return None


def restore_code_backup(backup_path, app_root):
    '''
    Extract a code tarball over app_root.

    Returns (success, error_message).
    '''
    if not os.path.isfile(backup_path):
        return (False, 'Backup file not found: %s' % backup_path)

    try:
        with tarfile.open(backup_path, 'r:gz') as tar:
            # Safety: reject any paths that escape app_root
            for member in tar.getmembers():
                target = os.path.realpath(os.path.join(app_root, member.name))
                if not target.startswith(os.path.realpath(app_root)):
                    return (False, 'Unsafe path in backup: %s' % member.name)

            tar.extractall(path=app_root)

        log.info('Code restored from %s', backup_path)
        return (True, '')
    except Exception as e:
        log.exception('Failed to restore code backup')
        return (False, 'Restore failed: %s' % str(e))


def restore_data_backup(backup_path, data_dir):
    '''
    Restore JSON data from a backup snapshot.

    Returns (success, error_message).
    '''
    if not os.path.isfile(backup_path):
        return (False, 'Backup file not found: %s' % backup_path)

    try:
        with open(backup_path, 'r') as f:
            snapshot = json.load(f)

        for fname, content in snapshot.items():
            fpath = os.path.join(data_dir, fname)
            tmp_path = fpath + '.tmp'
            with open(tmp_path, 'w') as f:
                json.dump(content, f, indent=2)
            os.replace(tmp_path, fpath)

        log.info('Data restored from %s', backup_path)
        return (True, '')
    except Exception as e:
        log.exception('Failed to restore data backup')
        return (False, 'Restore failed: %s' % str(e))


def get_update_history(data_dir):
    '''Read update_history.json and return list of update records.'''
    history_path = os.path.join(data_dir, 'update_history.json')
    if not os.path.isfile(history_path):
        return []

    try:
        with open(history_path, 'r') as f:
            return json.load(f)
    except Exception:
        log.exception('Failed to read update history')
        return []


def record_update(data_dir, from_ver, to_ver, method, code_backup, data_backup):
    '''
    Append an update record to update_history.json (atomic write).
    '''
    os.makedirs(data_dir, exist_ok=True)
    history_path = os.path.join(data_dir, 'update_history.json')

    history = get_update_history(data_dir)
    history.append({
        'from_version': from_ver,
        'to_version': to_ver,
        'method': method,
        'code_backup': code_backup,
        'data_backup': data_backup,
        'timestamp': datetime.datetime.utcnow().isoformat(),
    })

    tmp_path = history_path + '.tmp'
    try:
        with open(tmp_path, 'w') as f:
            json.dump(history, f, indent=2)
        os.replace(tmp_path, history_path)
    except Exception:
        log.exception('Failed to record update')
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# EOF
