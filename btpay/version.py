#
# Version information for BTPay
#
# Single source of truth: pyproject.toml → _build_info.py (generated at build time)
# Runtime git lookups are used as fallback only when _build_info has no commit.
#
import subprocess
from pathlib import Path

from btpay._build_info import VERSION, GIT_COMMIT, GIT_BRANCH

__version__ = VERSION

APP_ROOT = Path(__file__).resolve().parent.parent


def get_version():
    '''Return the version string.'''
    return VERSION


def get_git_info():
    '''Return git commit/branch/dirty info, or None if unavailable.'''
    commit = GIT_COMMIT
    branch = GIT_BRANCH

    # If build info has commit, use it (no subprocess needed)
    if commit:
        return {'commit': commit, 'branch': branch, 'dirty': False}

    # Fallback: live git query (dev mode, editable install)
    try:
        r = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=5, cwd=APP_ROOT,
        )
        if r.returncode != 0:
            return None

        b = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, timeout=5, cwd=APP_ROOT,
        )
        d = subprocess.run(
            ['git', 'diff', '--quiet'],
            capture_output=True, timeout=5, cwd=APP_ROOT,
        )
        return {
            'commit': r.stdout.strip(),
            'branch': b.stdout.strip(),
            'dirty': d.returncode != 0,
        }
    except Exception:
        return None


def get_full_version_string():
    '''Return version string, e.g. "0.1.1 (abc1234)" or just "0.1.1".'''
    ver = get_version()
    git = get_git_info()
    if git and git.get('commit'):
        suffix = git['commit']
        if git.get('dirty'):
            suffix += '-dirty'
        return '%s (%s)' % (ver, suffix)
    return ver


# EOF
