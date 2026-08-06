import pathlib

import leads

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


def msg(fixture, ts="1785666152.880169", attachments=None):
    return {
        "ts": ts,
        "text": (FIX / fixture).read_text(encoding="utf-8"),
        "attachments": attachments or [],
    }


# --- normalize_domain -------------------------------------------------------

def test_normalize_strips_scheme_www_and_case():
    assert leads.normalize_domain("<https://Www.exampleRealty.com>") == "examplerealty.com"


def test_normalize_handles_slack_link_with_label():
    assert leads.normalize_domain("<http://Beauty4everslc.com|Beauty4everslc.com>") == "beauty4everslc.com"


def test_normalize_ignores_leading_free_text():
    assert leads.normalize_domain("Wild <http://removals.co.uk|removals.co.uk>") == "removals.co.uk"


def test_normalize_rejects_email_in_website_field():
    assert leads.normalize_domain("<mailto:Info@example.com|Info@example.com>") is None


def test_normalize_reads_bare_domain_without_link():
    assert leads.normalize_domain("goodfellowsauto.com") == "goodfellowsauto.com"


def test_normalize_drops_path_and_query():
    assert leads.normalize_domain("<https://example.com/pricing?a=1>") == "example.com"


def test_normalize_returns_none_on_empty():
    assert leads.normalize_domain("") is None
    assert leads.normalize_domain("n/a") is None


# --- is_test_lead ----------------------------------------------------------

def test_test_lead_detected_by_name():
    assert leads.is_test_lead("Anatoliy Test Labinskiy", "a@real.com") is True


def test_test_lead_detected_by_internal_email():
    assert leads.is_test_lead("Real Person", "dmitriy@gsmgrowthagency.com") is True


def test_real_lead_not_flagged():
    assert leads.is_test_lead("Jordan alvarez Alvarez", "owner@example.com") is False


# --- field ----------------------------------------------------------------

def test_field_handles_colon_inside_bold():
    t = "*Client's name:* Jordan alvarez Alvarez"
    assert leads.field(t, "Client's name") == "Jordan alvarez Alvarez"


def test_field_handles_question_mark_label_without_colon():
    t = "*In 1-2 sentences, what's your biggest frustration?* We need more"
    assert leads.field(t, "In 1-2 sentences, what's your biggest frustration?") == "We need more"


def test_field_returns_empty_when_next_label_collapsed_onto_line():
    t = "*What type of business do you run?:* *Your business website:* <https://x.com>"
    assert leads.field(t, "What type of business do you run?") == ""


def test_field_unescapes_html_entities():
    t = "*What type of business do you run?:* Wellness &amp; therapy (spa)"
    assert leads.field(t, "What type of business do you run?") == "Wellness & therapy (spa)"


def test_field_missing_label_returns_empty():
    assert leads.field("*A:* 1", "Nope") == ""


# --- track_for ------------------------------------------------------------

def test_ecommerce_answer_selects_ecom_track():
    assert leads.track_for("E-commerce or online-only business") == "ecom"


def test_home_services_answer_selects_local_track():
    assert leads.track_for("Home services (contractor, HVAC, plumber, electrician, etc.)") == "local"


def test_unknown_business_type_defaults_to_local():
    assert leads.track_for("") == "local"


# --- parse_booked_message -------------------------------------------------

def test_parses_real_booking_end_to_end():
    lead = leads.parse_booked_message(msg("booked_marcio.txt"))
    assert lead.name == "Jordan alvarez Alvarez"
    assert lead.email == "owner@example.com"
    assert lead.domain == "examplerealty.com"
    assert lead.business_type.startswith("Real estate")
    assert lead.track == "local"
    assert lead.budget == "$3,000+ per month"
    assert lead.decision_role == "I'm the sole decision-maker"
    assert lead.urgency == "I could start within the next 2 weeks"
    assert lead.manager == "Sample Manager"
    assert lead.thread_ts == "1785666152.880169"


def test_collapsed_message_yields_no_domain_and_empty_business_type():
    lead = leads.parse_booked_message(msg("booked_collapsed.txt"))
    assert lead.domain is None
    assert lead.business_type == ""
    assert lead.track == "local"
    assert lead.budget == "$1,000 - $2,000 per month"


