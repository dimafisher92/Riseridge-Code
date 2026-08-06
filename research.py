"""Company and contact research from the open web.

The funnel's own answers are thin and self-reported, and for the Loom bookings
there are barely any. This searches for the company and the person, follows the
results it is allowed to follow, and returns a synthesised picture rather than
a list of links for someone else to open.

Two boundaries that are not negotiable:

- **LinkedIn, Crunchbase and the review aggregators are never fetched.** They
  prohibit automated access and block the IP of anything that tries. What IS
  used is the search engine's own title and snippet for those results, which
  the engine already published. That distinction matters: reading a search
  result about a LinkedIn page is not scraping LinkedIn.
- **Business-relevant only.** Company, role, tenure, size, products, press,
  reputation. Not personal-life material.

Everything returned carries the source it came from, and anything not
established is absent rather than guessed.
"""

import re
import urllib.parse

import websearch

# Hosts whose search snippets we read but whose pages we never request.
NO_FETCH = frozenset({
    "linkedin.com", "crunchbase.com", "glassdoor.com", "indeed.com",
    "zoominfo.com", "rocketreach.co", "apollo.io", "pitchbook.com",
    "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com",
})

ROLE_WORDS = (
    "founder", "co-founder", "cofounder", "owner", "ceo", "chief executive",
    "managing director", "director", "president", "partner", "principal",
    "head of", "vp ", "vice president", "manager", "proprietor",
)

# A snippet mentioning one of these is about the business, not the person.
PRESS_WORDS = ("launch", "award", "funding", "raise", "acquisition", "expand",
               "partnership", "wins", "named", "opens", "recall", "lawsuit")


def _no_fetch(domain):
    d = (domain or "").lower()
    return any(d == h or d.endswith("." + h) for h in NO_FETCH)


def _snippet_blob(row):
    return " ".join([row.get("title", ""), row.get("snippet", "")])


def find_role(rows, person):
    """The person's role, from search snippets about them.

    Snippets for a professional profile are formatted "Name - Role - Company",
    so the role is readable without ever requesting the profile page.
    """
    if not person:
        return None
    surname = person.strip().split()[-1].lower()
    for row in rows:
        blob = _snippet_blob(row)
        if surname not in blob.lower():
            continue
        for word in ROLE_WORDS:
            m = re.search(r"([^|·\-–—,]{0,40}\b%s\b[^|·\-–—,]{0,40})"
                          % re.escape(word), blob, re.I)
            if m:
                role = re.sub(r"\s+", " ", m.group(1)).strip(" -–—|·,")
                if 2 < len(role) < 80:
                    return {"value": role, "source": row.get("url", ""),
                            "evidence": re.sub(r"\s+", " ", blob)[:200]}
    return None


def _compact(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Below this, a name fragment is not distinctive enough to identify a company.
MIN_NAME = 6


def is_about(row, company, domain):
    """Whether a search result is plausibly about THIS company.

    A live run surfaced Trustpilot, Leafsnap and ConsumerHealthDigest reviews
    of "Clean Nutra" and "Clean Nutraceuticals" as though they belonged to
    CLEAN Nutritionals -- three different businesses with a shared prefix. A
    closer repeating another company's rating on a call is exactly the failure
    this project exists to avoid, so a result has to carry the full compacted
    company name or the prospect's own domain label to count.

    A shared prefix is not a match: "cleannutra" does not contain
    "cleannutritionals".
    """
    blob = _compact(" ".join([row.get("title", ""), row.get("snippet", ""),
                              row.get("url", "")]))
    name = _compact(company)
    if name and len(name) >= MIN_NAME and name in blob:
        return True
    label = _compact((domain or "").split(".")[0])
    if label and len(label) >= MIN_NAME and label in blob:
        return True
    return False


def _dedupe(rows):
    out, seen = [], set()
    for r in rows:
        key = r.get("url") or r.get("domain")
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def company_queries(company, domain):
    """Three, not five. Every query is a network round trip on a shared
    runner, and the marginal query added far less than it cost."""
    return [
        '"%s"' % company,
        "%s reviews" % company,
        "%s news" % company,
    ]


def person_queries(person, company):
    """Two. The first carries the role in its snippet almost every time."""
    return [
        '"%s" "%s"' % (person, company),
        '"%s" linkedin' % person,
    ]


def _classify(rows, own_domain):
    """Split results into the buckets a closer actually cares about."""
    own, press, profiles, reviews, other = [], [], [], [], []
    for r in rows:
        d = r.get("domain", "")
        blob = _snippet_blob(r).lower()
        if own_domain and (d == own_domain or d.endswith("." + own_domain)):
            own.append(r)
        elif _no_fetch(d):
            profiles.append(r)
        elif websearch.is_aggregator(d) or "review" in blob:
            reviews.append(r)
        elif any(w in blob for w in PRESS_WORDS):
            press.append(r)
        else:
            other.append(r)
    return {"own": own, "press": press, "profiles": profiles,
            "reviews": reviews, "other": other}


def run(company, domain, person="", *, search=None, fetch=None, limit=8,
        max_follow=2):
    """Research the company and the contact. Returns a structured picture.

    `max_follow` caps how many result pages are actually requested, so this
    stays cheap and polite. Blocked hosts are never among them.
    """
    search = search or websearch.search
    company_rows, person_rows = [], []

    for q in company_queries(company, domain):
        try:
            company_rows.extend(search(q, limit=limit))
        except Exception:
            continue
    for q in (person_queries(person, company) if person else []):
        try:
            person_rows.extend(search(q, limit=limit))
        except Exception:
            continue

    company_rows = _dedupe(company_rows)
    person_rows = _dedupe(person_rows)

    # Discard results that are about some other business with a similar name.
    # Counted rather than silently dropped, so the dossier can say the search
    # was noisy instead of implying it found nothing.
    seen_total = len(company_rows)
    company_rows = [r for r in company_rows if is_about(r, company, domain)]
    discarded = seen_total - len(company_rows)

    if not company_rows and not person_rows:
        return None

    buckets = _classify(company_rows, domain)

    # Follow a few third-party pages for real detail. Own-site pages are
    # already covered by the dossier crawl, and blocked hosts are excluded.
    followed = []
    if fetch:
        for r in (buckets["press"] + buckets["other"])[:max_follow]:
            if _no_fetch(r.get("domain", "")):
                continue
            try:
                status, body = fetch(r["url"])
            except Exception:
                continue
            if status == 200 and body:
                text = re.sub(r"<[^>]+>", " ",
                              re.sub(r"<(script|style)\b.*?</\1>", " ", body,
                                     flags=re.I | re.S))
                followed.append({
                    "url": r["url"], "domain": r["domain"],
                    "title": r.get("title", ""),
                    "extract": re.sub(r"\s+", " ", text).strip()[:600],
                })

    role = find_role(person_rows, person)

    return {
        "company": company,
        "domain": domain,
        "person": person,
        "queries_run": len(company_queries(company, domain))
                       + (len(person_queries(person, company)) if person else 0),
        "results_seen": len(company_rows) + len(person_rows),
        "results_discarded_as_other_companies": discarded,
        "role": role,
        "press": buckets["press"][:5],
        "profiles": buckets["profiles"][:5],
        "reviews": buckets["reviews"][:5],
        "mentions": buckets["other"][:6],
        "person_results": person_rows[:6],
        "followed": followed,
        "limits": [
            "LinkedIn, Crunchbase and the review aggregators are not fetched -- "
            "they prohibit automated access. Their search snippets are read "
            "instead, which the search engine already published.",
            "Business-relevant information only.",
        ],
    }
