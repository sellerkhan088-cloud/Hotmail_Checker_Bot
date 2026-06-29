"""
Daily reset cron — resets daily line limits at midnight UTC automatically.
Runs as a background asyncio task, no user interaction needed.
"""
import asyncio
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


async def run_daily_reset():
    """
    Runs indefinitely. At each UTC midnight, resets all users' daily_lines_used.
    Also expires plans that have passed their expiry date.
    """
    while True:
        now          = datetime.utcnow()
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait_seconds = (next_midnight - now).total_seconds()
        logger.debug(f"[DailyReset] Next reset in {wait_seconds/3600:.1f}h at {next_midnight} UTC")
        await asyncio.sleep(wait_seconds)

        try:
            await _do_reset()
        except Exception as exc:
            logger.error(f"[DailyReset] error: {exc}", exc_info=True)


async def _do_reset():
    from core.database import get_db
    from sqlalchemy import update

    loop = asyncio.get_event_loop()

    def _reset_sync():
        with get_db() as db:
            from core.database import User
            now = datetime.utcnow()

            # Reset daily lines for all users
            db.query(User).update({
                User.daily_lines_used: 0,
                User.last_reset: now,
            })

            # Expire plans past their expiry date
            expired = db.query(User).filter(
                User.plan != 'free',
                User.plan_expires != None,
                User.plan_expires < now
            ).all()

            count_reset   = db.query(User).count()
            count_expired = len(expired)

            for user in expired:
                user.plan         = 'free'
                user.plan_expires = None
                user.current_threads = 50

            db.commit()
            return count_reset, count_expired

    count_reset, count_expired = await loop.run_in_executor(None, _reset_sync)
    logger.debug(f"[DailyReset] Reset {count_reset} users. Expired {count_expired} plans.")
