"""Band selection is the number the operator says out loud. These tests pin the
matrix, the anchor tier, and the two asymmetries that a naive average gets
wrong."""

import pytest

import pricing


def dossier(**company):
    return {"company": {k: {"value": v, "source": "https://example.com/about"}
                        for k, v in company.items()}}


def evidence(**metrics):
    """Evidence-shaped dict with {'value': x} wrappers, as collect.py writes."""
    out = {"traffic": {}, "backlinks": {}, "paid": {}}
    for k, v in metrics.items():
        if k == "referring_domains":
            out["backlinks"][k] = {"value": v}
        elif k in ("estimated_monthly_spend_usd",):
            out["paid"][k] = {"value": v}
        elif k == "landing_pages":
            out["paid"][k] = v
        else:
            out["traffic"][k] = {"value": v}
    return out


# --- the matrix itself ------------------------------------------------------

def test_matrix_matches_the_six_decks():
    assert pricing.MATRIX[("ecom", "low")] == {
        "foundation": 1500, "growth": 2500, "dominate": 4000}
    assert pricing.MATRIX[("ecom", "mid")] == {
        "foundation": 2500, "growth": 5000, "dominate": 8000}
    assert pricing.MATRIX[("ecom", "high")] == {
        "foundation": 4000, "growth": 6500, "dominate": 9000}
    assert pricing.MATRIX[("ecom", "euro")] == {
        "foundation": 1800, "growth": 2500, "dominate": 4000}
    assert pricing.MATRIX[("local", "low")] == {
        "foundation": 1500, "growth": 2500, "dominate": 3500}
    assert pricing.MATRIX[("local", "high")] == {
        "foundation": 2500, "growth": 4000, "dominate": 6500}


@pytest.mark.parametrize("size_class,local_anchor,ecom_anchor", [
    ("micro", 2500, 2500),
    ("small", 2500, 2500),
    ("mid", 4000, 5000),
    ("large", 4000, 6500),
])
def test_anchor_price_by_size_class(size_class, local_anchor, ecom_anchor):
    """The spec's anchor table, which is what gets quoted."""
    lb = pricing.band_for("local", size_class)
    eb = pricing.band_for("ecom", size_class)
    assert pricing.MATRIX[("local", lb)]["growth"] == local_anchor
    assert pricing.MATRIX[("ecom", eb)]["growth"] == ecom_anchor


def test_growth_is_the_anchor_on_both_tracks():
    assert pricing.ANCHOR_TIER == "growth"
    for track in ("local", "ecom"):
        rec = pricing.recommend(track, dossier=dossier(employee_count=30))
        assert rec["anchor_tier"] == "growth"
        assert rec["anchor_price"] == rec["prices"]["growth"]


def test_local_is_the_default_track_not_ecom():
    """609 bookings: 259 local against 19 ecom. LOCAL must be reachable with no
    special casing, and its bands differ from ECOM at mid/large."""
    local = pricing.recommend("local", dossier=dossier(employee_count=30))
    ecom = pricing.recommend("ecom", dossier=dossier(employee_count=30))
    assert local["band"] == "high" and ecom["band"] == "mid"
    assert local["anchor_price"] != ecom["anchor_price"]


# --- the step-down comes from the prospect's OWN band ------------------------

def test_step_down_uses_the_same_band_not_the_bottom_of_the_matrix():
    """A Mid ECOM prospect stepping down gets that band's Foundation ($2,500),
    not the low band's ($1,500). Pulling Foundation from the low band would
    undercharge by up to $2,500."""
    rec = pricing.recommend("ecom", dossier=dossier(employee_count=30))
    assert rec["band"] == "mid"
    assert rec["step_down"]["price"] == 2500
    assert rec["step_up"]["price"] == 8000


def test_step_down_and_up_track_a_large_local_prospect():
    rec = pricing.recommend("local", dossier=dossier(employee_count=80))
    assert rec["size_class"] == "large" and rec["band"] == "high"
    assert (rec["step_down"]["price"], rec["anchor_price"],
            rec["step_up"]["price"]) == (2500, 4000, 6500)


# --- asymmetry 1: organic raises, never lowers ------------------------------

