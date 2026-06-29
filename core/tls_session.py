"""
TLS Fingerprinting Session — Production Grade

What is TLS fingerprinting?
  Every browser has a unique TLS "fingerprint" — the exact combination of
  cipher suites, extensions, elliptic curves, and compression methods it
  sends in the ClientHello packet. Microsoft, TikTok, Cloudflare and others
  check this fingerprint to detect bots. Python's default `requests` library
  sends a generic Python fingerprint that gets flagged instantly.

  curl_cffi impersonates real browser TLS handshakes at the C level (libcurl),
  making the connection indistinguishable from a real Chrome/Edge/Safari browser.

Profiles available:
  Chrome 120, 119, 116, 110, 107 — most compatible with MS APIs
  Edge 101, 99 — second best for Microsoft specifically
  Safari 17.0, 15.5 — good for Apple/iCloud
  Firefox 120 — good for general use

Each profile sets:
  - Exact TLS cipher suite order
  - TLS extensions (ALPN, SNI, session ticket, etc.)
  - Elliptic curves (ECDH)
  - HTTP/2 settings
  - Correct User-Agent + Sec-Ch headers to match
"""

import random
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── curl_cffi availability ────────────────────────────────────────────────────
try:
    from curl_cffi import requests as _cf
    from curl_cffi.requests import Session as _CfSession
    TLS_AVAILABLE = True
    logger.debug("curl_cffi TLS fingerprinting: ACTIVE")
except ImportError:
    TLS_AVAILABLE = False
    logger.debug(
        "curl_cffi not installed — falling back to requests (no TLS fingerprinting). "
        "Install with: pip install curl_cffi"
    )
    import requests as _requests

# ── Browser profiles ──────────────────────────────────────────────────────────
# Each profile: (curl_cffi_name, user_agent, sec_ch_ua)
_PROFILES = {
    'chrome124': (
        'chrome124',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
    ),
    'chrome120': (
        'chrome120',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    ),
    'chrome119': (
        'chrome119',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        '"Google Chrome";v="119", "Chromium";v="119", "Not-A.Brand";v="24"',
    ),
    'chrome116': (
        'chrome116',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
        '"Chromium";v="116", "Not)A;Brand";v="24", "Google Chrome";v="116"',
    ),
    'edge101': (
        'edge101',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.64 Safari/537.36 Edg/101.0.1210.53',
        '"Microsoft Edge";v="101", "Chromium";v="101", ";Not A Brand";v="99"',
    ),
    'safari17_0': (
        'safari17_0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        None,  # Safari doesn't send Sec-Ch-Ua
    ),
    'safari15_5': (
        'safari15_5',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 12_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.4 Safari/605.1.15',
        None,
    ),
}

# Best profiles for Microsoft login specifically
_MS_PROFILES = ['chrome124', 'chrome120', 'chrome119', 'edge101']
# Best for general use
_GENERAL_PROFILES = list(_PROFILES.keys())


def _build_headers(profile_name: str) -> Dict[str, str]:
    """Build matching headers for a TLS profile."""
    cfg = _PROFILES.get(profile_name, _PROFILES['chrome120'])
    _, ua, sec_ch = cfg

    headers = {
        'User-Agent':              ua,
        'Accept':                  'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language':         'en-US,en;q=0.9',
        'Accept-Encoding':         'gzip, deflate, br',
        'Connection':              'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest':          'document',
        'Sec-Fetch-Mode':          'navigate',
        'Sec-Fetch-Site':          'none',
        'Sec-Fetch-User':          '?1',
    }
    if sec_ch:
        headers['Sec-Ch-Ua']          = sec_ch
        headers['Sec-Ch-Ua-Mobile']   = '?0'
        headers['Sec-Ch-Ua-Platform'] = '"Windows"'
    return headers


