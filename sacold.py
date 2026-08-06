"""Extract metrics from cold (any-domain, read-only) SearchAtlas responses.

Cold endpoints are the only ones that work for a prospect we have never
touched. They yield per-keyword rows, a keyword total, and a raw backlink
total. Everything else — domain-level traffic, traffic value, authority,
trust, competitors, native position buckets, referring-domain counts — is
warm-only.

Domain-level traffic and traffic value are deliberately NOT estimated from
cold data. A cold response carries no domain total, only a per-keyword
`traffic`/`traffic_pct` share; scaling row 0 up by its share only works if
row 0 happens to carry the largest share, which the API does not guarantee.
A tiny-share keyword landing first would amplify rounding noise into a wildly
wrong headline figure, printed in a client-facing PDF and read aloud to a
prospect. Real traffic comes only from the warm project detail — a Site
Explorer project is created for every prospect, so cold is only a fallback
for when warming fails, and losing the estimate costs little next to the
risk of a confidently wrong number.

Every extractor returns None for an absent field. Returning 0 would put a
fabricated figure into a document read aloud to a prospect.
"""


class ColdError(Exception):
    pass


def _num(d, *names):
    """First present, non-null numeric value among `names`. None otherwise."""
    for n in names:
        v = d.get(n)
        if v is not None:
            return v
    return None


def keyword_rows(payload):
    """Normalise organic-keyword rows. Rows without a keyword are dropped."""
    if not isinstance(payload, dict):
        return []
    out = []
    for r in payload.get("results") or []:
        if not isinstance(r, dict):
            continue
        kw = r.get("keyword")
        if not kw:
            continue
        out.append({
            "keyword": kw,
            "volume": _num(r, "search_volume", "volume"),
            "position": _num(r, "position"),
            "cpc": _num(r, "cpc"),
            "difficulty": _num(r, "keyword_difficulty", "difficulty"),
            "traffic": _num(r, "traffic"),
            "traffic_pct": _num(r, "traffic_pct"),
            "traffic_cost": _num(r, "traffic_cost"),
            "traffic_cost_pct": _num(r, "traffic_cost_pct"),
            "url": r.get("ranking_url") or r.get("url"),
        })
    return out


def total_keywords(payload):
    if not isinstance(payload, dict):
        return None
    v = _num(payload, "total_count", "count")
    return int(v) if isinstance(v, (int, float)) else None


def backlink_totals(payload):
    if not isinstance(payload, dict):
        payload = {}
    # Verified against a real recording: the cold backlinks payload has exactly
    # these top-level keys - apply_cr_total_override, enriched, results,
    # total_count. There is NO referring-domain count cold; that field lives at
    # data.competitor_research.referring_domains on the warm project detail and
    # is read by sawarm.referring_domains(). Returning None here is honest.
    total = _num(payload, "total_count", "backlink_count")
    return {
        "total_backlinks": int(total) if isinstance(total, (int, float)) else None,
        "referring_domains": None,
    }


def brand_signal(payload):
    if not isinstance(payload, dict):
        payload = {}
    # The endpoint takes `domains` plural and may answer per-domain.
    if isinstance(payload.get("results"), list) and payload["results"]:
        first = payload["results"][0]
        if isinstance(first, dict):
            payload = first
    return {
        "score": _num(payload, "score", "brand_signal_score"),
        "branded_volume": _num(payload, "branded_search_volume", "branded_volume"),
    }
