import derive


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
    return derive.brand_token_set("getpetermd.com", "PeterMD")


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


def test_brand_split_is_none_when_token_set_is_empty():
    """An unclassifiable brand (no usable domain or name) must not be
    reported as 0% brand / 100% non-brand — that would be a fabricated claim
    that none of the traffic is branded, when the truth is nothing could be
    classified at all."""
    r = [{"keyword": "some query", "volume": 100, "position": 5, "traffic": 50}]
    s = derive.brand_split(r, "ab.io", "AB")
    assert s["brand_pct"] is None
    assert s["nonbrand_pct"] is None
    assert s["brand_traffic"] is None
    assert s["nonbrand_traffic"] is None
    assert s["classified"] == 1


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
    """No row carries a position, so every band's count is genuinely zero -
    but bucket_rows only ever sees the cold, page-capped sample, not the
    full corpus, so a zero count cannot be told apart from "the sample
    happened not to cover this band." All bands must be None, never 0."""
    b = derive.bucket_rows([{"keyword": "a", "position": None}])
    assert all(v is None for v in b.values())


def test_bucket_rows_boundaries_are_inclusive():
    r = [{"keyword": "a", "position": 3}, {"keyword": "b", "position": 10},
         {"keyword": "c", "position": 20}, {"keyword": "d", "position": 50},
         {"keyword": "e", "position": 100}]
    b = derive.bucket_rows(r)
    assert b == {"1-3": 1, "4-10": 1, "11-20": 1, "21-50": 1, "51-100": 1,
                 "100_plus": None}


# --- brand_hit word-run fallback ------------------------------------------

def test_brand_hit_catches_lowercase_word_run_when_allowed():
    assert derive.brand_hit("peter md", toks(), True) == "petermd"


def test_brand_hit_misses_lowercase_word_run_when_not_allowed():
    assert derive.brand_hit("peter md", toks(), False) is None


def test_brand_hit_catches_word_run_with_extra_trailing_word():
    assert derive.brand_hit("peter md trt", toks(), True) == "petermd"


def test_brand_hit_catches_word_run_with_extra_leading_word():
    assert derive.brand_hit("peter md reviews", toks(), True) == "petermd"


def test_brand_hit_does_not_overmatch_a_genuine_nonbrand_query():
    assert derive.brand_hit("best trt online", toks(), True) is None


def test_fully_generic_true_for_category_brand():
    assert derive._fully_generic("golfcourseprint", derive._GENERIC_VOCAB) is True


def test_fully_generic_false_for_distinctive_brand():
    assert derive._fully_generic("petermd", derive._GENERIC_VOCAB) is False


def test_brand_hit_length_guard_blocks_very_short_token():
    """MIN_BRAND_TOKEN_LEN (5) still blocks a token too short to be trusted
    for the word-run concatenation pass. Neither word alone contains "abco",
    so the strict classifier's own check does not fire here; only the
    word-run fallback could match "ab" + "co" -> "abco", and the 4-character
    token must not clear the guard. A 5-letter token (matching the sibling
    classifier's own floor) is allowed through on purpose — see
    test_brand_hit_matches_a_short_brand_via_its_own_token."""
    assert derive.brand_hit("ab co reviews", {"abco"}, True) is None


# --- brand_split / money_keywords with word-run fallback ------------------

def test_brand_split_real_fixture_reports_higher_brand_share():
    import sacold
    import saprobe

    payload = saprobe.load("cold_getpetermd_com_organic_keywords")
    rows_ = sacold.keyword_rows(payload)
    s = derive.brand_split(rows_, "getpetermd.com", "PeterMD")
    assert s["brand_pct"] > 80


def test_money_keywords_excludes_word_run_branded_terms():
    r = [{"keyword": "peter md trt", "volume": 2900, "position": 42, "traffic": 208},
         {"keyword": "enclomiphene", "volume": 90500, "position": 41, "traffic": 5}]
    got = derive.money_keywords(r, toks())
    assert [k["keyword"] for k in got] == ["enclomiphene"]


# --- brand_token_set: whole-brand tokens only, no bare category words -----

def test_brand_token_set_getpetermd_includes_expected_tokens():
    t = derive.brand_token_set("getpetermd.com", "PeterMD")
    assert {"getpetermd", "getpetermdcom", "petermd"} <= t
    # and the word-run fallback still works off this token set
    assert derive.brand_hit("peter md", t, derive._allow_word_runs(t))
    assert derive.brand_hit("peter md trt", t, derive._allow_word_runs(t))
    assert derive.brand_hit("peter md reviews", t, derive._allow_word_runs(t))
    assert derive.brand_hit("best trt online", t, derive._allow_word_runs(t)) is None


def test_brand_token_set_does_not_leak_bare_word_shift():
    t = derive.brand_token_set("homeshiftteam.com", "Home Shift Team")
    assert "shift" not in t
    allow = derive._allow_word_runs(t)
    assert derive.brand_hit("shift work sleep disorder", t, allow) is None
    assert derive.brand_hit("home shift team reviews", t, allow)


def test_brand_token_set_does_not_leak_bare_word_transport():
    t = derive.brand_token_set("asaptransportsolutions.com", "ASAP Transport Solutions")
    assert "transport" not in t
    allow = derive._allow_word_runs(t)
    assert derive.brand_hit("transport a car across country", t, allow) is None
    assert derive.brand_hit("asap transport solutions reviews", t, allow)


def test_brand_token_set_does_not_leak_bare_word_window():
    t = derive.brand_token_set("thewindowdoorstore.com", "The Window Door Store")
    assert "window" not in t
    allow = derive._allow_word_runs(t)
    assert derive.brand_hit("window replacement cost", t, allow) is None


def test_brand_token_set_does_not_leak_bare_word_injury():
    t = derive.brand_token_set("smartinjurypros.com", "Smart Injury Pros")
    assert "injury" not in t
    allow = derive._allow_word_runs(t)
    assert derive.brand_hit("injury lawyer near me", t, allow) is None


def test_brand_token_set_keeps_a_short_brand_its_own_token():
    """MIN_BRAND_TOKEN_LEN must not cost a short brand its own token: a
    5-character brand name is still exactly as long as the sibling
    classifier's own floor, so it must survive."""
    t = derive.brand_token_set("zappo.com", "Zappo")
    assert "zappo" in t


def test_brand_hit_matches_a_short_brand_via_its_own_token():
    t = derive.brand_token_set("zappo.com", "Zappo")
    assert derive.brand_hit("zappo reviews", t, True)


def test_brand_split_golfcourseprint_regression_holds_with_brand_token_set():
    """golfcourseprint is fully generic so word runs stay disabled, and the
    50/50 split from the original category-brand regression still holds."""
    r = [{"keyword": "custom golf course prints", "volume": 500, "position": 8,
          "traffic": 100},
         {"keyword": "GolfCoursePrint.com", "volume": 100, "position": 1,
          "traffic": 100}]
    s = derive.brand_split(r, "golfcourseprint.com", "Golf Course Print")
    assert s["brand_pct"] == 50


def test_money_keywords_window_replacement_cost_is_not_dropped():
    """The costlier failure mode: money_keywords excludes branded terms, so a
    bare 'window' token would silently drop this row from the one table meant
    to show the prospect what they are invisible for. This test would have
    failed before brand_token_set replaced sa_client.brand_tokens."""
    t = derive.brand_token_set("thewindowdoorstore.com", "The Window Door Store")
    r = [{"keyword": "window replacement cost", "volume": 5000, "position": 30,
          "traffic": 0}]
    got = derive.money_keywords(r, t)
    assert [k["keyword"] for k in got] == ["window replacement cost"]
