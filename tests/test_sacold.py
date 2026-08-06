import sacold
import saprobe


# --- against real recordings -----------------------------------------------
#
# The brief's fixture names (cold_organic_keywords, cold_backlinks) predate the
# domain-scoping fix landed in Task 2 (see fixtures/api/README.md). Real
# fixtures on disk are domain-scoped: cold_<slug>_organic_keywords,
# cold_<slug>_backlinks, cold_<slug>_brand_signal for slug in
# {getpetermd_com, trtnation_com}. Using the getpetermd.com pair here since
# that is the "coherent" capture documented in the README.

def test_keyword_rows_parse_from_real_fixture():
    rows = sacold.keyword_rows(saprobe.load("cold_getpetermd_com_organic_keywords"))
    assert rows, "recorded fixture yielded no keyword rows"
    r = rows[0]
    for key in ("keyword", "volume", "position"):
        assert key in r, "missing normalised key %s" % key
    assert isinstance(r["keyword"], str) and r["keyword"]


def test_total_keywords_from_real_fixture():
    n = sacold.total_keywords(saprobe.load("cold_getpetermd_com_organic_keywords"))
    assert isinstance(n, int) and n > 0


def test_backlink_totals_from_real_fixture():
    """The recorded response carries a real backlink total. If this fails, the
    field name in backlink_totals does not match the recording — fix the
    extractor against fixtures/api/README.md. If the field is genuinely absent
    from the recording, report that rather than relaxing the assertion."""
    t = sacold.backlink_totals(saprobe.load("cold_getpetermd_com_backlinks"))
    assert isinstance(t["total_backlinks"], int)
    assert t["total_backlinks"] > 0


def test_backlink_totals_from_real_fixture_trtnation():
    """Cross-domain confirmation the field name isn't a getpetermd.com fluke."""
    t = sacold.backlink_totals(saprobe.load("cold_trtnation_com_backlinks"))
    assert isinstance(t["total_backlinks"], int)
    assert t["total_backlinks"] > 0


def test_brand_signal_from_real_fixture():
    """Real brand_signal payload wraps the record in results[0]; score sits at
    top level of that record. See fixtures/api/README.md."""
    s = sacold.brand_signal(saprobe.load("cold_getpetermd_com_brand_signal"))
    assert isinstance(s["score"], (int, float))


def test_cold_backlinks_real_fixture_has_no_referring_domains():
    """Verified against fixtures/api/README.md: the cold backlinks payload's
    top-level keys are exactly apply_cr_total_override, enriched, results,
    total_count. No referring-domain count exists cold."""
    t = sacold.backlink_totals(saprobe.load("cold_getpetermd_com_backlinks"))
    assert t["referring_domains"] is None


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


# --- deliberate absence of traffic derivation ------------------------------

def test_cold_does_not_estimate_domain_traffic():
    """Cold responses carry no domain-level traffic total, only a per-keyword
    `traffic`/`traffic_pct` share. Scaling one row's figure up by its share
    only works if that row happens to carry the largest share, which the API
    does not guarantee — a tiny-share keyword landing first would amplify
    rounding noise into a wildly wrong headline figure, printed in a
    client-facing PDF and read aloud to a prospect. The operator decided cold
    must never estimate domain traffic or traffic value at all; real traffic
    comes only from the warm project detail. This test pins that decision so
    a share-scaled estimate is not reinstated later."""
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
