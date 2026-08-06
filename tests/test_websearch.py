"""Keyless search and the answer-source method.

The claim this method supports is narrow and the tests hold it there: the
business is or is not present in the pages an AI answer is assembled from. It
must never harden into "ChatGPT did not name you", which is a different claim
and one this cannot check.
"""

import pytest

import aiprobe
import websearch

HTML = """
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftrtnation.com%2Fclinics&amp;rut=x">
     TRTNation &mdash; Best TRT Clinics</a>
  <a class="result__snippet" href="#">Our clinics offer testosterone therapy.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.yelp.com%2Fsearch%3Ffind%3Dtrt">
     Top 10 TRT Clinics - Yelp</a>
  <a class="result__snippet" href="#">Reviews of clinics near you.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgetpetermd.com%2F">
     PeterMD Telehealth</a>
  <a class="result__snippet" href="#">Online testosterone care.</a>
</div>
"""

LITE = """
<a rel="nofollow" href="https://acme-plumbing.com/" class='result-link'>Acme Plumbing</a>
<td class='result-snippet'>Denver plumber since 1998.</td>
<a rel="nofollow" href="https://angi.com/denver" class='result-link'>Angi Denver</a>
<td class='result-snippet'>Find a pro.</td>
"""


def fetch_for(body, status=200):
    return lambda url: (status, body)


# --- parsing ----------------------------------------------------------------

def test_html_endpoint_results_are_parsed():
    rows = websearch.parse(HTML)
    assert [r["domain"] for r in rows] == ["trtnation.com", "yelp.com",
                                           "getpetermd.com"]
    assert rows[0]["title"].startswith("TRTNation")
    assert "testosterone" in rows[0]["snippet"]


def test_the_redirect_wrapper_is_unwrapped():
    """Results come wrapped in /l/?uddg=<encoded>. Left wrapped, every result
    would look like it came from duckduckgo.com."""
    rows = websearch.parse(HTML)
    assert rows[0]["url"] == "https://trtnation.com/clinics"
    assert not any("duckduckgo" in r["domain"] for r in rows)


def test_lite_endpoint_markup_is_parsed_too():
    rows = websearch.parse(LITE)
    assert [r["domain"] for r in rows] == ["acme-plumbing.com", "angi.com"]


def test_ranks_are_assigned_in_order():
    assert [r["rank"] for r in websearch.parse(HTML)] == [1, 2, 3]


def test_duplicate_domains_collapse():
    body = HTML + HTML
    assert len(websearch.parse(body)) == 3


def test_limit_is_honoured():
    assert len(websearch.parse(HTML, limit=2)) == 2


def test_unparseable_markup_yields_nothing_rather_than_junk():
    assert websearch.parse("<html><body>blocked</body></html>") == []
    assert websearch.parse("") == []


def test_www_is_stripped_from_the_domain():
    assert websearch.registrable("https://www.example.com/x") == "example.com"


# --- aggregators ------------------------------------------------------------

@pytest.mark.parametrize("domain,expected", [
    ("yelp.com", True), ("www.angi.com", True), ("m.facebook.com", True),
    ("reddit.com", True), ("acme-plumbing.com", False),
    ("trtnation.com", False),
])
def test_aggregator_classification(domain, expected):
    assert websearch.is_aggregator(domain) is expected


def test_aggregators_are_flagged_on_the_row():
    rows = websearch.parse(HTML)
    by = {r["domain"]: r for r in rows}
    assert by["yelp.com"]["aggregator"] is True
    assert by["trtnation.com"]["aggregator"] is False


# --- search ----------------------------------------------------------------

def test_search_returns_rows_from_the_first_working_endpoint():
    rows = websearch.search("best trt clinic", fetch=fetch_for(HTML))
    assert len(rows) == 3


def test_search_falls_back_to_the_second_endpoint():
    calls = []

    def fetch(url):
        calls.append(url)
        return (200, LITE) if "lite" in url else (200, "<html>blocked</html>")

    rows = websearch.search("best plumber", fetch=fetch)
    assert len(calls) == 2
    assert [r["domain"] for r in rows] == ["acme-plumbing.com", "angi.com"]


def test_a_blocked_search_returns_empty_not_an_exception():
    """One blocked query must not take down an audit."""
    assert websearch.search("x", fetch=lambda url: (429, "")) == []
    assert websearch.search("x", fetch=lambda url: (None, "")) == []


# --- the analysis -----------------------------------------------------------

