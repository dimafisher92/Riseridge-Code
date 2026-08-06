"""Dossier extraction, entirely offline through an injected fetcher.

The bar for every field: it is either sourced with the snippet it came from, or
unknown. A field this module invents moves the price band silently and gets
repeated out loud on a sales call.
"""

import pytest

import dossier


def fetcher(pages, robots=None):
    """A fake fetch(url) -> (status, text) over a {path_or_url: html} map."""
    def fetch(url):
        if url.endswith("/robots.txt"):
            return (200, robots) if robots else (404, "")
        for key, html in pages.items():
            if url == key or url.rstrip("/").endswith(key.rstrip("/")):
                return 200, html
        return 404, ""
    return fetch


HOME = "https://acme.com/"


def build(pages, **kw):
    return dossier.build("acme.com", fetch=fetcher(pages), **kw)


# --- the founding-year false positive --------------------------------------

def test_bare_four_digit_number_is_not_a_founding_year():
    """The failure that prompted the anchoring: a modern telehealth brand
    reported as 'founded 1989' off an unanchored number. A closer repeating
    that on a call would not survive it."""
    got, _ = dossier.years_in_business(
        "Call 1989 today for a free quote. Suite 1989, Building 4.", HOME)
    assert got["value"] is None


def test_copyright_year_is_not_a_founding_year():
    got, _ = dossier.years_in_business("© 1989 Acme Inc. All rights reserved.",
                                       HOME)
    assert got["value"] is None


def test_copyright_near_an_anchor_word_is_still_excluded():
    got, _ = dossier.years_in_business(
        "Acme Home Services. Copyright 1989 Acme. Established leader.", HOME)
    assert got["value"] is None


@pytest.mark.parametrize("text,year", [
    ("Proudly serving Denver since 1998.", 1998),
    ("Founded in 2011 by two brothers.", 2011),
    ("Acme Plumbing, est. 1975", 1975),
    ("Established 2004 and still family run.", 2004),
    ("In business since 2016.", 2016),
])
def test_anchored_founding_year_is_accepted(text, year):
    got, age = dossier.years_in_business(text, HOME)
    assert got["value"] == year
    assert age["value"] == dossier.CURRENT_YEAR - year


def test_founding_year_carries_the_snippet_it_matched():
    """The operator has to be able to sanity-check the figure before repeating
    it, which means seeing the sentence it came from."""
    got, _ = dossier.years_in_business("Proudly serving Denver since 1998.", HOME)
    assert "since 1998" in got["evidence"]
    assert got["source"] == HOME


def test_explicit_years_phrase_without_a_year():
    _, age = dossier.years_in_business("Over 30 years of experience.", HOME)
    assert age["value"] == 30


def test_future_year_is_rejected():
    got, _ = dossier.years_in_business("Founded in 2099.", HOME)
    assert got["value"] is None


# --- headcount --------------------------------------------------------------

@pytest.mark.parametrize("text,n", [
    ("Our team of 45 technicians is ready.", 45),
    ("We employ over 120 employees across the state.", 120),
    ("A staff of 8 keeps things running.", 8),
    ("Our 30 installers are factory trained.", 30),
])
def test_explicit_headcount(text, n):
    assert dossier.headcount(text, HOME)["value"] == n


def test_headcount_is_unknown_when_not_stated():
    """Never estimated: a guessed headcount moves the price band."""
    assert dossier.headcount("We are a friendly local team.", HOME)["value"] is None


def test_headcount_carries_evidence():
    got = dossier.headcount("Our team of 45 technicians is ready.", HOME)
    assert "45" in got["evidence"]
    assert got["source"] == HOME


# --- locations --------------------------------------------------------------

def test_explicit_location_count_wins():
    got = dossier.location_count("Serving you from 12 locations.", [], HOME)
    assert got["value"] == 12
    assert "explicit" in got["method"]


def test_location_pages_are_a_lower_bound():
    links = ["https://acme.com/locations/denver",
             "https://acme.com/locations/boulder",
             "https://acme.com/locations/aurora",
             "https://acme.com/about"]
    got = dossier.location_count("", links, HOME)
    assert got["value"] == 3
    assert "lower bound" in got["method"]


