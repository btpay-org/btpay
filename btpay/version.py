#
# Version information for BTPay
#
import subprocess
from pathlib import Path

__version__ = '0.1.1'

APP_ROOT = Path(__file__).resolve().parent.parent


def get_version():
    '''Return version from git tag, installed package, or __version__.'''
    # Prefer git tag (handles tag-based updates without changing source)
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--exact-match'],
            capture_output=True, text=True, timeout=5, cwd=APP_ROOT,
        )
        if result.returncode == 0:
            tag = result.stdout.strip()
            return tag.lstrip('v') if tag else __version__
    except Exception:
        pass
    # Fall back to installed package metadata
    try:
        from importlib.metadata import version
        return version('btpay')
    except Exception:
        return __version__


def get_git_info():
    '''Return git commit/branch/dirty info, or None if unavailable.'''
    try:
        commit = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=5, cwd=APP_ROOT,
        )
        if commit.returncode != 0:
            return None

        branch = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, timeout=5, cwd=APP_ROOT,
        )

        dirty = subprocess.run(
            ['git', 'diff', '--quiet'],
            capture_output=True, timeout=5, cwd=APP_ROOT,
        )

        return {
            'commit': commit.stdout.strip(),
            'branch': branch.stdout.strip(),
            'dirty': dirty.returncode != 0,
        }
    except Exception:
        return None


def get_full_version_string():
    '''Return version string, e.g. "0.1.0 (abc1234)" or just "0.1.0".'''
    ver = get_version()
    git = get_git_info()
    if git and git.get('commit'):
        suffix = git['commit']
        if git.get('dirty'):
            suffix += '-dirty'
        return f'{ver} ({suffix})'
    return ver


# EOF
