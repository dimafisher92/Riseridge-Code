"""Build a provenance-stamped evidence file for a prospect domain.

Assembly (`build_evidence`) is pure and separate from fetching so the whole
thing is testable offline against recorded fixtures. Fetching is split cold
versus warm because warm data requires creating a Site Explorer project, which
is the single authorised write in this pipeline and is dry-run by default.

A metric that came back absent is written as a null value, never as zero. The
renderer omits its section, which is the whole point: these numbers get read
aloud to a prospect.

Usage:
    python collect.py trtnation.com --name "TRT Nation"
    python collect.py getpetermd.com --name PeterMD --apply
"""

import argparse
import datetime
import json
import os
import time

import derive
import sacold
import sa_client
import sawarm

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state", "prospects")

COLD_SOURCE = "searchatlas:competitor-research (cold)"
COLD_NOT_READY_SOURCE = "searchatlas:competitor-research (cold, not ready yet)"
WARM_SOURCE = "searchatlas:competitor-research (site explorer project)"

NOT_READY_MAX_RETRIES = 4


def _get_ready(sa, service, path, params=None, max_retries=NOT_READY_MAX_RETRIES):
    """GET, honouring the async not-ready contract.

    The organic-keywords endpoint answers {"results": [], "total_count": 0,
    "should_retry": true} before it is warm. Reading that as real data puts
    "0 keywords" in front of a prospect as fact, so retry, and if it is still
    not ready at the cap return the payload with should_retry intact so the
    caller can refuse to derive figures from it.
    """
    payload = None
    for attempt in range(max_retries + 1):
        payload = sa.get(service, path, params=params or {})
        if not isinstance(payload, dict) or not payload.get("should_retry"):
            return payload
        if attempt == max_retries:
            return payload
        time.sleep(min(payload.get("retry_after") or 5, 30))
    return payload


def _usable(payload):
    """False when the endpoint said it was still warming up. Deriving a
    figure from a not-ready payload would print 0 as fact."""
    return not (isinstance(payload, dict) and payload.get("should_retry"))


def _sample_basis_source(n):
    """Shared source string for any figure derived from the page-capped cold
    organic-keywords sample (up to 100 rows) rather than the full corpus.
    Both position buckets and the brand/non-brand split are computed from
    this same sample, and ranking_keyword_count reports the domain's true
    total, so silently labelling either figure "derived from ranking-keyword
    rows" (no count) would let a report show sample-based numbers beside a
    stated total of thousands with no signal that the two are not the same
    basis."""
    return ("derived from the top %d ranking-keyword rows "
            "(sample, not a full-corpus distribution)" % n)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _m(value, source, pulled_at):
    """Wrap one metric with its provenance. A None value is preserved."""
    return {"value": value, "source": source, "pulled_at": pulled_at}


# Measured on getpetermd.com: buried non-branded keywords above the volume floor
# number 2 at one page, 19 at two, 90 at five. Rows come back traffic-sorted, so
# the first page holds the keywords already ranking WELL -- the buried money terms
# the report exists to surface only appear deeper. Five pages fills the table.
KEYWORD_PAGES = 5
KEYWORD_PAGE_SIZE = 100


def _fetch_keyword_pages(sa, domain):
    """Walk up to KEYWORD_PAGES of organic keywords into one merged payload.

    total_count stays the DOMAIN total, not the row count, so the report can say
    "3,846 ranking keywords" while disclosing that the distribution beside it is
    a sample of the rows actually fetched.
    """
    rows, total, not_ready = [], None, False
    for page in range(1, KEYWORD_PAGES + 1):
        p = _get_ready(sa, "keyword",
                       "/api/v2/competitor-research/organic-keywords/",
                       params={"target": domain, "page": page,
                               "page_size": KEYWORD_PAGE_SIZE})
        if not isinstance(p, dict):
            break
        if p.get("should_retry"):
            # Only a not-ready FIRST page makes the corpus unusable; a later one
            # just ends the walk with what we already have.
            if page == 1:
                not_ready = True
            break
        got = p.get("results") or []
        if total is None:
            total = p.get("total_count")
        rows.extend(got)
        if len(got) < KEYWORD_PAGE_SIZE:
            break
    merged = {"results": rows, "total_count": total}
    if not_ready:
        merged["should_retry"] = True
    return merged


def fetch_cold(sa, domain):
    """Read-only, works for any domain."""
    out = {}
    out["organic_keywords"] = _fetch_keyword_pages(sa, domain)
    out["backlinks"] = _get_ready(
        sa, "keyword", "/api/v2/competitor-research/backlinks/",
        params={"target": domain})
    out["brand_signal"] = _get_ready(
        sa, "keyword", "/api/v4/brand-signal-score/retrieve",
        params={"domains": domain})
    return out


def fetch_warm(sa, project_id):
    """Requires an existing Site Explorer project. Read-only."""
    base = "/api/v2/competitor-research/%d/" % project_id
    out = {"project_detail": _get_ready(sa, "keyword", base),
           "organic": _get_ready(sa, "keyword", base + "data-extended/",
                                 params={"context": "organic"})}
    for ctx in ("anchors", "refdomains", "organic_competitors"):
        out[ctx] = _get_ready(sa, "keyword", base + "view-more/",
                              params={"context": ctx})
    return out


