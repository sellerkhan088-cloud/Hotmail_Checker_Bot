"""
IMAP Utilities — Production Grade
Every provider correctly mapped, auth method documented,
known-broken providers auto-skipped with proper logging.

BASIC AUTH STATUS (email:password without app password):
  ✅ WORKS:     Outlook/Hotmail/Live, AOL, GMX, Mail.com, Comcast, Cox,
                AT&T, Verizon, Zoho, Earthlink, Charter, Frontier,
                Optonline, Ziggo, Web.de, T-Online, Libero, Virgin
  ⚠️ HIT/MISS: iCloud (works if no 2FA), Yandex (region dependent)
  ❌ BROKEN:   Gmail (disabled basic auth May 2022)
               Yahoo (disabled basic auth May 2022)
               Protonmail (encrypted bridge required)
               Fastmail (app password required)
"""

import imaplib
import ssl
import socket
import logging
import email as email_lib
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

# ── Provider map ──────────────────────────────────────────────────────────────
# Format: domain → (imap_host, port, basic_auth_works)

IMAP_PROVIDERS: Dict[str, Tuple[str, int, bool]] = {
    # ── Microsoft (basic auth works via IMAP) ──────────────────────────────
    'outlook.com':        ('outlook.office365.com',   993, True),
    'hotmail.com':        ('outlook.office365.com',   993, True),
    'live.com':           ('outlook.office365.com',   993, True),
    'msn.com':            ('outlook.office365.com',   993, True),
    'live.co.uk':         ('outlook.office365.com',   993, True),
    'live.fr':            ('outlook.office365.com',   993, True),
    'live.de':            ('outlook.office365.com',   993, True),
    'live.it':            ('outlook.office365.com',   993, True),
    'live.nl':            ('outlook.office365.com',   993, True),
    'live.be':            ('outlook.office365.com',   993, True),
    'live.com.au':        ('outlook.office365.com',   993, True),
    'live.ca':            ('outlook.office365.com',   993, True),
    'live.jp':            ('outlook.office365.com',   993, True),
    'live.cn':            ('outlook.office365.com',   993, True),
    'live.com.mx':        ('outlook.office365.com',   993, True),
    'live.com.ar':        ('outlook.office365.com',   993, True),
    'live.com.br':        ('outlook.office365.com',   993, True),
    'live.co.za':         ('outlook.office365.com',   993, True),
    'live.in':            ('outlook.office365.com',   993, True),
    'live.se':            ('outlook.office365.com',   993, True),
    'live.dk':            ('outlook.office365.com',   993, True),
    'live.no':            ('outlook.office365.com',   993, True),
    'live.fi':            ('outlook.office365.com',   993, True),
    'live.at':            ('outlook.office365.com',   993, True),
    'live.ch':            ('outlook.office365.com',   993, True),
    'live.ie':            ('outlook.office365.com',   993, True),
    'live.pt':            ('outlook.office365.com',   993, True),
    'live.gr':            ('outlook.office365.com',   993, True),
    'live.ru':            ('outlook.office365.com',   993, True),
    'live.pl':            ('outlook.office365.com',   993, True),
    'live.cz':            ('outlook.office365.com',   993, True),
    'live.hu':            ('outlook.office365.com',   993, True),
    'live.ro':            ('outlook.office365.com',   993, True),
    'hotmail.co.uk':      ('outlook.office365.com',   993, True),
    'hotmail.fr':         ('outlook.office365.com',   993, True),
    'hotmail.de':         ('outlook.office365.com',   993, True),
    'hotmail.it':         ('outlook.office365.com',   993, True),
    'hotmail.es':         ('outlook.office365.com',   993, True),
    'hotmail.nl':         ('outlook.office365.com',   993, True),
    'hotmail.be':         ('outlook.office365.com',   993, True),
    'hotmail.se':         ('outlook.office365.com',   993, True),
    'hotmail.no':         ('outlook.office365.com',   993, True),
    'hotmail.dk':         ('outlook.office365.com',   993, True),
    'hotmail.fi':         ('outlook.office365.com',   993, True),
    'hotmail.ch':         ('outlook.office365.com',   993, True),
    'hotmail.at':         ('outlook.office365.com',   993, True),
    'hotmail.com.br':     ('outlook.office365.com',   993, True),
    'hotmail.com.ar':     ('outlook.office365.com',   993, True),
    'hotmail.com.mx':     ('outlook.office365.com',   993, True),
    'hotmail.co.jp':      ('outlook.office365.com',   993, True),
    'hotmail.co.za':      ('outlook.office365.com',   993, True),
    'hotmail.com.au':     ('outlook.office365.com',   993, True),
    'hotmail.ca':         ('outlook.office365.com',   993, True),
    'hotmail.pt':         ('outlook.office365.com',   993, True),
    'hotmail.gr':         ('outlook.office365.com',   993, True),
    'hotmail.ru':         ('outlook.office365.com',   993, True),
    'hotmail.pl':         ('outlook.office365.com',   993, True),
    'hotmail.cz':         ('outlook.office365.com',   993, True),
    'hotmail.hu':         ('outlook.office365.com',   993, True),
    'hotmail.ro':         ('outlook.office365.com',   993, True),
    'hotmail.ie':         ('outlook.office365.com',   993, True),
    'windowslive.com':    ('outlook.office365.com',   993, True),
    'passport.com':       ('outlook.office365.com',   993, True),

    # ── AOL / Verizon Media ────────────────────────────────────────────────
    'aol.com':            ('imap.aol.com',            993, True),
    'aim.com':            ('imap.aol.com',            993, True),
    'love.com':           ('imap.aol.com',            993, True),
    'ygm.com':            ('imap.aol.com',            993, True),
    'verizon.net':        ('incoming.verizon.net',    993, True),

    # ── GMX / Mail.com (1&1 group) ─────────────────────────────────────────
    'gmx.com':            ('imap.gmx.com',            993, True),
    'gmx.net':            ('imap.gmx.net',            993, True),
    'gmx.de':             ('imap.gmx.net',            993, True),
    'gmx.at':             ('imap.gmx.net',            993, True),
    'gmx.ch':             ('imap.gmx.net',            993, True),
    'mail.com':           ('imap.mail.com',           993, True),
    'email.com':          ('imap.mail.com',           993, True),
    'hailmail.net':       ('imap.mail.com',           993, True),
    'iname.com':          ('imap.mail.com',           993, True),
    'inoutbox.com':       ('imap.mail.com',           993, True),
    'internetemails.net': ('imap.mail.com',           993, True),
    'mailandftp.com':     ('imap.mail.com',           993, True),
    'mailbolt.com':       ('imap.mail.com',           993, True),
    'mailc.net':          ('imap.mail.com',           993, True),
    'mailcan.com':        ('imap.mail.com',           993, True),
    'mailhaven.com':      ('imap.mail.com',           993, True),
    'mailingaddress.org': ('imap.mail.com',           993, True),
    'mailite.com':        ('imap.mail.com',           993, True),
    'mailsent.net':       ('imap.mail.com',           993, True),
    'mailservice.ms':     ('imap.mail.com',           993, True),
    'mailvault.com':      ('imap.mail.com',           993, True),
    'ml1.net':            ('imap.mail.com',           993, True),
    'mm.st':              ('imap.mail.com',           993, True),
    'myfastmail.com':     ('imap.mail.com',           993, True),
    'proinbox.com':       ('imap.mail.com',           993, True),
    'promessage.com':     ('imap.mail.com',           993, True),
    'realemail.net':      ('imap.mail.com',           993, True),
    'sent.com':           ('imap.mail.com',           993, True),
    'speedymail.org':     ('imap.mail.com',           993, True),
    'swift-mail.com':     ('imap.mail.com',           993, True),
    'the-fastest.net':    ('imap.mail.com',           993, True),
    'the-quickest.com':   ('imap.mail.com',           993, True),
    'theinternetemail.com':('imap.mail.com',          993, True),
    'toothfairy.com':     ('imap.mail.com',           993, True),
    'veryfast.biz':       ('imap.mail.com',           993, True),
    'veryspeedy.net':     ('imap.mail.com',           993, True),
    'warpmail.net':       ('imap.mail.com',           993, True),
    'xsmail.com':         ('imap.mail.com',           993, True),
    'yepmail.net':        ('imap.mail.com',           993, True),
    'your-mail.com':      ('imap.mail.com',           993, True),

    # ── Web.de / T-Online (Germany) ────────────────────────────────────────
    'web.de':             ('imap.web.de',             993, True),
    't-online.de':        ('secureimap.t-online.de',  993, True),
    'freenet.de':         ('mx.freenet.de',           993, True),

    # ── US ISP providers ───────────────────────────────────────────────────
    'comcast.net':        ('imap.comcast.net',        993, True),
    'att.net':            ('imap.mail.att.net',       993, True),
    'sbcglobal.net':      ('imap.mail.att.net',       993, True),
    'bellsouth.net':      ('imap.mail.att.net',       993, True),
    'cox.net':            ('imap.cox.net',            993, True),
    'charter.net':        ('mobile.charter.net',      993, True),
    'earthlink.net':      ('imap.earthlink.net',      993, True),
    'optonline.net':      ('mail.optonline.net',      993, True),
    'frontier.com':       ('imap.frontier.com',       993, True),
    'windstream.net':     ('imap.windstream.net',     993, True),
    'centurytel.net':     ('imap.centurytel.net',     993, True),
    'embarqmail.com':     ('imap.embarqmail.com',     993, True),

    # ── European providers ─────────────────────────────────────────────────
    'ziggo.nl':           ('imap.ziggo.nl',           993, True),
    'xs4all.nl':          ('imap.xs4all.nl',          993, True),
    'planet.nl':          ('imap.planet.nl',          993, True),
    'wanadoo.fr':         ('imap.wanadoo.fr',         993, True),
    'orange.fr':          ('imap.orange.fr',          993, True),
    'sfr.fr':             ('imap.sfr.fr',             993, True),
    'free.fr':            ('imap.free.fr',            993, True),
    'laposte.net':        ('imap.laposte.net',        993, True),
    'libero.it':          ('imapmail.libero.it',      993, True),
    'virgilio.it':        ('imapmail.libero.it',      993, True),
    'tin.it':             ('imapmail.libero.it',      993, True),
    'alice.it':           ('imapmail.libero.it',      993, True),
    'tiscali.it':         ('imap.tiscali.it',         993, True),
    'tiscali.co.uk':      ('imap.tiscali.co.uk',      993, True),
    'virgin.net':         ('imap.virgin.net',         993, True),
    'virginmedia.com':    ('imap.virginmedia.com',    993, True),
    'btinternet.com':     ('imap.btinternet.com',     993, True),
    'btopenworld.com':    ('imap.btinternet.com',     993, True),
    'talk21.com':         ('imap.btinternet.com',     993, True),
    'ntlworld.com':       ('imap.ntlworld.com',       993, True),
    'talktalk.net':       ('imap.talktalk.net',       993, True),
    'sky.com':            ('imap.sky.com',            993, True),
    'skynet.be':          ('imap.skynet.be',          993, True),
    'telenet.be':         ('imap.telenet.be',         993, True),
    'proximus.be':        ('imap.proximus.be',        993, True),
    'swing.be':           ('imap.swing.be',           993, True),
    'voila.fr':           ('imap.voila.fr',           993, True),
    'numericable.fr':     ('mail.numericable.fr',     993, True),
    'bbox.fr':            ('imap.bbox.fr',            993, True),
    'noos.fr':            ('imap.noos.fr',            993, True),
    'club-internet.fr':   ('imap.club-internet.fr',   993, True),
    'neuf.fr':            ('imap.sfr.fr',             993, True),
    'cegetel.net':        ('imap.sfr.fr',             993, True),

    # ── Russia / CIS ───────────────────────────────────────────────────────
    'mail.ru':            ('imap.mail.ru',            993, True),
    'inbox.ru':           ('imap.mail.ru',            993, True),
    'bk.ru':              ('imap.mail.ru',            993, True),
    'list.ru':            ('imap.mail.ru',            993, True),
    'internet.ru':        ('imap.mail.ru',            993, True),
    'yandex.ru':          ('imap.yandex.ru',          993, True),
    'yandex.com':         ('imap.yandex.com',         993, True),
    'yandex.ua':          ('imap.yandex.ua',          993, True),
    'yandex.by':          ('imap.yandex.by',          993, True),
    'yandex.kz':          ('imap.yandex.kz',          993, True),
    'ya.ru':              ('imap.yandex.ru',          993, True),
    'rambler.ru':         ('imap.rambler.ru',         993, True),
    'lenta.ru':           ('imap.rambler.ru',         993, True),

    # ── Zoho ───────────────────────────────────────────────────────────────
    'zoho.com':           ('imap.zoho.com',           993, True),
    'zohomail.com':       ('imap.zoho.com',           993, True),

    # ── Apple iCloud (works if no 2FA, or app password provided) ──────────
    'icloud.com':         ('imap.mail.me.com',        993, True),
    'me.com':             ('imap.mail.me.com',        993, True),
    'mac.com':            ('imap.mail.me.com',        993, True),

    # ── KNOWN BROKEN — basic auth disabled ────────────────────────────────
    # These are mapped so we can detect and skip them cleanly
    # instead of wasting a thread on a connection that will always fail
    'gmail.com':          ('imap.gmail.com',          993, False),  # basic auth disabled 2022
    'googlemail.com':     ('imap.gmail.com',          993, False),  # same
    'yahoo.com':          ('imap.mail.yahoo.com',     993, False),  # basic auth disabled 2022
    'yahoo.co.uk':        ('imap.mail.yahoo.com',     993, False),
    'yahoo.fr':           ('imap.mail.yahoo.com',     993, False),
    'yahoo.de':           ('imap.mail.yahoo.com',     993, False),
    'yahoo.it':           ('imap.mail.yahoo.com',     993, False),
    'yahoo.es':           ('imap.mail.yahoo.com',     993, False),
    'yahoo.com.br':       ('imap.mail.yahoo.com',     993, False),
    'yahoo.com.ar':       ('imap.mail.yahoo.com',     993, False),
    'yahoo.com.mx':       ('imap.mail.yahoo.com',     993, False),
    'yahoo.co.jp':        ('imap.mail.yahoo.co.jp',   993, False),
    'yahoo.com.au':       ('imap.mail.yahoo.com',     993, False),
    'yahoo.ca':           ('imap.mail.yahoo.com',     993, False),
    'yahoo.co.in':        ('imap.mail.yahoo.com',     993, False),
    'ymail.com':          ('imap.mail.yahoo.com',     993, False),
    'rocketmail.com':     ('imap.mail.yahoo.com',     993, False),
    'protonmail.com':     ('127.0.0.1',               1143,False),  # bridge required
    'protonmail.ch':      ('127.0.0.1',               1143,False),
    'pm.me':              ('127.0.0.1',               1143,False),
    'fastmail.com':       ('imap.fastmail.com',       993, False),  # app password only
    'fastmail.fm':        ('imap.fastmail.fm',        993, False),
}