class TLSSession:
    """
    HTTP session with TLS fingerprinting.

    Usage:
        session = TLSSession(proxy='http://ip:port', profile='chrome120')
        r = session.get('https://login.live.com/...')
        r = session.post('https://...', data={...})

    Automatically:
    - Uses curl_cffi for real Chrome/Edge TLS handshake
    - Rotates User-Agent to match the TLS profile
    - Sets all matching Sec-Ch headers
    - Falls back to requests if curl_cffi not installed
    """

    def __init__(
        self,
        proxy:      str = None,
        profile:    str = None,
        for_microsoft: bool = True,
    ):
        self.proxy   = proxy
        self.proxies = {'http': proxy, 'https': proxy} if proxy else None

        # Pick profile
        if profile:
            self.profile = profile if profile in _PROFILES else 'chrome120'
        elif for_microsoft:
            self.profile = random.choice(_MS_PROFILES)
        else:
            self.profile = random.choice(_GENERAL_PROFILES)

        self._init_session()

    def _init_session(self):
        """Initialize the underlying session."""
        cfg = _PROFILES[self.profile]
        impersonate_name = cfg[0]

        if TLS_AVAILABLE:
            try:
                # curl_cffi: NO proxy at session init
                # proxy is passed per-request via _make_kwargs
                self._session = _CfSession(impersonate=impersonate_name)
                self._is_tls  = True
            except Exception as exc:
                self._session = _requests.Session()
                if self.proxies:
                    self._session.proxies = self.proxies
                self._is_tls  = False
        else:
            self._session = _requests.Session()
            if self.proxies:
                self._session.proxies = self.proxies
            self._is_tls  = False

        # Set matching headers
        self._session.headers.update(_build_headers(self.profile))

    def _make_kwargs(self, kwargs: dict) -> dict:
        """
        Inject proxy correctly per client type:
        - curl_cffi Session: use 'proxy' string only
        - requests Session:  use 'proxies' dict only
        Never pass both — curl_cffi throws TypeError.
        """
        kwargs.setdefault('timeout', 12)
        if self.proxy:
            if self._is_tls:
                # curl_cffi: proxy string only
                if 'proxy' not in kwargs:
                    kwargs['proxy'] = self.proxy
            else:
                # requests: proxies dict only
                if 'proxies' not in kwargs and self.proxies:
                    kwargs['proxies'] = self.proxies
        return kwargs

    def get(self, url: str, **kwargs):
        return self._session.get(url, **self._make_kwargs(kwargs))

    def post(self, url: str, **kwargs):
        return self._session.post(url, **self._make_kwargs(kwargs))

    def head(self, url: str, **kwargs):
        kwargs.setdefault('timeout', 8)
        return self._session.head(url, **self._make_kwargs(kwargs))

    def put(self, url: str, **kwargs):
        return self._session.put(url, **self._make_kwargs(kwargs))

    def request(self, method: str, url: str, **kwargs):
        return self._session.request(method, url, **self._make_kwargs(kwargs))

    @property
    def headers(self):
        return self._session.headers

    @headers.setter
    def headers(self, value):
        self._session.headers = value

    @property
    def cookies(self):
        return self._session.cookies

    def is_tls_active(self) -> bool:
        return self._is_tls

    def rotate_profile(self, for_microsoft: bool = True):
        """Rotate to a new random TLS profile — use between retries."""
        profiles = _MS_PROFILES if for_microsoft else _GENERAL_PROFILES
        profiles = [p for p in profiles if p != self.profile]
        if profiles:
            self.profile = random.choice(profiles)
            self._init_session()

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass


def new_session(proxy: str = None, for_microsoft: bool = True) -> TLSSession:
    """Convenience factory — creates a fresh TLS session."""
    return TLSSession(proxy=proxy, for_microsoft=for_microsoft)


def tls_status() -> str:
    if TLS_AVAILABLE:
        return "✅ curl_cffi TLS fingerprinting ACTIVE"
    return "⚠️ curl_cffi not installed — basic requests (no TLS fingerprint)"
