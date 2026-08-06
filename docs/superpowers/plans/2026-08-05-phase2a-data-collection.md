# RiseRidge Sales Phase 2a: Data Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a real, provenance-stamped `evidence.json` for any prospect domain, so the report engine built in Phase 1 has something true to render.

**Architecture:** Fetching splits on cold versus warm. Cold endpoints work for any domain and are read-only. Warm endpoints need a Site Explorer project, created by the single authorised write in this whole pipeline. Every live response is captured once into a committed fixture, and all parsing is written and tested against those fixtures, so the suite never touches the network and field names are verified against reality rather than recalled.

**Tech Stack:** Python 3.14 stdlib, `pytest` 9.1.1. The SearchAtlas HTTP client and the brand classifier are imported from `D:\Claude Code\searchatlas\` via a path shim rather than duplicated.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-riseridge-sales-audit-design.md`. Carried findings: `docs/superpowers/phase2-inbox.md`. Read both before starting.
- Python invoked as `python` from `D:\Claude Code\riseridge-sales`. Repo is on `master`, 123 tests passing.
- **A null value omits its section. It is never estimated, interpolated, or invented.** Every metric written to evidence carries `source` and `pulled_at`. A field that could not be fetched is written as `null`, never as a guess or a zero.
- **Exactly one write is permitted in this entire plan:** `POST /api/v2/competitor-research/` to create a Site Explorer project, one per unique normalised domain. Check for an existing project first. Record the project id in the evidence file so a re-run reuses it. No other POST, PATCH or DELETE of any kind.
- **Figures are volatile.** Two reads of the same project 20 minutes apart returned traffic 10,796 then 5,401 and `domain_authority` 54 then `null`. Snapshot once per prospect, stamp `pulled_at`, never re-pull mid-engagement.
- `organic-keywords?target=` is **async-warmed**: it can return `{"results": [], "total_count": 0, "should_retry": true, "retry_after": 10}`. Retry on `should_retry`; never read an empty first response as "no data".
- The cold `backlinks?target=` call took about two minutes. The client's default 60s timeout must be raised for it.
- **`backlink.searchatlas.com` returns 401 on every path.** All backlink data comes from the `keyword` service. Do not attempt that subdomain.
- **`organic-keywords` silently ignores every filter and sort parameter.** Paging is the only option. Never assume a filter took effect.
- Doc bugs, trust these over the published docs: `keyword_details` takes `query` not `keyword`; `brand-signal-score` takes `domains` plural; `data-extended/` needs `context` of only `organic` or `backlinks`.
- Vendor metric names **"Domain Rating" and "Trust Flow" are retained deliberately** (operator decision, 2026-08-05). They are not vendor *tool* names and must not be added to `FORBIDDEN_VENDORS`.
- Tests live in `tests/`, run as `python -m pytest`. No network access in any test.

---

### Task 1: SearchAtlas client shim

**Files:**
- Create: `sa_client.py`
- Create: `tests/test_sa_client.py`

**Interfaces:**
- Consumes: nothing in this repo.
- Produces: `sa_client.SearchAtlas` (the class), `sa_client.SearchAtlasError`, `sa_client.is_branded(raw, tokens)`, `sa_client.brand_tokens(domain, name)`, `sa_client.norm(s)`, and `sa_client.SA_ROOT` (the resolved path). Raises `ImportError` with an actionable message if the sibling project is absent.

Rationale for a shim over duplication: `searchatlas/searchatlas.py` already handles the Cloudflare-1010 User-Agent ban and trailing-slash 301s, and `searchatlas/prune_branded.py` already contains a carefully-reasoned brand classifier whose subtlety would be lost in a rewrite. Verified importable with no side effects.

- [ ] **Step 1: Write the failing test**

`tests/test_sa_client.py`:

```python
import sa_client


def test_exports_searchatlas_class():
    assert hasattr(sa_client.SearchAtlas, "get")
    assert hasattr(sa_client.SearchAtlas, "paginate")


def test_exports_error_type():
    assert issubclass(sa_client.SearchAtlasError, Exception)


def test_brand_tokens_includes_bare_and_suffixed_forms():
    toks = sa_client.brand_tokens("golfcourseprint.com", "Golf Course Print")
    assert "golfcourseprint" in toks


def test_generic_category_phrase_is_not_branded():
    """The hard case: a brand whose name is its own category. A lowercase,
    space-separated generic phrase must NOT be classified as branded."""
    toks = sa_client.brand_tokens("golfcourseprint.com", "Golf Course Print")
    assert sa_client.is_branded("custom golf course prints", toks) is None


def test_contiguous_brand_token_is_branded():
    toks = sa_client.brand_tokens("golfcourseprint.com", "Golf Course Print")
    assert sa_client.is_branded("GolfCoursePrint.com reviews", toks) is not None


def test_capitalised_run_is_branded():
    toks = sa_client.brand_tokens("golfcourseprint.com", "Golf Course Print")
    assert sa_client.is_branded("Golf Course Print pricing", toks) is not None


def test_unrelated_query_is_not_branded():
    toks = sa_client.brand_tokens("getpetermd.com", "PeterMD")
    assert sa_client.is_branded("trt cost", toks) is None


def test_sa_root_points_at_an_existing_directory():
    import os
    assert os.path.isdir(sa_client.SA_ROOT)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sa_client.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'sa_client'`

- [ ] **Step 3: Write minimal implementation**

`sa_client.py`:

```python
"""Bridge to the sibling SearchAtlas toolkit.

Imports rather than duplicates two things worth not rewriting:

  * `searchatlas.SearchAtlas` already handles the Cloudflare-1010 User-Agent ban
    (the default Python-urllib UA is blocked) and the trailing-slash 301s.
  * `prune_branded.is_branded` is a precise brand classifier. The naive check
    (strip spaces, look for the brand token) over-matches when a brand is built
    from generic category words: "custom golf course prints" collapses to a
    string containing "golfcourseprint" but is an unbranded, valuable query.
    Getting that wrong would corrupt the brand/non-brand split, which was the
    single strongest finding in the reference audit.
"""

import os
import sys

SA_ROOT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "searchatlas")

if not os.path.isdir(SA_ROOT):
    raise ImportError(
        "SearchAtlas toolkit not found at %s. This project imports its HTTP "
        "client and brand classifier rather than duplicating them." % SA_ROOT)

if SA_ROOT not in sys.path:
    # APPEND, never insert(0). This repo's own modules must win: the sibling
    # contains sales.py, local.py and registry.py, all plausible names here, and
    # fronting its path would silently shadow ours for the whole process.
    sys.path.append(SA_ROOT)

from searchatlas import SearchAtlas, SearchAtlasError       # noqa: E402
from prune_branded import is_branded, brand_tokens, norm    # noqa: E402

__all__ = ["SearchAtlas", "SearchAtlasError", "is_branded", "brand_tokens",
           "norm", "SA_ROOT"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sa_client.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Confirm the full suite is intact**

Run: `python -m pytest -q`
Expected: 131 passed (123 prior + 8 new)

- [ ] **Step 6: Commit**

```bash
git add sa_client.py tests/test_sa_client.py
git commit -m "feat: bridge to the SearchAtlas client and brand classifier"
```

---

### Task 2: Capture live API fixtures

**Files:**
- Create: `saprobe.py`
- Create: `fixtures/api/` (populated by running the probe)
- Create: `tests/test_saprobe.py`

**Interfaces:**
- Consumes: `sa_client.SearchAtlas` from Task 1.
- Produces: `saprobe.capture(sa, name, service, path, params=None, trim=None) -> dict` which fetches, optionally trims, writes `fixtures/api/<name>.json`, and returns the payload. `saprobe.FIXTURES` (path constant). `saprobe.load(name) -> dict` for tests. CLI: `python saprobe.py [--domain D] [--warm-id N]`.

This task exists to remove guesswork. Every later task parses recorded responses, so field names are verified against reality and the suite runs offline. It performs **no writes** — the warm project id is passed in, not created (project creation is Task 4).

- [ ] **Step 1: Write the failing test**

`tests/test_saprobe.py`:

```python
import json

import pytest

import saprobe