def test_weak_organic_cannot_pull_a_real_company_down_a_band():
    """The HVAC case. A 45-staff, 6-location operator with almost no organic
    footprint is Mid. Three organic metrics all reading 'small' are not three
    independent facts -- they are one fact measured three times, and the weak
    organic is the deficiency being sold, not evidence of a small company."""
    rec = pricing.recommend(
        "local",
        dossier=dossier(employee_count=45, location_count=6),
        evidence=evidence(monthly_organic_visits=120, ranking_keyword_count=40,
                          traffic_value_usd=300, referring_domains=8))
    assert rec["size_class"] == "mid"
    assert rec["band"] == "high"
    assert rec["anchor_price"] == 4000


def test_strong_organic_does_raise_a_small_company():
    """The asymmetry runs one way only: scale proven by organic is still scale."""
    rec = pricing.recommend(
        "ecom",
        dossier=dossier(employee_count=6),
        evidence=evidence(monthly_organic_visits=90000,
                          ranking_keyword_count=25000))
    assert rec["size_floor"] == "small"
    assert rec["size_class"] == "large"
    assert rec["size_raised_to"] == "large"


def test_the_asymmetry_is_stated_in_the_output():
    """The operator has to defend the number, so the reason a class did not move
    has to be visible, not implicit in the code."""
    rec = pricing.recommend(
        "local", dossier=dossier(employee_count=45),
        evidence=evidence(monthly_organic_visits=100))
    assert "cannot lower" in rec["size_basis"]


# --- asymmetry 2: company facts are a floor, not an average term ------------

def test_confirmed_headcount_sets_a_floor():
    rec = pricing.recommend("local", dossier=dossier(employee_count=60))
    assert rec["size_class"] == "large"
    assert rec["size_floor"] == "large"


def test_ownership_structure_floors_a_franchise_at_mid():
    """One franchise location's website looks small. The franchise does not."""
    rec = pricing.recommend("local", dossier=dossier(ownership="franchise"))
    assert rec["size_class"] == "mid"


def test_pe_backed_floors_at_large():
    rec = pricing.recommend("local", dossier=dossier(ownership="PE-backed"))
    assert rec["size_class"] == "large"


def test_the_highest_floor_wins():
    rec = pricing.recommend(
        "local", dossier=dossier(employee_count=4, location_count=12))
    assert rec["size_class"] == "large"


# --- unknowns are never assumed --------------------------------------------

def test_nothing_established_yields_unknown_not_micro():
    """An invented headcount silently moves the price band. Absent evidence must
    read as unknown, and must say so loudly."""
    rec = pricing.recommend("local")
    assert rec["size_class"] == "unknown"
    assert rec["band"] == "low"
    assert any("No size signal" in f for f in rec["flags"])


def test_unknown_signals_are_listed_not_hidden():
    rec = pricing.recommend("local", dossier=dossier(employee_count=20))
    assert "location_count" in rec["unknown_signals"]
    assert "monthly_organic_visits" in rec["unknown_signals"]


def test_every_signal_is_returned_with_its_reasoning():
    rec = pricing.recommend("local", dossier=dossier(employee_count=20))
    assert rec["signals"], "recommendation must never be a silent choice"
    for s in rec["signals"]:
        assert s["group"] and s["signal"] and s["note"]
        assert s["kind"] in ("floor", "raise")


# --- budget is a sanity check, not a cap ------------------------------------

@pytest.mark.parametrize("answer,amount,open_ended", [
    ("$1,000 - $2,000", 2000, False),
    ("$3,000+", 3000, True),
    ("Less than $1,000", 1000, False),
    ("$2000-$3000", 3000, False),
])
def test_budget_ceiling_parsing(answer, amount, open_ended):
    got = pricing.budget_ceiling(answer)
    assert got["amount"] == amount
    assert got["open_ended"] is open_ended


def test_unanswered_budget_is_none():
    """325 of 609 bookings do not answer it at all."""
    assert pricing.budget_ceiling("") is None
    assert pricing.budget_ceiling(None) is None


def test_budget_below_the_anchor_flags_the_gap_but_does_not_cap():
    rec = pricing.recommend("local", dossier=dossier(employee_count=45),
                            budget_answer="$1,000 - $2,000")
    assert rec["anchor_price"] == 4000, "the band must not be capped by budget"
    assert any("above the stated budget" in f for f in rec["flags"])
    assert any("step down to Foundation" in f for f in rec["flags"])


