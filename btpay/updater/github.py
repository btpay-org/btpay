#
# GitHub release fetcher — queries tags and releases via the GitHub API.
#
# Uses requests if available, falls back to stdlib urllib.
#
import json
import logging
import time
import urllib.request

from btpay.updater.version_compare import sort_versions

log = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes


def _fetch_json(url, timeout=15):
    '''Fetch JSON from a URL using stdlib urllib (no external deps).'''
    req = urllib.request.Request(url, headers={
        'User-Agent': 'BTPay/1.0',
        'Accept': 'application/vnd.github.v3+json',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class GitHubReleaseFetcher:
    '''
    Fetches tags and releases from a GitHub repository.

    Usage:
        fetcher = GitHubReleaseFetcher(repo='btpay-org/btpay')
        tags = fetcher.fetch_tags()
        releases = fetcher.fetch_releases()
    '''

    def __init__(self, repo='btpay-org/btpay', proxy=None):
        self.repo = repo
        self.proxy = proxy
        self._cache = {}  # key -> (timestamp, data)

    def fetch_tags(self):
        '''
        GET /repos/{repo}/tags, return list of
        {'tag': 'v0.1.0', 'commit': 'abc123'} sorted by version (newest first).
        '''
        cached = self._get_cached('tags')
        if cached is not None:
            return cached

        url = 'https://api.github.com/repos/%s/tags' % self.repo

        try:
            data = _fetch_json(url)
        except Exception:
            log.exception('Failed to fetch tags from %s', url)
            return []

        tags = []
        for item in data:
            name = item.get('name', '')
            sha = item.get('commit', {}).get('sha', '')
            tags.append({'tag': name, 'commit': sha})

        # Sort by version, newest first
        tag_names = sort_versions([t['tag'] for t in tags])
        tag_map = {t['tag']: t for t in tags}
        result = [tag_map[name] for name in tag_names if name in tag_map]

        self._set_cached('tags', result)
        return result

    def fetch_releases(self):
        '''
        GET /repos/{repo}/releases, return list of
        {'tag': str, 'name': str, 'body': str, 'date': str, 'prerelease': bool}.
        '''
        cached = self._get_cached('releases')
        if cached is not None:
            return cached

        url = 'https://api.github.com/repos/%s/releases' % self.repo

        try:
            data = _fetch_json(url)
        except Exception:
            log.exception('Failed to fetch releases from %s', url)
            return []

        releases = []
        for item in data:
            releases.append({
                'tag': item.get('tag_name', ''),
                'name': item.get('name', ''),
                'body': item.get('body', ''),
                'date': item.get('published_at', ''),
                'prerelease': item.get('prerelease', False),
            })

        self._set_cached('releases', releases)
        return releases

    def _get_cached(self, key):
        '''Return cached data if still valid, else None.'''
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.time() - ts > _CACHE_TTL:
            del self._cache[key]
            return None
        return data

    def _set_cached(self, key, data):
        '''Store data in cache with current timestamp.'''
        self._cache[key] = (time.time(), data)


# EOF
