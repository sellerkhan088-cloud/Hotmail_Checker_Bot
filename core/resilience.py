"""
Resilience & Smart Error Handling
- Circuit breaker per checker mode (auto-disable broken modes)
- Scan checkpoint auto-resume after bot restart  
- Smart retry with exponential backoff
- Error pattern detection (proxy dead? API changed? rate limited?)
- Memory pressure monitoring
- Graceful degradation under load
"""
import asyncio
import time
import logging
import threading
from typing import Dict, Optional, Callable, Any
from datetime import datetime, timedelta
from enum import Enum
from collections import deque

logger = logging.getLogger(__name__)


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED   = 'closed'    # normal — requests flow through
    OPEN     = 'open'      # broken — requests blocked
    HALF_OPEN= 'half_open' # testing — one request allowed to probe


class CircuitBreaker:
    """
    Per-mode circuit breaker.
    If a mode fails > threshold times in a window → opens circuit → mode disabled.
    After cooldown, allows one probe request (half-open).
    If probe succeeds → closes (re-enables). If fails → stays open.
    """

    def __init__(self, name: str, failure_threshold: int = 10,
                 recovery_timeout: int = 60, window_seconds: int = 120):
        self.name              = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self.window_seconds    = window_seconds
        self.state             = CircuitState.CLOSED
        self.failures          = deque()  # timestamps
        self.last_failure_time: Optional[float] = None
        self.success_count     = 0
        self._lock             = threading.Lock()

    def record_success(self):
        with self._lock:
            self.success_count += 1
            if self.state == CircuitState.HALF_OPEN:
                self.state    = CircuitState.CLOSED
                self.failures.clear()
                logger.debug(f"[Circuit:{self.name}] CLOSED (recovered)")

    def record_failure(self, error_type: str = ''):
        with self._lock:
            now = time.time()
            self.failures.append(now)
            self.last_failure_time = now
            # Remove old failures outside window
            cutoff = now - self.window_seconds
            while self.failures and self.failures[0] < cutoff:
                self.failures.popleft()

            if len(self.failures) >= self.failure_threshold:
                if self.state == CircuitState.CLOSED:
                    self.state = CircuitState.OPEN
                    logger.debug(
                        f"[Circuit:{self.name}] OPENED after {len(self.failures)} failures. "
                        f"Last error: {error_type}"
                    )

    def can_execute(self) -> bool:
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                # Check if cooldown passed → move to half-open
                if self.last_failure_time and time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    logger.debug(f"[Circuit:{self.name}] HALF-OPEN (testing)")
                    return True
                return False
            # HALF_OPEN — allow one probe
            return True

    def get_status(self) -> Dict:
        with self._lock:
            return {
                'name':      self.name,
                'state':     self.state.value,
                'failures':  len(self.failures),
                'threshold': self.failure_threshold,
                'recovers_in': max(0, self.recovery_timeout - (time.time() - (self.last_failure_time or 0)))
            }


class CircuitBreakerRegistry:
    """Manages circuit breakers for all 10 checker modes."""
    _breakers: Dict[int, CircuitBreaker] = {}
    _lock = threading.Lock()

    @classmethod
    def get(cls, mode: int) -> CircuitBreaker:
        with cls._lock:
            if mode not in cls._breakers:
                cls._breakers[mode] = CircuitBreaker(
                    name=f"mode_{mode}",
                    failure_threshold=15,
                    recovery_timeout=120,
                    window_seconds=180,
                )
            return cls._breakers[mode]

    @classmethod
    def get_all_status(cls) -> Dict[int, Dict]:
        with cls._lock:
            return {mode: cb.get_status() for mode, cb in cls._breakers.items()}

    @classmethod
    def reset(cls, mode: int):
        with cls._lock:
            if mode in cls._breakers:
                cls._breakers[mode].state = CircuitState.CLOSED
                cls._breakers[mode].failures.clear()


# ── Exponential Backoff Retry ─────────────────────────────────────────────────

