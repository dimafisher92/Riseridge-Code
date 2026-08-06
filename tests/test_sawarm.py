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


def test_find_project_reads_url_from_metrics_on_the_real_shape():
    """Verified against the live search endpoint: a real row has no top-level
    url/domain_url - the domain is a BARE string (no scheme) nested at
    row.metrics.url. Before this fix, find_project always returned None for
    a domain that demonstrably has a project, so ensure_project would create
    a duplicate paid project on every single run. This is the test that
    would have caught that bug."""
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 824060, "metrics": {"url": "getpetermd.com"}}]}
    assert sawarm.find_project(FakeSA(), "getpetermd.com") == 824060


def test_find_project_still_reads_the_old_top_level_url_shape():
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 7, "url": "https://example.com"}]}
    assert sawarm.find_project(FakeSA(), "example.com") == 7


def test_find_project_metrics_without_url_raises_warmerror_not_none():
    """A row with an id but no readable url (metrics present but lacking
    `url`) is most likely a freshly created, not-yet-crawled project - not
    proof the domain has no project. Returning None here (the original,
    now-wrong behaviour) let ensure_project(apply=True) conclude "safe to
    create" and issue a duplicate paid POST. It must raise WarmError, naming
    the ambiguity, instead."""
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 5, "metrics": {"domain_rating": 57}}]}
    with pytest.raises(sawarm.WarmError):
        sawarm.find_project(FakeSA(), "example.com")


def test_find_project_metrics_none_raises_warmerror_not_none():
    """Same ambiguity as above via a different shape: `metrics` itself is
    None rather than missing `url`. Must not crash, and must not be read as
    a clean 'not found' either."""
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 5, "metrics": None}]}
    with pytest.raises(sawarm.WarmError):
        sawarm.find_project(FakeSA(), "example.com")


def test_find_project_unidentifiable_row_does_not_prevent_a_later_match():
    """An unidentifiable row must not poison the whole search: if a later
    row (same page or a later page) positively matches, that match wins and
    no error is raised."""
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 5, "metrics": None},
                                {"id": 824060, "url": "https://match.com"}]}
    assert sawarm.find_project(FakeSA(), "match.com") == 824060


def test_find_project_rejects_empty_domain():
    class FakeSA:
        def get(self, service, path, params=None):
            raise AssertionError("must not search with an empty target")
    with pytest.raises(ValueError):
        sawarm.find_project(FakeSA(), "")


def test_find_project_rejects_none_domain():
    class FakeSA:
        def get(self, service, path, params=None):
            raise AssertionError("must not search with an empty target")
    with pytest.raises(ValueError):
        sawarm.find_project(FakeSA(), None)


def test_find_project_zero_rows_is_genuinely_not_found():
    """Distinct from the ambiguous cases above: a search that returns no
    rows at all is real absence, not ambiguity, and must still return None."""
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}
    assert sawarm.find_project(FakeSA(), "nobody-has-this.com") is None


def test_ensure_project_write_safety_is_non_vacuous_against_real_shape():
    """The write-safety guarantee only means something if find_project can
    actually find a real-shaped match. With the metrics.url fix, a
    real-shaped search response must short-circuit ensure_project to 'found'
    and issue no POST - proving the guarantee holds, not merely that it is
    unreachable because find_project always fails."""
    posted = []

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 824060, "metrics": {"url": "getpetermd.com"}}]}

        def post(self, *a, **k):
            posted.append(a)
            return {}

    pid, action = sawarm.ensure_project(FakeSA(), "getpetermd.com", apply=True)
    assert (pid, action) == (824060, "found")
    assert posted == []


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


def test_ensure_project_treats_id_zero_as_found_not_missing():
    """A project id of 0 must still short-circuit to 'found' and never POST.
    `if existing:` would treat 0 as falsy and risk a duplicate create; the
    guarantee must not depend on real ids never being 0."""
    posted = []

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": 0, "url": "https://zero.com"}]}

        def post(self, *a, **k):
            posted.append(a)
            return {"id": 999}

    pid, action = sawarm.ensure_project(FakeSA(), "zero.com", apply=True)
    assert (pid, action) == (0, "found")
    assert posted == [], "must not create a project when id 0 already exists"


def test_ensure_project_post_exception_then_recheck_finds_it_created():
    """A POST can raise (timeout, connection reset) after the server already
    created the project. The response is gone but the project isn't - a
    re-check must recover the id rather than the caller retrying blind and
    creating a second paid project."""
    class FakeSA:
        def __init__(self):
            self.get_calls = 0

        def get(self, service, path, params=None):
            self.get_calls += 1
            if self.get_calls == 1:
                return {"results": []}  # initial find_project: not found
            return {"results": [{"id": 777,
                                 "url": "https://timeout-created.com"}]}

        def post(self, *a, **k):
            raise TimeoutError("simulated timeout")

    pid, action = sawarm.ensure_project(
        FakeSA(), "timeout-created.com", apply=True)
    assert (pid, action) == (777, "created")


def test_ensure_project_post_exception_then_recheck_finds_nothing():
    """POST raises and the project genuinely never landed: report 'failed',
    not an exception, and do not fabricate an id."""
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}

        def post(self, *a, **k):
            raise TimeoutError("simulated timeout")

    pid, action = sawarm.ensure_project(FakeSA(), "new.com", apply=True)
    assert (pid, action) == (None, "failed")