# Legacy alias for backward compatibility
IMAP_SERVERS = {domain: host for domain, (host, port, works) in IMAP_PROVIDERS.items()}


def imap_server(email: str) -> str:
    """Get IMAP host for an email domain."""
    domain = email.split('@')[-1].lower()
    provider = IMAP_PROVIDERS.get(domain)
    if provider:
        return provider[0]
    return f'imap.{domain}'


def imap_port(email: str) -> int:
    """Get IMAP port for an email domain."""
    domain = email.split('@')[-1].lower()
    provider = IMAP_PROVIDERS.get(domain)
    return provider[1] if provider else 993


def basic_auth_works(email: str) -> bool:
    """
    Returns False for providers that have disabled basic auth.
    Prevents wasting threads on Gmail/Yahoo which will always fail.
    """
    domain = email.split('@')[-1].lower()
    provider = IMAP_PROVIDERS.get(domain)
    if provider is None:
        return True  # unknown provider — try it
    return provider[2]


def get_provider_name(email: str) -> str:
    """Human-readable provider name for logging."""
    domain = email.split('@')[-1].lower()
    names = {
        'outlook.office365.com': 'Outlook',
        'imap.gmail.com':        'Gmail',
        'imap.mail.yahoo.com':   'Yahoo',
        'imap.aol.com':          'AOL',
        'imap.mail.me.com':      'iCloud',
        'imap.gmx.com':          'GMX',
        'imap.gmx.net':          'GMX',
        'imap.mail.com':         'Mail.com',
        'imap.mail.ru':          'Mail.ru',
        'imap.yandex.ru':        'Yandex',
        'imap.yandex.com':       'Yandex',
        'imap.zoho.com':         'Zoho',
    }
    server = imap_server(email)
    return names.get(server, domain)


