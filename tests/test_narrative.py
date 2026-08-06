"""The authoring layer.

Two things are being pinned here. First, that the token contract is complete --
build_html raises on any token the templates reference and the author did not
supply, so a gap here is a hard render failure in production. Second, that the
lead finding is chosen by the documented editorial order and each candidate's
own threshold, not by a score.
"""

import pathlib
import re

import pytest

import evidence
import narrative
import render

FIX = pathlib.Path(__file__).parent.parent / "fixtures"
TEMPLATES = pathlib.Path(__file__).parent.parent / "templates"


def ev_of(**blocks):
    base = {"domain": "acme.com", "business_name": "Acme",
            "generated_at": "2026-08-06T00:00:00Z"}
    base.update(blocks)
    return evidence.Evidence(base)


def metric(v):
    return {"value": v}


# --- the token contract -----------------------------------------------------

def template_tokens():
    found = set()
    for path in list((TEMPLATES / "sections").glob("*.html")) + [TEMPLATES / "base.html"]:
        found |= set(re.findall(r"\{\{([a-z0-9_]+)\}\}", path.read_text(encoding="utf-8"), re.I))
    return found


def test_every_template_token_is_authored():
    """A token the templates use and the author does not supply is a hard
    RenderError in production, not a cosmetic gap."""
    ev = evidence.Evidence.load(FIX / "petermd_evidence.json")
    supplied = set(narrative.build_tokens(ev))
    missing = template_tokens() - supplied
    assert not missing, "tokens not authored: %s" % sorted(missing)


def test_the_real_fixture_renders_end_to_end():
    ev = evidence.Evidence.load(FIX / "petermd_evidence.json")
    ev.validate()
    html = render.build_html(ev, narrative.build_tokens(ev))
    assert "{{" not in html
    assert "Acme" not in html


def test_a_sparse_evidence_file_still_renders():
    """Most prospects will not have every block. The section and tile gates
    handle absence, but only if the author still supplies the tokens."""
    ev = ev_of(traffic={"ranking_keyword_count": metric(400)})
    html = render.build_html(ev, narrative.build_tokens(ev))
    assert "{{" not in html


def test_no_table_cell_renders_as_a_bare_dash():
    """A dash in a data table is an absent figure printed as if it were real."""
    ev = evidence.Evidence.load(FIX / "petermd_evidence.json")
    html = render.build_html(ev, narrative.build_tokens(ev))
    assert ">%s<" % render.DASH not in html.replace(" ", "")


# --- editorial priority -----------------------------------------------------

def test_brand_dominance_leads_when_it_qualifies():
    """The reference report led with the brand split and the spec calls it the
    strongest finding there."""
    ev = ev_of(brand_split={"brand_pct": metric(95), "nonbrand_pct": metric(5)},
               position_buckets={"11-20": metric(900), "21-50": metric(3000)})
    t = narrative.build_tokens(ev)
    assert "already knew your name" in t["finding_headline"]
    assert "95%" in t["finding_headline"]


def test_ai_absence_leads_when_brand_split_does_not_qualify():
    ev = ev_of(
        brand_split={"brand_pct": metric(20), "nonbrand_pct": metric(80)},
        ai_visibility={"platforms": [
            {"platform": "ChatGPT", "brand_named": False,
             "competitors_named": ["trtnation"], "topics_total": 5,
             "topics_present": 0}]})
    t = narrative.build_tokens(ev)
    assert "do not know you exist" in t["finding_headline"]


def test_near_misses_lead_when_nothing_above_them_qualifies():
    ev = ev_of(position_buckets={"11-20": metric(300), "21-50": metric(900),
                                 "1-3": metric(10), "4-10": metric(20)})
    t = narrative.build_tokens(ev)
    assert "just off page one" in t["finding_headline"]
    assert "1,200" in t["finding_headline"]


