"""Site Explorer project lifecycle and warm metric extraction.

Most of the audit's credibility figures (authority, trust, native position
buckets, competitors, paid) exist only once a domain has a Site Explorer
project. Creating one is the single authorised write in this pipeline, so it is
dry-run by default and never fires when a project already exists.
"""

import re

SERVICE = "keyword"
COLLECTION = "/api/v2/competitor-research/"


class WarmError(Exception):
    pass


def _host(url):
    """Bare lowercase host from a project url, for comparing against a domain."""
    if not url:
        return ""
    h = re.sub(r"^https?://", "", str(url).strip().lower())
    h = h.split("/")[0].split("?")[0]
    return re.sub(r"^w{2,}\.", "", h)


def _num(d, *names):
    for n in names:
        v = d.get(n)
        if v is not None:
            return v
    return None


def _at(payload, *dotted_paths):
    """First non-null value among dotted paths. None if none resolve.

    Warm metrics are NESTED, verified against a real recording: domain_rating
    sits at data.domain_rating while trust_flow sits at
    data.competitor_research.trust_flow. Reading them top-level silently
    returns None, which would drop real figures out of the report with no error.
    Numeric strings are coerced, because some of these fields come back as
    strings (referring_domain_type_direct is '896', not 896).
    """
    for path in dotted_paths:
        node = payload
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is None:
            continue
        if isinstance(node, str):
            try:
                node = float(node) if "." in node else int(node)
            except ValueError:
                continue
        if isinstance(node, (int, float)):
            return node
    return None


FIND_PROJECT_MAX_PAGES = 20


def find_project(sa, domain):
    """Existing Site Explorer project id for a domain, or None. Read-only.

    Rejects an empty target up front: `_host("")`, `_host(None)`, and
    `_host` of a row missing its url all collapse to the same empty string,
    so if this were allowed through, an empty or `None` domain (which
    `leads.normalize_domain` returns by design for social-only and blank-
    website leads) would false-match the first unidentifiable row and its
    figures would land in the result as if they belonged to this prospect.

    Paginates explicitly with a `page` param (there is no recorded fixture
    for this search endpoint, so its pagination shape is unverified; an
    explicit page counter needs no assumption about a `next`/cursor field
    the response may or may not carry). Walks up to
    `FIND_PROJECT_MAX_PAGES` pages, stopping as soon as a page comes back
    with fewer than `page_size` rows (the last page) or a match is found.

    A row whose extracted host is empty (missing/None `metrics`, or
    `metrics` without a `url`) is not compared against the target at all -
    it is skipped, and remembered. Such a row is NOT evidence the project is
    absent (it is most likely a freshly created, not-yet-crawled project
    whose `metrics` hasn't populated yet); treating it as a definitive
    non-match and returning None would let `ensure_project` create a
    duplicate, paid project. If the search returned at least one row that
    could not be identified and none of the readable rows matched, this
    raises `WarmError` instead of returning None, naming the ambiguity
    rather than silently resolving it into a write.

    If the page cap is hit without a match or reaching the end of results -
    i.e. every single page was full and none matched - that is likewise NOT
    reported as "no project exists": it also raises `WarmError`.
    """
    target = _host(domain)
    if not target:
        raise ValueError(
            "find_project(%r): domain has no readable host - refusing to "
            "search for a project against an empty target" % (domain,))
    page_size = 50
    any_unidentifiable = False
    for page_num in range(1, FIND_PROJECT_MAX_PAGES + 1):
        r = sa.get(SERVICE, COLLECTION,
                   params={"search": domain, "page": page_num,
                           "page_size": page_size}) or {}
        results = r.get("results") or []
        for row in results:
            if not isinstance(row, dict):
                continue
            # Verified against the live search endpoint: the real row shape
            # carries the domain at row.metrics.url (a BARE domain, no
            # scheme - e.g. "getpetermd.com"), not row.url or row.domain_url.
            # Neither of those top-level keys exists on a real row; reading
            # them silently returned None for every row, so find_project
            # always reported "not found" and ensure_project would create a
            # duplicate paid project on every run. metrics is read first;
            # the old top-level keys are kept as fallbacks in case the shape
            # varies by endpoint or a future capture.
            url = ((row.get("metrics") or {}).get("url")
                   or row.get("url") or row.get("domain_url"))
            host = _host(url)
            if not host:
                # Unreadable row: not a match, and not proof of absence
                # either - skip it, but remember it so a clean "not found"
                # on this or a later page cannot be reported.
                any_unidentifiable = True
                continue
            if host == target:
                pid = row.get("id")
                if pid is not None:
                    return int(pid)
        if len(results) < page_size:
            if any_unidentifiable:
                raise WarmError(
                    "find_project(%r): search returned at least one row "
                    "with no readable url and no row positively matched - "
                    "refusing to report 'not found', since that ambiguity "
                    "could trigger a duplicate project create" % (domain,))
            return None
    else:
        raise WarmError(
            "find_project(%r): hit the %d-page cap without finding a match "
            "or reaching the end of results - refusing to report 'not "
            "found', since that could trigger a duplicate project create" %
            (domain, FIND_PROJECT_MAX_PAGES))


