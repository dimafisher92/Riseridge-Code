"""The internal sales script.

It is read by a human immediately before a call, so the properties that matter
are: nothing invented, nothing HTML-escaped into gibberish, and every unknown
visibly marked as unknown.
"""

import evidence
import leads
import narrative
import pricing
import salesscript


def lead(**kw):
    base = dict(thread_ts="1.1", name="Jordan Alvarez", email="j@example.com",
                domain="examplerealty.com", business_type="Real estate",
                track="local", budget="$1,000 - $2,000",
                frustration="leads dried up after the market shifted",
                tried="SEO agency, Google Ads", decision_role="I decide",
                urgency="ASAP", timezone="MST", manager="Sam", funnel="SEO")
    base.update(kw)
    return leads.Lead(**base)


def ev_of(**blocks):
    base = {"domain": "examplerealty.com", "business_name": "Example Realty",
            "generated_at": "2026-08-06T00:00:00Z"}
    base.update(blocks)
    return evidence.Evidence(base)


def metric(v):
    return {"value": v}


# --- plain text, not HTML ---------------------------------------------------

def test_no_html_entities_reach_slack():
    """Slack does not decode HTML entities: an &mdash; here prints literally as
    '&mdash;' in front of the closer."""
    ev = ev_of(brand_split={"brand_pct": metric(95), "nonbrand_pct": metric(5)})
    text = salesscript.build(lead(), evidence=ev,
                             findings=narrative.findings_for(ev, "Example Realty"))
    for entity in ("&mdash;", "&middot;", "&amp;", "&ndash;", "&ldquo;"):
        assert entity not in text


def test_no_html_tags_reach_slack():
    ev = ev_of(brand_split={"brand_pct": metric(95), "nonbrand_pct": metric(5)})
    text = salesscript.build(lead(), evidence=ev,
                             findings=narrative.findings_for(ev, "Example Realty"))
    assert "<li>" not in text and "<p>" not in text


# --- the prospect's own words ----------------------------------------------

def test_the_script_opens_on_their_stated_frustration():
    text = salesscript.build(lead())
    assert "leads dried up after the market shifted" in text


def test_unanswered_funnel_fields_are_marked_not_blank():
    text = salesscript.build(lead(frustration="", budget="", urgency=""))
    assert text.count("(not answered)") >= 3


def test_the_call_time_limitation_is_stated_for_funnels_that_lack_it():
    """SEO and VSL bookings carry no appointment time, only lead creation."""
    text = salesscript.build(lead(appointment_at=""))
    assert "not supplied by this funnel" in text


def test_a_real_appointment_time_is_shown_when_the_funnel_has_one():
    text = salesscript.build(lead(appointment_at="2026-08-12T15:00:00Z"))
    assert "2026-08-12T15:00:00Z" in text


# --- objection handling -----------------------------------------------------

def test_objections_fire_from_the_already_tried_answer():
    text = salesscript.build(lead(tried="SEO agency, Google Ads"))
    assert "SEO agency" in text
    assert "Do not sell SEO" in text
    assert "ownership versus rental" in text


def test_a_multi_select_answer_fires_several_objections():
    got = salesscript.objections_for("SEO agency, Google Ads, Facebook ads")
    assert len(got) == 3


def test_nothing_tried_gets_the_competitor_led_opening():
    got = salesscript.objections_for("Nothing yet")
    assert got and "competitor section" in got[0][1]


def test_an_empty_tried_answer_says_so():
    text = salesscript.build(lead(tried=""))
    assert "Nothing flagged" in text


# --- discovery --------------------------------------------------------------

def test_discovery_questions_are_seeded_by_the_findings():
    ev = ev_of(ai_visibility={"platforms": [
        {"platform": "ChatGPT", "brand_named": False, "topics_present": 0,
         "topics_total": 5, "competitors_named": []}]})
    found = narrative.findings_for(ev, "Example Realty")
    text = salesscript.build(lead(), evidence=ev, findings=found)
    assert "ChatGPT or Gemini" in text


def test_base_discovery_always_present():
    text = salesscript.build(lead())
    assert "What is a new customer worth to you" in text


# --- impact is measured, never invented ------------------------------------

def test_impact_uses_real_figures_when_they_exist():
    ev = ev_of(traffic={"traffic_value_usd": metric(61900)},
               position_buckets={"11-20": metric(268), "21-50": metric(834)})
    text = salesscript.build(lead(), evidence=ev)
    assert "$61,900" in text
    assert "1,102 keywords rank 11-50" in text