def test_capture_writes_named_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"a": 1}, {"a": 2}], "total_count": 2}

    out = saprobe.capture(FakeSA(), "demo", "keyword", "/x/")
    assert out["total_count"] == 2
    written = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert written["payload"]["total_count"] == 2


def test_capture_records_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))

    class FakeSA:
        def get(self, service, path, params=None):
            return {"ok": True}

    saprobe.capture(FakeSA(), "demo", "keyword", "/x/", params={"target": "d.com"})
    rec = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert rec["service"] == "keyword"
    assert rec["path"] == "/x/"
    assert rec["params"] == {"target": "d.com"}
    assert rec["captured_at"]


def test_capture_trims_result_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"i": i} for i in range(500)], "total_count": 500}

    out = saprobe.capture(FakeSA(), "big", "keyword", "/x/", trim=3)
    assert len(out["results"]) == 3
    assert out["total_count"] == 500, "trimming must not touch the real total"


def test_capture_retries_when_response_says_should_retry(tmp_path, monkeypatch):
    """organic-keywords is async-warmed: an empty first response with
    should_retry must not be read as 'no data'."""
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))
    monkeypatch.setattr(saprobe.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class FakeSA:
        def get(self, service, path, params=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"results": [], "total_count": 0,
                        "should_retry": True, "retry_after": 1}
            return {"results": [{"k": "trt cost"}], "total_count": 1}

    out = saprobe.capture(FakeSA(), "warmed", "keyword", "/x/")
    assert calls["n"] == 2
    assert out["total_count"] == 1


def test_capture_gives_up_after_max_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))
    monkeypatch.setattr(saprobe.time, "sleep", lambda s: None)

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [], "total_count": 0, "should_retry": True,
                    "retry_after": 1}

    out = saprobe.capture(FakeSA(), "never", "keyword", "/x/", max_retries=2)
    assert out["should_retry"] is True, "returns the last response rather than hanging"


def test_load_reads_payload_only(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))
    (tmp_path / "z.json").write_text(json.dumps(
        {"service": "keyword", "path": "/x/", "params": {}, "captured_at": "t",
         "payload": {"hello": "world"}}), encoding="utf-8")
    assert saprobe.load("z") == {"hello": "world"}


def test_load_raises_actionable_error_for_missing_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))
    with pytest.raises(FileNotFoundError) as e:
        saprobe.load("nope")
    assert "saprobe.py" in str(e.value), "error must say how to regenerate it"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_saprobe.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'saprobe'`

- [ ] **Step 3: Write minimal implementation**

`saprobe.py`:

```python
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
import time

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "api")

# The async-warm contract on organic-keywords.
DEFAULT_MAX_RETRIES = 4


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def capture(sa, name, service, path, params=None, trim=None,
            max_retries=DEFAULT_MAX_RETRIES):
    """Fetch one endpoint and record it. Returns the payload.

    Retries while the response carries `should_retry`, because organic-keywords
    is async-warmed and answers an empty list before it is ready.
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
                   "captured_at": _now(), "payload": payload}, fh, indent=1)
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

    print("cold endpoints for", d)
    capture(sa, "cold_organic_keywords", "keyword",
            "/api/v2/competitor-research/organic-keywords/",
            params={"target": d, "page": 1, "page_size": 100}, trim=25)
    capture(sa, "cold_backlinks", "keyword",
            "/api/v2/competitor-research/backlinks/",
            params={"target": d}, trim=25)
    capture(sa, "cold_keyword_details", "keyword", "/api/v1/keyword_details",
            params={"query": "trt cost", "country_code": "us"})
    capture(sa, "cold_brand_signal", "keyword",
            "/api/v4/brand-signal-score/retrieve", params={"domains": d})

    if a.warm_id:
        print("warm endpoints for project", a.warm_id)
        base = "/api/v2/competitor-research/%d/" % a.warm_id
        capture(sa, "warm_project_detail", "keyword", base)
        capture(sa, "warm_organic", "keyword", base + "data-extended/",
                params={"context": "organic"})
        for ctx in ("anchors", "refdomains", "organic_competitors"):
            capture(sa, "warm_" + ctx, "keyword", base + "view-more/",
                    params={"context": ctx}, trim=25)

    print("fixtures in", FIXTURES)
    for f in sorted(os.listdir(FIXTURES)):
        print("  ", f, "%.1f KB" % (os.path.getsize(
            os.path.join(FIXTURES, f)) / 1024))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_saprobe.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Capture the real fixtures**

`main()` already constructs the client with `timeout=300`, because the cold backlinks call was observed taking about two minutes and the client's default is 60s.

Run: `python saprobe.py --domain trtnation.com`
Expected: writes four `cold_*.json` fixtures and prints their sizes.

Then capture warm endpoints against the pre-existing project for `getpetermd.com`:

Run: `python saprobe.py --domain getpetermd.com --warm-id 824060`
Expected: writes `warm_*.json` fixtures too.

If any single capture fails, report the exact error and which fixture is missing. Do NOT fabricate a fixture by hand — the whole point is that these are real recordings.

- [ ] **Step 6: Record the observed response shapes**

Write a short note to `fixtures/api/README.md` listing, for each fixture, the top-level keys and the keys of one representative record. This is the reference later tasks parse against. Derive it from the real files:

```bash
python -c "
import json, os
d='fixtures/api'
for f in sorted(os.listdir(d)):
    if not f.endswith('.json'): continue
    p=json.load(open(os.path.join(d,f),encoding='utf-8'))['payload']
    print('##', f)
    if isinstance(p, dict):
        print('  top:', sorted(p.keys())[:25])
        r=p.get('results')
        if isinstance(r,list) and r and isinstance(r[0],dict):
            print('  record:', sorted(r[0].keys()))
    else:
        print('  type:', type(p).__name__)
"
```

Paste that output into `fixtures/api/README.md` under a heading explaining the fixtures are real recordings regenerated by `saprobe.py`.

- [ ] **Step 7: Commit**

```bash
git add saprobe.py tests/test_saprobe.py fixtures/api/
git commit -m "feat: capture live SearchAtlas responses as offline fixtures"
```

---

### Task 3: Cold metric extraction

**Files:**
- Create: `sacold.py`
- Create: `tests/test_sacold.py`

**Interfaces:**
- Consumes: `saprobe.load(name)` from Task 2.
- Produces:
  - `sacold.keyword_rows(payload) -> list[dict]` with normalised keys `keyword, volume, position, cpc, difficulty, traffic, traffic_pct, traffic_cost, traffic_cost_pct, url`
  - `sacold.total_keywords(payload) -> int | None`
  - (no traffic derivation: operator decision 2026-08-05, see below)
  - `sacold.backlink_totals(payload) -> dict` with keys `total_backlinks, referring_domains`
  - `sacold.brand_signal(payload) -> dict` with keys `score, branded_volume`
  - `sacold.ColdError(Exception)`

Every function must return `None` (or omit the key) rather than guess when the recorded response lacks the field. Read the actual field names from `fixtures/api/README.md` produced in Task 2; the names below are what the spike observed and must be confirmed against the recordings.

- [ ] **Step 1: Write the failing test**

`tests/test_sacold.py`:

```python
import pytest

import sacold
import saprobe


# --- against real recordings -----------------------------------------------

def test_keyword_rows_parse_from_real_fixture():
    rows = sacold.keyword_rows(saprobe.load("cold_organic_keywords"))
    assert rows, "recorded fixture yielded no keyword rows"
    r = rows[0]
    for key in ("keyword", "volume", "position"):
        assert key in r, "missing normalised key %s" % key
    assert isinstance(r["keyword"], str) and r["keyword"]


def test_total_keywords_from_real_fixture():
    n = sacold.total_keywords(saprobe.load("cold_organic_keywords"))
    assert isinstance(n, int) and n > 0


def test_backlink_totals_from_real_fixture():
    """The recorded response carries a real backlink total. If this fails, the
    field name in backlink_totals does not match the recording — fix the
    extractor against fixtures/api/README.md. If the field is genuinely absent
    from the recording, report that rather than relaxing the assertion."""
    t = sacold.backlink_totals(saprobe.load("cold_backlinks"))
    assert isinstance(t["total_backlinks"], int)
    assert t["total_backlinks"] > 0


# --- normalisation and null-safety on synthetic input ----------------------

def test_keyword_rows_normalises_field_names():
    payload = {"results": [{"keyword": "trt cost", "search_volume": 2900,
                            "position": 42, "cpc": 3.5,
                            "keyword_difficulty": 61, "traffic": 12,
                            "traffic_pct": 0.4, "traffic_cost": 40,
                            "traffic_cost_pct": 0.5,
                            "ranking_url": "https://x.com/trt"}]}
    r = sacold.keyword_rows(payload)[0]
    assert r["keyword"] == "trt cost"
    assert r["volume"] == 2900
    assert r["position"] == 42
    assert r["cpc"] == 3.5
    assert r["difficulty"] == 61
    assert r["url"] == "https://x.com/trt"


def test_keyword_rows_returns_empty_for_missing_results():
    assert sacold.keyword_rows({}) == []


def test_keyword_rows_skips_rows_with_no_keyword():
    payload = {"results": [{"search_volume": 10}, {"keyword": "ok"}]}
    rows = sacold.keyword_rows(payload)
    assert [r["keyword"] for r in rows] == ["ok"]


def test_keyword_row_missing_metric_becomes_none_not_zero():
    """A missing volume must be None. Zero would print as a real figure."""
    r = sacold.keyword_rows({"results": [{"keyword": "k"}]})[0]
    assert r["volume"] is None
    assert r["position"] is None


def test_total_keywords_returns_none_when_absent():
    assert sacold.total_keywords({}) is None


# --- traffic is deliberately NOT derived ----------------------------------

def test_cold_does_not_estimate_domain_traffic():
    """Operator decision 2026-08-05: a cold domain exposes no traffic total,
    only a per-keyword share. Scaling one row up by its share can be wildly
    wrong when that share is small and rounded, and these figures are read
    aloud to prospects. Traffic and traffic value are warm-only; absent means
    the tile is omitted. This test fails if anyone reinstates an estimator."""
    assert not hasattr(sacold, "derive_traffic")
    assert not hasattr(sacold, "derive_traffic_value")


# --- backlinks and brand signal -------------------------------------------

def test_backlink_totals_reads_total_count():
    assert sacold.backlink_totals({"total_count": 8775})["total_backlinks"] == 8775


def test_backlink_totals_missing_becomes_none():
    t = sacold.backlink_totals({})
    assert t["total_backlinks"] is None
    assert t["referring_domains"] is None


def test_cold_never_reports_referring_domains():
    """Verified against a real recording: the cold backlinks response carries no
    referring-domain count. It is warm-only, so cold must return None even when
    the payload looks rich."""
    t = sacold.backlink_totals({"total_count": 8775, "results": [{"x": 1}]})
    assert t["total_backlinks"] == 8775
    assert t["referring_domains"] is None


def test_brand_signal_reads_score():
    assert sacold.brand_signal({"score": 49.4})["score"] == 49.4


def test_brand_signal_missing_becomes_none():
    assert sacold.brand_signal({})["score"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sacold.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'sacold'`

- [ ] **Step 3: Write minimal implementation**

`sacold.py`:

```python
"""Extract metrics from cold (any-domain, read-only) SearchAtlas responses.