def imap_connect(
    email: str,
    password: str,
    timeout: int = 20,
    skip_broken: bool = True,
) -> Optional[imaplib.IMAP4_SSL]:
    """
    Open an authenticated IMAP connection.

    Args:
        email:        Full email address
        password:     Account password
        timeout:      Connection timeout in seconds
        skip_broken:  If True, immediately returns None for providers
                      with basic auth disabled (Gmail, Yahoo, etc.)
                      This saves a wasted connection attempt.

    Returns:
        Authenticated IMAP4_SSL connection, or None on any failure.
    """
    # Fast-skip for known broken providers
    if skip_broken and not basic_auth_works(email):
        provider = get_provider_name(email)
        logger.debug(f"Skipping {provider} — basic auth disabled (email: {email.split('@')[0]})")
        return None

    server  = imap_server(email)
    port    = imap_port(email)

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode    = ssl.CERT_REQUIRED

        conn = imaplib.IMAP4_SSL(
            host=server,
            port=port,
            ssl_context=ctx,
        )
        # Set socket timeout after connection established
        conn.socket().settimeout(timeout)
        conn.login(email, password)
        return conn

    except imaplib.IMAP4.error as exc:
        # Wrong credentials, account locked, or bad auth
        err = str(exc).lower()
        if any(s in err for s in ('invalid', 'failed', 'denied', 'disabled', 'locked')):
            logger.debug(f"IMAP auth failed for {email}: {exc}")
        return None

    except (OSError, socket.timeout, ssl.SSLError, ConnectionRefusedError) as exc:
        logger.debug(f"IMAP connect failed {email}@{server}: {type(exc).__name__}")
        return None

    except Exception as exc:
        logger.debug(f"IMAP unexpected {email}: {type(exc).__name__}: {exc}")
        return None


def get_email_body(msg) -> str:
    """Extract plain text body from email.message object."""
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get('Content-Disposition', ''))
            if ct == 'text/plain' and 'attachment' not in cd:
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    body   += part.get_payload(decode=True).decode(charset, errors='ignore')
                except Exception:
                    pass
    else:
        try:
            charset = msg.get_content_charset() or 'utf-8'
            body    = msg.get_payload(decode=True).decode(charset, errors='ignore')
        except Exception:
            pass
    return body


def get_provider_stats() -> Dict:
    """Summary of provider support for admin display."""
    working = sum(1 for _, _, works in IMAP_PROVIDERS.values() if works)
    broken  = sum(1 for _, _, works in IMAP_PROVIDERS.values() if not works)
    return {
        'total':   len(IMAP_PROVIDERS),
        'working': working,
        'broken':  broken,
        'domains': list(IMAP_PROVIDERS.keys()),
    }
