"""A small in-process sliding-window rate limiter (per key, e.g. client IP).

It is per worker process, so with N workers the real limit is N times higher. Account-level protection
(failed_logins on app_users) is stored in the database and holds across workers.
"""

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_hits: int, per_seconds: float):
        self.max_hits = max_hits
        self.per_seconds = per_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record one attempt for `key`. False when the limit for the current window is already used up."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.per_seconds:
                hits.popleft()
            if len(hits) >= self.max_hits:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                # Forget idle keys so memory stays bounded
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.per_seconds]:
                    del self._hits[k]
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()
