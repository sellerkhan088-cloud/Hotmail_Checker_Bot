"""
Production Scan Queue
- Async job queue with priority tiers (VIP > free)
- Max concurrent scan enforcement
- Scan state persistence (resume after restart)
- Per-user scan isolation
- Real-time progress broadcasting
"""
import asyncio
import logging
import time
import json
import os
from typing import Dict, Optional, Callable, Any
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ScanJob:
    session_id:  str
    user_id:     int
    plan:        str
    mode:        int
    threads:     int
    lines:       list
    keywords:    list
    priority:    int = 0       # higher = runs first (VIP gets higher)
    created_at:  float = field(default_factory=time.time)
    message_id:  int = 0       # telegram message to update
    chat_id:     int = 0
    filename:    str = ''

    def __lt__(self, other):
        # Higher priority first, then FIFO
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.created_at < other.created_at


class ScanQueueManager:
    """
    Central scan scheduler for all users.

    Architecture:
    - One asyncio PriorityQueue
    - N worker coroutines pulling from queue (N = MAX_CONCURRENT_SCANS)
    - Per-user active scan tracking
    - Graceful stop/pause per session
    """

    def __init__(self, max_concurrent: int = 50):
        self.max_concurrent  = max_concurrent
        self._queue          = asyncio.PriorityQueue()
        self._active:        Dict[str, Any]    = {}   # session_id → engine
        self._user_active:   Dict[int, str]    = {}   # user_id → session_id
        self._queue_pos:     Dict[int, int]    = {}   # user_id → queue position
        self._lock           = asyncio.Lock()
        self._workers        = []
        self._running        = False
        self._progress_cb:   Optional[Callable] = None
        self._hit_cb:        Optional[Callable] = None
        self._complete_cb:   Optional[Callable] = None

    def set_callbacks(self, progress=None, hit=None, complete=None):
        self._progress_cb = progress
        self._hit_cb      = hit
        self._complete_cb = complete

    async def start(self):
        """Start worker pool."""
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(self.max_concurrent)
        ]
        logger.debug(f"ScanQueue started with {self.max_concurrent} workers")

    async def stop(self):
        self._running = False
        for w in self._workers:
            w.cancel()
        logger.debug("ScanQueue stopped")

    async def enqueue(self, job: ScanJob) -> Dict:
        """
        Add a scan job to the queue.
        Returns {'ok': True, 'position': N} or {'ok': False, 'reason': '...'}
        """
        async with self._lock:
            # Check if user already has an active scan
            if job.user_id in self._user_active:
                return {'ok': False, 'reason': 'You already have an active scan running.'}

            # Free plan queue limit
            if job.plan == 'free':
                from core.config import MAX_QUEUE_FREE
                queue_size = self._queue.qsize()
                if queue_size >= MAX_QUEUE_FREE:
                    return {'ok': False, 'reason': f'Queue full ({queue_size} scans waiting). Try again in a few minutes.'}

            # Priority: yearly=4, monthly=3, weekly=2, free=1
            plan_priority = {'yearly': 4, 'monthly': 3, 'weekly': 2, 'free': 1}
            job.priority = plan_priority.get(job.plan, 1)

            position = self._queue.qsize()
            await self._queue.put(job)
            self._queue_pos[job.user_id] = position
            logger.debug(f"Enqueued scan {job.session_id} for user {job.user_id} (plan={job.plan}, pos={position})")
            return {'ok': True, 'position': position}

    async def stop_scan(self, session_id: str) -> bool:
        """Stop an active scan."""
        engine = self._active.get(session_id)
        if engine:
            engine.stop()
            return True
        return False

    async def pause_scan(self, session_id: str) -> bool:
        engine = self._active.get(session_id)
        if engine:
            engine.pause()
            return True
        return False

    async def resume_scan(self, session_id: str) -> bool:
        engine = self._active.get(session_id)
        if engine:
            engine.resume()
            return True
        return False

    def get_active_count(self) -> int:
        return len(self._active)

    def get_queue_size(self) -> int:
        return self._queue.qsize()

    def get_user_session(self, user_id: int) -> Optional[str]:
        return self._user_active.get(user_id)

    def is_user_scanning(self, user_id: int) -> bool:
        return user_id in self._user_active

    def get_engine(self, session_id: str):
        return self._active.get(session_id)

    async def _worker(self, worker_id: int):
        """Single worker coroutine — pulls jobs and runs them."""
        while self._running:
            try:
                job: ScanJob = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

            async with self._lock:
                self._user_active[job.user_id] = job.session_id
                self._queue_pos.pop(job.user_id, None)

            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"Worker {worker_id} job error: {exc}", exc_info=True)
            finally:
                async with self._lock:
                    self._active.pop(job.session_id, None)
                    self._user_active.pop(job.user_id, None)
                self._queue.task_done()

    async def _run_job(self, job: ScanJob):
        """Run a single scan job with full progress tracking."""
        from checker_engine import CheckerEngine
        from core.proxy_manager import global_proxy_manager

        logger.debug(f"Starting scan {job.session_id} (user={job.user_id}, mode={job.mode}, lines={len(job.lines)})")

        engine = CheckerEngine(
            session_id    = job.session_id,
            mode          = job.mode,
            threads       = job.threads,
            lines         = job.lines,
            proxy_rotator = global_proxy_manager,
            keywords      = job.keywords,
        )

        async with self._lock:
            self._active[job.session_id] = engine

        # Run engine in thread pool to avoid blocking event loop
        loop = asyncio.get_event_loop()

        async def _progress_loop():
            """Update progress every 3s while scan runs."""
            while not engine.is_finished() and not engine.stopped:
                if self._progress_cb:
                    try:
                        await self._progress_cb(job, engine.get_stats())
                    except Exception:
                        pass
                await asyncio.sleep(3)

        progress_task = asyncio.create_task(_progress_loop())

        try:
            # Run blocking engine in thread executor
            await loop.run_in_executor(None, engine.start)
        finally:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        # Final progress update
        if self._progress_cb:
            try:
                await self._progress_cb(job, engine.get_stats())
            except Exception:
                pass

        # Completion callback
        if self._complete_cb:
            try:
                await self._complete_cb(job, engine)
            except Exception as exc:
                logger.error(f"complete_cb error: {exc}")

        logger.debug(f"Scan {job.session_id} complete: hits={engine.stats['hits']} checked={engine.stats['checked']}")


# Global singleton
scan_queue = ScanQueueManager(max_concurrent=50)
