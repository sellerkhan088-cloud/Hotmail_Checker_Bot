"""
Orbit Checker — Production Config
Supports 100+ concurrent users with proper resource limits per plan
"""
import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
ADMIN_IDS   = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orbit_checker.db")

# ── Webhook (required at scale) ───────────────────────────────────────────────
WEBHOOK_URL    = os.getenv("WEBHOOK_URL", "")      # https://yourdomain.com/webhook
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")   # random secret token
WEBHOOK_PORT   = int(os.getenv("WEBHOOK_PORT", "8443"))
USE_WEBHOOK    = bool(WEBHOOK_URL)

# ── Redis (session state + scan queue + caching) ──────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── Discord webhooks ──────────────────────────────────────────────────────────
DISCORD_WEBHOOK_HITS      = os.getenv("DISCORD_WEBHOOK_HITS", "")
DISCORD_WEBHOOK_XGPU      = os.getenv("DISCORD_WEBHOOK_XGPU", "")
DISCORD_WEBHOOK_MINECRAFT = os.getenv("DISCORD_WEBHOOK_MINECRAFT", "")
DISCORD_WEBHOOK_CARDS     = os.getenv("DISCORD_WEBHOOK_CARDS", "")
DISCORD_WEBHOOK_ADMIN     = os.getenv("DISCORD_WEBHOOK_ADMIN", "")

# ── Results channel ───────────────────────────────────────────────────────────
RESULTS_CHANNEL_ID = os.getenv("RESULTS_CHANNEL_ID", "")

# ── Global scan engine limits ─────────────────────────────────────────────────
MAX_CONCURRENT_SCANS    = int(os.getenv("MAX_CONCURRENT_SCANS", "50"))
MAX_TOTAL_THREADS       = int(os.getenv("MAX_TOTAL_THREADS", "2000"))
WORKER_POOL_SIZE        = int(os.getenv("WORKER_POOL_SIZE", "500"))
PROXY_HEALTH_INTERVAL   = int(os.getenv("PROXY_HEALTH_INTERVAL", "300"))  # 5 min

# ── File limits ───────────────────────────────────────────────────────────────
MAX_FILE_SIZE_MB    = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT_MESSAGES  = int(os.getenv("RATE_LIMIT_MESSAGES", "20"))   # per minute
RATE_LIMIT_UPLOADS   = int(os.getenv("RATE_LIMIT_UPLOADS", "5"))     # per minute
RATE_LIMIT_SCANS     = int(os.getenv("RATE_LIMIT_SCANS", "2"))       # per minute

# ── Plan limits ───────────────────────────────────────────────────────────────
PLAN_LIMITS = {
    "free": {
        "daily":         5_000,
        "threads":       100,
        "files":         1,
        "keywords":      3,
        "queue":         True,
        "resume":        False,    # /resume is VIP only
        "session_max":   2_000,    # 2k per session for free
        "max_queue_pos": 5,
        "proxy_weight":  0.5,
        "results_channel":  False,
        "discord_webhook":  False,
        "real_time_hits":   False,
        "domain_filter":    True,  # skip non-MS domains in MS modes
    },
    "weekly": {
        "daily":         15_000,
        "threads":       200,
        "files":         5,
        "keywords":      10,
        "queue":         False,
        "resume":        True,
        "session_max":   10_000,   # 10k per session for all VIP
        "max_queue_pos": 2,
        "proxy_weight":  1.0,
        "results_channel":  True,
        "discord_webhook":  False,
        "real_time_hits":   True,
        "domain_filter":    True,
    },
    "monthly": {
        "daily":         999_999,
        "threads":       300,
        "files":         5,
        "keywords":      999,
        "queue":         False,
        "resume":        True,
        "session_max":   10_000,
        "max_queue_pos": 0,
        "proxy_weight":  1.5,
        "results_channel":  True,
        "discord_webhook":  True,
        "real_time_hits":   True,
        "domain_filter":    True,
    },
    "yearly": {
        "daily":         999_999,
        "threads":       500,
        "files":         10,
        "keywords":      999,
        "queue":         False,
        "resume":        True,
        "session_max":   10_000,
        "max_queue_pos": 0,
        "proxy_weight":  2.0,
        "results_channel":  True,
        "discord_webhook":  True,
        "real_time_hits":   True,
        "domain_filter":    True,
    },
}

REFERRAL_BONUS     = 1_000
SESSION_MAX_LINES  = 3_000
MAX_QUEUE_FREE     = 3
QUEUE_WAIT_SECONDS = 30

API_MODES = {
    1:  {"name": "🔥 All-in-One",      "desc": "Everything — Xbox + MC + Payment + PSN + 350 Services + Inbox + Keywords + Country — VIP ONLY", "vip": True},
    2:  {"name": "🎮 Supercell",        "desc": "CoC, CR, Brawl Stars, Hay Day + API stats",    "vip": False},
    3:  {"name": "🎮 Roblox",           "desc": "Account, Robux, groups, badges, friends",      "vip": False},
    4:  {"name": "🎮 Xbox",             "desc": "Game Pass, XGPU, gamerscore",                  "vip": False},
    5:  {"name": "🎮 PSN",               "desc": "PlayStation Network account, orders, ID",      "vip": False},
    6:  {"name": "📊 Full Scan",        "desc": "Country + Name + Keywords + Rewards + Payment (Recommended)", "vip": False},
    7:  {"name": "🚀 Speed Mode",       "desc": "Login validation only — max CPM",              "vip": False},
    8:  {"name": "⛏️ Minecraft",        "desc": "Ownership, capes, namechange, Hypixel",        "vip": False},
    9:  {"name": "💳 MS Payment",       "desc": "Payment Cards + Balance + Subscriptions",      "vip": False},
    10: {"name": "🍥 Crunchyroll",       "desc": "Account, plan tier, trial, payment, watch history", "vip": False},
}

API_MODE_DESCRIPTIONS = {k: v["desc"] for k, v in API_MODES.items()}

GLOBAL_PROXY_FILE = os.getenv("GLOBAL_PROXY_FILE", "global_proxies.txt")

# ── OxaPay ────────────────────────────────────────────────────────────────────
import os as _os2
OXAPAY_API_KEY   = _os2.getenv('OXAPAY_API_KEY', '')
OXAPAY_MERCHANT  = _os2.getenv('OXAPAY_MERCHANT', '')  # merchant key for invoice
OXAPAY_IPN_URL   = _os2.getenv('OXAPAY_IPN_URL', '')   # your webhook URL for auto-confirm