def test_non_booked_message_returns_none():
    assert leads.parse_booked_message({"ts": "1", "text": "*New Lead from the SEO Funnel*\n"}) is None


def test_stuck_appointment_message_returns_none():
    t = "*Appointment stuck in booked for more than 5 days*\n\nClient's name: X\n"
    assert leads.parse_booked_message({"ts": "1", "text": t}) is None


def test_test_lead_returns_none():
    t = (FIX / "booked_marcio.txt").read_text(encoding="utf-8").replace(
        "owner@example.com", "anatolii@gsmgrowthagency.com")
    assert leads.parse_booked_message({"ts": "1", "text": t}) is None


def test_attachment_supplies_site_title_and_description():
    att = [{"title": "Home Shift Team: Houses for Sale",
            "text": "Find Houses for Sale &amp; Real Estate"}]
    lead = leads.parse_booked_message(msg("booked_marcio.txt", attachments=att))
    assert lead.site_title == "Home Shift Team: Houses for Sale"
    assert "Real Estate" in lead.site_description


def test_vsl_funnel_booking_is_also_parsed():
    t = (FIX / "booked_marcio.txt").read_text(encoding="utf-8").replace(
        "*Appointment booked from the SEO Funnel*",
        "*Appointment booked from the VSL Funnel*")
    assert leads.parse_booked_message({"ts": "1", "text": t}) is not None


def test_missing_email_label_does_not_leak_unrelated_mailto():
    t = (
        "*Appointment booked from the SEO Funnel*\n\n"
        "*Client's name:* Jane Doe\n\n"
        "*Questionnaire:*\n\n"
        "*Your business website:* <mailto:info@somebiz.com|info@somebiz.com>\n"
    )
    lead = leads.parse_booked_message({"ts": "1", "text": t})
    assert lead is not None
    assert lead.email == ""
    assert lead.email != "info@somebiz.com"


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


# --- Phase 2a review fix 1: social-host check must be suffix-aware --------

def test_web_facebook_subdomain_is_rejected():
    assert leads.normalize_domain("https://web.facebook.com/mybiz") is None


def test_business_facebook_subdomain_is_rejected():
    assert leads.normalize_domain("https://business.facebook.com/mybiz") is None


def test_g_page_short_link_is_rejected():
    assert leads.normalize_domain("https://g.page/mybiz") is None


def test_maps_google_subdomain_is_rejected():
    assert leads.normalize_domain("https://maps.google.com/?q=mybiz") is None


def test_bitly_shortener_is_rejected():
    assert leads.normalize_domain("https://bit.ly/abc") is None


def test_lookalike_domain_is_not_rejected():
    """mygoogleadsagency.com does not end with '.google.com' and must not be
    caught by the suffix-aware social-host check."""
    assert leads.normalize_domain("https://mygoogleadsagency.com") == "mygoogleadsagency.com"


# --- Phase 2a review fix 2: TEST_PATTERN must not fire on hyphenated words -

def test_hyphenated_test_drive_domain_is_kept():
    assert leads.is_test_lead("Someone", "x@test-drive.com") is False


def test_hyphenated_load_testing_domain_is_kept():
    assert leads.is_test_lead("Owner", "owner@load-testing.io") is False


def test_hyphenated_unit_test_labs_domain_is_kept():
    assert leads.is_test_lead("Someone", "a@unit-test-labs.com") is False


def test_name_test_still_flagged_after_hyphen_fix():
    assert leads.is_test_lead("Anatoliy Test Labinskiy", "a@real.com") is True


def test_testing_account_still_flagged_after_hyphen_fix():
    assert leads.is_test_lead("Testing Account", "x@y.com") is True


def test_test_email_local_part_still_flagged_after_hyphen_fix():
    assert leads.is_test_lead("Someone", "test@realbusiness.com") is True


def test_tester_word_is_flagged():
    assert leads.is_test_lead("Someone", "tester@x.com") is True


def test_bestestates_still_kept_after_hyphen_fix():
    assert leads.is_test_lead("Ann Bell", "ann@bestestates.com") is False


def test_contestwinners_still_kept_after_hyphen_fix():
    assert leads.is_test_lead("Joe Smith", "info@contestwinners.com") is False


def test_latestyle_still_kept_after_hyphen_fix():
    assert leads.is_test_lead("Sam Fox", "sam@latestyle.com") is False