def test_open_ended_budget_does_not_flag():
    """'$3,000+' cannot distinguish a $3k business from a $30k one, so it is
    never evidence that the anchor is too high."""
    rec = pricing.recommend("local", dossier=dossier(employee_count=45),
                            budget_answer="$3,000+")
    assert not any("above the stated budget" in f for f in rec["flags"])


def test_budget_above_the_anchor_is_silent():
    rec = pricing.recommend("local", dossier=dossier(employee_count=4),
                            budget_answer="$3,000+")
    assert not any("above the stated budget" in f for f in rec["flags"])


# --- upfront discount -------------------------------------------------------

def test_three_months_upfront_is_ten_percent_off():
    assert pricing.upfront_quote(2500) == 6750      # 7500 - 10%
    assert pricing.upfront_quote(4000) == 10800
    rec = pricing.recommend("local", dossier=dossier(employee_count=45))
    assert rec["upfront"]["growth"] == 10800
    assert "10%" in rec["upfront_terms"]


def test_every_tier_carries_the_upfront_option():
    rec = pricing.recommend("ecom", dossier=dossier(employee_count=30))
    for tier in pricing.TIERS:
        assert rec["upfront"][tier] == pricing.upfront_quote(rec["prices"][tier])


# --- currency ---------------------------------------------------------------

def test_european_ecom_uses_the_euro_deck():
    rec = pricing.recommend("ecom", dossier=dossier(employee_count=30),
                            currency="EUR")
    assert rec["band"] == "euro"
    assert rec["currency"] == "EUR"
    assert rec["anchor_price"] == 2500


def test_euro_single_band_limitation_is_flagged_not_hidden():
    """The euro deck has one band, so a Large European prospect prices the same
    as a Micro one. That has to be visible."""
    rec = pricing.recommend("ecom", dossier=dossier(employee_count=200),
                            currency="EUR")
    assert rec["size_class"] == "large"
    assert any("single band" in f for f in rec["flags"])


def test_local_track_ignores_currency_because_there_is_no_euro_local_deck():
    rec = pricing.recommend("local", dossier=dossier(employee_count=30),
                            currency="EUR")
    assert rec["band"] == "high"


# --- urgency ----------------------------------------------------------------

@pytest.mark.parametrize("urgency,expected", [
    ("ASAP", "hard"),
    ("Within the next 1-3 months", "moderate"),
    ("Just researching for now", "light"),
    ("", "unknown"),
])
def test_push_level_from_urgency(urgency, expected):
    rec = pricing.recommend("local", dossier=dossier(employee_count=4),
                            urgency=urgency)
    assert rec["push_to_dominate"] == expected


def test_a_big_company_in_a_hurry_gets_pushed_hard():
    rec = pricing.recommend("local", dossier=dossier(employee_count=60),
                            urgency="Within the next 1-3 months")
    assert rec["push_to_dominate"] == "hard"


# --- guards -----------------------------------------------------------------

def test_unknown_track_is_rejected():
    with pytest.raises(pricing.PricingError):
        pricing.recommend("saas")
    with pytest.raises(pricing.PricingError):
        pricing.band_for("saas", "mid")


def test_evidence_object_and_plain_dict_read_the_same():
    """recommend() is called with a collect.py dict in the pipeline and an
    Evidence object in tests; both must resolve metrics identically."""
    import evidence as ev_mod
    raw = evidence(monthly_organic_visits=90000, ranking_keyword_count=25000)
    raw.update({"domain": "x.com", "business_name": "X", "generated_at": "now"})
    a = pricing.recommend("ecom", evidence=raw)
    b = pricing.recommend("ecom", evidence=ev_mod.Evidence(raw))
    assert a["size_class"] == b["size_class"] == "large"


def test_format_recommendation_shows_every_signal_and_flag():
    rec = pricing.recommend("local", dossier=dossier(employee_count=45),
                            budget_answer="$1,000 - $2,000")
    text = pricing.format_recommendation(rec)
    assert "ANCHOR ON    GROWTH" in text
    assert "employee_count" in text
    assert "FLAGS" in text
    for s in rec["signals"]:
        assert s["signal"] in text
