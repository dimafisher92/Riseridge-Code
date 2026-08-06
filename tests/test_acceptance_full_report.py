"""Acceptance: a complete report rendered from REAL collected evidence.

Phase 1's acceptance test rendered a hand-built fixture transcribed from the
reference PDF. This one renders what the collector actually produced from the
live API, which is the only thing that proves the two halves fit together.

Regenerate the evidence with `python collect.py getpetermd.com --name PeterMD`
(dry-run, read-only) if this skips.
"""

import pathlib
import re

import pytest

import evidence
import render

REAL = (pathlib.Path(__file__).parent.parent / "state" / "prospects" /
        "getpetermd.com" / "evidence.json")

pytestmark = pytest.mark.skipif(
    not REAL.exists(),
    reason="run `python collect.py getpetermd.com --name PeterMD` first")


def _rows(items, *cols):
    """Table rows from evidence records. Numeric columns get the num class.

    A record whose FIRST column is empty is skipped: the anchors feed contains
    rows with a blank anchor string, and rendering one puts an empty cell beside
    a real number in a client-facing table.
    """
    out = []
    for it in items:
        label_key = cols[0][0]
        if not (it.get(label_key) or "").strip():
            continue
        cells = []
        for key, kind in cols:
            v = it.get(key)
            if kind is None:
                cells.append("<td>%s</td>" % (v or ""))
            else:
                cells.append("<td class='num'>%s</td>" % render.fmt(v, kind))
        out.append("<tr>%s</tr>" % "".join(cells))
    return "".join(out)