def test_impact_says_so_when_nothing_is_measurable():
    """Better an explicit 'run this as discovery' than a confident number the
    closer cannot defend."""
    text = salesscript.build(lead(), evidence=ev_of())
    assert "No headline figure was measurable" in text


def test_no_findings_warns_against_overclaiming():
    text = salesscript.build(lead(), evidence=ev_of(), findings=[])
    assert "Do not overclaim" in text


# --- pricing ----------------------------------------------------------------

def test_the_price_block_anchors_on_growth():
    rec = pricing.recommend("local", dossier={
        "company": {"employee_count": {"value": 45}}})
    text = salesscript.build(lead(), recommendation=rec)
    assert "ANCHOR       GROWTH  USD 4,000/month" in text
    assert "Foundation  USD 2,500/month" in text
    assert "Dominate    USD 6,500/month" in text


def test_the_upfront_option_is_quoted():
    rec = pricing.recommend("local", dossier={
        "company": {"employee_count": {"value": 45}}})
    text = salesscript.build(lead(), recommendation=rec)
    assert "10% off" in text
    assert "10,800" in text


def test_pricing_flags_reach_the_closer():
    rec = pricing.recommend("local",
                            dossier={"company": {"employee_count": {"value": 45}}},
                            budget_answer="$1,000 - $2,000")
    text = salesscript.build(lead(), recommendation=rec)
    assert "above the stated budget" in text


def test_unknown_size_signals_are_listed_for_the_closer_to_ask_about():
    rec = pricing.recommend("local", dossier={
        "company": {"employee_count": {"value": 45}}})
    text = salesscript.build(lead(), recommendation=rec)
    assert "size signals not established" in text


# --- dossier ----------------------------------------------------------------

def test_dossier_fields_appear_with_unknowns_marked():
    d = {"company": {"employee_count": {"value": 45},
                     "ownership": {"value": None}},
         "unknown_fields": ["ownership"]}
    text = salesscript.build(lead(), dossier=d)
    assert "employee count     45" in text
    assert "ownership          unknown" in text
    assert "not established: ownership" in text


def test_boolean_dossier_fields_read_as_yes_or_no():
    """'published prices   False' looks like a bug rather than a fact about the
    business."""
    d = {"company": {"published_prices": {"value": False}}, "unknown_fields": []}
    text = salesscript.build(lead(), dossier=d)
    assert "published prices   no" in text
    assert "False" not in text


# --- the closing rule -------------------------------------------------------

def test_the_script_closes_by_forbidding_invented_figures():
    text = salesscript.build(lead())
    assert "do not fill the gap on the call" in text


def test_it_builds_with_nothing_but_a_lead():
    """collect or dossier can fail for a prospect. The closer still gets a
    usable brief rather than a traceback."""
    text = salesscript.build(lead())
    assert "SALES SCRIPT" in text
    assert "DISCOVERY QUESTIONS" in text


def test_it_builds_from_a_plain_dict_lead():
    text = salesscript.build({"name": "Casey", "domain": "casey.com"})
    assert "Casey" in text


def test_a_tiny_traffic_value_is_not_framed_against_the_retainer():
    """A live run produced 'organic is worth $24/month' as 'the number to
    compare the retainer against' -- next to a $5,000 anchor that sentence
    argues the prospect out of the deal."""
    rec = pricing.recommend("ecom", revenue="$50K - $100K /month")
    ev = ev_of(traffic={"traffic_value_usd": metric(24)})
    text = salesscript.build(lead(), evidence=ev, recommendation=rec)
    assert "compare the retainer against" not in text
    assert "Do NOT frame the retainer against this figure" in text
    assert "$24/month" in text


def test_a_large_traffic_value_is_still_the_comparison():
    rec = pricing.recommend("ecom", revenue="$50K - $100K /month")
    ev = ev_of(traffic={"traffic_value_usd": metric(61900)})
    text = salesscript.build(lead(), evidence=ev, recommendation=rec)
    assert "compare the retainer against" in text


def test_traffic_value_framing_without_a_recommendation_is_unchanged():
    ev = ev_of(traffic={"traffic_value_usd": metric(61900)})
    assert "compare the retainer against" in salesscript.build(lead(), evidence=ev)
