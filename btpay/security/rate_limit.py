#
# In-memory rate limiter — sliding window algorithm
#
import time
from collections import defaultdict, deque


class RateLimiter:
    '''
    Sliding window rate limiter.

    Relies on CPython's GIL for thread safety of dict/deque operations
    instead of explicit locks.

    Usage:
        limiter = RateLimiter()
        if not limiter.check('login:192.168.1.1', max_attempts=5, window_seconds=60):
            raise TooManyAttempts()
    '''

    def __init__(self, cleanup_interval=300):
        self._windows = defaultdict(deque)      # key -> deque of timestamps
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.time()

    def check(self, key, max_attempts, window_seconds):
        '''
        Check if an action is allowed under rate limits.
        Returns True if allowed, False if rate-limited.
        '''
        now = time.time()
        cutoff = now - window_seconds
        window = self._windows[key]

        # Remove expired entries
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= max_attempts:
            return False

        # Record this attempt
        window.append(now)

        # Periodic cleanup
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup(now)

        return True

    def record_failure(self, key):
        '''Record a failed attempt (same as a successful check but without the limit check).'''
        self._windows[key].append(time.time())

    def remaining(self, key, max_attempts, window_seconds):
        '''Return how many attempts are remaining.'''
        now = time.time()
        cutoff = now - window_seconds
        window = self._windows[key]

        while window and window[0] < cutoff:
            window.popleft()

        return max(0, max_attempts - len(window))

    def reset(self, key):
        '''Clear rate limit for a key.'''
        self._windows.pop(key, None)

    def _cleanup(self, now):
        '''Remove stale entries to prevent memory growth.'''
        stale_keys = [
            key for key, window in self._windows.items()
            if not window or window[-1] < now - 3600
        ]
        for key in stale_keys:
            del self._windows[key]
        self._last_cleanup = now

# EOF