def build_evidence(cold, warm, domain, business_name, generated_at,
                   project_id=None):
    """Pure assembly. No I/O, no network."""
    at = generated_at
    ok_payload = cold.get("organic_keywords") or {}
    bl_payload = cold.get("backlinks") or {}
    ok_usable = _usable(ok_payload)
    bl_usable = _usable(bl_payload)
    rows = sacold.keyword_rows(ok_payload)
    # collect.py fetches two warm payloads for the headline overview -
    # organic and project_detail. overview() takes both and returns whichever
    # resolves first per field, so a future nesting shift in one payload
    # cannot silently null all three traffic figures while the real numbers
    # sit unused in the other.
    ov = sawarm.overview(warm.get("organic") or {}, warm.get("project_detail") or {})
    auth = sawarm.authority(warm.get("project_detail") or {})
    native = sawarm.position_buckets(warm.get("organic") or {})
    bl = (sacold.backlink_totals(bl_payload) if bl_usable
          else {"total_backlinks": None, "referring_domains": None})
    # Referring domains and the richer backlink total are warm-only; cold
    # carries neither. Prefer warm, fall back to the cold total_count.
    wpd = warm.get("project_detail") or {}
    refs = sawarm.referring_domains(wpd)              # total, 3021 in reference
    refs_direct = sawarm.referring_domains_direct(wpd)  # direct subset, 896
    total_bl = sawarm.backlink_count(wpd)
    if total_bl is None:
        total_bl = bl["total_backlinks"]
        total_bl_src = COLD_SOURCE if bl_usable else COLD_NOT_READY_SOURCE
    else:
        total_bl_src = WARM_SOURCE
    # NOTE: derive.brand_token_set, not sa_client.brand_tokens. The latter
    # emits bare words out of a multi-word business name ("transport" out of
    # "ASAP Transport Solutions"), which would wrongly exclude real money
    # keywords like "window replacement cost" from the report.
    tokens = derive.brand_token_set(domain, business_name)

    # Traffic and traffic value are WARM-ONLY by operator decision (2026-08-05).
    # A cold domain exposes no domain total, only a per-keyword share, and
    # scaling one row up by its share can be wildly wrong if that row's share is
    # small and rounded. These figures get read aloud to prospects, so when the
    # warm figure is absent the metric stays null and its tile is omitted.
    visits = ov["monthly_organic_visits"]
    visits_src = WARM_SOURCE

    kw_count = ov["ranking_keyword_count"]
    kw_src = WARM_SOURCE
    if kw_count is None:
        if ok_usable:
            kw_count = sacold.total_keywords(ok_payload)
            kw_src = COLD_SOURCE
        else:
            kw_count = None
            kw_src = COLD_NOT_READY_SOURCE

    value = ov["traffic_value_usd"]
    value_src = WARM_SOURCE

    # Native buckets when the domain is warm, otherwise counted from rows.
    # sample_rows stays None for native (warm) buckets — they cover the full
    # ranked-keyword corpus, not a sample — and is set to the row count only
    # when the derived (cold, page-capped) path is used, so the renderer can
    # tell a sample from a census without parsing the source string.
    if any(v is not None for v in native.values()):
        buckets, bucket_src, sample_rows = native, WARM_SOURCE, None
    elif rows:
        buckets = derive.bucket_rows(rows)
        bucket_src = _sample_basis_source(len(rows))
        sample_rows = len(rows)
    else:
        buckets, bucket_src, sample_rows = {k: None for k in native}, COLD_SOURCE, None

    # brand_split is derived from the same page-capped row sample as the
    # cold position buckets above, so its source string names the same
    # sample size rather than the bare, count-less constant that used to sit
    # here — see _sample_basis_source. `classified` (not len(rows)) is the
    # number of rows brand_split actually looked at.
    split = derive.brand_split(rows, domain, business_name)
    split_src = _sample_basis_source(split["classified"])

    ev = {
        "domain": domain,
        "business_name": business_name,
        "generated_at": at,
        "searchatlas_project_id": project_id,
        "traffic": {
            "monthly_organic_visits": _m(visits, visits_src, at),
            "ranking_keyword_count": _m(kw_count, kw_src, at),
            "traffic_value_usd": _m(value, value_src, at),
        },
        "brand_split": {
            "brand_pct": _m(split["brand_pct"], split_src, at),
            "nonbrand_pct": _m(split["nonbrand_pct"], split_src, at),
            "method": "brand-token match over ranking keywords, "
                      "capitalised-run aware",
            "sample_rows": split["classified"],
        },
        "position_buckets": dict(
            {k: _m(v, bucket_src, at) for k, v in buckets.items()},
            source=bucket_src, pulled_at=at, sample_rows=sample_rows),
        "money_keywords": derive.money_keywords(rows, tokens),
        "backlinks": {
            "total_backlinks": _m(total_bl, total_bl_src, at),
            "referring_domains": _m(refs, WARM_SOURCE, at),
            "referring_domains_direct": _m(refs_direct, WARM_SOURCE, at),
            "authority": dict(_m(auth["domain_rating"], WARM_SOURCE, at),
                              metric_name="Domain Rating"),
            "trust": dict(_m(auth["trust_flow"], WARM_SOURCE, at),
                          metric_name="Trust Flow"),
            "spam_score": _m(auth["spam_score"], WARM_SOURCE, at),
            "top_anchors": sawarm.anchors(warm.get("anchors") or {}),
        },
        "competitors": sawarm.competitors(warm.get("organic_competitors") or {}),
        # Populated by the browser probe in Phase 2c.
        "ai_visibility": {"platforms": []},
        # A site audit needs a crawl budget, so these stay null and the
        # technical section stays absent.
        "technical": {"ai_crawler_access": _m(None, "not collected", at),
                      "structured_data": _m(None, "not collected", at),
                      "core_web_vitals": _m(None, "not collected", at)},
        "paid": {"estimated_monthly_spend_usd": _m(None, "not collected", at),
                 "paid_keywords": [], "landing_pages": []},
        "scorecard": {},
    }
    return ev


