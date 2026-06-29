"""
Rate limiter — per-user sliding window, prevents bot spam at scale
"""
import time
import asyncio
from typing import Dict, Tuple
from collections import defaultdict


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window       = window_seconds
        self._buckets: Dict[int, list] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def is_allowed(self, user_id: int) -> Tuple[bool, int]:
        async with self._lock:
            now    = time.time()
            bucket = self._buckets[user_id]
            self._buckets[user_id] = [t for t in bucket if now - t < self.window]
            if len(self._buckets[user_id]) < self.max_requests:
                self._buckets[user_id].append(now)
                return True, self.max_requests - len(self._buckets[user_id])
            return False, 0


class RateLimiterSync:
    """Thread-safe sync version for use inside worker threads."""
    import threading as _threading

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window       = window_seconds
        self._buckets: Dict[int, list] = defaultdict(list)
        import threading
        self._lock = threading.Lock()

    def is_allowed(self, user_id: int) -> bool:
        with self._lock:
            now    = time.time()
            bucket = self._buckets[user_id]
            self._buckets[user_id] = [t for t in bucket if now - t < self.window]
            if len(self._buckets[user_id]) < self.max_requests:
                self._buckets[user_id].append(now)
                return True
            return False


# Global rate limiter instances
_message_limiter = RateLimiter(max_requests=20, window_seconds=60)
_upload_limiter  = RateLimiter(max_requests=5,  window_seconds=60)
_scan_limiter    = RateLimiter(max_requests=2,  window_seconds=60)


async def check_message_rate(user_id: int) -> bool:
    ok, _ = await _message_limiter.is_allowed(user_id)
    return ok

async def check_upload_rate(user_id: int) -> bool:
    ok, _ = await _upload_limiter.is_allowed(user_id)
    return ok

async def check_scan_rate(user_id: int) -> bool:
    ok, _ = await _scan_limiter.is_allowed(user_id)
    return ok