def test_priority_is_the_documented_order_not_the_biggest_number():
    """1,530 near misses is a bigger number than 70%, and the brand split still
    leads. There is no exchange rate between a percentage and a count, so the
    order is an explicit judgment rather than arithmetic."""
    ev = ev_of(brand_split={"brand_pct": metric(70), "nonbrand_pct": metric(30)},
               position_buckets={"11-20": metric(530), "21-50": metric(1000)})
    t = narrative.build_tokens(ev)
    assert "already knew your name" in t["finding_headline"]
    assert narrative.FINDING_ORDER.index("brand_dominance") < \
        narrative.FINDING_ORDER.index("near_misses")


# --- thresholds -------------------------------------------------------------

def test_a_finding_below_its_threshold_does_not_qualify():
    """Leading with a weak finding wastes the report's strongest page."""
    ev = ev_of(brand_split={"brand_pct": metric(40), "nonbrand_pct": metric(60)})
    keys = [f["key"] for f in narrative.findings_for(ev, "Acme")]
    assert "brand_dominance" not in keys


def test_brand_dominance_qualifies_exactly_at_its_threshold():
    ev = ev_of(brand_split={"brand_pct": metric(narrative.BRAND_DOMINANCE_PCT),
                            "nonbrand_pct": metric(35)})
    assert [f["key"] for f in narrative.findings_for(ev, "Acme")] == \
        ["brand_dominance"]


def test_ai_absence_does_not_fire_when_the_brand_is_named():
    ev = ev_of(ai_visibility={"platforms": [
        {"platform": "ChatGPT", "brand_named": True, "topics_present": 3,
         "topics_total": 5, "competitors_named": []}]})
    keys = [f["key"] for f in narrative.findings_for(ev, "Acme")]
    assert "ai_absence" not in keys


def test_link_relevance_needs_both_volume_and_a_weak_nonbrand_share():
    strong_nonbrand = ev_of(
        backlinks={"referring_domains": metric(400),
                   "total_backlinks": metric(9000)},
        brand_split={"brand_pct": metric(40), "nonbrand_pct": metric(60)})
    assert "link_relevance" not in [
        f["key"] for f in narrative.findings_for(strong_nonbrand, "Acme")]


def test_paid_dependence_needs_real_spend():
    ev = ev_of(paid={"estimated_monthly_spend_usd": metric(50)})
    assert narrative.findings_for(ev, "Acme") == []


# --- the fallback -----------------------------------------------------------

def test_section_three_is_always_authored():
    """Section 3 is ungated, so an empty evidence file must still produce a
    headline rather than a RenderError."""
    t = narrative.build_tokens(ev_of())
    assert t["finding_headline"]
    assert t["finding_body_html"]
    assert t["finding_why_html"]


def test_the_fallback_does_not_manufacture_an_insight():
    """When nothing clears its bar, the report states what was measured instead
    of inventing a finding the data does not support."""
    t = narrative.build_tokens(ev_of())
    assert "no estimate has been substituted" in t["finding_data_callout_html"]


# --- figures come from evidence, prose is generated ------------------------

def test_every_figure_traces_to_the_evidence_file():
    ev = ev_of(traffic={"monthly_organic_visits": metric(10800),
                        "ranking_keyword_count": metric(3800),
                        "traffic_value_usd": metric(61900)},
               position_buckets={"1-3": metric(77), "4-10": metric(171)})
    t = narrative.build_tokens(ev)
    assert t["visits"] == "10.8K"
    assert t["keyword_count"] == "3,800"
    assert t["traffic_value"] == "$61.9K"
    assert t["pos_1_3"] == "77"
    assert t["pos_4_10"] == "171"


def test_an_absent_metric_stays_absent():
    """The renderer's gates drop the surrounding markup; the author must not
    substitute a number."""
    t = narrative.build_tokens(ev_of())
    assert t["visits"] == render.DASH
    assert t["keyword_count"] == render.DASH


def test_the_scorecard_is_passed_through_never_invented():
    """collect.py cannot produce a scorecard without a crawl budget, so the
    section stays absent. Deriving scores from unrelated metrics would put three
    invented numbers in front of a prospect."""
    ev = ev_of()
    t = narrative.build_tokens(ev)
    assert t["score_content"] == render.DASH
    assert "scorecard" not in ev.present_sections()