def test_no_location_signal_is_unknown():
    assert dossier.location_count("", ["https://acme.com/about"], HOME)["value"] is None


# --- ownership --------------------------------------------------------------

def test_franchise_beats_independently_owned():
    """'Independently owned and operated' is a franchise tagline. Matching it
    first would misclassify a franchisee as an independent business and drop
    the price band."""
    got = dossier.ownership(
        "Each franchise is independently owned and operated.", HOME)
    assert got["value"] == "franchise"


def test_private_equity_is_detected():
    got = dossier.ownership("Acme is a portfolio company of Ridgeline Capital.",
                            HOME)
    assert got["value"] == "pe-backed"


def test_family_owned_is_independent():
    assert dossier.ownership("A family-owned business.", HOME)["value"] == "independent"


def test_ownership_unknown_when_silent():
    assert dossier.ownership("We fix boilers.", HOME)["value"] is None


# --- platform ---------------------------------------------------------------

def test_shopify_plus_beats_shopify():
    html = '<script src="https://cdn.shopify.com/x.js"></script> shopify plus'
    assert dossier.platform(html, HOME)["value"] == "Shopify Plus"


def test_wordpress_detected():
    assert dossier.platform('<link href="/wp-content/x.css">',
                            HOME)["value"] == "WordPress"


def test_platform_unknown_for_plain_html():
    assert dossier.platform("<html><body>hi</body></html>", HOME)["value"] is None


# --- published prices -------------------------------------------------------

def test_three_prices_on_a_page_counts_as_published():
    got = dossier.published_prices(
        {HOME: "<p>$99</p><p>$149</p><p>$249</p>"})
    assert got["value"] is True
    assert got["source"] == HOME


def test_one_price_is_not_published_pricing():
    got = dossier.published_prices({HOME: "<p>from $99</p>"})
    assert got["value"] is False


# --- decision authority -----------------------------------------------------

@pytest.mark.parametrize("answer,expected", [
    ("I make the decision myself", "sole"),
    ("Me and my business partner decide together", "shared"),
    ("It goes to the board for approval", "committee"),
])
def test_decision_authority(answer, expected):
    assert dossier.decision_authority(answer)["value"] == expected


def test_unanswered_decision_question_is_unknown():
    assert dossier.decision_authority("")["value"] is None


# --- crawl behaviour --------------------------------------------------------

def test_crawl_stays_on_the_prospect_host():
    home = ('<a href="/about">About</a>'
            '<a href="https://facebook.com/acme">FB</a>'
            '<a href="https://acme.com/team">Team</a>')
    links = dossier.internal_links(home, HOME)
    assert "https://acme.com/about" in links
    assert "https://acme.com/team" in links
    assert not any("facebook" in u for u in links)


def test_crawl_respects_the_page_cap():
    home = "".join('<a href="/p%d">x</a>' % i for i in range(50))
    pages = {"https://acme.com/": home}
    pages.update({"https://acme.com/p%d" % i: "<p>page</p>" for i in range(50)})
    got, _seen = dossier.crawl("acme.com", fetch=fetcher(pages), max_pages=5)
    assert len(got) <= 5


def test_robots_disallow_is_honoured():
    """An unattended crawler ignoring robots.txt gets the runner IP blocked."""
    home = '<a href="/private">p</a><a href="/about">a</a>'
    pages = {"https://acme.com/": home,
             "https://acme.com/private": "<p>secret</p>",
             "https://acme.com/about": "<p>about</p>"}
    fetch = fetcher(pages, robots="User-agent: *\nDisallow: /private\n")
    got, _ = dossier.crawl("acme.com", fetch=fetch)
    assert "https://acme.com/about" in got
    assert "https://acme.com/private" not in got


def test_unreachable_robots_allows_the_crawl():
    pages = {"https://acme.com/": '<a href="/about">a</a>',
             "https://acme.com/about": "<p>about</p>"}
    got, _ = dossier.crawl("acme.com", fetch=fetcher(pages))
    assert "https://acme.com/about" in got


