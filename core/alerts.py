"""
Smart Alerts System
- Rate limit warnings (proxy + API)
- Subscription expiry alerts (7d, 3d, 1d before)
- Auto result sending with smart formatting
- Per-hit real-time Telegram alerts for VIP
- Proxy pool warnings to users
- Plan upgrade nudges based on usage patterns
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


# ── Alert message templates ───────────────────────────────────────────────────

def proxy_ratelimited_msg(available: int, total: int, user_has_proxies: bool) -> str:
    if user_has_proxies:
        return (
            f"⚠️ *Proxy Pool Warning*\n\n"
            f"Global proxies are heavily rate-limited right now.\n"
            f"🌐 Available: {available}/{total}\n\n"
            f"Your personal proxies are active and being used as fallback. ✅\n"
            f"Upload more with ⚙️ Settings → 🌐 Proxy Settings"
        )
    return (
        f"⚠️ *Proxy Pool Rate Limited*\n\n"
        f"Global proxy pool is under heavy load.\n"
        f"🌐 Available: {available}/{total}\n\n"
        f"📉 This will slow your CPM significantly.\n\n"
        f"💡 *Add your own proxies for best speed:*\n"
        f"⚙️ Settings → 🌐 Proxy Settings → Upload Proxies\n\n"
        f"Supports: `http://ip:port` `socks5://user:pass@ip:port`"
    )


def no_proxies_msg() -> str:
    return (
        "🔴 *No Proxies Available*\n\n"
        "The global proxy pool is empty or all proxies are dead.\n\n"
        "Your scan will run without proxies — *very slow and may get IP banned.*\n\n"
        "💡 *Add your own proxies:*\n"
        "⚙️ Settings → 🌐 Proxy Settings → 📤 Upload Proxies\n\n"
        "Supported formats:\n"
        "`http://ip:port`\n"
        "`socks5://user:pass@ip:port`\n"
        "`ip:port:user:pass`\n"
        "`ip:port` _(no auth)_"
    )


def subscription_expiry_msg(plan: str, days_left: int) -> str:
    urgency = "🔴" if days_left <= 1 else ("🟡" if days_left <= 3 else "🟠")
    return (
        f"{urgency} *Subscription Expiring Soon*\n\n"
        f"📅 Plan: *{plan.upper()}*\n"
        f"⏰ Expires in: *{days_left} day{'s' if days_left != 1 else ''}*\n\n"
        f"Renew now to keep:\n"
        f"{'• Unlimited daily scans' if plan in ('monthly','yearly') else '• 15,000 lines/day'}\n"
        f"• Priority queue bypass\n"
        f"• Max threads\n"
        f"• Real-time hit alerts\n\n"
        f"📞 Contact @KansOrbit to renew"
    )


def plan_expired_msg(old_plan: str) -> str:
    return (
        f"❌ *Subscription Expired*\n\n"
        f"Your *{old_plan.upper()}* plan has expired.\n"
        f"You've been downgraded to Free.\n\n"
        f"*Free plan limits:*\n"
        f"• 5,000 lines/day\n"
        f"• 50 threads max\n"
        f"• Queue required\n\n"
        f"💎 *Upgrade to continue without limits*\n"
        f"📞 Contact @KansOrbit"
    )


def daily_limit_warning_msg(used: int, total: int, plan: str) -> str:
    pct = (used / max(total, 1)) * 100
    remaining = max(0, total - used)
    if plan == 'free':
        upgrade_hint = "\n\n💎 *Upgrade for unlimited daily scans*\n/start → 👑 Membership"
    else:
        upgrade_hint = "\n\n⏰ Resets at midnight UTC"
    return (
        f"⚠️ *Daily Limit Warning*\n\n"
        f"📊 Used: {used:,}/{total:,} ({pct:.0f}%)\n"
        f"📉 Remaining: {remaining:,} lines"
        f"{upgrade_hint}"
    )


def scan_complete_summary(stats: Dict, filename: str, elapsed_sec: float) -> str:
    hits      = stats.get('hits', 0)
    checked   = stats.get('checked', 0)
    bad       = stats.get('bad', 0)
    twofa     = stats.get('twofa', 0)
    errors    = stats.get('errors', 0)
    cpm       = stats.get('cpm', 0)
    xgpu      = stats.get('xgpu', 0)
    xgp       = stats.get('xgp', 0)
    mc        = stats.get('minecraft', 0)
    capes     = stats.get('capes', 0)
    payment   = stats.get('payment', 0)
    valid_mail= stats.get('valid_mail', 0)
    rate      = (hits / max(checked, 1)) * 100
    mins      = int(elapsed_sec // 60)
    secs      = int(elapsed_sec % 60)

    lines = [
        f"✅ *Scan Complete*",
        f"",
        f"📄 `{filename}`",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📊 Checked: `{checked:,}`",
        f"💎 Hits: `{hits:,}` ({rate:.1f}%)",
        f"🔒 2FA: `{twofa:,}`",
        f"❌ Bad: `{bad:,}`",
        f"⚠️ Errors: `{errors:,}`",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]
    if xgpu:    lines.append(f"⭐ XGPU: `{xgpu:,}`")
    if xgp:     lines.append(f"🎮 XGP: `{xgp:,}`")
    if mc:      lines.append(f"⛏️ Minecraft: `{mc:,}`")
    if capes:   lines.append(f"🧣 Capes: `{capes:,}`")
    if payment: lines.append(f"💳 Cards: `{payment:,}`")
    if valid_mail: lines.append(f"📧 Valid Mail: `{valid_mail:,}`")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏱️ Duration: `{mins}m {secs}s`")
    lines.append(f"🚀 Avg CPM: `{cpm:,}`")
    return "\n".join(lines)


def real_time_hit_msg(result: Dict) -> str:
    """Short real-time hit notification for VIP users."""
    email     = result.get('email', '?')
    password  = result.get('password', '?')
    gamertag  = result.get('gamertag', '')
    has_xgpu  = result.get('xgp_type') == 'XGPU'
    has_xgp   = result.get('has_gamepass', False)
    has_mc    = result.get('has_mc') or result.get('has_minecraft', False)
    mc_user   = result.get('username') or result.get('mc_username', '')
    capes     = result.get('capes') or result.get('mc_capes') or []
    balance   = result.get('balance', '')
    rewards   = result.get('rewards_points', '')
    cards     = result.get('payment_methods', [])
    capture   = result.get('_capture_line', f'{email}:{password}')

    if has_xgpu:    icon = '⭐'
    elif has_xgp:   icon = '🎮'
    elif has_mc:    icon = '⛏️'
    elif cards:     icon = '💳'
    else:           icon = '✅'

    lines = [f"{icon} *New Hit*", f""]
    lines.append(f"`{email}:{password}`")
    if gamertag:    lines.append(f"🎮 GT: `{gamertag}`")
    if has_xgpu:    lines.append(f"⭐ XGPU")
    elif has_xgp:   lines.append(f"🎮 XGP")
    if mc_user:     lines.append(f"⛏️ MC: `{mc_user}`")
    if capes:       lines.append(f"🧣 {', '.join(capes[:3])}")
    if balance:     lines.append(f"💰 Balance: `{balance}`")
    if rewards:     lines.append(f"🏆 Rewards: `{rewards}`")
    if cards:
        c = cards[0]
        disp = c['display'] if isinstance(c, dict) else str(c)
        lines.append(f"💳 {disp}")
    return "\n".join(lines)


def upgrade_nudge_msg(plan: str, usage_pct: float, feature: str = '') -> str:
    if plan != 'free':
        return ''
    msg = (
        f"💡 *Upgrade Tip*\n\n"
        f"You've used {usage_pct:.0f}% of your daily limit.\n\n"
        f"*VIP members get:*\n"
        f"• Unlimited daily scans\n"
        f"• 200 threads (4× faster)\n"
        f"• Real-time hit alerts\n"
        f"• Skip queue\n"
        f"• Discord webhook support\n"
    )
    if feature:
        msg += f"• {feature}\n"
    msg += f"\n📞 Contact @KansOrbit to upgrade"
    return msg


def queue_position_msg(position: int, estimated_wait: int, plan: str) -> str:
    upgrade = "" if plan != 'free' else "\n\n💎 *VIP users skip the queue instantly!*\n/start → 👑 Membership"
    return (
        f"⏳ *Queue Position: #{position + 1}*\n\n"
        f"⏱️ Estimated wait: ~{estimated_wait}s\n"
        f"🔄 Your scan will start automatically\n"
        f"💡 You'll be notified when it begins"
        f"{upgrade}"
    )


def proxy_added_success_msg(count: int, total: int) -> str:
    return (
        f"✅ *Proxies Added*\n\n"
        f"📥 New: `{count:,}`\n"
        f"🌐 Total in pool: `{total:,}`\n\n"
        f"Your proxies are now active and will be used\n"
        f"when global proxies are rate-limited. ✅"
    )


def scan_progress_msg(stats: dict, filename: str, mode_name: str, elapsed_str: str = '') -> str:
    s   = stats
    pct = (s['checked'] / max(s['total'], 1)) * 100

    extras = []
    if s.get('xgpu'):      extras.append(f"⭐ XGPU: {s['xgpu']}")
    if s.get('xgp'):       extras.append(f"🎮 XGP: {s['xgp']}")
    if s.get('minecraft'): extras.append(f"⛏️ MC: {s['minecraft']}")
    if s.get('capes'):     extras.append(f"🧣 Capes: {s['capes']}")
    if s.get('payment'):   extras.append(f"💳 Cards: {s['payment']}")
    if s.get('supercell'): extras.append(f"🎮 SC: {s['supercell']}")
    if s.get('roblox'):    extras.append(f"🎮 Roblox: {s['roblox']}")
    if s.get('tiktok'):    extras.append(f"📱 TikTok: {s['tiktok']}")
    if s.get('crunchyroll_premium'): extras.append(f"🍥 CR: {s['crunchyroll_premium']}")
    extras_str = "\n" + "\n".join(extras) if extras else ""

    dur_line = f"⏱️ Duration: {elapsed_str}\n" if elapsed_str else ""

    return (
        f"📊 *{s['checked']:,} / {s['total']:,}* ({pct:.1f}%)\n\n"
        f"✅ HIT: {s['hits']}\n"
        f"🔒 2FA: {s['twofa']}\n"
        f"❌ BAD: {s['bad']}\n"
        f"⚠️ ERROR: {s['errors']}"
        f"{extras_str}\n\n"
        f"{dur_line}"
        f"⚡ CPM: {s['cpm']:,}\n\n"
        f"_/pause · /stop_"
    )