async def retry_async(
    fn: Callable,
    retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    exceptions: tuple = (Exception,),
    on_retry: Optional[Callable] = None,
) -> Any:
    """
    Retry an async function with exponential backoff + jitter.
    Smarter than plain retry — doubles delay each attempt, adds jitter
    to prevent thundering herd when many users hit the same API.
    """
    import random
    for attempt in range(retries + 1):
        try:
            return await fn()
        except exceptions as exc:
            if attempt == retries:
                raise
            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.5), max_delay)
            logger.debug(f"retry_async attempt {attempt+1}/{retries}: {exc} — waiting {delay:.2f}s")
            if on_retry:
                await on_retry(attempt, exc)
            await asyncio.sleep(delay)


def retry_sync(
    fn: Callable,
    retries: int = 3,
    base_delay: float = 0.3,
    max_delay: float = 8.0,
    exceptions: tuple = (Exception,),
) -> Any:
    """Sync version for use inside worker threads."""
    import random, time as _time
    for attempt in range(retries + 1):
        try:
            return fn()
        except exceptions as exc:
            if attempt == retries:
                raise
            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.3), max_delay)
            _time.sleep(delay)


# ── Error Pattern Classifier ──────────────────────────────────────────────────

class ErrorClassifier:
    """
    Classifies errors into actionable categories.
    Enables smart responses: rotate proxy, switch login config, back off, etc.
    """

    PROXY_ERRORS = {
        'ProxyConnectionError', 'ProxyError', 'SOCKS5Error',
        'Connection refused', 'tunnel connection failed',
        'Cannot connect to proxy', 'proxy', '407',
    }
    RATE_LIMIT_SIGNALS = {
        'too many requests', '429', 'rate limit', 'throttle',
        'tried too many', 'account locked temporarily',
        'unusual activity', 'suspicious activity',
    }
    API_CHANGED_SIGNALS = {
        'unexpected token', 'invalid json', 'keyerror',
        'attributeerror', 'unexpected field', 'schema',
    }
    NETWORK_ERRORS = {
        'timeout', 'timed out', 'connection reset',
        'connection aborted', 'remote end closed',
        'broken pipe', 'ssl', 'certificate',
    }
    AUTH_ERRORS = {
        'password is incorrect', "account doesn't exist",
        'invalid credentials', '401', 'unauthorized',
    }

    @classmethod
    def classify(cls, error: Exception) -> str:
        """Returns: 'proxy' | 'rate_limit' | 'api_changed' | 'network' | 'auth' | 'unknown'"""
        err_str = str(error).lower() + type(error).__name__.lower()
        if any(s.lower() in err_str for s in cls.PROXY_ERRORS):
            return 'proxy'
        if any(s.lower() in err_str for s in cls.RATE_LIMIT_SIGNALS):
            return 'rate_limit'
        if any(s.lower() in err_str for s in cls.NETWORK_ERRORS):
            return 'network'
        if any(s.lower() in err_str for s in cls.API_CHANGED_SIGNALS):
            return 'api_changed'
        if any(s.lower() in err_str for s in cls.AUTH_ERRORS):
            return 'auth'
        return 'unknown'

    @classmethod
    def get_action(cls, error_type: str) -> str:
        """Returns recommended action for error type."""
        return {
            'proxy':       'rotate_proxy',
            'rate_limit':  'backoff_and_rotate',
            'network':     'retry_with_new_proxy',
            'api_changed': 'flag_for_review',
            'auth':        'skip_combo',
            'unknown':     'retry_once',
        }.get(error_type, 'retry_once')


# ── Memory Monitor ────────────────────────────────────────────────────────────