Cold endpoints are the only ones that work for a prospect we have never
touched. They give per-keyword rows, a keyword total and a raw backlink count.
Everything else is warm-only: authority, trust, competitors, native position
buckets, AND domain traffic plus traffic value.

Traffic is warm-only by deliberate choice, not by API limitation. A cold
response does carry a per-keyword share, so a total could be back-computed --
but scaling one row up by a small, rounded share amplifies quantization noise
into a wildly wrong headline figure, and these numbers are read aloud to
prospects. Absent beats approximate here.

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sacold.py -v`
Expected: PASS. If a "real fixture" test fails, the recorded field names differ from those assumed — read `fixtures/api/README.md`, correct the `_num(...)` name lists in `sacold.py` to match the recording, and re-run. **Do not weaken or delete a fixture-backed assertion to get green**; its whole purpose is to catch exactly this.

- [ ] **Step 5: Commit**

```bash
git add sacold.py tests/test_sacold.py
git commit -m "feat: extract metrics from cold SearchAtlas responses"
```

---

### Task 4: Site Explorer project lifecycle and warm extraction

**Files:**
- Create: `sawarm.py`
- Create: `tests/test_sawarm.py`

**Interfaces:**
- Consumes: `sa_client.SearchAtlas`, `saprobe.load`.
- Produces:
  - `sawarm.find_project(sa, domain) -> int | None` (read-only lookup)
  - `sawarm.ensure_project(sa, domain, country_code="US", apply=False) -> tuple[int | None, str]` returning `(project_id, action)` where action is one of `"found"`, `"created"`, `"would-create"`, `"failed"`
  - `sawarm.position_buckets(payload) -> dict` with keys `1-3, 4-10, 11-20, 21-50, 51-100, 100_plus`
  - `sawarm.authority(payload) -> dict` with keys `domain_rating, domain_power, authority_score, spam_score, trust_flow, citation_flow`
  - `sawarm.overview(payload) -> dict` with keys `monthly_organic_visits, ranking_keyword_count, traffic_value_usd`
  - `sawarm.anchors(payload) -> list[dict]` with keys `anchor, count`
  - `sawarm.competitors(payload) -> list[dict]` with keys `domain, monthly_visits, ranking_keywords`
  - `sawarm.referring_domains(payload) -> int | float | None` (the TOTAL, from the doubled-f `reffering_domains`; warm-only)
  - `sawarm.referring_domains_direct(payload) -> int | float | None` (the smaller `referring_domains` subset)
  - `sawarm.backlink_count(payload) -> int | float | None`
  - `sawarm.WarmError(Exception)`

**Warm metrics are nested.** Verified against a real recording: `domain_rating` is at
`data.domain_rating`, `trust_flow` at `data.competitor_research.trust_flow`,
`referring_domains` at `data.competitor_research.referring_domains` (= 896),
`backlink_count` at `data.competitor_research.backlink_count` (= 27955). Some are
strings (`referring_domain_type_direct` is `'896'`). Reading them top-level returns
`None` silently, which drops real figures with no error, so every extractor uses the
`_at()` dotted-path resolver rather than `_num()`.

**This task contains the only write in the entire pipeline.** `ensure_project` must be dry-run by default: with `apply=False` it returns `("would-create", ...)` without POSTing. Only `apply=True` writes, and only after `find_project` returns nothing.

- [ ] **Step 1: Write the failing test**

`tests/test_sawarm.py`:

```python
import pytest

import saprobe
import sawarm


# --- project lifecycle: the one write, guarded -----------------------------

def test_find_project_returns_existing_id():
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 824060, "url": "https://getpetermd.com"}]}
    assert sawarm.find_project(FakeSA(), "getpetermd.com") == 824060


def test_find_project_matches_on_normalised_domain():
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 7, "url": "https://WWW.Example.COM/"}]}
    assert sawarm.find_project(FakeSA(), "example.com") == 7


def test_find_project_ignores_a_different_domain():
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 9, "url": "https://other.com"}]}
    assert sawarm.find_project(FakeSA(), "example.com") is None


def test_ensure_project_reuses_existing_and_never_writes():
    posted = []

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 824060, "url": "https://getpetermd.com"}]}

        def post(self, *a, **k):
            posted.append(a)
            return {}

    pid, action = sawarm.ensure_project(FakeSA(), "getpetermd.com", apply=True)
    assert (pid, action) == (824060, "found")
    assert posted == [], "must not create a project when one already exists"


def test_ensure_project_is_dry_run_by_default():
    posted = []

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}

        def post(self, *a, **k):
            posted.append(a)
            return {"id": 111}

    pid, action = sawarm.ensure_project(FakeSA(), "new.com")
    assert action == "would-create"
    assert pid is None
    assert posted == [], "dry run must not POST"


