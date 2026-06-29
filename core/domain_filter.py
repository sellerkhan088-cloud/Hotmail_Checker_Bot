"""
Domain Filter
- MS/Xbox/MC/CR modes: Microsoft domains only — others skipped instantly
- IMAP modes: all domains accepted
- Per-mode routing so no wasted threads on dead domains
"""
from typing import Set, FrozenSet

# Import working providers from IMAP utils
try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from checkers._imap_utils import IMAP_PROVIDERS, basic_auth_works
    _IMAP_LOADED = True
except ImportError:
    _IMAP_PROVIDERS = {}
    _IMAP_LOADED = False

# Microsoft OAuth supported domains — accounts that can actually log in
MS_DOMAINS: FrozenSet[str] = frozenset({
    'outlook.com', 'hotmail.com', 'live.com', 'msn.com',
    'live.co.uk', 'live.fr', 'live.de', 'live.it', 'live.nl',
    'live.com.au', 'live.ca', 'live.jp', 'live.cn', 'live.be',
    'live.com.mx', 'live.com.ar', 'live.com.br', 'live.co.za',
    'live.in', 'live.se', 'live.dk', 'live.no', 'live.fi',
    'live.at', 'live.ch', 'live.ie', 'live.pt', 'live.gr',
    'live.ru', 'live.pl', 'live.cz', 'live.hu', 'live.ro',
    'hotmail.co.uk', 'hotmail.fr', 'hotmail.de', 'hotmail.it',
    'hotmail.es', 'hotmail.nl', 'hotmail.be', 'hotmail.se',
    'hotmail.no', 'hotmail.dk', 'hotmail.fi', 'hotmail.ch',
    'hotmail.at', 'hotmail.com.br', 'hotmail.com.ar',
    'hotmail.com.mx', 'hotmail.co.jp', 'hotmail.co.za',
    'hotmail.com.au', 'hotmail.ca', 'hotmail.pt', 'hotmail.gr',
    'hotmail.ru', 'hotmail.pl', 'hotmail.cz', 'hotmail.hu',
    'hotmail.ro', 'hotmail.ie',
    'windowslive.com', 'passport.com',
    'microsoft.com',
})

# Modes that require Microsoft domain
MS_ONLY_MODES: FrozenSet[int] = frozenset({4, 6, 7, 8, 9, 10})

# Modes that work on all domains (IMAP based)
ALL_DOMAIN_MODES: FrozenSet[int] = frozenset({2, 3, 5})

# Mode 1 (All-in-One VIP) — MS domain for MS parts, but IMAP parts run on all
AIO_MODE = 1


def get_email_domain(email: str) -> str:
    try:
        return email.split('@')[1].lower()
    except (IndexError, AttributeError):
        return ''


def is_ms_domain(email: str) -> bool:
    return get_email_domain(email) in MS_DOMAINS


def should_check(email: str, mode: int) -> bool:
    """
    Returns True if this email should be checked for this mode.
    Returns False if it should be skipped (wrong domain for mode).
    """
    domain = get_email_domain(email)
    if not domain:
        return False

    if mode in MS_ONLY_MODES:
        return domain in MS_DOMAINS

    if mode in ALL_DOMAIN_MODES:
        return bool(domain)  # IMAP works on all domains but must have a domain

    if mode == AIO_MODE:
        # All-in-One: always run (MS parts auto-skip if not MS domain)
        return True

    return True


def filter_combos_by_mode(combos: list, mode: int) -> tuple:
    """
    Split combos into (to_check, skipped) based on mode domain requirements.
    Returns (filtered_list, skip_count).
    """
    if mode in ALL_DOMAIN_MODES or mode == AIO_MODE:
        # Still filter malformed lines
        filtered = [l for l in combos if ':' in l and '@' in l.split(':', 1)[0]]
        return filtered, len(combos) - len(filtered)

    filtered = []
    skipped  = 0
    for line in combos:
        if ':' not in line:
            skipped += 1
            continue
        email = line.split(':', 1)[0].strip()
        if should_check(email, mode):
            filtered.append(line)
        else:
            skipped += 1
    return filtered, skipped