def test_scorecard_bands_come_from_the_supplied_score():
    ev = ev_of(scorecard={"content_quality": {"value": 28, "basis": "x"},
                          "authority": {"value": 71, "basis": "y"},
                          "user_experience": {"value": 49, "basis": "z"}})
    t = narrative.build_tokens(ev)
    assert (t["score_content_band"], t["score_authority_band"],
            t["score_ux_band"]) == ("Critical", "Solid", "Needs work")


def test_scorecard_explanations_use_the_stated_basis():
    ev = ev_of(scorecard={"authority": {"value": 71,
                                        "basis": "measured link authority"}})
    t = narrative.build_tokens(ev)
    assert "measured link authority" in t["scorecard_explanations_html"]


# --- AI section -------------------------------------------------------------

def test_ai_rows_distinguish_absent_from_named():
    ev = ev_of(ai_visibility={"platforms": [
        {"platform": "ChatGPT", "brand_named": False, "topics_present": 0,
         "topics_total": 5, "competitors_named": ["trtnation"]},
        {"platform": "Gemini", "brand_named": True, "topics_present": 2,
         "topics_total": 5, "competitors_named": []}]})
    t = narrative.build_tokens(ev)
    assert "Not named" in t["ai_platform_rows_html"]
    assert "Named in 2 of 5" in t["ai_platform_rows_html"]


def test_ai_rows_never_contain_a_bare_dash():
    ev = ev_of(ai_visibility={"platforms": [
        {"platform": "ChatGPT", "brand_named": False, "topics_present": 0,
         "topics_total": 5, "competitors_named": []}]})
    t = narrative.build_tokens(ev)
    assert render.DASH not in t["ai_platform_rows_html"]


def test_the_verbatim_excerpt_is_quoted_as_proof():
    ev = ev_of(ai_visibility={"platforms": [
        {"platform": "ChatGPT", "brand_named": False, "topics_present": 0,
         "topics_total": 5, "competitors_named": ["trtnation"],
         "verbatim_excerpt": "Most people go to Trtnation for this."}]})
    t = narrative.build_tokens(ev)
    assert "Trtnation" in t["ai_gap_html"]


# --- the plan is sequenced from the findings -------------------------------

def test_the_plan_reflects_what_was_actually_found():
    ev = ev_of(position_buckets={"11-20": metric(300), "21-50": metric(900)})
    t = narrative.build_tokens(ev)
    assert "page two" in t["plan_days_31_60_html"]


def test_the_plan_omits_work_for_findings_that_did_not_fire():
    ev = ev_of(brand_split={"brand_pct": metric(95), "nonbrand_pct": metric(5)})
    t = narrative.build_tokens(ev)
    assert "page two" not in t["plan_days_31_60_html"]


def test_the_plan_always_has_all_three_phases():
    for ev in (ev_of(), evidence.Evidence.load(FIX / "petermd_evidence.json")):
        t = narrative.build_tokens(ev)
        for key in ("plan_days_1_30_html", "plan_days_31_60_html",
                    "plan_days_61_90_html"):
            assert "<li>" in t[key], key


# --- escaping ---------------------------------------------------------------

def test_business_names_with_an_ampersand_survive():
    """Prospect-typed form data routinely contains '&' -- "Smith & Sons
    Plumbing" -- and the authored HTML tokens are NOT escaped by the renderer."""
    ev = evidence.Evidence({"domain": "smith.com",
                            "business_name": "Smith & Sons",
                            "generated_at": "2026-08-06T00:00:00Z"})
    t = narrative.build_tokens(ev)
    assert "&amp;" in t["exec_summary_intro_html"]
    assert "& Sons" not in t["exec_summary_intro_html"]
    html = render.build_html(ev, t)
    assert "{{" not in html


