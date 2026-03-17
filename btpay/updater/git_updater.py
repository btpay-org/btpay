#
# Git-based updater — fetch tags and checkout releases via local git.
#
import logging
import os
import subprocess

log = logging.getLogger(__name__)

_TIMEOUT = 60


def is_git_available():
    '''Check if git is installed and accessible.'''
    try:
        result = subprocess.run(
            ['git', '--version'],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_git_repo(path):
    '''Check if the given path is inside a git repository.'''
    git_dir = os.path.join(path, '.git')
    return os.path.isdir(git_dir)


def is_clean(path):
    '''Return True if the working tree has no uncommitted changes.'''
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True, text=True, timeout=_TIMEOUT, cwd=path,
        )
        if result.returncode != 0:
            return False
        return result.stdout.strip() == ''
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def fetch_tags(path, proxy=None):
    '''
    Fetch tags from origin. Sets ALL_PROXY env var if proxy is specified.
    Returns (success, output).
    '''
    env = os.environ.copy()
    if proxy:
        env['ALL_PROXY'] = proxy

    try:
        result = subprocess.run(
            ['git', 'fetch', '--tags', 'origin'],
            capture_output=True, text=True, timeout=_TIMEOUT, cwd=path,
            env=env,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            log.warning('git fetch --tags failed: %s', output)
            return (False, output)
        return (True, output)
    except FileNotFoundError:
        return (False, 'git not found')
    except subprocess.TimeoutExpired:
        return (False, 'git fetch timed out')


def checkout_tag(path, tag):
    '''
    Checkout a specific tag. Returns (success, output).
    '''
    try:
        result = subprocess.run(
            ['git', 'checkout', 'tags/%s' % tag],
            capture_output=True, text=True, timeout=_TIMEOUT, cwd=path,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            log.warning('git checkout tags/%s failed: %s', tag, output)
            return (False, output)
        return (True, output)
    except FileNotFoundError:
        return (False, 'git not found')
    except subprocess.TimeoutExpired:
        return (False, 'git checkout timed out')


def current_tag(path):
    '''Return the current exact tag, or None if HEAD is not tagged.'''
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--exact-match'],
            capture_output=True, text=True, timeout=_TIMEOUT, cwd=path,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# EOF