def run(domain, business_name, *, apply=False, sa=None):
    """Fetch and assemble. Returns (evidence, project action).

    `apply` and `sa` are keyword-only on purpose. `run(domain, name, my_sa)`
    is an easy mistake to make — `sa` reads as the natural third positional
    argument — and binding a truthy client object to a positional `apply`
    would silently flip the dry-run guard on and let the one write in this
    pipeline (creating a paid Site Explorer project) fire unintentionally.
    Keyword-only turns that mistake into a TypeError at the call site.

    Validates `domain` up front, before touching the network or the
    filesystem: `run` is public and later phases call it directly, so the
    guard that used to live only in `write_evidence` (much later in the
    pipeline) is applied here too, at the layer that actually needs it.
    """
    _safe_domain_component(domain)
    sa = sa or sa_client.SearchAtlas()

    existing_id = _existing_project_id(domain)
    if existing_id is not None:
        # A prospect already collected once recorded its project id in its
        # evidence file. Reusing it here means a re-run never re-derives the
        # id through find_project (and, on the old code path, never risks
        # ensure_project concluding "no project exists" and creating a
        # duplicate paid one) - see Finding 2 route (a).
        project_id, action = existing_id, "reused"
    else:
        project_id, action = sawarm.ensure_project(sa, domain, apply=apply)
    cold = fetch_cold(sa, domain)
    warm = fetch_warm(sa, project_id) if project_id else {}
    ev = build_evidence(cold, warm, domain, business_name, _now(),
                        project_id=project_id)
    return ev, action


def _existing_project_id(domain):
    """Project id already recorded for `domain`, or None.

    Read before calling `ensure_project` so a prospect collected once is
    never re-searched for, and — the point of this check — never
    re-created. `write_evidence` writes `searchatlas_project_id` but until
    now nothing ever read it back, so every run re-derived (or risked
    re-creating) the project from scratch.
    """
    p = os.path.join(STATE, domain, "evidence.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data.get("searchatlas_project_id")


def _safe_domain_component(domain):
    """Validate `domain` is safe to use as a single path segment.

    Rejects empty, ".", "..", and anything containing a path separator, so a
    malformed or malicious domain can never write outside
    `state/prospects/<domain>/` or collapse into the shared prospects root.
    Both separators are rejected on every platform. `os.path.basename` only
    splits on the platform's own separator, so on Linux it happily accepts
    "a\\b" — and this pipeline runs on Linux runners while the evidence it
    writes is read on Windows, where that same string is a path. A domain is
    never allowed to contain either character, so checking both costs nothing
    and removes the platform from the answer. A bare ".." survives basename
    (basename("..") is ".."), so the dot-segments are checked explicitly.
    """
    if (not domain or domain in (".", "..")
            or "/" in domain or "\\" in domain
            or os.path.basename(domain) != domain):
        raise ValueError("unsafe domain: %r" % (domain,))
    return domain


def write_evidence(evidence_dict, *, base=None):
    domain = _safe_domain_component(evidence_dict["domain"])
    d = os.path.join(base or STATE, domain)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "evidence.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(evidence_dict, fh, indent=1)
    return p


def main():
    import evidence as ev_mod

    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--name", required=True, help="business name for the report")
    ap.add_argument("--apply", action="store_true",
                    help="allow creating a Site Explorer project (the one write)")
    a = ap.parse_args()

    ev, action = run(a.domain, a.name, apply=a.apply)
    p = write_evidence(ev)
    reader = ev_mod.Evidence(ev)
    reader.validate()
    print("project: %s" % action)
    print("written: %s" % p)
    print("sections present: %s" % sorted(reader.present_sections()))
    for path in ("traffic.monthly_organic_visits", "traffic.ranking_keyword_count",
                 "traffic.traffic_value_usd", "brand_split.brand_pct",
                 "backlinks.total_backlinks", "backlinks.authority",
                 "backlinks.trust"):
        print("  %-36s %s" % (path, reader.get(path)))
    print("money keywords: %d" % len(ev["money_keywords"]))


if __name__ == "__main__":
    main()
