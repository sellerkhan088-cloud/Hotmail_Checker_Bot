"""
ORBIT Startup Verification
Run: python check_startup.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def check(name, fn):
    try:
        start = time.time()
        ok, msg = fn()
        ms = int((time.time()-start)*1000)
        status = "✅" if ok else "❌"
        print(f"  {status} {name:<30} {msg} ({ms}ms)")
        return ok
    except Exception as e:
        print(f"  ❌ {name:<30} ERROR: {e}")
        return False

print("\n" + "═"*55)
print("  ORBIT HOTMAIL CHECKER — STARTUP CHECK")
print("═"*55 + "\n")
all_ok = True

# 1. Python version
def chk_python():
    v = sys.version_info
    ok = v.major == 3 and v.minor >= 8
    return ok, f"Python {v.major}.{v.minor}.{v.micro}"
all_ok &= check("Python version", chk_python)

# 2. Required packages
def chk_packages():
    missing = []
    for pkg in ['requests','telegram','sqlalchemy','aiofiles']:
        try: __import__(pkg)
        except: missing.append(pkg)
    return not missing, f"{'all installed' if not missing else 'MISSING: '+str(missing)}"
all_ok &= check("Required packages", chk_packages)

# 3. Config
def chk_config():
    try:
        os.environ.setdefault('BOT_TOKEN', 'x')
        os.environ.setdefault('ADMIN_IDS', '0')
        os.environ.setdefault('DATABASE_URL', 'sqlite:///orbit.db')
        from core.config import BOT_TOKEN, ADMIN_IDS
        ok = len(BOT_TOKEN) > 10 and BOT_TOKEN != 'x'
        return ok, f"Token: {'✅' if ok else '❌ NOT SET'}"
    except Exception as e: return False, str(e)
all_ok &= check("Bot config (.env)", chk_config)

# 4. Database
def chk_db():
    from core.database import init_db
    init_db(os.getenv('DATABASE_URL','sqlite:///orbit.db'))
    return True, "connected"
all_ok &= check("Database", chk_db)

# 5. Scanner engine
def chk_scanner():
    from checkers.scanner_engine import HotmailChecker
    hc = HotmailChecker()
    return True, "HotmailChecker loaded"
all_ok &= check("scanner_engine", chk_scanner)

# 6. Checker engines
engines = {
    'mc_engine':          ('checkers.mc_engine', 'MCEngine'),
    'xbox_engine':        ('checkers.xbox_engine', 'MicrosoftAuthenticator'),
    'supercell_engine':   ('checkers.supercell_engine', 'SupercellScanner'),
    'crunchyroll_engine': ('checkers.crunchyroll_engine', 'CrunchyrollAccountChecker'),
    'rewards_engine':     ('checkers.rewards_engine', 'RewardsChecker'),
    'PSN_Checker':        ('checkers.PSN_Checker', 'PSNSearcher'),
}
for name, (mod, cls) in engines.items():
    def chk_eng(m=mod, c=cls):
        _mod = __import__(m, fromlist=[c])
        getattr(_mod, c)
        return True, f"{c} loaded"
    all_ok &= check(f"  {name}", chk_eng)

# 7. Proxy
def chk_proxy():
    from core.proxy_manager import GlobalProxyManager
    pm = GlobalProxyManager()
    cnt = pm.count() if hasattr(pm, 'count') else 0
    if cnt == 0:
        return False, "⚠️  No proxy loaded (add via /addproxy)"
    return True, f"{cnt} proxy/proxies loaded"
check("Proxy", chk_proxy)  # Warning only, not failure

# 8. Quick login test
def chk_login():
    from checkers.scanner_engine import HotmailChecker
    import requests
    s = requests.Session()
    r = s.get("https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=1&emailAddress=test@hotmail.com",
              headers={"X-OneAuth-AppName": "Outlook Lite"}, timeout=8)
    ok = r.status_code == 200
    return ok, f"MS API reachable (HTTP {r.status_code})"
all_ok &= check("MS API connectivity", chk_login)

print()
print("═"*55)
if all_ok:
    print("  ✅  ALL CHECKS PASSED — Ready to run")
    print("  →  python bot.py")
else:
    print("  ❌  SOME CHECKS FAILED — Fix above before running")
print("═"*55 + "\n")
