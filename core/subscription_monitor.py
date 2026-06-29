"""
Subscription Monitor
- Background task checking expiry every hour
- Sends alerts at 7d, 3d, 1d before expiry
- Sends expiry notification on day of
- Tracks which alerts have been sent (no duplicates)
- Auto-downgrades expired plans
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Set

logger = logging.getLogger(__name__)

# Track which alerts have been sent to avoid duplicates
_alerted_7d: Set[int]  = set()
_alerted_3d: Set[int]  = set()
_alerted_1d: Set[int]  = set()
_alerted_exp: Set[int] = set()


async def run_subscription_monitor(bot):
    """Background task — checks every hour for expiring/expired subscriptions."""
    while True:
        try:
            await _check_subscriptions(bot)
        except Exception as exc:
            logger.error(f"[SubMonitor] error: {exc}", exc_info=True)
        await asyncio.sleep(3600)  # check every hour


async def _check_subscriptions(bot):
    from core.database import get_db, User
    from core.alerts import subscription_expiry_msg, plan_expired_msg

    def _get_users():
        with get_db() as db:
            users = db.query(User).filter(
                User.plan != 'free',
                User.plan_expires != None,
                User.is_banned == False,
            ).all()
            return [
                {
                    'id': u.id,
                    'telegram_id': u.telegram_id,
                    'plan': u.plan,
                    'expires': u.plan_expires,
                }
                for u in users
            ]

    import asyncio
    users = await asyncio.get_event_loop().run_in_executor(None, _get_users)
    now   = datetime.utcnow()

    for u in users:
        tid     = u['telegram_id']
        plan    = u['plan']
        expires = u['expires']
        if not expires:
            continue

        days_left = (expires - now).total_seconds() / 86400

        # Already expired
        if days_left <= 0:
            if tid not in _alerted_exp:
                _alerted_exp.add(tid)
                await _send_alert(bot, tid, plan_expired_msg(plan))
                await _downgrade_user(u['id'])
            continue

        # 1 day warning
        if days_left <= 1 and tid not in _alerted_1d:
            _alerted_1d.add(tid)
            await _send_alert(bot, tid, subscription_expiry_msg(plan, 1))

        # 3 day warning
        elif days_left <= 3 and tid not in _alerted_3d:
            _alerted_3d.add(tid)
            await _send_alert(bot, tid, subscription_expiry_msg(plan, 3))

        # 7 day warning
        elif days_left <= 7 and tid not in _alerted_7d:
            _alerted_7d.add(tid)
            await _send_alert(bot, tid, subscription_expiry_msg(plan, 7))

        # Reset alert flags if plan was renewed (days_left > 7 again)
        if days_left > 7:
            _alerted_7d.discard(tid)
            _alerted_3d.discard(tid)
            _alerted_1d.discard(tid)
            _alerted_exp.discard(tid)


async def _send_alert(bot, telegram_id: int, text: str):
    try:
        await bot.send_message(
            chat_id    = telegram_id,
            text       = text,
            parse_mode = 'Markdown',
        )
        logger.debug(f"[SubMonitor] Alert sent to {telegram_id}")
    except Exception as exc:
        logger.debug(f"[SubMonitor] Failed to send to {telegram_id}: {exc}")


async def _downgrade_user(user_db_id: int):
    from core.database import get_db, User
    def _do():
        with get_db() as db:
            u = db.query(User).filter(User.id == user_db_id).first()
            if u:
                u.plan            = 'free'
                u.plan_expires    = None
                u.current_threads = 50
    import asyncio
    await asyncio.get_event_loop().run_in_executor(None, _do)
