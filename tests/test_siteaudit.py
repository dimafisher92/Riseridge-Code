"""The website and SEO audit.

The evidence rule applies here as everywhere: a check that could not be run is
absent, not a zero. "We fetched no pages" and "every page fails" must never
look the same in front of a prospect.
"""

import siteaudit

GOOD = """<html><head>
<title>Whey Protein Isolate 1kg - CLEAN Nutritionals</title>
<meta name="description" content="Premium whey protein isolate in a one kilogram tub, third-party tested and made in Australia with free shipping.">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta property="og:title" content="Whey Protein Isolate">
<link rel="canonical" href="https://acme.com/whey">
<script type="application/ld+json">{"@type":"Product","name":"Whey"}</script>
</head><body><h1>Whey Protein Isolate</h1>
<img src="a.jpg" alt="tub of whey">
<p>%s</p></body></html>""" % ("word " * 400)

BAD = """<html><head></head><body>
<h1>One</h1><h1>Two</h1><img src="a.jpg"><img src="b.jpg">
<p>short</p></body></html>"""


def test_nothing_to_audit_returns_none_not_an_empty_report():
    """A zero-page audit that rendered as 'everything failed' would be a
    fabricated finding."""
    assert siteaudit.audit({}) is None
    assert siteaudit.audit(None) is None


def test_a_clean_page_passes_its_checks():
    r = siteaudit.audit({"https://acme.com/whey": GOOD})
    failing = {c["key"] for c in r["checks"] if c["failing"]}
    for key in ("title_missing", "description_missing", "h1_missing",
                "structured_data", "canonical", "viewport", "open_graph",
                "thin_content", "image_alt"):
        assert key not in failing, "%s should pass on a well-built page" % key


def test_a_bad_page_fails_the_right_checks():
    r = siteaudit.audit({"https://acme.com/x": BAD})
    failing = {c["key"] for c in r["checks"] if c["failing"]}
    for key in ("title_missing", "description_missing", "structured_data",
                "canonical", "viewport", "open_graph", "thin_content",
                "h1_multiple", "image_alt"):
        assert key in failing, "%s should fail on a bare page" % key


def test_every_check_reports_a_count_and_a_denominator():
    """A bare grade is not defensible on a call; the closer needs 'N of M'."""
    r = siteaudit.audit({"https://acme.com/x": BAD, "https://acme.com/y": GOOD})
    for c in r["checks"]:
        assert c["checked"] > 0
        assert 0 <= c["failing"] <= c["checked"]
        assert c["detail"], "%s has no explanation" % c["key"]


def test_duplicate_titles_are_counted():
    r = siteaudit.audit({"https://acme.com/a": GOOD, "https://acme.com/b": GOOD})
    dupe = [c for c in r["checks"] if c["key"] == "title_duplicate"][0]
    assert dupe["failing"] == 1


def test_structured_data_types_are_reported():
    r = siteaudit.audit({"https://acme.com/whey": GOOD})
    assert r["structured_data_types"] == ["Product"]


def test_noindex_is_detected():
    page = '<html><head><title>x</title><meta name="robots" content="noindex,follow"></head><body><h1>h</h1></body></html>'
    r = siteaudit.audit({"https://acme.com/x": page})
    noindex = [c for c in r["checks"] if c["key"] == "noindex"][0]
    assert noindex["failing"] == 1


def test_title_length_flags_only_the_out_of_range_ones():
    short = '<html><head><title>Hi</title></head><body><h1>h</h1></body></html>'
    r = siteaudit.audit({"https://acme.com/a": short, "https://acme.com/b": GOOD})
    length = [c for c in r["checks"] if c["key"] == "title_length"][0]
    assert length["failing"] == 1


def test_status_escalates_with_the_proportion_failing():
    all_bad = siteaudit.audit({"https://acme.com/1": BAD, "https://acme.com/2": BAD})
    canonical = [c for c in all_bad["checks"] if c["key"] == "canonical"][0]
    assert canonical["status"] == "problem"

    clean = siteaudit.audit({"https://acme.com/whey": GOOD})
    canonical = [c for c in clean["checks"] if c["key"] == "canonical"][0]
    assert canonical["status"] == "ok"


def test_headline_issues_are_worst_first_and_exclude_passes():
    r = siteaudit.audit({"https://acme.com/x": BAD, "https://acme.com/y": GOOD})
    issues = siteaudit.headline_issues(r, limit=5)
    assert issues, "a half-broken site must surface something"
    assert all(c["status"] != "ok" for c in issues)
    pcts = [c["pct_failing"] or 0 for c in issues]
    assert pcts == sorted(pcts, reverse=True)


def test_the_sample_scope_is_stated_not_implied():
    """This audits the pages reached from the homepage, not the whole site,
    and the report has to say so."""
    r = siteaudit.audit({"https://acme.com/x": GOOD})
    assert "not the whole site" in r["scope_note"]
    assert r["pages_audited"] == 1


def test_unchecked_signals_are_unknown_rather_than_failed():
    r = siteaudit.audit({"https://acme.com/x": GOOD})
    assert r["robots_txt"] is None
    assert r["sitemap_found"] is None


def test_summary_line_counts_add_up():
    r = siteaudit.audit({"https://acme.com/x": BAD, "https://acme.com/y": GOOD})
    assert (r["problem_count"] + r["warning_count"] + r["passed_count"]
            == len(r["checks"]))
    assert "checks run across 2 pages" in siteaudit.summary_line(r)


def test_page_facts_survive_malformed_html():
    r = siteaudit.audit({"https://acme.com/x": "<html><body><p>unclosed"})
    assert r["pages_audited"] == 1


def test_word_count_ignores_scripts_and_styles():
    page = ("<html><head><title>t</title><script>" + ("junk " * 500)
            + "</script></head><body><h1>h</h1><p>only four words here</p></body></html>")
    facts = siteaudit.page_facts("u", page)
    assert facts["word_count"] < 20