def test_ensure_project_post_exception_then_recheck_also_raises_is_unknown():
    """POST raises AND the follow-up re-check raises too: this is genuinely
    ambiguous - a project may or may not exist - and must be reported as a
    distinct 'unknown' status rather than silently coerced into 'failed'
    (which would look safe to retry) or left to propagate as an exception."""
    class FakeSA:
        def __init__(self):
            self.get_calls = 0

        def get(self, service, path, params=None):
            self.get_calls += 1
            if self.get_calls == 1:
                return {"results": []}  # initial find_project: not found
            raise ConnectionError("simulated connection reset")

        def post(self, *a, **k):
            raise TimeoutError("simulated timeout")

    pid, action = sawarm.ensure_project(FakeSA(), "new.com", apply=True)
    assert (pid, action) == (None, "unknown")


def test_ensure_project_rejects_empty_domain_and_issues_no_post():
    class FakeSA:
        def get(self, service, path, params=None):
            raise AssertionError("must not search with an empty target")

        def post(self, *a, **k):
            raise AssertionError("must not POST for an empty target")

    with pytest.raises(ValueError):
        sawarm.ensure_project(FakeSA(), "", apply=True)


def test_ensure_project_200_no_id_recheck_finds_it_created():
    """A 200 body with no usable `id` is not proof the create failed - the
    body may just be malformed or partial while the project landed. A
    re-check that finds it must report "created", not "failed" (which would
    invite a caller to retry a POST that probably already succeeded)."""
    class FakeSA:
        def __init__(self):
            self.get_calls = 0

        def get(self, service, path, params=None):
            self.get_calls += 1
            if self.get_calls == 1:
                return {"results": []}  # initial find_project: not found
            return {"results": [{"id": 909, "url": "https://no-id-body.com"}]}

        def post(self, *a, **k):
            return {"detail": "accepted"}  # 200, but no "id" key

    pid, action = sawarm.ensure_project(FakeSA(), "no-id-body.com", apply=True)
    assert (pid, action) == (909, "created")


def test_ensure_project_200_no_id_recheck_finds_nothing_is_failed():
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}

        def post(self, *a, **k):
            return {"detail": "accepted"}  # 200, but no "id" key

    pid, action = sawarm.ensure_project(FakeSA(), "new.com", apply=True)
    assert (pid, action) == (None, "failed")


def test_ensure_project_post_returns_non_dict_body_does_not_crash():
    """r.get("id") used to sit outside the try, so a non-dict 200 body (e.g.
    a bare list, or None) raised AttributeError after the write already
    happened, bypassing all of the unknown/failed reasoning. It must instead
    fall through to the same re-check path as a missing id."""
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": []}

        def post(self, *a, **k):
            return None

    pid, action = sawarm.ensure_project(FakeSA(), "new.com", apply=True)
    assert (pid, action) == (None, "failed")


def test_find_project_matches_on_page_two():
    """The search endpoint has no fixture, so pagination shape is unverified;
    a real project sitting past page 1 must still be found, not treated as
    absent (which would risk a duplicate create)."""
    class FakeSA:
        def get(self, service, path, params=None):
            page = params.get("page")
            if page == 1:
                return {"results": [{"id": i, "url": "https://other-%d.com" % i}
                                    for i in range(50)]}
            if page == 2:
                return {"results": [{"id": 42, "url": "https://match.com"}]}
            return {"results": []}

    assert sawarm.find_project(FakeSA(), "match.com") == 42


def test_find_project_no_match_across_two_pages_returns_none():
    class FakeSA:
        def get(self, service, path, params=None):
            page = params.get("page")
            if page == 1:
                return {"results": [{"id": i, "url": "https://other-%d.com" % i}
                                    for i in range(50)]}
            if page == 2:
                return {"results": [{"id": 999, "url": "https://also-other.com"}]}
            return {"results": []}

    assert sawarm.find_project(FakeSA(), "nomatch.com") is None


def test_find_project_raises_when_page_cap_is_hit():
    """Every page is full (page_size rows) and none match, all the way to the
    cap: this must NOT resolve to None (which ensure_project would read as
    'safe to create'). It must be visible as an error."""
    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"id": i, "url": "https://other-%d.com" % i}
                                for i in range(50)]}

    with pytest.raises(sawarm.WarmError):
        sawarm.find_project(FakeSA(), "never-found.com")


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


def test_overview_from_real_fixture():
    """Real capture is flat (no data/competitor_research nesting): organic_traffic
    5401, organic_keywords 3846, organic_traffic_cost 36660. organic_keywords is
    the real ranking-keyword-count field name, not keyword_count/keywords_count -
    verified against fixtures/api/README.md."""
    o = sawarm.overview(saprobe.load("warm_getpetermd_com_organic"))
    assert o["monthly_organic_visits"] == 5401
    assert o["ranking_keyword_count"] == 3846
    assert o["traffic_value_usd"] == 36660


def test_overview_finds_a_value_present_only_in_a_later_payload():
    """overview() takes one or more payloads and returns, per field, the
    first non-None value found across them in argument order - so a field
    absent from the first payload but present in a later one must still be
    found rather than reported as null."""
    first = {}
    second = {"traffic": 999, "keyword_count": 42, "organic_traffic_cost": 7}
    o = sawarm.overview(first, second)
    assert o["monthly_organic_visits"] == 999
    assert o["ranking_keyword_count"] == 42
    assert o["traffic_value_usd"] == 7


def test_overview_from_real_fixture_pair_prefers_first_resolving_payload():
    """collect.py calls sawarm.overview(warm["organic"], warm["project_detail"]).
    All three headline fields resolve from the real organic fixture alone;
    passing project_detail second must not change the result."""
    organic = saprobe.load("warm_getpetermd_com_organic")
    project_detail = saprobe.load("warm_getpetermd_com_project_detail")
    o = sawarm.overview(organic, project_detail)
    assert o["monthly_organic_visits"] == 5401
    assert o["ranking_keyword_count"] == 3846
    assert o["traffic_value_usd"] == 36660


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