def test_protest_domain_is_kept():
    assert leads.is_test_lead("Someone", "protest@x.com") is False


def test_maria_testa_name_is_kept():
    assert leads.is_test_lead("Maria Testa", "maria@realbusiness.com") is False


# --- Phase 2a review fix 3: raw website text is retained for unusable leads -

def test_social_only_lead_keeps_raw_website_text():
    t = (
        "*Appointment booked from the SEO Funnel*\n\n"
        "*Client's name:* Jane Doe\n\n"
        "*Email:* jane@example.com\n\n"
        "*Your business website:* <https://www.facebook.com/mybiz>\n"
    )
    lead = leads.parse_booked_message({"ts": "1", "text": t})
    assert lead.domain is None
    assert "facebook.com/mybiz" in lead.website_raw


def test_blank_website_lead_has_empty_raw_text():
    t = (
        "*Appointment booked from the SEO Funnel*\n\n"
        "*Client's name:* Jane Doe\n\n"
        "*Email:* jane@example.com\n\n"
        "*Your business website:* \n"
    )
    lead = leads.parse_booked_message({"ts": "1", "text": t})
    assert lead.domain is None
    assert lead.website_raw == ""


# --- Loom Outreach funnel: plain labels, richer fields ----------------------

def _loom():
    return {"ts": "1786013292.155829",
            "text": (FIX / "booked_loom_outreach.txt").read_text(encoding="utf-8"),
            "attachments": [{"title": "CLEAN Nutritionals",
                             "text": "Clean nutritional products"}]}


def test_loom_outreach_is_recognised_as_a_booking():
    """Third funnel, different wording, leading :emoji: and no bold markers."""
    assert leads.is_booked(":date: New Appointment Booked Loom Outreach\n") is True


def test_stuck_nag_is_still_not_a_booking():
    """'Appointment stuck in booked...' contains 'booked' but not the phrase."""
    assert leads.is_booked("*Appointment stuck in booked for more than 5 days*\n") is False


def test_new_lead_is_still_not_a_booking():
    assert leads.is_booked("*New Lead from the SEO Funnel*\n") is False


def test_field_reads_a_plain_unbolded_label():
    """Loom labels are not bold, so the bold-only pattern found nothing."""
    assert leads.field("Invitee: Alex Sample-Booking\n", "Invitee") == "Alex Sample-Booking"


def test_field_plain_label_must_be_line_anchored():
    """A label word mid-sentence must not be mistaken for a field."""
    assert leads.field("we asked the Invitee: nothing useful", "Invitee") == ""


def test_loom_lead_parses_every_field():
    l = leads.parse_booked_message(_loom())
    assert l.name == "Alex Sample-Booking"
    assert l.email == "alex.sample@example.com"
    assert l.domain == "cleanfoods.example.com.au"
    assert l.funnel_source == "loom"
    assert l.phone == "+61 400 000 000"
    assert l.appointment_at == "2026-08-05T22:30:00.000000Z"
    assert l.meeting_url.startswith("https://us02web.zoom.us/j/00000000000")
    assert l.event_type == "Ecom AI SEO Strategic Call"
    assert "$50K" in l.revenue


def test_loom_query_string_and_multi_label_tld_survive_normalisation():
    l = leads.parse_booked_message(_loom())
    assert l.domain == "cleanfoods.example.com.au", "?srsltid= must be stripped"


def test_loom_event_name_selects_the_ecom_track():
    """Loom has no business-type question; the call name carries the track."""
    assert leads.parse_booked_message(_loom()).track == "ecom"


def test_seo_funnel_lead_still_parses_and_is_tagged_seo():
    lead = leads.parse_booked_message(msg("booked_marcio.txt"))
    assert lead.name == "Jordan alvarez Alvarez"
    assert lead.domain == "examplerealty.com"
    assert lead.funnel_source == "seo"
    assert lead.phone == "", "the SEO funnel carries no phone"
    assert lead.appointment_at == "", "the SEO funnel carries no appointment time"


def test_vsl_funnel_lead_is_tagged_vsl():
    t = (FIX / "booked_marcio.txt").read_text(encoding="utf-8").replace(
        "*Appointment booked from the SEO Funnel*",
        "*Appointment booked from the VSL Funnel*")
    assert leads.parse_booked_message({"ts": "1", "text": t}).funnel_source == "vsl"
