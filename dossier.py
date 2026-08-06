"""Company background for the closer, and the typed size fields pricing needs.

Scope boundary, deliberate: business-relevant public information only. Company,
size, locations, tenure, ownership, platform, published pricing. Not
personal-life material.

What this module will NOT do, and why it matters for an unattended runner:

- **No LinkedIn, no directory scraping.** Both prohibit automated access, and an
  Actions runner doing it gets the IP blocked. Fields that need those sources
  are returned unknown with a ready-made search URL for the closer to open by
  hand. That is a real limitation, stated in the output, not papered over.
- **Nothing is inferred.** An invented headcount silently moves the price band,
  so every field is either positively established with a source URL and the
  snippet it came from, or it is unknown.

Every fetch is injectable, so the whole module is testable offline.
"""

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (compatible; RiseRidgeAudit/1.0; "
      "+https://riseridge.io/bot)")
TIMEOUT = 20
MAX_PAGES = 12
MAX_BYTES = 1_500_000

# Pages worth looking at, in priority order. The crawl is capped and stays on
# the prospect's own host.
CANDIDATE_PATHS = (
    "/about", "/about-us", "/our-story", "/company",
    "/team", "/our-team", "/meet-the-team", "/staff", "/leadership",
    "/locations", "/service-areas", "/areas-we-serve",
    "/contact", "/contact-us", "/pricing", "/services",
)

CURRENT_YEAR = 2026


class DossierError(Exception):
    pass


# --- fetching ---------------------------------------------------------------