def _recheck_after_post(sa, domain):
    """Re-derive the outcome of a POST whose response could not be trusted -
    either an exception, or a 200 body with no usable `id`. Never guesses:
    only a positive `find_project` match is reported as "created"; anything
    else that cannot positively confirm the project exists is "failed"
    (recheck ran cleanly and found nothing) or "unknown" (the recheck itself
    could not tell)."""
    try:
        recheck = find_project(sa, domain)
    except Exception:
        return None, "unknown"
    if recheck is not None:
        return recheck, "created"
    return None, "failed"


def ensure_project(sa, domain, country_code="US", apply=False):
    """Return (project_id, action).

    action is "found" (reused), "created", "would-create" (dry run),
    "failed" (POST completed but returned no usable id, or definitively did
    not create one), or "unknown" (a POST exception occurred and the
    follow-up re-check could not determine whether it landed - a human must
    look before any retry). The POST is the only write in this pipeline: it
    fires only when no project exists AND apply is True.

    Rejects an empty target domain up front, same reasoning as
    `find_project`: an empty/None domain must never reach a search or a
    POST body.
    """
    if not _host(domain):
        raise ValueError(
            "ensure_project(%r): domain has no readable host - refusing to "
            "search for or create a project against an empty target" %
            (domain,))
    existing = find_project(sa, domain)
    if existing is not None:
        return existing, "found"
    if not apply:
        return None, "would-create"
    body = {"url": "https://%s" % _host(domain), "country_code": country_code}
    # Real client signature is post(service, path, json_body=None, params=None).
    try:
        r = sa.post(SERVICE, COLLECTION, json_body=body)
    except Exception:
        # The POST may have landed server-side even though the exception
        # means no confirmed response came back (timeout, connection reset -
        # plausible given the client's 300s timeout). Do NOT guess: a blind
        # retry here could create a second paid project. Re-check instead.
        return _recheck_after_post(sa, domain)
    # Handling stays inside this try/except-free block but still guards
    # against a non-dict body: `r.get("id")` used to sit outside the try and
    # raised AttributeError on a non-dict response, bypassing all of the
    # unknown/failed reasoning below after the write had already happened.
    pid = r.get("id") if isinstance(r, dict) else None
    if pid is not None:
        return int(pid), "created"
    # A 200 with no usable id is NOT the same as a definitively failed
    # create - re-check exactly as the exception path does, rather than
    # returning "failed" (which invites a caller to retry a POST that
    # probably landed).
    return _recheck_after_post(sa, domain)


def position_buckets(payload):
    """Report buckets from native ones.

    Native granularity is 1-3 / 4-10 / 11-20 / 21-30 / 31-40 / 41-50 / 51-100 /
    100+. The report's 21-50 row is the sum of the three middle buckets, which
    is an aggregation of real values rather than an estimate. A bucket with no
    contributing value at all stays None so its row can be omitted.
    """
    p = payload if isinstance(payload, dict) else {}

    def g(name):
        key = "organic_keywords_" + name
        return _at(p, key,
                   "data." + key,
                   "data.competitor_research." + key)

    mid = [g("21_to_30"), g("31_to_40"), g("41_to_50")]
    present = [v for v in mid if v is not None]
    return {
        "1-3": g("top_3"),
        "4-10": g("4_to_10"),
        "11-20": g("11_to_20"),
        "21-50": sum(present) if present else None,
        "51-100": g("51_to_100"),
        "100_plus": g("100_plus"),
    }


def authority(payload):
    """Link authority and trust. Paths verified against a real recording; the
    top-level spellings are listed last as a fallback, not as the primary."""
    p = payload if isinstance(payload, dict) else {}
    cr = "data.competitor_research."
    return {
        "domain_rating": _at(p, "data.domain_rating", cr + "domain_rating",
                             "domain_rating"),
        "domain_power": _at(p, "data.domain_power", cr + "domain_power",
                            "domain_power"),
        "authority_score": _at(p, cr + "authority_score", "data.authority_score",
                               "authority_score"),
        "spam_score": _at(p, "data.spam_score", cr + "spam_score", "spam_score"),
        "trust_flow": _at(p, cr + "trust_flow", "data.trust_flow", "trust_flow"),
        "citation_flow": _at(p, cr + "citation_flow", "data.citation_flow",
                             "citation_flow"),
    }