def test_competitor_rows_escape_their_input():
    ev = ev_of(competitors=[{"domain": "a<b>.com", "monthly_visits": 10,
                             "ranking_keywords": 5}])
    t = narrative.build_tokens(ev)
    assert "<b>" not in t["competitor_rows_html"]


def test_rows_skips_records_with_an_empty_label():
    """The anchors feed contains blank-anchor rows; rendering one leaves an
    empty cell beside a real number in a client-facing table."""
    out = narrative.rows([{"anchor": "", "count": 5},
                          {"anchor": "acme", "count": 3}],
                         ("anchor", None), ("count", "int"))
    assert out.count("<tr>") == 1


# --- report date ------------------------------------------------------------

def test_report_date_comes_from_the_evidence_stamp():
    ev = ev_of()
    assert narrative.build_tokens(ev)["report_date"] == "August 2026"


def test_a_malformed_stamp_falls_back_to_now():
    ev = evidence.Evidence({"domain": "a.com", "business_name": "A",
                            "generated_at": "not-a-date"})
    assert narrative.build_tokens(ev)["report_date"]


# --- the keyless answer-source method --------------------------------------

def source_evidence(present=False):
    """ai_visibility as probe_sources produces it."""
    return {"ai_visibility": {
        "method": "answer-source",
        "method_note": "Measured from the web sources that rank for each "
                       "question; it is not a transcript of any assistant.",
        "questions": ["best plumber in Denver", "who are the top rated plumber"],
        "topics": [
            {"question": "best plumber in Denver", "brand_present": present,
             "brand_rank": 2 if present else None,
             "competitors_named": ["rivalplumbing"],
             "aggregator_sources": ["yelp.com"],
             "business_sources": ["rivalplumbing.com"], "sources_total": 8},
            {"question": "who are the top rated plumber", "brand_present": False,
             "brand_rank": None, "competitors_named": [],
             "aggregator_sources": ["angi.com"], "business_sources": [],
             "sources_total": 6},
        ],
        "summary": {"questions_searched": 2,
                    "questions_present": 1 if present else 0,
                    "competitors_named": ["rivalplumbing"],
                    "aggregator_sources": ["yelp.com", "angi.com"]},
    }}


def test_the_source_method_renders_question_rows_not_engine_rows():
    ev = ev_of(**source_evidence())
    t = narrative.build_tokens(ev)
    assert "What a buyer asks" in t["ai_table_head_html"]
    assert "AI platform" not in t["ai_table_head_html"]
    assert "best plumber in Denver" in t["ai_platform_rows_html"]


def test_the_source_method_never_claims_an_assistant_was_asked():
    """It measures the source pool. Saying 'ChatGPT did not name you' would be
    a claim this method cannot support."""
    ev = ev_of(**source_evidence())
    t = narrative.build_tokens(ev)
    blob = " ".join([t["ai_platform_rows_html"], t["ai_gap_html"],
                     t["ai_intro_html"], t["finding_body_html"],
                     t["finding_headline"], t["finding_data_callout_html"]])
    for engine in ("ChatGPT", "Perplexity", "Gemini", "Copilot"):
        assert engine not in blob


def test_the_method_note_is_printed_with_the_table():
    ev = ev_of(**source_evidence())
    t = narrative.build_tokens(ev)
    assert "not a transcript" in t["ai_method_note_html"]


def test_the_engine_method_still_renders_engine_rows():
    ev = ev_of(ai_visibility={"platforms": [
        {"platform": "ChatGPT", "brand_named": False, "topics_present": 0,
         "topics_total": 5, "competitors_named": []}]})
    t = narrative.build_tokens(ev)
    assert "AI platform" in t["ai_table_head_html"]
    assert "ChatGPT" in t["ai_platform_rows_html"]
    assert t["ai_method_note_html"] == ""


def test_absence_from_the_source_pool_is_a_finding():
    ev = ev_of(**source_evidence(present=False))
    keys = [f["key"] for f in narrative.findings_for(ev, "Acme")]
    assert "ai_absence" in keys
    t = narrative.build_tokens(ev)
    assert "missing from the answers" in t["finding_headline"]


