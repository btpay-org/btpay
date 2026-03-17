#
# Suspicious request detection
#
# Returns True if a request looks like a hacking attempt.
# These get a terse 404 response (play dumb).
#
import re, logging

log = logging.getLogger(__name__)

# Bogus file extensions (match at end of path or before query string)
_EXPLOIT_EXTENSIONS = {
    '.php', '.asp', '.aspx', '.sql', '.bak', '.env', '.git', '.cgi',
}

# Path components that must match as full segments between slashes
_EXPLOIT_PATH_COMPONENTS = {
    'wp-admin', 'wp-login', 'wp-content', 'wp-includes',
    'xmlrpc', 'phpmyadmin', 'adminer',
    'cgi-bin', 'shell', 'cmd', 'eval',
}

# Dangerous patterns in URL
_DANGEROUS_PATTERNS = [
    re.compile(r'\.\./'),              # directory traversal
    re.compile(r'<script', re.I),       # XSS
    re.compile(r'\$\{jndi:', re.I),     # Log4j / JNDI injection
    re.compile(r'file:', re.I),         # file: protocol
    re.compile(r'%00'),                 # null byte
    re.compile(r'union\s+select', re.I),  # SQL injection
]


def is_hacking_request(path, method='GET', content_length=0):
    '''
    Check if a request looks like a hacking attempt.

    Returns True if suspicious, False if normal.
    '''
    if not path:
        return False

    # URL too long
    if len(path) > 200:
        log.warning("Hack: URL too long (%d): %s..." % (len(path), path[:80]))
        return True

    # Non-ASCII in URL
    try:
        path.encode('ascii')
    except UnicodeEncodeError:
        log.warning("Hack: non-ASCII URL: %s" % path[:80])
        return True

    path_lower = path.lower()

    # Strip query string for extension check
    path_part = path_lower.split('?', 1)[0]

    # Bogus file extensions
    for ext in _EXPLOIT_EXTENSIONS:
        if path_part.endswith(ext):
            log.warning("Hack: exploit extension '%s' in %s" % (ext, path[:80]))
            return True

    # Exploit path components (exact segment match)
    segments = path_part.split('/')
    for segment in segments:
        if segment in _EXPLOIT_PATH_COMPONENTS:
            log.warning("Hack: exploit path '%s' in %s" % (segment, path[:80]))
            return True

    # Dangerous patterns
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(path):
            log.warning("Hack: dangerous pattern in %s" % path[:80])
            return True

    # Double-encoded slashes
    if '%252f' in path_lower or '%255c' in path_lower:
        log.warning("Hack: double-encoded slash in %s" % path[:80])
        return True

    return False

# EOF
