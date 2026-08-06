import json
import os

import pytest

import collect
import derive
import evidence
import sacold
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


def test_cold_only_buckets_match_row_derived_counts_exactly():
    """Rewrite of the original weak assertion (`v is None or isinstance(v,
    (int, float))`), which accepts 0 and therefore passes against exactly
    the zeroed-buckets regression it was meant to catch. With warm data
    absent and cold rows present, every band must equal derive.bucket_rows'
    real count for those rows, not merely "some number or None"."""
    cold, _ = payloads()
    rows = sacold.keyword_rows(cold["organic_keywords"])
    expected = derive.bucket_rows(rows)
    ev = collect.build_evidence(cold, {}, "getpetermd.com", "PeterMD",
                                "2026-08-05T00:00:00Z")
    e = evidence.Evidence(ev)
    for band in ("1-3", "4-10", "11-20", "21-50", "51-100"):
        assert e.get("position_buckets." + band) == expected[band]


def test_missing_both_warm_and_cold_leaves_every_bucket_exactly_none():
    """The actual regression the original test's name promised to guard:
    with no warm data AND no cold rows, buckets must be None, never 0."""
    ev = collect.build_evidence({}, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    for band in ("1-3", "4-10", "11-20", "21-50", "51-100", "100_plus"):
        assert ev["position_buckets"][band]["value"] is None


def test_cold_only_evidence_falls_back_to_row_derived_buckets():
    cold, _ = payloads()
    ev = collect.build_evidence(cold, {}, "getpetermd.com", "PeterMD",
                                "2026-08-05T00:00:00Z")
    src = (ev.get("position_buckets") or {}).get("source")
    assert src and "derived" in src.lower()


def test_derived_buckets_source_names_the_sample_size():
    """The derived (cold, page-capped) path must say it is a sample, not a
    full-corpus distribution, since the cold call is capped at one page
    while ranking_keyword_count reports the domain's true total."""
    cold, _ = payloads()
    rows = sacold.keyword_rows(cold["organic_keywords"])
    ev = collect.build_evidence(cold, {}, "getpetermd.com", "PeterMD",
                                "2026-08-05T00:00:00Z")
    src = ev["position_buckets"]["source"]
    assert "sample" in src.lower()
    assert str(len(rows)) in src


def test_sample_rows_is_set_to_row_count_when_derived():
    cold, _ = payloads()
    rows = sacold.keyword_rows(cold["organic_keywords"])
    ev = collect.build_evidence(cold, {}, "getpetermd.com", "PeterMD",
                                "2026-08-05T00:00:00Z")
    assert ev["position_buckets"]["sample_rows"] == len(rows)


def test_sample_rows_is_none_when_warm_buckets_are_used():
    ev = built()
    assert ev["position_buckets"]["sample_rows"] is None


def test_sample_rows_is_none_when_no_data_at_all():
    ev = collect.build_evidence({}, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    assert ev["position_buckets"]["sample_rows"] is None


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


# --- write_evidence path-traversal guard ----------------------------------

@pytest.mark.parametrize("bad_domain", ["..", "a/b", "a\\b", ""])
def test_write_evidence_rejects_unsafe_domains(tmp_path, bad_domain):
    ev = collect.build_evidence({}, {}, bad_domain, "X",
                                "2026-08-05T00:00:00Z")
    with pytest.raises(ValueError):
        collect.write_evidence(ev, base=str(tmp_path))


def test_write_evidence_still_works_for_a_normal_domain(tmp_path):
    ev = collect.build_evidence({}, {}, "getpetermd.com", "PeterMD",
                                "2026-08-05T00:00:00Z")
    p = collect.write_evidence(ev, base=str(tmp_path))
    assert p.endswith("evidence.json")
    assert "getpetermd.com" in p
    reloaded = json.loads(open(p, encoding="utf-8").read())
    assert reloaded["domain"] == "getpetermd.com"


# --- async not-ready contract: 0 must never reach evidence as fact -------

def test_not_ready_organic_keywords_yields_none_not_zero_and_traffic_absent():
    """The organic-keywords endpoint answers an empty list with
    should_retry before it is warm. Reading that as real data would print
    "0 ranking keywords" as fact for every brand-new prospect on the default
    (warm-absent) dry-run path. A genuinely empty but READY payload (no
    should_retry) still yields a real 0 - see the sibling test below - the
    two cases are told apart by should_retry, not by emptiness, because an
    empty result set alone cannot distinguish "still warming up" from
    "domain truly ranks for nothing"."""
    cold = {"organic_keywords": {"results": [], "total_count": 0,
                                 "should_retry": True},
            "backlinks": {}}
    ev = collect.build_evidence(cold, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    assert ev["traffic"]["ranking_keyword_count"]["value"] is None
    assert "traffic" not in evidence.Evidence(ev).present_sections()


def test_ready_but_empty_organic_keywords_yields_a_real_zero():
    """No should_retry flag: the endpoint is ready and genuinely reports
    zero ranking keywords. Unlike the not-ready case above, this 0 is a real
    measured value, not a guess sitting in for missing data, so it must be
    reported as 0, not None."""
    cold = {"organic_keywords": {"results": [], "total_count": 0},
            "backlinks": {}}
    ev = collect.build_evidence(cold, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    assert ev["traffic"]["ranking_keyword_count"]["value"] == 0


def test_not_ready_backlinks_yields_none_not_zero_and_backlinks_absent():
    cold = {"organic_keywords": {"results": [], "total_count": 0},
            "backlinks": {"results": [], "total_count": 0,
                          "should_retry": True}}
    ev = collect.build_evidence(cold, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    assert ev["backlinks"]["total_backlinks"]["value"] is None
    assert "backlinks" not in evidence.Evidence(ev).present_sections()


def test_ready_but_empty_backlinks_yields_a_real_zero():
    cold = {"organic_keywords": {"results": [], "total_count": 0},
            "backlinks": {"results": [], "total_count": 0}}
    ev = collect.build_evidence(cold, {}, "x.com", "X", "2026-08-05T00:00:00Z")
    assert ev["backlinks"]["total_backlinks"]["value"] == 0


def test_get_ready_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class FakeSA:
        def get(self, service, path, params=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"results": [], "should_retry": True, "retry_after": 1}
            return {"results": [{"k": 1}], "total_count": 1}

    out = collect._get_ready(FakeSA(), "keyword", "/x/")
    assert calls["n"] == 2
    assert out["total_count"] == 1


def test_get_ready_returns_last_payload_at_cap(monkeypatch):
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [], "should_retry": True, "retry_after": 1}

    out = collect._get_ready(FakeSA(), "keyword", "/x/", max_retries=2)
    assert out["should_retry"] is True, "returns the last response rather than hanging"


# --- reusing a recorded project id ----------------------------------------

def test_run_reuses_recorded_project_id_and_issues_no_post(tmp_path, monkeypatch):
    """write_evidence records searchatlas_project_id but nothing read it
    back, so every run re-derived (or risked re-creating) the project. A
    prospect already collected once must short-circuit straight to its
    recorded id, reported as action "reused", with no search and no POST -
    even with apply=True."""
    monkeypatch.setattr(collect, "STATE", str(tmp_path))
    d = tmp_path / "existing.com"
    d.mkdir()
    (d / "evidence.json").write_text(
        json.dumps({"domain": "existing.com", "searchatlas_project_id": 824060}),
        encoding="utf-8")

    posted = []

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}

        def post(self, *a, **k):
            posted.append(a)
            return {"id": 1}

    ev, action = collect.run("existing.com", "Existing", apply=True, sa=FakeSA())
    assert action == "reused"
    assert ev["searchatlas_project_id"] == 824060
    assert posted == [], "reusing a recorded id must never POST"


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


def test_run_rejects_an_empty_domain_before_touching_sa():
    """run is public and later phases call it directly, so the domain guard
    that used to live only in write_evidence (much later in the pipeline)
    must also apply here, before any search or fetch - and before a
    SearchAtlas client is even constructed, since no sa is passed here."""
    with pytest.raises(ValueError):
        collect.run("", "X")


def test_run_rejects_a_third_positional_argument():
    """`apply` and `sa` must be keyword-only. `run(domain, name, my_sa)` is
    an easy mistake — `sa` reads as the natural third positional argument —
    and binding a truthy client to a positional `apply` would silently
    enable the one write in this pipeline. This must raise TypeError, not
    accept the call and flip dry-run off."""

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}

        def post(self, *a, **k):
            raise AssertionError("must never be called")

    with pytest.raises(TypeError):
        collect.run("new.com", "New", FakeSA())
