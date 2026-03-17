#
# Zip-based updater — validate and apply zip releases.
#
# Used when the deployment is not a git checkout (e.g. Docker, tarball install).
#
import io
import logging
import os
import shutil
import tempfile
import zipfile

log = logging.getLogger(__name__)

PROTECTED_PATHS = {'config.py', 'config_local.py', 'data', '.git', '.venv', '.env'}
REQUIRED_FILES = {'pyproject.toml', 'btpay'}
MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50 MB

_SKIP_PATTERNS = {'__pycache__', '.pyc'}


def validate_zip(file_bytes):
    '''
    Validate a zip archive for safe extraction.

    Returns {'valid': bool, 'version': str, 'error': str, 'file_count': int}.
    '''
    result = {'valid': False, 'version': '', 'error': '', 'file_count': 0}

    # Size check
    if len(file_bytes) > MAX_ZIP_SIZE:
        result['error'] = 'Zip file exceeds maximum size of %d bytes' % MAX_ZIP_SIZE
        return result

    # Valid zip?
    if not zipfile.is_zipfile(io.BytesIO(file_bytes)):
        result['error'] = 'Not a valid zip file'
        return result

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zf:
            names = zf.namelist()
            result['file_count'] = len(names)

            # Path traversal check
            for name in names:
                if '..' in name or os.path.isabs(name):
                    result['error'] = 'Zip contains unsafe path: %s' % name
                    return result

            # Strip common prefix if all files share one (e.g. btpay-v0.2.0/)
            prefix = _detect_prefix(names)

            # Check for protected paths
            for name in names:
                relative = _strip_prefix(name, prefix)
                top_level = relative.split('/')[0] if '/' in relative else relative
                if top_level in PROTECTED_PATHS:
                    result['error'] = 'Zip contains protected path: %s' % top_level
                    return result

            # Check required files exist
            stripped = set()
            for name in names:
                relative = _strip_prefix(name, prefix)
                top_level = relative.split('/')[0] if '/' in relative else relative
                stripped.add(top_level)

            missing = REQUIRED_FILES - stripped
            if missing:
                result['error'] = 'Zip missing required files: %s' % ', '.join(sorted(missing))
                return result

            # Read version from pyproject.toml
            version = ''
            for name in names:
                relative = _strip_prefix(name, prefix)
                if relative == 'pyproject.toml':
                    try:
                        content = zf.read(name).decode('utf-8')
                        version = _extract_version(content)
                    except Exception:
                        pass
                    break

            result['valid'] = True
            result['version'] = version
    except zipfile.BadZipFile:
        result['error'] = 'Corrupted zip file'
    except Exception as e:
        result['error'] = 'Zip validation error: %s' % str(e)

    return result


def apply_zip(file_bytes, app_root, staging_dir=None):
    '''
    Extract zip to staging area, then copy non-protected files to app_root.

    Returns {'success': bool, 'version': str, 'files_updated': int, 'error': str}.
    '''
    result = {'success': False, 'version': '', 'files_updated': 0, 'error': ''}

    # Validate first
    validation = validate_zip(file_bytes)
    if not validation['valid']:
        result['error'] = validation['error']
        return result

    result['version'] = validation['version']
    use_tmp = staging_dir is None
    staging = staging_dir or tempfile.mkdtemp(prefix='btpay_update_')

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zf:
            names = zf.namelist()
            prefix = _detect_prefix(names)

            # Extract to staging
            for name in names:
                relative = _strip_prefix(name, prefix)
                if not relative or name.endswith('/'):
                    continue

                # Skip __pycache__ and .pyc
                if _should_skip(relative):
                    continue

                # Skip protected paths
                top_level = relative.split('/')[0]
                if top_level in PROTECTED_PATHS:
                    continue

                dest = os.path.join(staging, relative)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(name) as src, open(dest, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

        # Copy staged files to app_root
        files_updated = 0
        for dirpath, dirnames, filenames in os.walk(staging):
            rel_dir = os.path.relpath(dirpath, staging)
            for fname in filenames:
                src_path = os.path.join(dirpath, fname)
                if rel_dir == '.':
                    dest_path = os.path.join(app_root, fname)
                else:
                    dest_path = os.path.join(app_root, rel_dir, fname)

                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy2(src_path, dest_path)
                files_updated += 1

        result['success'] = True
        result['files_updated'] = files_updated

    except Exception as e:
        result['error'] = 'Failed to apply zip update: %s' % str(e)
        log.exception('Zip update failed')
    finally:
        if use_tmp and os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)

    return result


def _detect_prefix(names):
    '''Detect a common directory prefix shared by all zip entries.'''
    if not names:
        return ''
    # Check if all entries start with the same top-level directory
    tops = set()
    for name in names:
        parts = name.split('/')
        if len(parts) > 1:
            tops.add(parts[0])
        else:
            return ''  # Has root-level files, no common prefix
    if len(tops) == 1:
        return tops.pop() + '/'
    return ''


def _strip_prefix(name, prefix):
    '''Remove the common prefix from a zip entry name.'''
    if prefix and name.startswith(prefix):
        return name[len(prefix):]
    return name


def _should_skip(relative):
    '''Check if a file should be skipped during extraction.'''
    parts = relative.split('/')
    for part in parts:
        if part == '__pycache__':
            return True
    if relative.endswith('.pyc'):
        return True
    return False


def _extract_version(pyproject_content):
    '''Extract version string from pyproject.toml content.'''
    import re
    m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', pyproject_content)
    if m:
        return m.group(1)
    return ''


# EOF
