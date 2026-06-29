"""
Xbox Checker — Xbox Live auth, Game Pass, XGPU, gamerscore.
Fixed: no double auth, proper XSTS per relying party, gamerscore via correct endpoint.
"""
import logging
from typing import Dict, Optional
from .microsoft_base import MicrosoftBase

logger = logging.getLogger(__name__)

# Xbox product IDs
_XGPU_PRODUCT  = 'CFQ7TTC0K6L8'   # Xbox Game Pass Ultimate
_XGP_PRODUCT   = 'CFQ7TTC0K5DJ'   # Xbox Game Pass PC
_XGLIVE_PRODUCT = 'CFQ7TTC0K5CH'  # Xbox Live Gold


class XboxChecker(MicrosoftBase):
    def __init__(self, session=None, timeout: int = 20, proxy: str = None):
        super().__init__(session, timeout, proxy)
        self.xbl_token: Optional[str] = None
        self.uhs:       Optional[str] = None
        self.gamertag:  Optional[str] = None
        self._xbox_authed = False  # prevent double auth

    def authenticate_xbox_live(self) -> bool:
        """XBL auth. Skips if already done."""
        if self._xbox_authed:
            return bool(self.xbl_token and self.uhs)
        if not self.token:
            return False

        kw = {'timeout': self.timeout}
        if self.proxies:
            kw['proxies'] = self.proxies

        try:
            r = self.session.post(
                'https://user.auth.xboxlive.com/user/authenticate',
                json={
                    'Properties': {
                        'AuthMethod': 'RPS',
                        'SiteName':   'user.auth.xboxlive.com',
                        'RpsTicket':  self.token,
                    },
                    'RelyingParty': 'http://auth.xboxlive.com',
                    'TokenType':    'JWT',
                },
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                **kw
            )
            if r.status_code == 200:
                data            = r.json()
                self.xbl_token  = data.get('Token')
                xui             = data.get('DisplayClaims', {}).get('xui', [{}])[0]
                self.uhs        = xui.get('uhs')
                self.gamertag   = xui.get('gtg')
                self._xbox_authed = True
                return bool(self.xbl_token and self.uhs)
            elif r.status_code == 401:
                logger.debug(f"Xbox auth 401 — token may be expired")
        except Exception as exc:
            logger.debug(f"xbox_auth: {exc}")

        self._xbox_authed = True  # mark done even on failure to prevent retries
        return False

    def _get_xsts(self, relying_party: str) -> Optional[str]:
        """Get XSTS token for a given relying party."""
        if not self.xbl_token:
            return None
        kw = {'timeout': self.timeout}
        if self.proxies:
            kw['proxies'] = self.proxies
        try:
            r = self.session.post(
                'https://xsts.auth.xboxlive.com/xsts/authorize',
                json={
                    'Properties': {
                        'SandboxId':  'RETAIL',
                        'UserTokens': [self.xbl_token],
                    },
                    'RelyingParty': relying_party,
                    'TokenType':    'JWT',
                },
                headers={'Content-Type': 'application/json'},
                **kw
            )
            if r.status_code == 200:
                return r.json().get('Token')
            if r.status_code == 403:
                # Account banned from Xbox or service unavailable
                logger.debug(f"XSTS 403 for {relying_party}")
        except Exception as exc:
            logger.debug(f"xsts {relying_party}: {exc}")
        return None

    def check_game_pass(self) -> Dict:
        """Check Xbox subscriptions. Returns full result dict."""
        result = {
            'has_xbox':      False,
            'gamertag':      None,
            'gamerscore':    None,
            'has_gamepass':  False,
            'gamepass_type': None,
            'subscriptions': [],
            'xbox_gold':     False,
        }

        if not self.authenticate_xbox_live():
            return result

        result['has_xbox']  = True
        result['gamertag']  = self.gamertag

        xsts = self._get_xsts('http://xboxlive.com')
        if not xsts:
            return result

        auth_header = f'XBL3.0 x={self.uhs};{xsts}'
        kw = {'timeout': self.timeout}
        if self.proxies:
            kw['proxies'] = self.proxies

        # Subscriptions — real endpoint used by Xbox app
        try:
            r = self.session.get(
                'https://emerald.xboxservices.com/xboxcomfd/subscriptionsV4',
                headers={
                    'Authorization':          auth_header,
                    'x-xbl-contract-version': '3',
                    'Accept':                 'application/json',
                },
                **kw
            )
            if r.status_code == 200:
                for sub in r.json().get('subscriptions', []):
                    pid   = sub.get('productId', '')
                    state = sub.get('state', '')
                    if state == 'Active':
                        result['subscriptions'].append(pid)
                        if _XGPU_PRODUCT in pid:
                            result['has_gamepass']  = True
                            result['gamepass_type'] = 'XGPU'
                        elif _XGP_PRODUCT in pid or 'gamepass' in pid.lower():
                            result['has_gamepass']  = True
                            if not result['gamepass_type']:
                                result['gamepass_type'] = 'XGP'
                        elif _XGLIVE_PRODUCT in pid or 'gold' in pid.lower():
                            result['xbox_gold'] = True
        except Exception as exc:
            logger.debug(f"subscriptions: {exc}")

        # Gamerscore — correct v2 contract endpoint
        try:
            r2 = self.session.get(
                'https://profile.xboxlive.com/users/me/profile/settings',
                params={'settings': 'Gamerscore,AccountTier,XboxOneRep'},
                headers={
                    'Authorization':          auth_header,
                    'x-xbl-contract-version': '2',
                    'Accept':                 'application/json',
                },
                **kw
            )
            if r2.status_code == 200:
                for s in r2.json().get('profileUsers', [{}])[0].get('settings', []):
                    if s.get('id') == 'Gamerscore':
                        result['gamerscore'] = s.get('value')
        except Exception as exc:
            logger.debug(f"gamerscore: {exc}")

        return result
