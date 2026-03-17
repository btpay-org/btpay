#
# Post-update restart and pip install utilities.
#
import logging
import os
import signal
import subprocess
import sys

log = logging.getLogger(__name__)


def is_gunicorn():
    '''Check if the current process is running under gunicorn.'''
    server_sw = os.environ.get('SERVER_SOFTWARE', '')
    return server_sw.startswith('gunicorn')


def trigger_restart():
    '''
    Send SIGHUP to the parent process to trigger a graceful restart (gunicorn).

    Returns {'restarted': bool, 'message': str}.
    '''
    if not is_gunicorn():
        return {
            'restarted': False,
            'message': 'Not running under gunicorn — manual restart required',
        }

    try:
        ppid = os.getppid()
        os.kill(ppid, signal.SIGHUP)
        log.info('Sent SIGHUP to parent process %d', ppid)
        return {
            'restarted': True,
            'message': 'SIGHUP sent to gunicorn master (pid %d)' % ppid,
        }
    except OSError as e:
        log.exception('Failed to send SIGHUP')
        return {
            'restarted': False,
            'message': 'Failed to send SIGHUP: %s' % str(e),
        }


def pip_install(app_root, full=False):
    '''
    Run pip install for the application.

    If full=True, runs `pip install -e .` (installs all dependencies).
    If full=False, runs `pip install -e . --no-deps` (code only).

    Returns (success, output).
    '''
    cmd = [sys.executable, '-m', 'pip', 'install', '-e', '.']
    if not full:
        cmd.append('--no-deps')

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=300, cwd=app_root,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            log.warning('pip install failed: %s', output)
            return (False, output)
        log.info('pip install succeeded')
        return (True, output)
    except FileNotFoundError:
        return (False, 'Python executable not found')
    except subprocess.TimeoutExpired:
        return (False, 'pip install timed out after 300s')


# EOF