def test_ensure_project_creates_only_with_apply():
    posted = []

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}

        def post(self, service, path, json_body=None, params=None):
            posted.append((service, path, json_body))
            return {"id": 555}

    pid, action = sawarm.ensure_project(FakeSA(), "new.com", apply=True)
    assert (pid, action) == (555, "created")
    assert len(posted) == 1
    service, path, body = posted[0]
    assert path == "/api/v2/competitor-research/"
    assert body["url"] == "https://new.com"
    assert body["country_code"] == "US"


def test_ensure_project_reports_failure_without_raising():
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}

        def post(self, *a, **k):
            return {"detail": "quota exceeded"}

    pid, action = sawarm.ensure_project(FakeSA(), "new.com", apply=True)
    assert pid is None
    assert action == "failed"


# --- warm extraction against real recordings ------------------------------

def test_position_buckets_from_real_fixture():
    b = sawarm.position_buckets(saprobe.load("warm_getpetermd_com_organic"))
    assert set(b) >= {"1-3", "4-10", "11-20", "21-50", "51-100"}
    assert any(v is not None for v in b.values()), (
        "no bucket resolved from the real recording - the paths in "
        "position_buckets do not match it. Fix the paths against "
        "fixtures/api/README.md, do not relax this assertion.")


def test_authority_from_real_fixture():
    """Values verified in the recording: domain_rating 57, trust_flow 25,
    citation_flow 35, authority_score 35. spam_score and domain_authority came
    back null, which is consistent with these figures being volatile."""
    a = sawarm.authority(saprobe.load("warm_getpetermd_com_project_detail"))
    assert a["domain_rating"] == 57
    assert a["trust_flow"] == 25
    assert a["citation_flow"] == 35
    assert a["authority_score"] == 35


def test_referring_domains_returns_the_total_not_the_subset():
    """Both spellings exist with different values. reffering_domains (doubled f)
    is the total, 3021, corroborated by backlinks_trend total_refdomains.
    referring_domains is the 896 direct subset. Returning 896 as 'referring
    domains' understates the prospect's link profile 3.4x."""
    d = saprobe.load("warm_getpetermd_com_project_detail")
    assert sawarm.referring_domains(d) == 3021


def test_referring_domains_direct_returns_the_subset():
    d = saprobe.load("warm_getpetermd_com_project_detail")
    assert sawarm.referring_domains_direct(d) == 896


def test_referring_domains_prefers_the_doubled_f_total():
    """Guard the exact confusion: with both keys present, take the total."""
    payload = {"data": {"competitor_research": {
        "reffering_domains": 3021, "referring_domains": 896}}}
    assert sawarm.referring_domains(payload) == 3021
    assert sawarm.referring_domains_direct(payload) == 896


def test_backlink_count_from_real_fixture():
    d = saprobe.load("warm_getpetermd_com_project_detail")
    assert sawarm.backlink_count(d) == 27955


def test_competitors_from_real_fixture():
    c = sawarm.competitors(saprobe.load("warm_getpetermd_com_organic_competitors"))
    assert c, "no competitor rows resolved from the real recording"
    assert c[0]["domain"]


def test_anchors_from_real_fixture():
    a = sawarm.anchors(saprobe.load("warm_getpetermd_com_anchors"))
    assert a, "no anchor rows resolved from the real recording"
    assert a[0]["anchor"]


# --- nested resolution ----------------------------------------------------

def test_at_resolves_a_nested_path():
    assert sawarm._at({"data": {"domain_rating": 57}}, "data.domain_rating") == 57


def test_at_coerces_a_numeric_string():
    assert sawarm._at({"data": {"x": "896"}}, "data.x") == 896


def test_at_skips_a_non_numeric_string_and_tries_the_next_path():
    payload = {"a": "n/a", "b": 5}
    assert sawarm._at(payload, "a", "b") == 5


def test_at_returns_none_when_no_path_resolves():
    assert sawarm._at({"data": {}}, "data.nope", "also.nope") is None


def test_at_does_not_crash_on_a_list_intermediate():
    assert sawarm._at({"a": [1, 2]}, "a.b") is None


def test_authority_prefers_nested_over_top_level():
    """A top-level spelling exists only as a fallback; the nested one wins."""
    payload = {"domain_rating": 1, "data": {"domain_rating": 57}}
    assert sawarm.authority(payload)["domain_rating"] == 57


# --- warm extraction normalisation ---------------------------------------

def test_position_buckets_sums_21_to_50_from_three_native_buckets():
    """Native buckets are 21-30/31-40/41-50; the report's 21-50 row is their
    sum. That is an aggregation of real values, not an estimate."""
    payload = {"organic_keywords_top_3": 77, "organic_keywords_4_to_10": 162,
               "organic_keywords_11_to_20": 253, "organic_keywords_21_to_30": 370,
               "organic_keywords_31_to_40": 454, "organic_keywords_41_to_50": 453,
               "organic_keywords_51_to_100": 1783,
               "organic_keywords_100_plus": 294}
    b = sawarm.position_buckets(payload)
    assert b["1-3"] == 77
    assert b["4-10"] == 162
    assert b["11-20"] == 253
    assert b["21-50"] == 370 + 454 + 453
    assert b["51-100"] == 1783
    assert b["100_plus"] == 294


def test_position_buckets_none_when_all_absent():
    b = sawarm.position_buckets({})
    assert all(v is None for v in b.values())


def test_position_buckets_partial_sum_uses_only_present_buckets():
    """If only some of the three sub-buckets are present, sum those rather than
    treating a missing one as zero-and-therefore-complete."""
    b = sawarm.position_buckets({"organic_keywords_21_to_30": 10,
                                 "organic_keywords_41_to_50": 5})
    assert b["21-50"] == 15


def test_authority_normalises_all_six_metrics():
    payload = {"domain_rating": 57, "domain_power": 35, "authority_score": 35,
               "spam_score": 2, "trust_flow": 25, "citation_flow": 35}
    a = sawarm.authority(payload)
    assert a["domain_rating"] == 57
    assert a["trust_flow"] == 25
    assert a["citation_flow"] == 35
    assert a["spam_score"] == 2


def test_authority_missing_metric_is_none_not_zero():
    a = sawarm.authority({"domain_rating": 57})
    assert a["trust_flow"] is None


def test_overview_normalises_traffic_fields():
    o = sawarm.overview({"traffic": 10796, "keyword_count": 3783,
                         "organic_traffic_cost": 36660})
    assert o["monthly_organic_visits"] == 10796
    assert o["ranking_keyword_count"] == 3783
    assert o["traffic_value_usd"] == 36660


def test_overview_missing_fields_are_none():
    o = sawarm.overview({})
    assert o["monthly_organic_visits"] is None


def test_anchors_normalise_and_sort_by_count_descending():
    payload = {"results": [{"anchor": "a", "backlinks_num": 5},
                           {"anchor": "b", "backlinks_num": 1500}]}
    got = sawarm.anchors(payload)
    assert got[0] == {"anchor": "b", "count": 1500}


def test_anchors_skip_rows_without_an_anchor():
    assert sawarm.anchors({"results": [{"backlinks_num": 3}]}) == []


def test_competitors_normalise_field_names():
    payload = {"results": [{"competitor": "trtnation.com",
                            "competitor_traffic": 21200,
                            "competitor_keywords": 5500}]}
    c = sawarm.competitors(payload)[0]
    assert c == {"domain": "trtnation.com", "monthly_visits": 21200,
                 "ranking_keywords": 5500}


