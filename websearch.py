"""Keyless web search, for measuring the source pool AI answers are built from.

No API key and no account. DuckDuckGo's HTML and lite endpoints are used
because they return plain server-rendered results; both are parsed the same
way and either can serve.

This is deliberately small and defensive. A search that gets blocked, rate
limited or reshaped returns nothing, and the caller omits the section rather
than reporting an empty result as a finding. That distinction is the whole
point: "we could not measure" and "you do not appear" look identical in a
table and mean opposite things.
"""

import html as html_mod
import re
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
TIMEOUT = 25

ENDPOINTS = (
    "https://html.duckduckgo.com/html/?q=%s",
    "https://lite.duckduckgo.com/lite/?q=%s",
)

# Aggregators, directories and platforms. They dominate these results and are
# not competitors in any sense the prospect cares about -- nobody loses a job
# to Yelp. Tracked separately so the report can say "the answers are built from
# directories, not from businesses", which is itself a finding.
AGGREGATORS = frozenset({
    "yelp.com", "yellowpages.com", "angi.com", "angieslist.com", "thumbtack.com",
    "homeadvisor.com", "houzz.com", "bbb.org", "nextdoor.com", "porch.com",
    "facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com",
    "youtube.com", "tiktok.com", "pinterest.com", "reddit.com", "quora.com",
    "google.com", "bing.com", "mapquest.com", "tripadvisor.com", "indeed.com",
    "wikipedia.org", "amazon.com", "ebay.com", "walmart.com", "expertise.com",
    "trustpilot.com", "glassdoor.com", "crunchbase.com", "manta.com",
})


class SearchError(Exception):
    pass


def _fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return None, ""


UDDG = re.compile(r"[?&]uddg=([^&]+)")
TAGS = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")

# Both endpoints wrap each result title in an anchor carrying a result class.
LINK = re.compile(
    r'<a[^>]+class=["\'][^"\']*result[-_]+(?:a|link)[^"\']*["\'][^>]*'
    r'href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
LINK_ALT = re.compile(
    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*result[-_]'
    r'(?:a|link)[^"\']*["\'][^>]*>(.*?)</a>', re.I | re.S)
SNIPPET = re.compile(
    r'class=["\'][^"\']*result[-_]+snippet[^"\']*["\'][^>]*>(.*?)</(?:a|td|div)>',
    re.I | re.S)


def _text(raw):
    return WS.sub(" ", html_mod.unescape(TAGS.sub(" ", raw or ""))).strip()


def _real_url(href):
    """Unwrap the /l/?uddg= redirect the HTML endpoint wraps results in."""
    m = UDDG.search(href or "")
    if m:
        return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def registrable(url):
    """Bare lowercase host without www., or '' when the URL is unusable."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    host = host.split(":")[0]
    return re.sub(r"^www\d*\.", "", host)


def parse(body, limit=10):
    """Result rows from either endpoint's markup."""
    pairs = LINK.findall(body or "") or LINK_ALT.findall(body or "")
    snippets = [_text(s) for s in SNIPPET.findall(body or "")]

    out, seen = [], set()
    for i, (href, title) in enumerate(pairs):
        url = _real_url(html_mod.unescape(href))
        domain = registrable(url)
        if not domain or domain.endswith("duckduckgo.com"):
            continue
        if domain in seen:
            continue
        seen.add(domain)
        out.append({
            "rank": len(out) + 1,
            "title": _text(title),
            "url": url,
            "domain": domain,
            "snippet": snippets[i] if i < len(snippets) else "",
            "aggregator": is_aggregator(domain),
        })
        if len(out) >= limit:
            break
    return out


def is_aggregator(domain):
    d = (domain or "").lower()
    return any(d == a or d.endswith("." + a) for a in AGGREGATORS)


def search(query, *, fetch=None, limit=10):
    """Top results for a query, or [] if the search could not be made.

    Returns [] rather than raising: one blocked query must not take down an
    audit. The caller distinguishes "no results" from "not measured" by
    checking whether ANY query in the set returned rows.
    """
    fetch = fetch or _fetch
    encoded = urllib.parse.quote_plus(query)
    for template in ENDPOINTS:
        status, body = fetch(template % encoded)
        if status != 200 or not body:
            continue
        rows = parse(body, limit=limit)
        if rows:
            return rows
    return []