def test_the_brand_is_found_by_domain():
    rows = websearch.parse(HTML)
    got = aiprobe.analyse_sources(rows, ["petermd"], ["trtnation"])
    assert got["brand_present"] is True
    assert got["brand_rank"] == 3


def test_absence_is_reported_as_absence():
    rows = websearch.parse(HTML)
    got = aiprobe.analyse_sources(rows, ["acmeclinic"], ["trtnation"])
    assert got["brand_present"] is False
    assert got["brand_rank"] is None
    assert got["competitors_named"] == ["trtnation"]


def test_directories_are_separated_from_businesses():
    """'The answers are built from directories, not businesses' is itself a
    finding, and a directory is not a competitor -- nobody loses a job to
    Yelp."""
    got = aiprobe.analyse_sources(websearch.parse(HTML), ["nobody"], [])
    assert got["aggregator_sources"] == ["yelp.com"]
    assert "yelp.com" not in got["business_sources"]
    assert "trtnation.com" in got["business_sources"]


def test_competitors_only_match_the_known_set():
    got = aiprobe.analyse_sources(websearch.parse(HTML), ["nobody"], ["marek"])
    assert got["competitors_named"] == []


# --- the probe --------------------------------------------------------------

def test_probe_sources_needs_no_api_key(tmp_path):
    got = aiprobe.probe_sources(
        "PeterMD", "getpetermd.com", vertical="trt-clinic", category="TRT clinic",
        competitors=["trtnation"], search=lambda q, limit=10: websearch.parse(HTML),
        cache_base=str(tmp_path))
    assert got["method"] == aiprobe.SOURCE_METHOD
    assert got["summary"]["questions_present"] == len(got["topics"])
    assert got["summary"]["competitors_named"] == ["trtnation"]


def test_probe_sources_returns_none_when_nothing_could_be_searched(tmp_path):
    """A blocked search must omit the section, not report a zero."""
    got = aiprobe.probe_sources(
        "Acme", "acme.com", vertical="v", category="plumber",
        search=lambda q, limit=10: [], cache_base=str(tmp_path))
    assert got is None


def test_a_failed_search_is_not_cached(tmp_path):
    aiprobe.probe_sources("Acme", "acme.com", vertical="v", category="plumber",
                          search=lambda q, limit=10: [],
                          cache_base=str(tmp_path))
    assert aiprobe.load_cache("v", str(tmp_path)) is None


def test_the_vertical_cache_avoids_re_searching(tmp_path):
    calls = []

    def search(q, limit=10):
        calls.append(q)
        return websearch.parse(HTML)

    base = str(tmp_path)
    aiprobe.probe_sources("Alpha Clinic", "alphaclinic.com", vertical="trt",
                          category="TRT clinic", search=search, cache_base=base)
    first = len(calls)
    aiprobe.probe_sources("Beta Clinic", "betaclinic.com", vertical="trt",
                          category="TRT clinic", search=search, cache_base=base)
    assert len(calls) == first, "a cached vertical must not re-search"


def test_questions_stay_unbranded(tmp_path):
    """A branded question measures brand recall, not discovery.

    "Best" is the realistic collision: the generated question "best plumber"
    contains the brand, so this set would measure whether people who already
    know the company can find it -- which is not the question being asked.
    """
    with pytest.raises(aiprobe.ProbeError):
        aiprobe.probe_sources("Best", "bestplumbing.com",
                              vertical="v", category="plumber",
                              search=lambda q, limit=10: websearch.parse(HTML),
                              cache_base=str(tmp_path))


def test_a_brand_that_does_not_collide_passes_the_guard(tmp_path):
    got = aiprobe.probe_sources("Denver Plumber Co", "denverplumber.com",
                                vertical="v", category="plumber",
                                search=lambda q, limit=10: websearch.parse(HTML),
                                cache_base=str(tmp_path))
    assert got is not None


def test_the_method_and_its_limit_are_recorded(tmp_path):
    got = aiprobe.probe_sources(
        "Acme", "acme.com", vertical="v", category="TRT clinic",
        search=lambda q, limit=10: websearch.parse(HTML),
        cache_base=str(tmp_path))
    assert "not a transcript" in got["method_note"]
    assert got["source"] == "open web search"


def test_a_search_that_raises_is_contained(tmp_path):
    def boom(q, limit=10):
        raise RuntimeError("network died")

    assert aiprobe.probe_sources("Acme Plumbing", "acmeplumbing.com",
                                 vertical="v", category="plumber",
                                 search=boom, cache_base=str(tmp_path)) is None
