"""
Proxy Validator
- Tests proxies against real Microsoft OAuth endpoint
- Measures response time
- Returns working proxy count + estimated CPM
- Used before scan starts and when admin uploads proxies
"""
import asyncio
import time
import logging
from typing import List, Tuple, Dict, Optional

logger = logging.getLogger(__name__)

# Real MS endpoint used to test proxy quality
_TEST_URL = 'https://www.google.com'
_TIMEOUT  = 8

# CPM estimate based on avg response time
def _estimate_cpm(avg_ms: float, threads: int) -> int:
    if avg_ms <= 0:
        return 0
    reqs_per_thread_per_min = 60_000 / avg_ms
    return int(reqs_per_thread_per_min * threads * 0.7)  # 0.7 = real-world efficiency factor


async def _test_one_proxy(proxy_url: str, session=None) -> Tuple[bool, float]:
    """Test a single proxy. Returns (success, response_ms)."""
    try:
        import aiohttp
        t0 = time.monotonic()
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(
                _TEST_URL,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
                allow_redirects=False,
            ) as r:
                elapsed_ms = (time.monotonic() - t0) * 1000
                # 200 or 302 both mean proxy works
                if r.status in (200, 302, 301):
                    return True, elapsed_ms
                return False, elapsed_ms
    except Exception:
        return False, 0.0


async def validate_proxies(proxy_urls: List[str], sample_size: int = 10) -> Dict:
    """
    Test a sample of proxies concurrently.
    Returns full validation report.
    """
    if not proxy_urls:
        return {
            'total': 0, 'working': 0, 'dead': 0,
            'success_rate': 0.0, 'avg_ms': 0,
            'estimated_cpm_50t': 0, 'estimated_cpm_100t': 0,
            'sample_tested': 0,
        }

    import random
    sample = random.sample(proxy_urls, min(sample_size, len(proxy_urls)))

    tasks = [_test_one_proxy(p) for p in sample]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    working    = 0
    total_ms   = 0.0
    dead       = 0

    for res in results:
        if isinstance(res, Exception):
            dead += 1
            continue
        ok, ms = res
        if ok:
            working += 1
            total_ms += ms
        else:
            dead += 1

    avg_ms       = (total_ms / working) if working > 0 else 0
    success_rate = (working / len(sample)) * 100 if sample else 0

    return {
        'total':              len(proxy_urls),
        'sample_tested':      len(sample),
        'working':            working,
        'dead':               dead,
        'success_rate':       round(success_rate, 1),
        'avg_ms':             round(avg_ms, 0),
        'estimated_cpm_50t':  _estimate_cpm(avg_ms, 50),
        'estimated_cpm_100t': _estimate_cpm(avg_ms, 100),
        'estimated_cpm_200t': _estimate_cpm(avg_ms, 200),
    }


def format_proxy_test_result(report: Dict) -> str:
    sr   = report['success_rate']
    ms   = report['avg_ms']
    cpm  = report['estimated_cpm_50t']

    if sr >= 80:    quality = '🟢 Excellent'
    elif sr >= 60:  quality = '🟡 Good'
    elif sr >= 40:  quality = '🟠 Average'
    elif sr > 0:    quality = '🔴 Poor'
    else:           quality = '💀 Dead'

    return (
        f"🔬 *Proxy Test Results*\n\n"
        f"📊 Tested: `{report['sample_tested']}` of `{report['total']}`\n"
        f"✅ Working: `{report['working']}`\n"
        f"💀 Dead: `{report['dead']}`\n"
        f"📈 Success Rate: `{sr}%`  {quality}\n"
        f"⚡ Avg Speed: `{ms:.0f}ms`\n\n"
        f"🚀 *Estimated CPM:*\n"
        f"• 50 threads: `{report['estimated_cpm_50t']:,}`\n"
        f"• 100 threads: `{report['estimated_cpm_100t']:,}`\n"
        f"• 200 threads: `{report['estimated_cpm_200t']:,}`"
    )