def referring_domains(payload):
    """TOTAL referring domains. Warm-only: no cold endpoint carries a count.

    Both spellings exist as separate keys in the same object, with different
    values, and picking the wrong one understates a prospect's link profile by
    roughly 3.4x:

        data.competitor_research.reffering_domains (doubled f) = 3021  <- total
        data.competitor_research.referring_domains             = 896   <- subset

    3021 is corroborated by data.competitor_research.backlinks_trend[-1]
    .total_refdomains = 3021, and 896 is corroborated by
    referring_domain_type_direct = '896', so 896 is the direct subset
    (follow-only is 790). The misspelling is upstream and must NOT be
    "normalised" away. Note data.competitor_research.backlinks.reffering_domains
    is a different thing again - a LIST of per-domain records, not a count.
    """
    p = payload if isinstance(payload, dict) else {}
    cr = "data.competitor_research."
    return _at(p, cr + "reffering_domains", "data.reffering_domains",
               "reffering_domains")


def referring_domains_direct(payload):
    """The smaller direct subset (896 in the reference capture). Kept distinct so
    nobody conflates it with the total above."""
    p = payload if isinstance(payload, dict) else {}
    cr = "data.competitor_research."
    return _at(p, cr + "referring_domains", "data.referring_domains",
               "referring_domains")


def backlink_count(payload):
    """Total backlinks from the warm project detail, which is richer and more
    trustworthy than the cold total_count."""
    p = payload if isinstance(payload, dict) else {}
    cr = "data.competitor_research."
    return _at(p, cr + "backlink_count", "data.backlink_count", "backlink_count")


def overview(*payloads):
    """Domain-level traffic overview. Warm-only (see sacold.py docstring).

    Accepts one or more payloads and, for each of the three metrics, returns
    the first non-None value found across them in argument order. collect.py
    fetches two warm payloads for this overview - `organic` and
    `project_detail` - but `overview`'s only fixture-backed test and its
    original docstring described `organic` alone. Both payloads happened to
    resolve today, so feeding only one in was invisible, but a future
    nesting shift in the untested payload could silently null all three
    headline traffic figures while the real numbers sat unused in the other.
    Passing both and taking whichever resolves closes that gap regardless of
    which payload actually carries the field.

    The real recording (warm_getpetermd_com_organic.json) is FLAT - no data/
    competitor_research nesting - with fields organic_traffic, organic_keywords
    (the ranking-keyword total, NOT keyword_count/keywords_count), and
    organic_traffic_cost. Those plain names are included alongside the nested
    candidates and the more generic synthetic names so both the real capture
    and hand-built payloads resolve correctly.
    """
    cr = "data.competitor_research."
    monthly = keyword_count = value = None
    for payload in payloads:
        p = payload if isinstance(payload, dict) else {}
        if monthly is None:
            monthly = _at(p, cr + "organic_traffic", cr + "traffic", "data.traffic",
                         "traffic", "organic_traffic")
        if keyword_count is None:
            keyword_count = _at(
                p, cr + "keywords_count", cr + "keyword_count",
                "data.keywords_count", "keyword_count", "keywords_count",
                "organic_keywords")
        if value is None:
            value = _at(
                p, cr + "organic_traffic_cost", cr + "traffic_cost",
                "data.organic_traffic_cost", "organic_traffic_cost",
                "traffic_cost")
    return {
        "monthly_organic_visits": monthly,
        "ranking_keyword_count": keyword_count,
        "traffic_value_usd": value,
    }


def anchors(payload):
    p = payload if isinstance(payload, dict) else {}
    out = []
    for r in p.get("results") or []:
        if not isinstance(r, dict) or not r.get("anchor"):
            continue
        out.append({"anchor": r["anchor"],
                    "count": _num(r, "backlinks_num", "domains_num")})
    out.sort(key=lambda x: (x["count"] is None, -(x["count"] or 0)))
    return out


def competitors(payload):
    p = payload if isinstance(payload, dict) else {}
    out = []
    for r in p.get("results") or []:
        if not isinstance(r, dict):
            continue
        dom = r.get("competitor") or r.get("domain")
        if not dom:
            continue
        out.append({
            "domain": dom,
            "monthly_visits": _num(r, "competitor_traffic", "traffic"),
            "ranking_keywords": _num(r, "competitor_keywords", "keyword_count"),
        })
    return out