def test_competitors_skip_rows_without_a_domain():
    assert sawarm.competitors({"results": [{"competitor_traffic": 1}]}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sawarm.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'sawarm'`

- [ ] **Step 3: Write minimal implementation**

`sawarm.py`:

```python
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


def find_project(sa, domain):
    """Existing Site Explorer project id for a domain, or None. Read-only."""
    target = _host(domain)
    r = sa.get(SERVICE, COLLECTION, params={"search": domain, "page_size": 50})
    for row in (r or {}).get("results") or []:
        if not isinstance(row, dict):
            continue
        if _host(row.get("url") or row.get("domain_url")) == target:
            pid = row.get("id")
            if pid is not None:
                return int(pid)
    return None


def ensure_project(sa, domain, country_code="US", apply=False):
    """Return (project_id, action).

    action is "found" (reused), "created", "would-create" (dry run), or
    "failed". The POST is the only write in this pipeline: it fires only when
    no project exists AND apply is True.
    """
    existing = find_project(sa, domain)
    if existing:
        return existing, "found"
    if not apply:
        return None, "would-create"
    body = {"url": "https://%s" % _host(domain), "country_code": country_code}
    # Real client signature is post(service, path, json_body=None, params=None).
    r = sa.post(SERVICE, COLLECTION, json_body=body) or {}
    pid = r.get("id")
    if pid is None:
        return None, "failed"
    return int(pid), "created"


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


def overview(payload):
    p = payload if isinstance(payload, dict) else {}
    cr = "data.competitor_research."
    return {
        "monthly_organic_visits": _at(
            p, cr + "organic_traffic", cr + "traffic", "data.traffic",
            "traffic", "organic_traffic"),
        "ranking_keyword_count": _at(
            p, cr + "keywords_count", cr + "keyword_count", "data.keywords_count",
            "keyword_count", "keywords_count"),
        "traffic_value_usd": _at(
            p, cr + "organic_traffic_cost", cr + "traffic_cost",
            "data.organic_traffic_cost", "organic_traffic_cost", "traffic_cost"),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sawarm.py -v`
Expected: PASS. As in Task 3, if a fixture-backed test fails the recorded field names differ — correct the `_num` name lists against `fixtures/api/README.md`. Do not weaken the assertion.

- [ ] **Step 5: Confirm the client's write signature**

Already verified by the plan author against `searchatlas/searchatlas.py:127`: the real signature is `post(self, service, path, json_body=None, params=None)`, which is why `ensure_project` passes `json_body=body` by keyword. Re-confirm it still holds rather than assuming:

Run: `python -c "import inspect, sa_client; print(inspect.signature(sa_client.SearchAtlas.post))"`
Expected: `(self, service, path, json_body=None, params=None)`. If it differs, correct `ensure_project` and note it in your report. Do NOT call `post` against the live API in this step.

- [ ] **Step 6: Commit**

```bash
git add sawarm.py tests/test_sawarm.py
git commit -m "feat: Site Explorer project lifecycle and warm metric extraction"
```

---

### Task 5: Derived metrics

**Files:**
- Create: `derive.py`
- Create: `tests/test_derive.py`

**Interfaces:**
- Consumes: `sa_client.is_branded`, `sa_client.brand_tokens` from Task 1; `sacold.keyword_rows` shape from Task 3.
- Produces:
  - `derive.brand_split(rows, domain, business_name) -> dict` with keys `brand_pct, nonbrand_pct, brand_traffic, nonbrand_traffic, classified` (int count) — all `None` when no row carries traffic
  - `derive.money_keywords(rows, brand_tokens_set, limit=8, min_volume=500) -> list[dict]` — non-branded, buried (position > 10), highest volume first
  - `derive.bucket_rows(rows) -> dict` — position buckets computed from raw rows, for cold domains with no native buckets

The brand split was the strongest finding in the reference audit ("95% of every visit comes from people who already knew the name"), so its classifier must not over-match. Brands whose name is their own category are the hard case and are covered by the imported classifier.

- [ ] **Step 1: Write the failing test**

`tests/test_derive.py`:

```python
import derive
import sa_client


def rows():
    return [
        {"keyword": "petermd", "volume": 5000, "position": 1, "traffic": 950},
        {"keyword": "peter md login", "volume": 800, "position": 1, "traffic": 40},
        {"keyword": "trt cost", "volume": 2900, "position": 42, "traffic": 5},
        {"keyword": "enclomiphene", "volume": 90500, "position": 41, "traffic": 5},
        {"keyword": "at home testosterone test", "volume": 6600, "position": 54,
         "traffic": 0},
    ]


def toks():
    return sa_client.brand_tokens("getpetermd.com", "PeterMD")


# --- brand split ----------------------------------------------------------

def test_brand_split_percentages_sum_to_100():
    s = derive.brand_split(rows(), "getpetermd.com", "PeterMD")
    assert s["brand_pct"] + s["nonbrand_pct"] == 100


def test_brand_split_identifies_brand_dominance():
    s = derive.brand_split(rows(), "getpetermd.com", "PeterMD")
    assert s["brand_pct"] > 90, "brand terms carry almost all the traffic here"


def test_brand_split_counts_classified_rows():
    s = derive.brand_split(rows(), "getpetermd.com", "PeterMD")
    assert s["classified"] == 5


def test_brand_split_is_none_when_no_row_has_traffic():
    r = [{"keyword": "trt cost", "volume": 10, "position": 5, "traffic": None}]
    s = derive.brand_split(r, "getpetermd.com", "PeterMD")
    assert s["brand_pct"] is None
    assert s["nonbrand_pct"] is None


def test_brand_split_empty_rows_is_none():
    s = derive.brand_split([], "x.com", "X")
    assert s["brand_pct"] is None


def test_brand_split_does_not_overmatch_a_category_brand():
    """A brand named after its category must not swallow generic queries."""
    r = [{"keyword": "custom golf course prints", "volume": 500, "position": 8,
          "traffic": 100},
         {"keyword": "GolfCoursePrint.com", "volume": 100, "position": 1,
          "traffic": 100}]
    s = derive.brand_split(r, "golfcourseprint.com", "Golf Course Print")
    assert s["brand_pct"] == 50, "only the contiguous-token row is branded"


# --- money keywords ------------------------------------------------------

def test_money_keywords_excludes_branded_terms():
    got = derive.money_keywords(rows(), toks())
    assert all("petermd" not in k["keyword"].replace(" ", "").lower()
               for k in got)


def test_money_keywords_excludes_terms_already_on_page_one():
    got = derive.money_keywords(rows(), toks())
    assert all(k["position"] > 10 for k in got)


def test_money_keywords_sorted_by_volume_descending():
    got = derive.money_keywords(rows(), toks())
    vols = [k["volume"] for k in got]
    assert vols == sorted(vols, reverse=True)
    assert vols[0] == 90500


def test_money_keywords_respects_min_volume():
    got = derive.money_keywords(rows(), toks(), min_volume=5000)
    assert [k["keyword"] for k in got] == ["enclomiphene", "at home testosterone test"]


def test_money_keywords_respects_limit():
    assert len(derive.money_keywords(rows(), toks(), limit=1)) == 1


def test_money_keywords_skips_rows_missing_volume_or_position():
    r = [{"keyword": "a", "volume": None, "position": 40},
         {"keyword": "b", "volume": 900, "position": None}]
    assert derive.money_keywords(r, toks()) == []


# --- buckets from raw rows ----------------------------------------------

def test_bucket_rows_assigns_each_position_band():
    r = [{"keyword": "a", "position": 2}, {"keyword": "b", "position": 7},
         {"keyword": "c", "position": 15}, {"keyword": "d", "position": 33},
         {"keyword": "e", "position": 77}, {"keyword": "f", "position": 140}]
    b = derive.bucket_rows(r)
    assert b == {"1-3": 1, "4-10": 1, "11-20": 1, "21-50": 1, "51-100": 1,
                 "100_plus": 1}


def test_bucket_rows_ignores_rows_without_a_position():
    b = derive.bucket_rows([{"keyword": "a", "position": None}])
    assert all(v == 0 for v in b.values())


def test_bucket_rows_boundaries_are_inclusive():
    r = [{"keyword": "a", "position": 3}, {"keyword": "b", "position": 10},
         {"keyword": "c", "position": 20}, {"keyword": "d", "position": 50},
         {"keyword": "e", "position": 100}]
    b = derive.bucket_rows(r)
    assert b == {"1-3": 1, "4-10": 1, "11-20": 1, "21-50": 1, "51-100": 1,
                 "100_plus": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_derive.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'derive'`

- [ ] **Step 3: Write minimal implementation**

`derive.py`:

```python
"""Metrics computed locally from fetched rows, never fetched directly.

The brand/non-brand split is the most consequential figure the audit prints:
the reference report's headline was that 95% of traffic came from people who
already knew the brand, meaning the site was not a customer-acquisition channel
at all. Classification therefore uses the imported precise classifier rather
than a naive substring check, because a brand named after its own category
("Golf Course Print") would otherwise swallow every generic query.
"""

import sa_client

BURIED_AFTER = 10   # position 11+ is off page one


def brand_split(rows, domain, business_name):
    """Split traffic between branded and non-branded queries.

    Returns None percentages when no row carries traffic — the split is
    unknowable then, and a fabricated 50/50 would be worse than an absent row.
    """
    tokens = sa_client.brand_tokens(domain, business_name or "")
    brand_t = nonbrand_t = 0.0
    seen_traffic = False

    for r in rows:
        t = r.get("traffic")
        if t is None:
            continue
        seen_traffic = True
        if sa_client.is_branded(r.get("keyword") or "", tokens):
            brand_t += float(t)
        else:
            nonbrand_t += float(t)

    total = brand_t + nonbrand_t
    if not seen_traffic or total <= 0:
        return {"brand_pct": None, "nonbrand_pct": None, "brand_traffic": None,
                "nonbrand_traffic": None, "classified": len(rows)}

    bp = int(round(brand_t / total * 100))
    return {
        "brand_pct": bp,
        "nonbrand_pct": 100 - bp,
        "brand_traffic": int(round(brand_t)),
        "nonbrand_traffic": int(round(nonbrand_t)),
        "classified": len(rows),
    }


def money_keywords(rows, tokens, limit=8, min_volume=500):
    """High-volume, non-branded terms the site does not yet rank on page one.

    These are the "every one of these searches is a customer asking Google for
    exactly what you sell, and you are invisible" table.
    """
    out = []
    for r in rows:
        vol, pos = r.get("volume"), r.get("position")
        if vol is None or pos is None:
            continue
        if vol < min_volume or pos <= BURIED_AFTER:
            continue
        if sa_client.is_branded(r.get("keyword") or "", tokens):
            continue
        out.append(r)
    out.sort(key=lambda r: -float(r["volume"]))
    return out[:limit]


def bucket_rows(rows):
    """Position buckets counted from raw rows, for cold domains with no native
    buckets. Rows without a position are not counted anywhere."""
    b = {"1-3": 0, "4-10": 0, "11-20": 0, "21-50": 0, "51-100": 0,
         "100_plus": 0}
    for r in rows:
        p = r.get("position")
        if p is None:
            continue
        p = float(p)
        if p <= 3:
            b["1-3"] += 1
        elif p <= 10:
            b["4-10"] += 1
        elif p <= 20:
            b["11-20"] += 1
        elif p <= 50:
            b["21-50"] += 1
        elif p <= 100:
            b["51-100"] += 1
        else:
            b["100_plus"] += 1
    return b
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_derive.py -v`
Expected: PASS, 16 passed

- [ ] **Step 5: Commit**

```bash
git add derive.py tests/test_derive.py
git commit -m "feat: derive brand split, money keywords and position buckets"
```

---

### Task 6: Lead-quality fixes from the Phase 2 inbox

**Files:**
- Modify: `leads.py`
- Modify: `tests/test_leads.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: unchanged signatures for `normalize_domain(raw)` and `is_test_lead(name, email)`; behaviour corrected. Adds `leads.SOCIAL_HOSTS` (frozenset).

Two defects recorded in `docs/superpowers/phase2-inbox.md` items 2 and 3, both of which mishandle real bookings. They belong here because `collect.py` in Task 7 audits whatever domain `normalize_domain` returns.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_leads.py`:

```python
# --- Phase 2a: social hosts are not the prospect's website ----------------

def test_facebook_page_is_not_accepted_as_the_audited_domain():
    """Local businesses routinely answer 'your business website' with a
    Facebook page. Auditing facebook.com would be nonsense and would create a
    Site Explorer project for Meta."""
    assert leads.normalize_domain("<https://www.facebook.com/mybiz>") is None


def test_instagram_profile_is_rejected():
    assert leads.normalize_domain("https://instagram.com/mybiz") is None


def test_other_social_hosts_are_rejected():
    for u in ("https://linkedin.com/company/x", "https://x.com/handle",
              "https://twitter.com/handle", "https://youtube.com/@chan",
              "https://tiktok.com/@x", "https://yelp.com/biz/x"):
        assert leads.normalize_domain(u) is None, u


def test_a_real_domain_is_still_accepted():
    assert leads.normalize_domain("<https://Www.exampleRealty.com>") == "examplerealty.com"


def test_repeated_w_typo_is_normalised():
    """Live data contains wwww. with four w's; the old strip only matched three."""
    assert leads.normalize_domain("<https://wwww.getpetermd.com>") == "getpetermd.com"


def test_five_w_typo_is_also_normalised():
    assert leads.normalize_domain("https://wwwww.example.com") == "example.com"


def test_single_w_prefix_is_not_stripped():
    """w.example.com is a legitimate host, not a typo."""
    assert leads.normalize_domain("https://w.example.com") == "w.example.com"


# --- Phase 2a: test-lead detection must not eat real leads ----------------

def test_real_lead_whose_domain_contains_test_is_kept():
    """bestestates.com contains 'test'. Discarding that booking silently loses
    a paying prospect."""
    assert leads.is_test_lead("Ann Bell", "ann@bestestates.com") is False


def test_contest_domain_is_kept():
    assert leads.is_test_lead("Joe Smith", "info@contestwinners.com") is False


def test_latest_in_domain_is_kept():
    assert leads.is_test_lead("Sam Fox", "sam@latestyle.com") is False


def test_standalone_test_word_is_still_flagged():
    assert leads.is_test_lead("Anatoliy Test Labinskiy", "a@real.com") is True


def test_internal_domain_is_still_flagged():
    assert leads.is_test_lead("Real Person", "dmitriy@gsmgrowthagency.com") is True


def test_testing_word_is_flagged():
    assert leads.is_test_lead("Testing Account", "x@y.com") is True


def test_test_as_email_local_part_is_flagged():
    assert leads.is_test_lead("Someone", "test@realbusiness.com") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_leads.py -v`
Expected: FAIL on the social-host, `wwww.` and `bestestates.com` tests.

- [ ] **Step 3: Write the implementation**

In `leads.py`, add the constant near the other module constants:

```python
# Answered in the "your business website" field often enough to matter. Auditing
# one of these would profile the platform instead of the prospect.
SOCIAL_HOSTS = frozenset({
    "facebook.com", "m.facebook.com", "fb.com", "instagram.com",
    "linkedin.com", "x.com", "twitter.com", "youtube.com", "youtu.be",
    "tiktok.com", "pinterest.com", "yelp.com", "nextdoor.com",
    "google.com", "goo.gl", "maps.app.goo.gl", "linktr.ee",
})
```

Replace the `www.` strip and add the social check in `normalize_domain`, so the
host-cleaning block reads:

```python
    host = host.split("/")[0].split("?")[0].split("#")[0].strip().lower()
    # Live data contains "wwww." with four w's; strip any run of two or more.
    # A single leading "w." is a legitimate host and is left alone.
    host = re.sub(r"^w{2,}\.", "", host)
    if "." not in host or host.endswith(".") or host.startswith("."):
        return None
    if host in SOCIAL_HOSTS:
        return None
    return host
```

Replace the test-lead pattern. The old `re.compile(r"test|gsmgrowthagency", re.I)`
matched any substring, so `bestestates.com` and `contestwinners.com` were
discarded silently:

```python
# Word-boundary match: a bare substring also matched real domains like
# bestestates.com and contestwinners.com, silently discarding paying leads.
TEST_PATTERN = re.compile(r"\btest(s|ing|er)?\b|gsmgrowthagency", re.I)
```

`is_test_lead` itself is unchanged — it already applies the pattern to name and
email. Note `test@realbusiness.com` still matches because `@` is a word
boundary.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_leads.py -v`
Expected: PASS, all leads tests including the 14 new ones.

- [ ] **Step 5: Confirm nothing else regressed and check the effect on live data**

Run: `python -m pytest -q`
Expected: all tests pass.

Run: `python leads.py --pages 3`
Expected: prints booked leads. Compare against the previous behaviour and report in your notes: how many leads now appear that previously did not, and whether any domain that used to be reported is now `(no domain)` because it was a social profile. State the real numbers.

- [ ] **Step 6: Commit**

```bash
git add leads.py tests/test_leads.py
git commit -m "fix: reject social hosts as audit domains, stop discarding real leads"
```

---

### Task 7: The collector

**Files:**
- Create: `collect.py`
- Create: `tests/test_collect.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces:
  - `collect.build_evidence(cold, warm, domain, business_name, generated_at, project_id=None) -> dict` — pure assembly, no I/O, where `cold` and `warm` are dicts of already-fetched payloads
  - `collect.fetch_cold(sa, domain) -> dict` and `collect.fetch_warm(sa, project_id) -> dict`
  - `collect.run(domain, business_name, apply=False, sa=None) -> tuple[dict, str]` returning `(evidence, action)`
  - `collect.write_evidence(evidence, base=None) -> str` writing `state/prospects/<domain>/evidence.json` and returning the path
  - CLI: `python collect.py <domain> --name "Business Name" [--apply]`

`build_evidence` is kept pure and separate from fetching so the whole assembly is testable offline against the Task 2 fixtures. Every metric it writes is wrapped as `{"value": v, "source": ..., "pulled_at": ...}` so `evidence.Evidence.get` unwraps it, and any metric that came back `None` is written as a null value so its section is omitted.

- [ ] **Step 1: Write the failing test**

`tests/test_collect.py`:

```python
import json

import pytest

import collect
import evidence
import saprobe


# One coherent domain. Blending cold fixtures from one company with warm
# fixtures from another produced a Frankenstein evidence file whose structural
# assertions still passed, which is exactly the trap domain-scoped fixture names
# exist to prevent.
SLUG = "getpetermd_com"


def payloads():
    return (
        {"organic_keywords": saprobe.load("cold_%s_organic_keywords" % SLUG),
         "backlinks": saprobe.load("cold_%s_backlinks" % SLUG),
         "brand_signal": saprobe.load("cold_%s_brand_signal" % SLUG)},
        {"project_detail": saprobe.load("warm_%s_project_detail" % SLUG),
         "organic": saprobe.load("warm_%s_organic" % SLUG),
         "anchors": saprobe.load("warm_%s_anchors" % SLUG),
         "organic_competitors": saprobe.load("warm_%s_organic_competitors" % SLUG)},
    )


def test_fixture_pair_is_one_domain():
    """Guard the trap directly: every fixture the pair loads must name the same
    domain in its recorded provenance."""
    import json
    import os
    names = ["cold_%s_organic_keywords" % SLUG, "cold_%s_backlinks" % SLUG,
             "warm_%s_project_detail" % SLUG, "warm_%s_organic" % SLUG]
    domains = set()
    for n in names:
        rec = json.load(open(os.path.join(saprobe.FIXTURES, n + ".json"),
                             encoding="utf-8"))
        domains.add(rec.get("domain"))
    assert len(domains) == 1, "fixture pair spans domains: %s" % domains


def built():
    cold, warm = payloads()
    return collect.build_evidence(cold, warm, "getpetermd.com", "PeterMD",
                                  "2026-08-05T00:00:00Z", project_id=824060)


# --- contract with the Phase 1 evidence reader ---------------------------

def test_built_evidence_passes_validation():
    evidence.Evidence(built()).validate()


def test_built_evidence_records_identity_and_project():
    ev = built()
    assert ev["domain"] == "getpetermd.com"
    assert ev["business_name"] == "PeterMD"
    assert ev["generated_at"] == "2026-08-05T00:00:00Z"
    assert ev["searchatlas_project_id"] == 824060


def test_metrics_are_wrapped_so_evidence_get_unwraps_them():
    """Assert the wrapper SHAPE, not just that get() returned something. A
    bare value would also satisfy an is-None-or-number check, so that form
    would pass against an unwrapped metric and miss the contract."""
    ev = built()
    raw = ev["traffic"]["ranking_keyword_count"]
    assert isinstance(raw, dict) and "value" in raw, "metric must be wrapped"
    assert evidence.Evidence(ev).get("traffic.ranking_keyword_count") == raw["value"]


def test_every_wrapped_metric_carries_provenance():
    ev = built()
    for section in ("traffic", "backlinks", "position_buckets"):
        for key, metric in (ev.get(section) or {}).items():
            if isinstance(metric, dict) and "value" in metric:
                assert metric.get("source"), "%s.%s lacks source" % (section, key)
                assert metric.get("pulled_at"), "%s.%s lacks pulled_at" % (section, key)


def test_sections_with_real_data_are_present():
    present = evidence.Evidence(built()).present_sections()
    assert "traffic" in present or "position_buckets" in present


def test_technical_section_is_absent_because_it_needs_a_crawl():
    """A site audit needs a crawl budget, so technical stays null and its
    section must not render."""
    assert "technical" not in evidence.Evidence(built()).present_sections()


def test_ai_visibility_is_absent_until_the_browser_probe_runs():
    assert "ai_visibility" not in evidence.Evidence(built()).present_sections()


# --- null-safety: missing data must not become invented data -------------

def test_empty_payloads_still_produce_a_valid_shell():
    ev = collect.build_evidence({}, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    evidence.Evidence(ev).validate()


def test_empty_payloads_leave_every_metric_section_absent():
    ev = collect.build_evidence({}, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    present = evidence.Evidence(ev).present_sections()
    assert present == set(), "no data must mean no sections, not zeros"


def test_missing_warm_data_does_not_zero_the_buckets():
    cold, _ = payloads()
    ev = collect.build_evidence(cold, {}, "getpetermd.com", "PeterMD",
                                "2026-08-05T00:00:00Z")
    e = evidence.Evidence(ev)
    for band in ("1-3", "4-10", "11-20", "21-50", "51-100"):
        v = e.get("position_buckets." + band)
        assert v is None or isinstance(v, (int, float))


def test_cold_only_evidence_falls_back_to_row_derived_buckets():
    cold, _ = payloads()
    ev = collect.build_evidence(cold, {}, "getpetermd.com", "PeterMD",
                                "2026-08-05T00:00:00Z")
    src = (ev.get("position_buckets") or {}).get("source")
    assert src and "derived" in src.lower()


# --- writing -------------------------------------------------------------

def test_write_evidence_creates_a_per_domain_file(tmp_path):
    ev = collect.build_evidence({}, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    p = collect.write_evidence(ev, base=str(tmp_path))
    assert p.endswith("evidence.json")
    assert "x.com" in p
    reloaded = json.loads(open(p, encoding="utf-8").read())
    assert reloaded["domain"] == "x.com"


def test_write_evidence_is_reloadable_by_the_phase_1_reader(tmp_path):
    ev = collect.build_evidence({}, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    p = collect.write_evidence(ev, base=str(tmp_path))
    evidence.Evidence.load(p).validate()


# --- the single write, guarded -------------------------------------------

def test_run_is_dry_run_by_default_and_never_posts():
    posted = []

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}

        def post(self, *a, **k):
            posted.append(a)
            return {"id": 1}

    ev, action = collect.run("new.com", "New", sa=FakeSA())
    assert action == "would-create"
    assert posted == []
    assert ev["searchatlas_project_id"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_collect.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'collect'`

- [ ] **Step 3: Write minimal implementation**

`collect.py`:

```python
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

import derive
import sacold
import sa_client
import sawarm

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state", "prospects")

COLD_SOURCE = "searchatlas:competitor-research (cold)"
WARM_SOURCE = "searchatlas:competitor-research (site explorer project)"
DERIVED = "derived from ranking-keyword rows"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _m(value, source, pulled_at):
    """Wrap one metric with its provenance. A None value is preserved."""
    return {"value": value, "source": source, "pulled_at": pulled_at}


def fetch_cold(sa, domain):
    """Read-only, works for any domain."""
    out = {}
    out["organic_keywords"] = sa.get(
        "keyword", "/api/v2/competitor-research/organic-keywords/",
        params={"target": domain, "page": 1, "page_size": 100})
    out["backlinks"] = sa.get(
        "keyword", "/api/v2/competitor-research/backlinks/",
        params={"target": domain})
    out["brand_signal"] = sa.get(
        "keyword", "/api/v4/brand-signal-score/retrieve",
        params={"domains": domain})
    return out


def fetch_warm(sa, project_id):
    """Requires an existing Site Explorer project. Read-only."""
    base = "/api/v2/competitor-research/%d/" % project_id
    out = {"project_detail": sa.get("keyword", base),
           "organic": sa.get("keyword", base + "data-extended/",
                             params={"context": "organic"})}
    for ctx in ("anchors", "refdomains", "organic_competitors"):
        out[ctx] = sa.get("keyword", base + "view-more/",
                          params={"context": ctx})
    return out


def build_evidence(cold, warm, domain, business_name, generated_at,
                   project_id=None):
    """Pure assembly. No I/O, no network."""
    at = generated_at
    rows = sacold.keyword_rows(cold.get("organic_keywords") or {})
    ov = sawarm.overview(warm.get("project_detail") or {})
    auth = sawarm.authority(warm.get("project_detail") or {})
    native = sawarm.position_buckets(warm.get("organic") or {})
    bl = sacold.backlink_totals(cold.get("backlinks") or {})
    # Referring domains and the richer backlink total are warm-only; cold
    # carries neither. Prefer warm, fall back to the cold total_count.
    wpd = warm.get("project_detail") or {}
    refs = sawarm.referring_domains(wpd)              # total, 3021 in reference
    refs_direct = sawarm.referring_domains_direct(wpd)  # direct subset, 896
    total_bl = sawarm.backlink_count(wpd)
    if total_bl is None:
        total_bl = bl["total_backlinks"]
        total_bl_src = COLD_SOURCE
    else:
        total_bl_src = WARM_SOURCE
    tokens = sa_client.brand_tokens(domain, business_name or "")

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
        kw_count = sacold.total_keywords(cold.get("organic_keywords") or {})
        kw_src = COLD_SOURCE

    value = ov["traffic_value_usd"]
    value_src = WARM_SOURCE

    # Native buckets when the domain is warm, otherwise counted from rows.
    if any(v is not None for v in native.values()):
        buckets, bucket_src = native, WARM_SOURCE
    elif rows:
        buckets, bucket_src = derive.bucket_rows(rows), DERIVED
    else:
        buckets, bucket_src = {k: None for k in native}, COLD_SOURCE

    split = derive.brand_split(rows, domain, business_name)

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
            "brand_pct": _m(split["brand_pct"], DERIVED, at),
            "nonbrand_pct": _m(split["nonbrand_pct"], DERIVED, at),
            "method": "brand-token match over ranking keywords, "
                      "capitalised-run aware",
        },
        "position_buckets": dict(
            {k: _m(v, bucket_src, at) for k, v in buckets.items()},
            source=bucket_src, pulled_at=at),
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


def run(domain, business_name, apply=False, sa=None):
    """Fetch and assemble. Returns (evidence, project action)."""
    sa = sa or sa_client.SearchAtlas()
    project_id, action = sawarm.ensure_project(sa, domain, apply=apply)
    cold = fetch_cold(sa, domain)
    warm = fetch_warm(sa, project_id) if project_id else {}
    ev = build_evidence(cold, warm, domain, business_name, _now(),
                        project_id=project_id)
    return ev, action


def write_evidence(evidence_dict, base=None):
    d = os.path.join(base or STATE, evidence_dict["domain"])
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_collect.py -v`
Expected: PASS. If a fixture-backed test fails, the recorded shapes differ from what the extractors assume — fix the extractor, not the test.

- [ ] **Step 5: Run the collector against a real cold prospect**

Run: `python collect.py trtnation.com --name "TRT Nation"`
Expected: prints `project: would-create` (no write, because dry run is the default), writes `state/prospects/trtnation.com/evidence.json`, and prints which sections are present with real figures.

Report the real output. State explicitly which metrics came back `None` and therefore which sections would be omitted from a report.

- [ ] **Step 6: Run against the warm reference domain**

`getpetermd.com` already has project `824060`, so this exercises the warm path without creating anything:

Run: `python collect.py getpetermd.com --name PeterMD`
Expected: prints `project: found`, and warm-only metrics (authority, trust, native buckets, competitors) are populated where the earlier reference report had them.

Report the real figures alongside the reference report's values (traffic 10.8K, 3.8K keywords, DR 71.8, trust flow 5, and buckets 77/171/268/834/1700) and comment on any large divergence. Do NOT adjust code to make them match — the figures are known to be volatile and the point is to observe, not to force agreement.

- [ ] **Step 7: Commit**

```bash
git add collect.py tests/test_collect.py
git commit -m "feat: build a provenance-stamped evidence file for a prospect"
```

---

## Self-Review

**Spec coverage.** This plan implements the spec's Data sources section in full: the cold/warm split, the two undocumented `?target=` routes, the authorised single write with all four of its guards (one per normalised domain, check-before-create, record the id, no other writes), the async-warm retry contract, the raised timeout for cold backlinks, the volatility snapshot-once rule via `pulled_at` on every metric, the native-versus-summed position buckets, and the brand/non-brand derivation reusing `prune_branded` rather than reinventing it. It also clears items 2 and 3 of `phase2-inbox.md`.

Deliberately not in 2a, with the slice that owns each: the eight remaining report sections and per-tile null gating (2b), `aiprobe.py` and the vertical cache (2c), `dossier.py`, `pricing.py` and the script generator (2d), `post.py` and the idempotency ledger (2e). `scorecard` is written as an empty dict here because scoring needs the AI probe and the technical audit; it stays absent until 2b decides its basis. `paid` stays null because paid data is warm-only per-project and was not part of the verified capture; 2b's paid-versus-organic section will be omitted until a later slice fills it, which the null-omits rule handles correctly.

**Placeholder scan.** No TBD, TODO, or "similar to Task N". Every code step carries runnable code. Tasks 3, 4 and 7 explicitly instruct the implementer to correct field names against the Task 2 recordings rather than trusting the names written here, and to never weaken a fixture-backed assertion — that is the designed mechanism for the one thing I cannot verify from here.

**Type consistency.** Checked across tasks: `sa_client.brand_tokens`/`is_branded` as used in Tasks 5 and 7 match Task 1. `saprobe.load` as used in Tasks 3, 4 and 7 matches Task 2. `sacold.keyword_rows` row keys (`keyword, volume, position, cpc, difficulty, traffic, traffic_pct, traffic_cost, traffic_cost_pct, url`) are consumed with exactly those names by `derive.brand_split`, `derive.money_keywords`, `derive.bucket_rows` and `collect.build_evidence`. `sawarm.position_buckets` returns the six keys `1-3, 4-10, 11-20, 21-50, 51-100, 100_plus`, and `derive.bucket_rows` returns the same six so they are interchangeable in `build_evidence`. `sawarm.overview`/`authority` key names match their consumers in Task 7. Evidence paths written by `build_evidence` match the dotted paths in `evidence.SECTION_REQUIREMENTS` exactly: `traffic.monthly_organic_visits`, `traffic.ranking_keyword_count`, `traffic.traffic_value_usd`, `brand_split.brand_pct`, `brand_split.nonbrand_pct`, `position_buckets.{1-3,4-10,11-20,21-50,51-100}`, `money_keywords`, `backlinks.{referring_domains,total_backlinks,authority,trust}`, `competitors`, `ai_visibility.platforms`, `technical.*`, `paid.*`.

The one signature risk is closed: `SearchAtlas.post` was checked against `searchatlas/searchatlas.py:127` and is `post(self, service, path, json_body=None, params=None)`, so `ensure_project` passes `json_body=` by keyword and the test fakes mirror that signature. Task 4 Step 5 re-confirms it rather than trusting this note.

The remaining open risk is inherent and handled by design rather than closed: the exact response field names in Tasks 3 and 4 come from a spike report, not from a recording I can read from here. That is precisely why Task 2 captures real responses first and why Tasks 3, 4 and 7 each carry fixture-backed tests plus an explicit instruction to correct the extractor against `fixtures/api/README.md` and never to weaken those assertions.
