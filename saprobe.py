"""Capture live SearchAtlas responses into committed fixtures.

Everything downstream parses these recordings rather than the live API, for
three reasons: field names get verified against reality instead of recalled,
the test suite runs offline and deterministically, and the figures are volatile
enough (traffic halved between two reads 20 minutes apart) that a snapshot is
the only honest basis for a document quoted on a sales call.

Read-only. Creating a Site Explorer project is a write and lives in sawarm.py.

Usage:
    python saprobe.py --domain trtnation.com            # cold endpoints
    python saprobe.py --domain getpetermd.com --warm-id 824060
"""

import argparse
import datetime
import json
import os
import re
import time

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "api")

# The async-warm contract on organic-keywords.
DEFAULT_MAX_RETRIES = 4


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def capture(sa, name, service, path, params=None, trim=None,
            max_retries=DEFAULT_MAX_RETRIES, domain=None):
    """Fetch one endpoint and record it. Returns the payload.

    Retries while the response carries `should_retry`, because organic-keywords
    is async-warmed and answers an empty list before it is ready.

    `domain` is recorded in provenance only (not sent to the API) so a
    fixture is self-identifying even if its filename is domain-independent
    (e.g. the keyword-details lookup).
    """
    payload = None
    for attempt in range(max_retries + 1):
        payload = sa.get(service, path, params=params or {})
        if not isinstance(payload, dict) or not payload.get("should_retry"):
            break
        if attempt == max_retries:
            break
        time.sleep(min(payload.get("retry_after") or 5, 30))

    if trim and isinstance(payload, dict) and isinstance(payload.get("results"), list):
        payload = dict(payload)
        payload["results"] = payload["results"][:trim]

    os.makedirs(FIXTURES, exist_ok=True)
    with open(os.path.join(FIXTURES, name + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"service": service, "path": path, "params": params or {},
                   "domain": domain, "captured_at": _now(), "payload": payload},
                  fh, indent=1)
    return payload


def load(name):
    """Return a recorded payload. Raises with regeneration instructions."""
    p = os.path.join(FIXTURES, name + ".json")
    if not os.path.exists(p):
        raise FileNotFoundError(
            "fixture %s missing. Regenerate with: python saprobe.py --domain "
            "<domain> [--warm-id <id>]" % p)
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)["payload"]


def main():
    import sa_client

    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="trtnation.com")
    ap.add_argument("--warm-id", type=int, default=None,
                    help="existing Site Explorer project id for warm endpoints")
    a = ap.parse_args()

    # Raised timeout: the cold backlinks call was observed taking ~2 minutes and
    # the client default is 60s.
    sa = sa_client.SearchAtlas(timeout=300)
    d = a.domain
    # Fixture names are domain-scoped so two consecutive runs against
    # different domains never overwrite each other's cold fixtures.
    slug = re.sub(r"[^a-z0-9]+", "_", d.lower()).strip("_")

    print("cold endpoints for", d)
    capture(sa, "cold_%s_organic_keywords" % slug, "keyword",
            "/api/v2/competitor-research/organic-keywords/",
            params={"target": d, "page": 1, "page_size": 100}, trim=25,
            domain=d)
    capture(sa, "cold_%s_backlinks" % slug, "keyword",
            "/api/v2/competitor-research/backlinks/",
            params={"target": d}, trim=25, domain=d)
    # Domain-independent lookup (fixed query text) -- not slugged, but still
    # stamped with the invoking --domain for provenance.
    capture(sa, "keyword_details_trt_cost", "keyword", "/api/v1/keyword_details",
            params={"query": "trt cost", "country_code": "us"}, domain=d)
    capture(sa, "cold_%s_brand_signal" % slug, "keyword",
            "/api/v4/brand-signal-score/retrieve", params={"domains": d},
            domain=d)

    if a.warm_id:
        print("warm endpoints for project", a.warm_id)
        base = "/api/v2/competitor-research/%d/" % a.warm_id
        capture(sa, "warm_%s_project_detail" % slug, "keyword", base, domain=d)
        capture(sa, "warm_%s_organic" % slug, "keyword", base + "data-extended/",
                params={"context": "organic"}, domain=d)
        for ctx in ("anchors", "refdomains", "organic_competitors"):
            capture(sa, "warm_%s_%s" % (slug, ctx), "keyword",
                    base + "view-more/", params={"context": ctx}, trim=25,
                    domain=d)

    print("fixtures in", FIXTURES)
    for f in sorted(os.listdir(FIXTURES)):
        print("  ", f, "%.1f KB" % (os.path.getsize(
            os.path.join(FIXTURES, f)) / 1024))


if __name__ == "__main__":
    main()