def test_a_dead_site_degrades_instead_of_crashing():
    """A prospect site being down must not take out the pipeline trying to
    produce the call brief."""
    d = dossier.build("acme.com", fetch=lambda url: (None, ""))
    assert d["domain"] == "acme.com"
    assert d["company"]["employee_count"]["value"] is None
    assert "employee_count" in d["unknown_fields"]


# --- assembly ---------------------------------------------------------------

def test_full_build_extracts_the_pricing_fields():
    pages = {
        "https://acme.com/": '<a href="/about">About</a><a href="/team">Team</a>'
                             '<link href="/wp-content/x.css">',
        "https://acme.com/about": "<p>Serving Denver since 1998. "
                                  "A family-owned business with 12 locations.</p>",
        "https://acme.com/team": "<p>Our team of 45 technicians is ready.</p>",
    }
    d = build(pages, business_name="Acme", contact_name="Jordan Alvarez")
    c = d["company"]
    assert c["employee_count"]["value"] == 45
    assert c["location_count"]["value"] == 12
    assert c["founded_year"]["value"] == 1998
    assert c["years_in_business"]["value"] == dossier.CURRENT_YEAR - 1998
    assert c["ownership"]["value"] == "independent"
    assert c["platform"]["value"] == "WordPress"


def test_the_dossier_feeds_pricing_directly():
    """dossier.py runs before pricing.py and its output is the hard input."""
    import pricing
    pages = {
        "https://acme.com/": '<a href="/team">Team</a>',
        "https://acme.com/team": "<p>Our team of 45 technicians.</p>",
    }
    rec = pricing.recommend("local", dossier=build(pages))
    assert rec["size_class"] == "mid"
    assert rec["anchor_price"] == 4000


def test_every_established_field_has_a_source():
    pages = {"https://acme.com/": "<p>Our team of 45 technicians. "
                                  "Serving since 1998.</p>"}
    d = build(pages)
    for key, f in d["company"].items():
        if f["value"] is not None and key != "published_prices":
            assert f["source"], "%s has a value but no source" % key
            assert f["method"], "%s has a value but no method" % key


def test_unknown_fields_are_listed_explicitly():
    d = build({"https://acme.com/": "<p>We fix boilers.</p>"})
    assert "employee_count" in d["unknown_fields"]
    assert "ownership" in d["unknown_fields"]


def test_research_urls_are_emitted_never_fetched():
    """LinkedIn prohibits automated access. The closer gets a clickable search
    instead, and the limitation is stated rather than hidden."""
    d = build({"https://acme.com/": "<p>hi</p>"}, contact_name="Jordan Alvarez",
              business_name="Acme")
    assert "linkedin.com" in d["research_urls"]["linkedin_company"]
    assert "linkedin_person" in d["research_urls"]
    assert "Jordan" in d["research_urls"]["linkedin_person"]
    assert any("prohibit automated access" in x for x in d["limits"])
    assert not any("linkedin" in p for p in d["pages_fetched"])


def test_page_count_is_labelled_a_lower_bound():
    d = build({"https://acme.com/": '<a href="/a">a</a><a href="/b">b</a>'})
    assert "lower bound" in d["company"]["page_count"]["method"]


def test_domain_is_required():
    with pytest.raises(dossier.DossierError):
        dossier.build("")


def test_format_dossier_is_slack_mrkdwn_not_a_padded_code_block():
    """The previous version was aligned columns inside a code fence, which
    Slack renders as a wall of monospace -- unreadable on a phone."""
    d = build({"https://acme.com/": "<p>Our team of 45 technicians.</p>"})
    text = dossier.format_dossier(d)
    assert "*The business*" in text
    assert "• *45* staff" in text
    assert "*Confirm on the call*" in text
    assert "```" not in text, "a code fence would kill the mrkdwn"


def test_format_dossier_links_are_slack_link_syntax():
    d = build({"https://acme.com/": "<p>hi</p>"}, business_name="Acme")
    text = dossier.format_dossier(d)
    assert "<https://acme.com|acme.com>" in text
    assert "](" not in text, "markdown link syntax does not render in Slack"