def test_presence_in_the_source_pool_is_not_an_absence_finding():
    ev = ev_of(**source_evidence(present=True))
    assert "ai_absence" not in [f["key"] for f in narrative.findings_for(ev, "A")]


def test_directories_are_called_out_as_the_opening():
    ev = ev_of(**source_evidence())
    t = narrative.build_tokens(ev)
    assert "directory listings" in t["ai_gap_html"]
    assert "yelp.com" in t["ai_gap_html"]


def test_the_source_section_renders_and_has_no_bare_dashes():
    ev = ev_of(**source_evidence())
    assert "ai_visibility" in ev.present_sections()
    html = render.build_html(ev, narrative.build_tokens(ev))
    assert "{{" not in html
    assert ">%s<" % render.DASH not in html.replace(" ", "")


def test_no_engine_is_named_anywhere_unless_it_was_individually_measured():
    """The cover used to list ChatGPT, Perplexity, Gemini, Copilot and Google
    AI Mode by name, which told the prospect all five were tested. Neither
    method does that: the keyless one measures a source pool, and the API one
    covers three engines at most."""
    ev = ev_of(**source_evidence())
    html = render.build_html(ev, narrative.build_tokens(ev))
    # Comments are stripped first: they never render, and the template carries
    # one that names the engines precisely to explain why they were removed.
    visible = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    for engine in ("ChatGPT", "Perplexity", "Gemini", "Copilot", "AI Mode"):
        assert engine not in visible, "%s named without measuring it" % engine


def test_engines_may_be_named_when_they_were_actually_asked():
    ev = ev_of(ai_visibility={"platforms": [
        {"platform": "ChatGPT", "brand_named": False, "topics_present": 0,
         "topics_total": 5, "competitors_named": []}]})
    html = render.build_html(ev, narrative.build_tokens(ev))
    assert "ChatGPT" in html


def test_the_cover_names_no_engine_at_all():
    """The cover renders before any evidence is known, so it cannot claim
    coverage of anything specific."""
    cover = (TEMPLATES / "sections" / "01_cover.html").read_text(encoding="utf-8")
    body = re.sub(r"<!--.*?-->", "", cover, flags=re.S)
    for engine in ("ChatGPT", "Perplexity", "Gemini", "Copilot"):
        assert engine not in body


# --- keeping prospect data off the page when it would break the render ------

def test_a_row_naming_a_vendor_is_dropped_not_rendered():
    """A backlink anchor can legitimately contain "majestic". verify_pdf sees
    only extracted text and fails the whole render for it, so the row goes."""
    out = narrative.rows([{"anchor": "Majestic Hotels", "count": 9},
                          {"anchor": "clean nutritionals", "count": 4}],
                         ("anchor", None), ("count", "int"))
    assert "Majestic" not in out
    assert "clean nutritionals" in out


def test_a_row_in_an_unrenderable_script_is_dropped():
    """Chrome falls back to Arial for a glyph the brand faces lack, which fails
    the embed gate and costs the entire PDF."""
    out = narrative.rows([{"keyword": "最好的补充剂", "volume": 100},
                          {"keyword": "protein powder", "volume": 90}],
                         ("keyword", None), ("volume", "int"))
    assert "protein powder" in out
    assert "补充" not in out


@pytest.mark.parametrize("text,ok", [
    ("Clean Nutritionals", True),
    ("Café Ürün", True),
    ("a — b … c", True),
    ("最好的补充剂", False),
    ("best 💪 protein", False),
    ("лучший", False),
])
def test_renderable_matches_the_brand_font_coverage(text, ok):
    assert narrative.renderable(text) is ok


def test_escaping_strips_unrenderable_characters():
    assert "💪" not in narrative._esc("protein 💪 powder")
    assert narrative._esc("Smith & Sons") == "Smith &amp; Sons"


def test_curly_quotes_are_normalised_rather_than_dropped():
    assert narrative._esc("Mary’s") == "Mary's"
