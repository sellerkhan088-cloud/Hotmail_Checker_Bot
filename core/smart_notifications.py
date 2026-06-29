"""
Smart Notification System
- Deduplication: never send same alert twice within cooldown window
- Priority queuing: critical alerts jump the queue
- Flood prevention: max N messages per user per minute
- Batch similar alerts: group 10 hits into one message instead of 10 messages
- Smart formatting based on alert importance
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class Priority(Enum):
    LOW      = 1
    NORMAL   = 2
    HIGH     = 3
    CRITICAL = 4


@dataclass
class Notification:
    user_id:   int
    text:      str
    priority:  Priority = Priority.NORMAL
    parse_mode:str      = 'Markdown'
    key:       str      = ''   # dedup key — same key = skip if sent recently
    created_at:float    = field(default_factory=time.time)


class SmartNotificationManager:
    """
    Central notification manager.
    All Telegram messages to users go through here.
    """
    MAX_PER_MIN     = 5      # max messages per user per minute
    DEDUP_WINDOW    = 300    # 5 min cooldown for same-key alerts
    HIT_BATCH_SIZE  = 5      # batch N hits into one message
    HIT_BATCH_DELAY = 10.0   # seconds to wait before sending hit batch

    def __init__(self):
        self._queues:   Dict[int, asyncio.Queue]     = {}
        self._sent_keys: Dict[str, float]            = {}  # key → last sent time
        self._rate_buckets: Dict[int, deque]         = defaultdict(lambda: deque())
        self._hit_batches:  Dict[int, List[Dict]]    = defaultdict(list)
        self._batch_timers: Dict[int, Optional[asyncio.Task]] = {}
        self._lock          = asyncio.Lock()
        self._bot           = None
        self._running       = False

    def set_bot(self, bot):
        self._bot = bot

    def _dedup_key(self, user_id: int, key: str) -> str:
        return f"{user_id}:{key}"

    def _is_rate_limited(self, user_id: int) -> bool:
        now    = time.time()
        bucket = self._rate_buckets[user_id]
        # Remove old entries
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        return len(bucket) >= self.MAX_PER_MIN

    def _record_sent(self, user_id: int):
        self._rate_buckets[user_id].append(time.time())

    async def send(self, notif: Notification) -> bool:
        """Queue a notification for sending."""
        if not self._bot:
            return False

        async with self._lock:
            # Dedup check
            if notif.key:
                dk    = self._dedup_key(notif.user_id, notif.key)
                last  = self._sent_keys.get(dk, 0)
                if time.time() - last < self.DEDUP_WINDOW:
                    return False  # already sent recently
                self._sent_keys[dk] = time.time()

            # Rate limit check (skip for critical)
            if notif.priority != Priority.CRITICAL and self._is_rate_limited(notif.user_id):
                return False

        try:
            await self._bot.send_message(
                notif.user_id,
                notif.text,
                parse_mode=notif.parse_mode
            )
            self._record_sent(notif.user_id)
            return True
        except Exception as exc:
            logger.debug(f"Notification to {notif.user_id}: {exc}")
            return False

    async def send_hit_batched(self, user_id: int, hit_result: Dict,
                               plan: str = 'free'):
        """
        Batch real-time hit notifications.
        Instead of spamming one message per hit, accumulate N hits
        then send one message listing all of them.
        Only for VIP plans.
        """
        if plan not in ('weekly', 'monthly', 'yearly'):
            return

        async with self._lock:
            self._hit_batches[user_id].append(hit_result)
            batch = self._hit_batches[user_id]

            # Send immediately if batch is full
            if len(batch) >= self.HIT_BATCH_SIZE:
                await self._flush_hit_batch(user_id)
                return

            # Cancel existing timer and set new one
            existing = self._batch_timers.get(user_id)
            if existing and not existing.done():
                existing.cancel()

        # Set timer to flush after delay
        async def _delayed_flush():
            await asyncio.sleep(self.HIT_BATCH_DELAY)
            async with self._lock:
                await self._flush_hit_batch(user_id)

        task = asyncio.create_task(_delayed_flush())
        self._batch_timers[user_id] = task

    async def _flush_hit_batch(self, user_id: int):
        """Send accumulated hits as one message."""
        batch = self._hit_batches.pop(user_id, [])
        if not batch:
            return

        lines = [f"⚡ *{len(batch)} New Hit{'s' if len(batch) > 1 else ''}*\n"]
        for i, r in enumerate(batch[:10], 1):
            em  = r.get('email', '?')
            pw  = r.get('password', '?')
            gt  = r.get('gamertag', '')
            mc  = r.get('username') or r.get('mc_username', '')
            xgt = r.get('xgp_type', '')
            cap = f"`{em}:{pw}`"
            tags = []
            if xgt == 'XGPU':  tags.append('⭐XGPU')
            elif xgt:           tags.append('🎮XGP')
            if mc:              tags.append(f'⛏️{mc}')
            if gt:              tags.append(f'🎮{gt}')
            tag_str = ' '.join(tags)
            lines.append(f"{i}. {cap} {tag_str}")

        text = '\n'.join(lines)
        await self.send(Notification(
            user_id   = user_id,
            text      = text,
            priority  = Priority.HIGH,
            parse_mode= 'Markdown',
        ))

    async def broadcast_smart(self, bot, user_ids: List[int],
                               text: str, delay: float = 0.05):
        """
        Smart broadcast with flood control.
        Sends in batches, handles Telegram flood waits automatically.
        """
        sent = 0
        failed = 0
        for uid in user_ids:
            try:
                await bot.send_message(uid, text, parse_mode='Markdown')
                sent += 1
            except Exception as exc:
                err = str(exc).lower()
                if 'flood' in err or '429' in err:
                    # Extract retry_after
                    import re
                    m = re.search(r'retry after (\d+)', err)
                    wait = int(m.group(1)) + 1 if m else 5
                    await asyncio.sleep(wait)
                    try:
                        await bot.send_message(uid, text, parse_mode='Markdown')
                        sent += 1
                    except Exception:
                        failed += 1
                elif 'blocked' in err or 'deactivated' in err:
                    failed += 1
                else:
                    failed += 1
            await asyncio.sleep(delay)
        return sent, failed

    async def cleanup_old_dedup_keys(self):
        """Periodic cleanup of expired dedup keys."""
        while True:
            await asyncio.sleep(600)  # every 10 min
            async with self._lock:
                now    = time.time()
                cutoff = now - self.DEDUP_WINDOW
                expired = [k for k, ts in self._sent_keys.items() if ts < cutoff]
                for k in expired:
                    del self._sent_keys[k]


# Global singleton
notif_manager = SmartNotificationManager()
