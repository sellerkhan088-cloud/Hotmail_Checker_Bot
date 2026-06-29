"""
Minecraft Checker — ownership, profile, capes, namechange, Hypixel, DonutSMP.
Fixed: no redundant XBL re-auth, correct entitlements endpoint,
       proper cape alias extraction, Hypixel public API (no key needed).
"""
import logging
from typing import Dict, Optional, List
from .xbox_checker import XboxChecker

logger = logging.getLogger(__name__)

MC_BASE  = 'https://api.minecraftservices.com'
MC_SESS  = 'https://sessionserver.mojang.com'


class MinecraftChecker(XboxChecker):
    def __init__(self, session=None, timeout: int = 20, proxy: str = None):
        super().__init__(session, timeout, proxy)
        self.mc_token:  Optional[str]  = None
        self.mc_profile:Optional[Dict] = None

    def _kw(self) -> dict:
        k = {'timeout': self.timeout}
        if self.proxies:
            k['proxies'] = self.proxies
        return k

    def get_minecraft_token(self) -> Optional[str]:
        """Exchange Xbox token for Minecraft access token."""
        # authenticate_xbox_live won't re-auth if already done
        if not self.authenticate_xbox_live():
            return None
        xsts = self._get_xsts('rp://api.minecraftservices.com/')
        if not xsts:
            return None
        try:
            r = self.session.post(
                f'{MC_BASE}/authentication/login_with_xbox',
                json={'identityToken': f'XBL3.0 x={self.uhs};{xsts}'},
                headers={'Content-Type': 'application/json'},
                **self._kw()
            )
            if r.status_code == 200:
                self.mc_token = r.json().get('access_token')
                return self.mc_token
        except Exception as exc:
            logger.debug(f"mc_token: {exc}")
        return None

    def check_ownership(self) -> bool:
        """Check Minecraft ownership via entitlements."""
        if not self.mc_token:
            return False
        try:
            # Primary endpoint
            r = self.session.get(
                f'{MC_BASE}/entitlements/mcstore',
                headers={'Authorization': f'Bearer {self.mc_token}'},
                **self._kw()
            )
            if r.status_code == 200 and r.json().get('items'):
                return True
            # Fallback: license endpoint
            r2 = self.session.get(
                f'{MC_BASE}/entitlements/license?requestId=check',
                headers={'Authorization': f'Bearer {self.mc_token}'},
                **self._kw()
            )
            if r2.status_code == 200:
                items = r2.json().get('items', [])
                return any('game_minecraft' in i.get('name', '') for i in items)
        except Exception as exc:
            logger.debug(f"ownership: {exc}")
        return False

    def get_profile(self) -> Optional[Dict]:
        """Get Minecraft profile — username, UUID, capes, skins."""
        if not self.mc_token:
            return None
        try:
            r = self.session.get(
                f'{MC_BASE}/minecraft/profile',
                headers={'Authorization': f'Bearer {self.mc_token}'},
                **self._kw()
            )
            if r.status_code == 200:
                self.mc_profile = r.json()
                return self.mc_profile
            if r.status_code == 404:
                # Logged in but no MC profile (no ownership)
                return None
        except Exception as exc:
            logger.debug(f"mc_profile: {exc}")
        return None

    def get_capes(self) -> List[str]:
        """Return list of cape names including Optifine check."""
        if not self.mc_profile:
            return []

        capes = []
        for cape in self.mc_profile.get('capes', []):
            # alias is the clean name (e.g. 'Migrator', 'Anniversary')
            alias = cape.get('alias') or cape.get('id', 'Unknown')
            if alias and alias not in capes:
                capes.append(alias)

        # Optifine cape check — public HTTP HEAD request
        username = self.mc_profile.get('name')
        if username:
            try:
                kw = {'timeout': 5}
                if self.proxies:
                    kw['proxies'] = self.proxies
                r = self.session.head(
                    f'http://s.optifine.net/capes/{username}.png',
                    **kw
                )
                if r.status_code == 200 and 'Optifine' not in capes:
                    capes.append('Optifine')
            except Exception:
                pass

        return capes

    def check_name_change_available(self) -> bool:
        """Check if account is eligible for free name change."""
        if not self.mc_token:
            return False
        try:
            r = self.session.get(
                f'{MC_BASE}/minecraft/profile/namechange',
                headers={'Authorization': f'Bearer {self.mc_token}'},
                **self._kw()
            )
            if r.status_code == 200:
                data = r.json()
                return data.get('nameChangeAllowed', False)
        except Exception as exc:
            logger.debug(f"namechange: {exc}")
        return False

    def check_ban_status(self) -> str:
        """Check if account is banned from multiplayer. Returns 'clean'|'banned'|'unknown'."""
        if not self.mc_token:
            return 'unknown'
        try:
            r = self.session.get(
                f'{MC_BASE}/privacy/blocklist',
                headers={'Authorization': f'Bearer {self.mc_token}'},
                **self._kw()
            )
            if r.status_code == 200:
                if r.json().get('blockedProfiles'):
                    return 'banned'
                return 'clean'
        except Exception:
            pass
        # Fallback: check session server (hard bans show as 204)
        uuid = (self.mc_profile or {}).get('id')
        username = (self.mc_profile or {}).get('name')
        if username:
            try:
                r2 = self.session.get(
                    f'{MC_SESS}/session/minecraft/profile/{username}',
                    timeout=8,
                    **(({'proxies': self.proxies} if self.proxies else {}))
                )
                if r2.status_code == 204:
                    return 'banned'
                if r2.status_code == 200:
                    return 'clean'
            except Exception:
                pass
        return 'unknown'

    def get_hypixel_stats(self, uuid: str) -> Optional[Dict]:
        """
        Hypixel stats via plancke.io (no API key).
        Extracts BW, SW, SkyBlock, level, karma.
        """
        if not uuid:
            return None
        try:
            kw = {'timeout': 10}
            if self.proxies:
                kw['proxies'] = self.proxies
            r = self.session.get(
                f'https://plancke.io/hypixel/player/stats/{uuid}',
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
                **kw
            )
            if r.status_code != 200:
                return None

            tx  = r.text
            out: Dict = {}

            def ext(start: str, end: str):
                try:
                    return tx.split(start)[1].split(end)[0].strip()
                except Exception:
                    return None

            out['level']       = ext('Network Level: <strong>', '</strong>')
            out['karma']       = ext('Karma: <strong>', '</strong>')
            out['first_login'] = ext('First Login: <strong>', '</strong>')
            out['last_login']  = ext('Last Login: <strong>', '</strong>')

            # BW
            bw_stars = ext('<td>Bedwars Stars</td><td>', '</td>')
            bw_wins  = ext('Final Kills/Deaths</th></tr><tr><td>Wins</td><td>', '</td>')
            bw_fkdr  = ext('<td>Final Kills/Deaths</td><td>', '</td>')
            if bw_stars: out['bw_stars'] = bw_stars
            if bw_wins:  out['bw_wins']  = bw_wins
            if bw_fkdr:  out['bw_fkdr']  = bw_fkdr

            # SW
            sw_stars = ext('<td>SkyWars Stars</td><td>', '</td>')
            sw_wins  = ext('Solo+Team Wins</th></tr><tr><td>Wins</td><td>', '</td>')
            if sw_stars: out['sw_stars'] = sw_stars
            if sw_wins:  out['sw_wins']  = sw_wins

            # Pit
            pit_gold = ext('Gold: <strong>', '</strong>')
            if pit_gold: out['pit_gold'] = pit_gold

            return out if any(out.values()) else None

        except Exception as exc:
            logger.debug(f"hypixel {uuid}: {exc}")
        return None

    def check_donut_smp(self) -> bool:
        """Check DonutSMP whitelist."""
        uuid = (self.mc_profile or {}).get('id')
        username = (self.mc_profile or {}).get('name')
        if not uuid and not username:
            return False
        try:
            kw = {'timeout': 8}
            if self.proxies:
                kw['proxies'] = self.proxies
            if uuid:
                r = self.session.get(
                    f'https://api.donutsmp.net/v1/whitelist/{uuid}',
                    **kw
                )
                if r.status_code == 200:
                    return r.json().get('whitelisted', False)
        except Exception:
            pass
        return False

    def check_full(self, email: str, password: str) -> Dict:
        """Full Minecraft check pipeline."""
        result = {
            'email': email, 'password': password,
            'status':         'BAD',
            'has_xbox':       False, 'gamertag':      None,
            'gamerscore':     None,  'xbox_gold':     False,
            'has_gamepass':   False, 'gamepass_type': None,
            'has_mc':         False, 'username':      None,
            'uuid':           None,  'capes':         [],
            'name_change':    False, 'ban_status':    'unknown',
            'donut_smp':      False, 'hypixel':       None,
        }

        success, status, _token = self.login(email, password)
        if not success:
            result['status'] = status
            return result

        result['status'] = 'HIT'

        # Xbox + Game Pass
        xbox = self.check_game_pass()
        result.update({
            'has_xbox':      xbox['has_xbox'],
            'gamertag':      xbox['gamertag'],
            'gamerscore':    xbox['gamerscore'],
            'xbox_gold':     xbox.get('xbox_gold', False),
            'has_gamepass':  xbox['has_gamepass'],
            'gamepass_type': xbox['gamepass_type'],
        })

        # Minecraft
        mc_tok = self.get_minecraft_token()
        if mc_tok:
            profile = self.get_profile()
            if profile:
                result['has_mc']     = True
                result['username']   = profile.get('name')
                result['uuid']       = profile.get('id')
                result['capes']      = self.get_capes()
                result['name_change']= self.check_name_change_available()
                result['ban_status'] = self.check_ban_status()
                result['donut_smp']  = self.check_donut_smp()
                if result['uuid']:
                    result['hypixel'] = self.get_hypixel_stats(result['uuid'])
            elif self.check_ownership():
                result['has_mc'] = True

        return result