def build_tokens(ev):
    """Identity plus authored narrative. Every FIGURE comes from evidence."""
    g = ev.get
    d = ev.data
    bl = d.get("backlinks") or {}
    brand = render.fmt(g("brand_split.brand_pct"), "pct")
    nonbrand = render.fmt(g("brand_split.nonbrand_pct"), "pct")
    sample = g("brand_split.sample_rows")

    return {
        "business_name": d.get("business_name"),
        "domain": d.get("domain"),
        "report_date": "August 2026",

        # --- exec summary (authored) ---
        "exec_summary_intro_html":
            "<p>PeterMD has built real momentum. The brand is known, the growth "
            "curve is healthy, and someone has clearly been working on this site.</p>",
        "exec_summary_findings_html":
            "<li>Almost all traffic comes from people who already knew the "
            "PeterMD name. The site is converting existing awareness rather than "
            "creating new demand.</li>"
            "<li>The searches that actually convert customers sit well off page "
            "one, where they earn nothing.</li>"
            "<li>Real link authority exists, but it is not concentrated in the "
            "categories that would make Google and the AI engines treat this as a "
            "medical authority.</li>",
        "exec_summary_close_html":
            "<p>None of this is out of reach. It is a matter of doing the work in "
            "the right order, on the right pages. This audit lays out exactly how.</p>",

        # --- the finding (authored, driven by the real split) ---
        "finding_headline":
            "%s of your traffic already knew your name" % brand,
        "finding_body_html":
            "<p>When we pulled every keyword this site gets clicks from and "
            "separated brand searches from problem searches, the split was "
            "stark. The marketing that drives awareness is working. The website "
            "itself is not currently a customer-acquisition channel &mdash; the "
            "people who do not already know PeterMD are ending up somewhere "
            "else.</p>",
        "finding_data_callout_html":
            "<p>Brand searches account for %s of traffic. Problem searches "
            "&mdash; the people who have a problem and no idea who you are "
            "&mdash; account for %s.</p>" % (brand, nonbrand),
        "finding_why_html":
            "<li>Every marketing dollar spent on awareness has to keep working, "
            "because nothing compounds from search.</li>"
            "<li>Competitors are collecting the problem searches and converting "
            "them into recurring patients.</li>"
            "<li>This is the fastest thing on this audit to fix, because the "
            "rankings already exist &mdash; they are just on the wrong page.</li>",

        # --- traffic and rankings (all from evidence) ---
        "visits": render.fmt(g("traffic.monthly_organic_visits"), "k"),
        "keyword_count": render.fmt(g("traffic.ranking_keyword_count")),
        "traffic_value": render.fmt(g("traffic.traffic_value_usd"), "usd"),
        "brand_pct": brand,
        "nonbrand_pct": nonbrand,
        "brand_split_basis": ("top %s keywords by traffic" % render.fmt(sample)
                              if sample else render.DASH),
        "money_keywords_rows_html": _rows(
            d.get("money_keywords") or [],
            ("keyword", None), ("volume", "int"), ("position", "int")),
        "traffic_close_html":
            "<p>Every one of those searches is somebody with money in their "
            "pocket asking Google to solve exactly what PeterMD sells. On every "
            "one of them, PeterMD is invisible.</p>",

        # --- position distribution ---
        "pos_1_3": render.fmt(g("position_buckets.1-3")),
        "pos_4_10": render.fmt(g("position_buckets.4-10")),
        "pos_11_20": render.fmt(g("position_buckets.11-20")),
        "pos_21_50": render.fmt(g("position_buckets.21-50")),
        "pos_51_100": render.fmt(g("position_buckets.51-100")),
        "position_buckets_close_html":
            "<p>Google already indexes and ranks these pages and considers them "
            "relevant enough to show. They are simply not strong enough to reach "
            "page one yet. Move a fraction into the top ten and non-brand traffic "
            "climbs sharply.</p>",

        # --- link profile ---
        "referring_domains": render.fmt(g("backlinks.referring_domains")),
        "total_backlinks": render.fmt(g("backlinks.total_backlinks")),
        "authority": render.fmt(g("backlinks.authority")),
        "authority_label": (bl.get("authority") or {}).get("metric_name",
                                                           "Link authority"),
        "trust": render.fmt(g("backlinks.trust")),
        "trust_label": (bl.get("trust") or {}).get("metric_name", "Link trust"),
        "anchor_rows_html": _rows(
            (bl.get("top_anchors") or [])[:8], ("anchor", None), ("count", "int")),
        "link_profile_findings_html":
            "<li>The link volume is real, so the investment has been made.</li>"
            "<li>What is missing is category relevance: authority from the wrong "
            "kind of site does not transfer medical trust, which is why it has "
            "not converted into rankings on the terms that matter.</li>"
            "<li>Redirecting that same effort toward health publications, doctor "
            "directories and industry press is the highest-leverage change "
            "available here.</li>",

        # --- competitors ---
        "competitor_rows_html": _rows(
            (d.get("competitors") or [])[:6],
            ("domain", None), ("monthly_visits", "k"), ("ranking_keywords", "int")),
        "competitor_pattern_html":
            "<li>Their content is pointed at what buyers actually search, at "
            "every stage from research to ready-to-buy.</li>"
            "<li>They do not spend content budget on the wrong audience.</li>"
            "<li>They show up when someone asks an AI assistant where to go.</li>",

        # --- the plan ---
        "plan_days_1_30_html":
            "<li>Full technical audit and cleanup of the issues dragging every "
            "page down.</li>"
            "<li>Link profile audit, with a disavow filed for anything risky.</li>"
            "<li>AI search visibility mapping: every question the assistants are "
            "being asked in this category, and where PeterMD needs to appear.</li>",
        "plan_days_31_60_html":
            "<li>Rebuild the highest-value pages in the structure AI models "
            "actually reward.</li>"
            "<li>Target the keywords already sitting on page two and push them "
            "into the top ten.</li>"
            "<li>Build new pages capturing the high-intent, non-branded searches "
            "PeterMD is invisible for today.</li>",
        "plan_days_61_90_html":
            "<li>Secure real third-party links from health publications, doctor "
            "directories and industry sites.</li>"
            "<li>Expand presence across all five major AI answer engines.</li>"
            "<li>Stand up reporting so the whole team can see rankings, traffic "
            "and revenue impact in real time.</li>",
        "plan_outcome_html":
            "<p>Based on what the data shows, the realistic outcome is a "
            "materially larger share of traffic coming from people who did not "
            "already know the brand &mdash; the difference between a channel that "
            "reinforces your existing marketing and one that generates net-new "
            "patients on its own.</p>",
    }


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    ev = evidence.Evidence.load(REAL)
    ev.validate()
    html = render.build_html(ev, build_tokens(ev))
    d = tmp_path_factory.mktemp("full")
    hp, pp = d / "audit.html", d / "audit.pdf"
    hp.write_text(html, encoding="utf-8")
    render.html_to_pdf(str(hp), str(pp))
    return render.verify_pdf(str(pp)), pp, ev, html


