"""
OxaPay Integration
- Create invoices for plan purchases
- Check payment status (polling + webhook)
- Auto-activate plan on payment confirmed
"""
import asyncio
import logging
import aiohttp
from datetime import datetime, timedelta
from typing import Optional, Dict

logger = logging.getLogger(__name__)

OXAPAY_BASE = 'https://api.oxapay.com'


async def create_invoice(
    merchant_key: str,
    amount_usd: float,
    description: str,
    order_id: str,
    lifetime_min: int = 30,
) -> Optional[Dict]:
    """
    Create OxaPay invoice. Returns dict with payLink + trackId or None on error.
    """
    payload = {
        'merchant':     merchant_key,
        'amount':       round(amount_usd, 2),
        'currency':     'USD',
        'lifeTime':     lifetime_min,
        'feePaidByPayer': 0,
        'underPaidCover': 2,
        'description':  description,
        'orderId':      order_id,
        'returnUrl':    '',
        'callbackUrl':  '',
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f'{OXAPAY_BASE}/merchants/request',
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                data = await r.json()
                if data.get('result') == 100:
                    return {
                        'track_id': str(data['trackId']),
                        'pay_link': data['payLink'],
                        'amount':   amount_usd,
                    }
                logger.debug(f"OxaPay invoice error: {data}")
    except Exception as exc:
        logger.error(f"OxaPay create_invoice: {exc}")
    return None


async def check_payment(merchant_key: str, track_id: str) -> str:
    """
    Check payment status. Returns: 'paid' | 'pending' | 'expired' | 'failed' | 'error'
    """
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f'{OXAPAY_BASE}/merchants/inquiry',
                json={'merchant': merchant_key, 'trackId': track_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
                if data.get('result') == 100:
                    status = data.get('status', '').lower()
                    if status in ('paid', 'confirmed'):
                        return 'paid'
                    if status in ('expired', 'cancelled'):
                        return 'expired'
                    if status == 'waiting':
                        return 'pending'
    except Exception as exc:
        logger.error(f"OxaPay check_payment: {exc}")
    return 'error'


async def poll_payment(
    merchant_key: str,
    track_id: str,
    bot,
    user_id: int,
    plan_name: str,
    on_paid_callback,
    timeout_min: int = 30,
):
    """
    Background task: polls OxaPay every 30s until paid or expired.
    Calls on_paid_callback(user_id, plan_name) when confirmed.
    """
    elapsed = 0
    max_sec = timeout_min * 60

    while elapsed < max_sec:
        await asyncio.sleep(30)
        elapsed += 30

        status = await check_payment(merchant_key, track_id)

        if status == 'paid':
            await on_paid_callback(user_id, plan_name)
            try:
                await bot.send_message(
                    user_id,
                    f"✅ *Payment Confirmed!*\n\n"
                    f"🎉 *{plan_name}* plan activated!\n"
                    f"Use /start to see your updated membership.",
                    parse_mode='Markdown'
                )
            except Exception:
                pass
            return

        if status == 'expired':
            try:
                await bot.send_message(
                    user_id,
                    "❌ *Payment Expired*\n\nInvoice expired. Use /membership to try again.",
                    parse_mode='Markdown'
                )
            except Exception:
                pass
            return

    # Timeout
    try:
        await bot.send_message(
            user_id,
            "⏰ *Payment window closed*\n\nUse /membership to create a new invoice.",
            parse_mode='Markdown'
        )
    except Exception:
        pass
