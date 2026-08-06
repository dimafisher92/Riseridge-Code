"""Acceptance: the engine must reproduce the reference PeterMD audit.

Bar for correctness. The reference PDF was hand-built in Google Docs; this
renders the same figures through the pipeline and checks they survive.
"""

import pathlib

import pytest

import evidence
import render

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    ev = evidence.Evidence.load(FIX / "petermd_evidence.json")
    ev.validate()
    tokens = {
        "business_name": "PeterMD",
        "domain": "getpetermd.com",
        "report_date": "July 2026",
        "exec_summary_intro_html": "<p>PeterMD has built real momentum.</p>",
        "exec_summary_findings_html":
            "<li>95% of every visit comes from people who already knew the name.</li>"
            "<li>Content is ranking for the wrong audience.</li>"
            "<li>A spam link network is attached to the domain.</li>"
            "<li>The money keywords sit on page 4 through 6.</li>",
        "exec_summary_close_html": "<p>The gap is closable. This audit lays out how.</p>",
        "score_content": render.fmt(ev.get("scorecard.content_quality")),
        "score_content_band": "Weak",
        "score_authority": render.fmt(ev.get("scorecard.authority")),
        "score_authority_band": "Weak",
        "score_ux": render.fmt(ev.get("scorecard.user_experience")),
        "score_ux_band": "Fair",
        "scorecard_explanations_html":
            "<li><strong>Content Quality:</strong> pages are not answering what customers ask.</li>"
            "<li><strong>Authority:</strong> high authority from the wrong categories carries no topical trust.</li>"
            "<li><strong>User Experience:</strong> the site works but is slower than it should be.</li>",
        "pos_1_3": render.fmt(ev.get("position_buckets.1-3")),
        "pos_4_10": render.fmt(ev.get("position_buckets.4-10")),
        "pos_11_20": render.fmt(ev.get("position_buckets.11-20")),
        "pos_21_50": render.fmt(ev.get("position_buckets.21-50")),
        "pos_51_100": render.fmt(ev.get("position_buckets.51-100")),
        "position_buckets_close_html":
            "<p>Move even a fraction into the top 10 and non-brand traffic doubles.</p>",
    }
    # Pinned to the four sections this reference reproduction covers. Phase 2b
    # grew DEFAULT_SECTIONS to twelve; this test stays a Phase 1 regression guard.
    html = render.build_html(ev, tokens, section_files=[
        "01_cover.html", "02_exec_summary.html",
        "04_scorecard.html", "07_position_buckets.html"])
    d = tmp_path_factory.mktemp("petermd")
    hp, pp = d / "audit.html", d / "audit.pdf"
    hp.write_text(html, encoding="utf-8")
    render.html_to_pdf(str(hp), str(pp))
    return render.verify_pdf(str(pp)), pp


def test_pdf_has_expected_page_count(rendered):
    info, _ = rendered
    assert info["pages"] == 4


def test_brand_fonts_embedded(rendered):
    info, _ = rendered
    flat = "".join(info["fonts"]).replace("-", "").replace(" ", "")
    assert "HankenGrotesk" in flat
    assert "CormorantGaramond" in flat


@pytest.mark.parametrize("figure", ["77", "171", "268", "834", "1,700"])
def test_position_bucket_figures_survive_to_pdf(rendered, figure):
    info, _ = rendered
    assert figure in info["text"]


@pytest.mark.parametrize("figure", ["28", "27", "49"])
def test_scorecard_figures_survive_to_pdf(rendered, figure):
    info, _ = rendered
    assert figure in info["text"]


def test_client_identity_present(rendered):
    info, _ = rendered
    assert "PeterMD" in info["text"]
    assert "getpetermd.com" in info["text"]


def test_contact_details_present(rendered):
    info, _ = rendered
    assert "riseridge.io" in info["text"]
    assert "+1 786 603 5778" in info["text"]


def test_no_vendor_tooling_named(rendered):
    """verify_pdf already enforces this; asserted explicitly as the requirement."""
    info, _ = rendered
    low = info["text"].lower()
    for vendor in render.FORBIDDEN_VENDORS:
        assert vendor not in low


def test_null_evidence_blocks_are_absent_from_present_sections():
    """paid.* and technical.* are all null in the fixture, so the section gate
    must not offer them. Asserted on present_sections rather than on absent PDF
    text, because absent text would also pass if the section simply did not
    exist yet."""
    ev = evidence.Evidence.load(FIX / "petermd_evidence.json")
    present = ev.present_sections()
    assert "paid" not in present
    assert "technical" not in present
    assert {"scorecard", "position_buckets", "traffic", "backlinks",
            "competitors", "money_keywords", "brand_split",
            "ai_visibility"} <= present


def test_evidence_gate_rejects_incomplete_file():
    bad = evidence.Evidence({"domain": "x.com"})
    with pytest.raises(evidence.EvidenceError):
        bad.validate()