def test_report_is_substantially_longer_than_phase_one(rendered):
    info, _, _, _html = rendered
    assert info["pages"] >= 7


def test_all_three_brand_fonts_embedded(rendered):
    info, _, _, _html = rendered
    flat = "".join(info["fonts"]).replace("-", "").replace(" ", "")
    for f in ("HankenGrotesk", "CormorantGaramond", "SpaceMono"):
        assert f in flat, "%s missing" % f


def test_every_present_section_appears(rendered):
    info, _, ev, _html = rendered
    headings = {"traffic": "Traffic & Rankings",
                "position_buckets": "Where the Rankings Already Sit",
                "backlinks": "The Link Profile",
                "competitors": "Winning Right Now"}
    for key, heading in headings.items():
        if key in ev.present_sections():
            assert heading in info["text"], "%s present but %r missing" % (key, heading)


def test_absent_sections_do_not_appear(rendered):
    info, _, ev, _html = rendered
    for key, heading in (("ai_visibility", "The AI Search Opportunity"),
                         ("paid", "Paid vs Organic"),
                         ("scorecard", "The Visibility Scorecard")):
        assert key not in ev.present_sections()
        assert heading not in info["text"], "%r rendered without evidence" % heading


def test_no_stat_tile_renders_as_a_bare_dash(rendered):
    """The em dash is legitimate punctuation in the prose, so this checks the
    HTML for a tile VALUE that is only a dash -- which would mean the tile gate
    failed and an authoritative-looking box is standing empty in front of a
    prospect."""
    _info, _pp, _ev, html = rendered
    for cls in ('class="v"', 'class="num"', 'class="band"'):
        bad = re.findall(r'<[^>]*%s[^>]*>\s*%s\s*<' % (cls, render.DASH), html)
        assert not bad, "%d %s element(s) render as a bare dash" % (len(bad), cls)


def test_no_dash_in_any_table_cell(rendered):
    """A dash inside a data table would be an absent figure printed as if real."""
    _info, _pp, _ev, html = rendered
    assert ">%s<" % render.DASH not in html.replace(" ", ""), (
        "a table cell contains only an em dash")


def test_no_unsubstituted_token_and_no_vendor_name(rendered):
    info, _, _, _html = rendered
    assert "{{" not in info["text"]
    low = info["text"].lower()
    for vendor in render.FORBIDDEN_VENDORS:
        assert vendor not in low


def test_the_sample_basis_is_disclosed(rendered):
    info, _, _, _html = rendered
    assert "keywords by traffic" in info["text"]


def test_money_keywords_table_is_not_thin(rendered):
    info, _, ev, _html = rendered
    n = len(ev.data.get("money_keywords") or [])
    assert n >= 5, "only %d money keywords; the 500-keyword fetch should fill it" % n


def test_no_table_row_has_an_empty_label_cell(rendered):
    """The anchors feed contains blank-anchor records; rendering one leaves an
    empty cell beside a real number in a client-facing table."""
    _info, _pp, _ev, html = rendered
    assert "<td></td>" not in html.replace(" ", "")
    assert "<td> </td>" not in html


def test_logo_is_present_on_cover_and_signoff(rendered):
    _info, _pp, _ev, html = rendered
    assert html.count('class="rr-mark"') == 2, "monogram on the cover and the CTA"
    assert "PARTIAL:logo" not in html, "partial was not expanded"


def test_logo_is_vector_not_a_raster_image(rendered):
    """Chrome's --print-to-pdf does not embed an external <img src="*.svg">, so a
    linked logo renders BLANK in the PDF while looking fine on screen. The mark
    must be inline SVG, which means the PDF carries zero raster images."""
    import fitz
    _info, pp, _ev, _html = rendered
    doc = fitz.open(str(pp))
    try:
        total = sum(len(page.get_images()) for page in doc)
        first = doc[0]
        assert len(first.get_drawings()) > 0, "cover has no vector art at all"
    finally:
        doc.close()
    assert total == 0, "%d raster image(s) embedded; the logo must stay vector" % total


def test_narrative_sections_always_render(rendered):
    info, _, _, _html = rendered
    for want in ("The Finding That Changes Everything", "The 90-Day Plan",
                 "What Happens Next", "riseridge.io"):
        assert want in info["text"]