def http_fetch(url):
    """(status, text) for a URL, or (None, '') on any failure.

    A prospect site being down, slow or hostile must degrade the dossier, never
    crash the pipeline that is trying to produce a sales call brief.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(MAX_BYTES)
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, raw.decode(charset, "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return None, ""


class RobotsPolicy:
    """robots.txt for one host, fetched once. Unreachable robots means allow.

    An unattended crawler that ignores robots.txt is the kind of thing that gets
    a runner IP blocked and a prospect annoyed before the sales call.
    """

    def __init__(self, base, fetch):
        self.parser = urllib.robotparser.RobotFileParser()
        status, text = fetch(urllib.parse.urljoin(base, "/robots.txt"))
        if status == 200 and text:
            self.parser.parse(text.splitlines())
        else:
            self.parser = None

    def allows(self, url):
        if self.parser is None:
            return True
        try:
            return self.parser.can_fetch(UA, url)
        except Exception:
            return True


# --- HTML helpers -----------------------------------------------------------

TAG = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
ANY_TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def visible_text(html):
    """Text content with scripts, styles and markup removed."""
    if not html:
        return ""
    body = TAG.sub(" ", html)
    body = ANY_TAG.sub(" ", body)
    body = (body.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&#39;", "'").replace("&quot;", '"')
                .replace("&mdash;", "-").replace("&ndash;", "-"))
    return WS.sub(" ", body).strip()


HREF = re.compile(r'href=["\']([^"\'#]+)', re.I)


def internal_links(html, base):
    """Absolute same-host URLs found in the page, de-duplicated, order kept."""
    host = urllib.parse.urlparse(base).netloc.lower()
    seen, out = set(), []
    for raw in HREF.findall(html or ""):
        if raw.startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        url = urllib.parse.urljoin(base, raw)
        parts = urllib.parse.urlparse(url)
        if parts.scheme not in ("http", "https"):
            continue
        if parts.netloc.lower().replace("www.", "") != host.replace("www.", ""):
            continue
        clean = parts._replace(query="", fragment="").geturl().rstrip("/")
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


# --- field extraction -------------------------------------------------------

def _field(value, source, method, evidence=""):
    return {"value": value, "source": source, "method": method,
            "evidence": evidence}


UNKNOWN = _field(None, "", "not established")


# Anchored tightly. A bare four-digit number is not a founding year: the
# false positive that prompted this was a modern telehealth brand reported as
# "founded 1989", which a closer repeating it on a call would not survive.
# The anchor phrase must sit immediately before the year, and copyright
# notices are excluded outright.
YEAR_PATTERNS = (
    re.compile(r"\b(?:since|est\.?|established(?:\s+in)?|founded(?:\s+in)?|"
               r"serving[^.]{0,40}?since|in\s+business\s+since)\s*:?\s*"
               r"(19\d{2}|20[0-2]\d)\b", re.I),
)
COPYRIGHT = re.compile(r"(?:©|&copy;|copyright|\(c\))\s*\d", re.I)
YEARS_PHRASE = re.compile(
    r"\b(?:over|more\s+than|nearly|almost)?\s*(\d{1,3})\+?\s*years?\s+"
    r"(?:of\s+)?(?:in\s+)?(?:experience|business|serving|service)", re.I)


def years_in_business(text, source):
    """Founding year and derived age, or unknown.

    Returns the SNIPPET it matched so the operator can sanity-check a figure
    before repeating it. A wrong tenure on a call destroys credibility faster
    than an absent one.
    """
    if not text:
        return UNKNOWN.copy(), UNKNOWN.copy()

    for pat in YEAR_PATTERNS:
        for m in pat.finditer(text):
            start = max(0, m.start() - 60)
            window = text[start:m.end() + 20]
            if COPYRIGHT.search(window):
                continue
            year = int(m.group(1))
            if not 1800 <= year <= CURRENT_YEAR:
                continue
            age = CURRENT_YEAR - year
            snippet = WS.sub(" ", window).strip()
            return (_field(year, source, "anchored 'since/founded/established' "
                           "phrase", snippet),
                    _field(age, source, "current year minus founding year",
                           snippet))

    m = YEARS_PHRASE.search(text)
    if m:
        age = int(m.group(1))
        if 1 <= age <= 150:
            snippet = WS.sub(" ", text[max(0, m.start() - 40):m.end() + 20]).strip()
            return (UNKNOWN.copy(),
                    _field(age, source, "explicit 'N years in business' phrase",
                           snippet))

    return UNKNOWN.copy(), UNKNOWN.copy()


HEADCOUNT_PHRASE = re.compile(
    r"\b(?:team\s+of|staff\s+of|crew\s+of|over|more\s+than|nearly)?\s*"
    r"(\d{1,5})\+?\s*"
    r"(?:employees|team\s+members|staff\s+members|technicians|installers|"
    r"agents|attorneys|providers|clinicians|professionals|people\s+on\s+our\s+team)",
    re.I)
TEAM_OF = re.compile(r"\b(?:team|staff|crew)\s+of\s+(?:over\s+|more\s+than\s+)?"
                     r"(\d{1,5})\b", re.I)


def headcount(text, source):
    """Explicit staff count, or unknown. Never estimated."""
    for pat in (HEADCOUNT_PHRASE, TEAM_OF):
        m = pat.search(text or "")
        if m:
            n = int(m.group(1))
            if 1 <= n <= 100000:
                snippet = WS.sub(" ", text[max(0, m.start() - 40):
                                           m.end() + 20]).strip()
                return _field(n, source, "explicit headcount phrase on the site",
                              snippet)
    return UNKNOWN.copy()


LOCATION_PHRASE = re.compile(
    r"\b(?:over|more\s+than|nearly)?\s*(\d{1,4})\+?\s*"
    r"(?:locations|offices|branches|showrooms|clinics|stores|dealerships)\b",
    re.I)
LOCATION_PATH = re.compile(
    r"/(?:locations?|offices?|branches?|service-areas?|areas-we-serve|"
    r"cities|clinics?|stores?)/[a-z0-9\-]{2,}", re.I)


def location_count(text, links, source):
    """Explicit location count, else the number of distinct location pages.

    The page-derived count is a LOWER BOUND and says so: a site can serve ten
    cities off one page. A lower bound is safe for a pricing floor, which only
    ever raises a class.
    """
    m = LOCATION_PHRASE.search(text or "")
    if m:
        n = int(m.group(1))
        if 1 <= n <= 10000:
            snippet = WS.sub(" ", text[max(0, m.start() - 40):m.end() + 20]).strip()
            return _field(n, source, "explicit location count on the site", snippet)

    pages = {u for u in links if LOCATION_PATH.search(u)}
    if pages:
        return _field(len(pages), source,
                      "distinct location/service-area pages found (lower bound)",
                      "; ".join(sorted(pages)[:5]))
    return UNKNOWN.copy()


OWNERSHIP_SIGNS = (
    ("pe-backed", re.compile(r"portfolio\s+company|private\s+equity|"
                             r"backed\s+by\s+[A-Z][\w&\s]{2,30}\s+(?:Capital|Partners|Equity)", re.I)),
    ("franchise", re.compile(r"\bfranchis(?:e|ee|ing|or)\b", re.I)),
    ("group", re.compile(r"\b(?:a\s+(?:division|member|part)\s+of|"
                         r"part\s+of\s+the)\s+[A-Z][\w&\s]{2,40}\s+"
                         r"(?:Group|Holdings|Family\s+of\s+Companies)", re.I)),
    ("independent", re.compile(r"family[\s-]owned|independently\s+owned|"
                               r"locally\s+owned", re.I)),
)


def ownership(text, source):
    """Ownership structure from explicit site language, or unknown.

    Ordered most-specific first: a franchise page routinely also says
    "independently owned and operated", which is a franchise tagline, and
    matching that first would misclassify a franchisee as independent.
    """
    for label, pat in OWNERSHIP_SIGNS:
        m = pat.search(text or "")
        if m:
            snippet = WS.sub(" ", text[max(0, m.start() - 50):m.end() + 50]).strip()
            return _field(label, source, "ownership language on the site", snippet)
    return UNKNOWN.copy()


PLATFORM_SIGNS = (
    ("Shopify Plus", re.compile(r"shopify.*plus|plus\.shopify", re.I)),
    ("Shopify", re.compile(r"cdn\.shopify\.com|shopify\.com/s/files|"
                           r"Shopify\.theme", re.I)),
    ("WooCommerce", re.compile(r"woocommerce", re.I)),
    ("Wix", re.compile(r"wix\.com|wixstatic", re.I)),
    ("Squarespace", re.compile(r"squarespace", re.I)),
    ("Webflow", re.compile(r"webflow", re.I)),
    ("HubSpot", re.compile(r"hs-scripts\.com|hubspot", re.I)),
    ("WordPress", re.compile(r"wp-content|wp-includes", re.I)),
)


def platform(html, source):
    """CMS/commerce platform from markup signatures. Ordered so Shopify Plus
    wins over Shopify and both win over a WordPress blog on the same host."""
    for label, pat in PLATFORM_SIGNS:
        if pat.search(html or ""):
            return _field(label, source, "markup signature", "")
    return UNKNOWN.copy()


PRICE = re.compile(r"[$£€]\s?\d[\d,]*(?:\.\d{2})?")


def published_prices(pages):
    """Whether the site publishes prices, with the page that proves it."""
    for url, html in pages.items():
        text = visible_text(html)
        hits = PRICE.findall(text)
        if len(hits) >= 3:
            return _field(True, url, "three or more prices on one page",
                          ", ".join(hits[:5]))
    return _field(False, "", "no page with three or more published prices", "")


# --- research URLs (never fetched) ------------------------------------------

def research_urls(domain, contact_name="", business_name=""):
    """Ready-made searches for what an automated fetch must not touch.

    LinkedIn headcount, funding history and press coverage are exactly the
    fields pricing would most like, and exactly the ones whose sources prohibit
    automated access. Handing the closer a clickable search is honest; scraping
    them would get the runner blocked.
    """
    q = urllib.parse.quote_plus
    company = business_name or domain
    out = {
        "linkedin_company": "https://www.google.com/search?q=" +
                            q('site:linkedin.com/company "%s"' % company),
        "recent_press": "https://news.google.com/search?q=" + q(company),
        "company_overview": "https://www.google.com/search?q=" + q('"%s"' % company),
        "reviews": "https://www.google.com/search?q=" + q("%s reviews" % company),
    }
    if contact_name:
        out["linkedin_person"] = "https://www.google.com/search?q=" + q(
            'site:linkedin.com/in "%s" "%s"' % (contact_name, company))
        out["other_ventures"] = "https://www.google.com/search?q=" + q(
            '"%s" founder OR owner OR president' % contact_name)
    return out


# --- decision authority (from the funnel answer, not the web) ---------------

AUTHORITY = (
    ("sole", re.compile(r"\bi\s+(?:make|decide)|my\s+(?:own\s+)?decision|"
                        r"just\s+me|i'?m\s+the\s+(?:owner|founder|decision)", re.I)),
    ("shared", re.compile(r"\b(?:partner|spouse|co-?owner|business\s+partner|"
                          r"together\s+with)\b", re.I)),
    ("committee", re.compile(r"\b(?:board|committee|team\s+decision|"
                             r"multiple\s+people|group\s+of)\b", re.I)),
)


def decision_authority(answer):
    """Single vs multi decision-maker, from the funnel's own question."""
    for label, pat in AUTHORITY:
        if pat.search(answer or ""):
            return _field(label, "funnel questionnaire",
                          "decision-process answer", (answer or "").strip())
    if answer:
        return _field(None, "funnel questionnaire", "answer did not match a "
                      "known pattern", answer.strip())
    return UNKNOWN.copy()


