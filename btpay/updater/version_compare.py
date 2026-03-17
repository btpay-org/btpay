#
# Version comparison utilities for semver-style tags.
#
import re

_VERSION_RE = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')


def parse_version(tag_str):
    '''Parse "v1.2.3" or "1.2.3" into tuple (1, 2, 3). Returns None on failure.'''
    tag_str = tag_str.strip()
    m = _VERSION_RE.match(tag_str)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_newer(a, b):
    '''True if version string a is newer than version string b.'''
    va = parse_version(a)
    vb = parse_version(b)
    if va is None or vb is None:
        return False
    return va > vb


def sort_versions(tags):
    '''Sort list of tag strings newest-first. Invalid tags are excluded.'''
    parsed = []
    for tag in tags:
        v = parse_version(tag)
        if v is not None:
            parsed.append((v, tag))
    parsed.sort(key=lambda x: x[0], reverse=True)
    return [tag for _, tag in parsed]


# EOF