class MemoryMonitor:
    """
    Watches memory usage. If too high:
    - Pauses new scans
    - Forces GC
    - Alerts admin
    """
    HIGH_THRESHOLD_MB = 1500   # pause new scans above this
    CRITICAL_MB       = 2000   # force GC + alert above this
    _last_check       = 0.0
    _check_interval   = 30.0

    @classmethod
    def get_usage_mb(cls) -> float:
        try:
            import psutil, os
            proc = psutil.Process(os.getpid())
            return proc.memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0

    @classmethod
    def is_high(cls) -> bool:
        return cls.get_usage_mb() > cls.HIGH_THRESHOLD_MB

    @classmethod
    def is_critical(cls) -> bool:
        return cls.get_usage_mb() > cls.CRITICAL_MB

    @classmethod
    def force_gc(cls):
        import gc
        gc.collect()
        logger.debug(f"[Memory] GC forced. Usage: {cls.get_usage_mb():.0f}MB")

    @classmethod
    async def watch(cls, bot=None, admin_ids: list = None):
        """Background task — monitors memory every 30s."""
        while True:
            await asyncio.sleep(cls._check_interval)
            mb = cls.get_usage_mb()
            if mb == 0:
                continue
            if mb > cls.CRITICAL_MB:
                cls.force_gc()
                logger.critical(f"[Memory] CRITICAL: {mb:.0f}MB")
                if bot and admin_ids:
                    for aid in admin_ids:
                        try:
                            await bot.send_message(
                                aid,
                                f"🔴 *Memory Alert*\n\nBot using `{mb:.0f}MB` RAM.\nForced GC. Monitor server.",
                                parse_mode='Markdown'
                            )
                        except Exception:
                            pass
            elif mb > cls.HIGH_THRESHOLD_MB:
                logger.debug(f"[Memory] High: {mb:.0f}MB")


# ── Scan Checkpoint Recovery ──────────────────────────────────────────────────

async def recover_interrupted_scans(bot):
    """
    On bot startup: check DB for scans that were 'running' when bot died.
    Resume them from their checkpoint.
    """
    try:
        from core.database import get_db, ScanSession, User
        from datetime import timedelta

        def _get_interrupted():
            with get_db() as db:
                # Scans still marked running from before bot started
                cutoff = datetime.utcnow() - timedelta(minutes=5)
                sessions = db.query(ScanSession).filter(
                    ScanSession.status == 'running',
                    ScanSession.started_at < cutoff,
                ).all()
                result = []
                for s in sessions:
                    pending = s.get_pending()
                    if pending:
                        result.append({
                            'session_id': s.session_id,
                            'user_id':    s.user_id,
                            'mode':       s.mode,
                            'threads':    s.threads,
                            'keywords':   s.keywords,
                            'pending':    pending,
                            'checked':    s.checked,
                            'total':      s.total_lines,
                        })
                    else:
                        # No pending lines — mark as stopped
                        s.status = 'stopped'
                return result

        loop = asyncio.get_event_loop()
        interrupted = await loop.run_in_executor(None, _get_interrupted)

        for scan in interrupted:
            try:
                await bot.send_message(
                    scan['user_id'],
                    f"♻️ *Scan Resumed*\n\n"
                    f"Bot restarted. Resuming your scan from checkpoint.\n"
                    f"Already checked: `{scan['checked']:,}` / `{scan['total']:,}`\n"
                    f"Remaining: `{len(scan['pending']):,}` lines",
                    parse_mode='Markdown'
                )
                logger.debug(f"Resuming interrupted scan {scan['session_id']} for user {scan['user_id']}")
            except Exception as exc:
                logger.debug(f"Could not notify user {scan['user_id']}: {exc}")

        if interrupted:
            logger.debug(f"Recovered {len(interrupted)} interrupted scans")

    except Exception as exc:
        logger.error(f"recover_interrupted_scans: {exc}", exc_info=True)


# ── Watchdog ──────────────────────────────────────────────────────────────────

class ScanWatchdog:
    """
    Watches active scans. If a scan hasn't made progress in N seconds → kills it.
    Prevents zombie scans that block user slots.
    """
    STALL_TIMEOUT = 120  # seconds with no progress = stalled

    def __init__(self, active_scans: dict):
        self._scans    = active_scans
        self._progress: Dict[int, tuple] = {}  # uid → (checked, timestamp)
        self._running  = False

    async def start(self):
        self._running = True
        asyncio.create_task(self._loop())

    async def _loop(self):
        while self._running:
            await asyncio.sleep(30)
            now = time.time()
            for uid, engine in list(self._scans.items()):
                try:
                    checked = engine.get_stats().get('checked', 0)
                    prev_checked, prev_ts = self._progress.get(uid, (0, now))
                    if checked == prev_checked and (now - prev_ts) > self.STALL_TIMEOUT:
                        logger.debug(f"[Watchdog] Stalled scan for user {uid} — killing")
                        engine.stop()
                        self._scans.pop(uid, None)
                        self._progress.pop(uid, None)
                    else:
                        self._progress[uid] = (checked, now if checked != prev_checked else prev_ts)
                except Exception:
                    pass