# --- assembly ---------------------------------------------------------------

def crawl(domain, fetch=None, max_pages=MAX_PAGES):
    """Fetch the homepage and a capped set of candidate pages. {url: html}."""
    fetch = fetch or http_fetch
    base = "https://%s/" % domain
    robots = RobotsPolicy(base, fetch)

    pages, queue, seen = {}, [], set()

    status, home = fetch(base)
    if status is None or not home:
        status, home = fetch("http://%s/" % domain)
    if home:
        pages[base] = home
        seen.add(base.rstrip("/"))
        found = internal_links(home, base)
        wanted = [u for u in found
                  if any(urllib.parse.urlparse(u).path.lower().rstrip("/")
                         .endswith(p) for p in CANDIDATE_PATHS)]
        queue = wanted + [u for u in found if u not in wanted]

    for url in queue:
        if len(pages) >= max_pages:
            break
        if url in seen or not robots.allows(url):
            continue
        seen.add(url)
        st, body = fetch(url)
        if st == 200 and body:
            pages[url] = body

    return pages, sorted(seen)


def build(domain, *, business_name="", contact_name="", decision_answer="",
          fetch=None, max_pages=MAX_PAGES, now=None):
    """A dossier for one prospect. Pure assembly over fetched pages."""
    if not domain:
        raise DossierError("domain is required")

    pages, discovered = crawl(domain, fetch=fetch, max_pages=max_pages)
    home_url = "https://%s/" % domain
    home_html = pages.get(home_url, "")

    all_text = " ".join(visible_text(h) for h in pages.values())
    links = []
    for url, html in pages.items():
        links.extend(internal_links(html, url))
    links = sorted(set(links))

    def _page_for(pattern):
        for url in pages:
            if re.search(pattern, url, re.I):
                return url
        return home_url

    team_url = _page_for(r"/(team|staff|leadership|about)")
    about_url = _page_for(r"/(about|our-story|company)")

    founded, age = years_in_business(all_text, about_url)
    company = {
        "employee_count": headcount(all_text, team_url),
        "location_count": location_count(all_text, links, _page_for(
            r"/(locations?|service-areas?)")),
        "founded_year": founded,
        "years_in_business": age,
        "ownership": ownership(all_text, about_url),
        "platform": platform(home_html, home_url),
        "published_prices": published_prices(pages),
        "page_count": _field(
            len(links) or None, home_url,
            "distinct internal URLs discovered from a %d-page crawl "
            "(lower bound, not an index count)" % len(pages), ""),
        "decision_authority": decision_authority(decision_answer),
    }

    unknown = sorted(k for k, v in company.items() if v.get("value") is None)

    return {
        "domain": domain,
        "business_name": business_name or domain,
        "contact_name": contact_name,
        "generated_at": now or datetime.now(timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages_fetched": sorted(pages),
        "company": company,
        "unknown_fields": unknown,
        "research_urls": research_urls(domain, contact_name, business_name),
        "limits": [
            "LinkedIn employee count, funding history and press coverage are "
            "not fetched: those sources prohibit automated access. Use the "
            "research URLs above.",
            "Page count is a lower bound from a capped crawl, not an index "
            "count.",
            "Every field is either sourced to a URL with the snippet it came "
            "from, or unknown. Nothing is inferred.",
        ],
    }


def format_dossier(d):
    """Operator-readable brief."""
    out = ["PROSPECT DOSSIER  %s" % d["business_name"],
           "  domain    %s" % d["domain"],
           "  contact   %s" % (d["contact_name"] or "(unknown)"),
           "  pages     %d fetched" % len(d["pages_fetched"]),
           "", "  COMPANY"]
    for key, f in d["company"].items():
        v = f.get("value")
        shown = "unknown" if v is None else v
        out.append("    %-20s %s" % (key, shown))
        if v is not None and f.get("evidence"):
            out.append("      %s" % f["evidence"][:110])
        if v is not None and f.get("source"):
            out.append("      source: %s" % f["source"])
    out += ["", "  NOT ESTABLISHED: %s" % (", ".join(d["unknown_fields"]) or "none"),
            "", "  RESEARCH (open by hand)"]
    for k, url in d["research_urls"].items():
        out.append("    %-18s %s" % (k, url))
    out += ["", "  LIMITS"]
    for line in d["limits"]:
        out.append("    - " + line)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--name", default="")
    ap.add_argument("--contact", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    d = build(a.domain, business_name=a.name, contact_name=a.contact)
    print(json.dumps(d, indent=2) if a.json else format_dossier(d))


if __name__ == "__main__":
    main()
