import os
import re
import subprocess

import pytest

import evidence
import render


def ev(extra=None):
    d = {"domain": "example.com", "business_name": "Example",
         "generated_at": "2026-08-04T00:00:00Z"}
    d.update(extra or {})
    return evidence.Evidence(d)


# --- fmt ------------------------------------------------------------------

def test_fmt_int_adds_thousands_separator():
    assert render.fmt(90500) == "90,500"


def test_fmt_k_abbreviates_thousands():
    assert render.fmt(10800, "k") == "10.8K"


def test_fmt_usd_prefixes_and_abbreviates():
    assert render.fmt(61900, "usd") == "$61.9K"


def test_fmt_pct_appends_sign():
    assert render.fmt(95, "pct") == "95%"


def test_fmt_none_returns_placeholder_dash():
    assert render.fmt(None) == "—"


def test_fmt_preserves_fractional_part_of_a_float_metric():
    """fixtures/petermd_evidence.json carries backlinks.authority = 71.8. The
    old `int(n)` truncation would print 71 and a human would read the wrong
    number to a prospect."""
    assert render.fmt(71.8) == "71.8"


def test_fmt_integral_float_still_formats_as_int_with_separator():
    assert render.fmt(1700.0) == "1,700"


def test_fmt_zero_is_not_treated_as_missing():
    assert render.fmt(0) == "0"


# --- section stripping ----------------------------------------------------

def test_absent_section_is_removed():
    h = "A<!--SECTION:paid-->PAID<!--/SECTION:paid-->B"
    assert render.strip_absent_sections(h, present=set()) == "AB"


def test_present_section_is_kept():
    h = "A<!--SECTION:paid-->PAID<!--/SECTION:paid-->B"
    out = render.strip_absent_sections(h, present={"paid"})
    assert "PAID" in out
    assert "SECTION:paid" not in out


def test_stripping_handles_two_sections_independently():
    h = ("<!--SECTION:paid-->P<!--/SECTION:paid-->"
         "<!--SECTION:traffic-->T<!--/SECTION:traffic-->")
    out = render.strip_absent_sections(h, present={"traffic"})
    assert "T" in out and "P" not in out


def test_stripping_removes_tokens_inside_absent_section():
    h = "<!--SECTION:paid-->{{paid_spend}}<!--/SECTION:paid-->"
    assert "{{" not in render.strip_absent_sections(h, present=set())


# --- token gate -----------------------------------------------------------

def test_assert_no_tokens_passes_on_clean_html():
    render.assert_no_tokens("<p>done</p>")


def test_assert_no_tokens_raises_and_names_the_token():
    with pytest.raises(render.RenderError) as e:
        render.assert_no_tokens("<p>{{business_name}}</p>")
    assert "business_name" in str(e.value)


def test_assert_no_tokens_reports_every_leftover():
    with pytest.raises(render.RenderError) as e:
        render.assert_no_tokens("{{a}} {{b}}")
    msg = str(e.value)
    assert "a" in msg and "b" in msg


# --- build_html -----------------------------------------------------------

def test_build_html_substitutes_tokens():
    out = render.build_html(ev(), {"business_name": "Example",
                                   "domain": "example.com",
                                   "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    assert "Example" in out
    assert "{{" not in out


def test_build_html_inlines_font_css_not_a_remote_link():
    out = render.build_html(ev(), {"business_name": "E", "domain": "d",
                                   "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    assert "fonts.googleapis.com" not in out
    assert "@font-face" in out


def test_build_html_sets_letter_page_size():
    out = render.build_html(ev(), {"business_name": "E", "domain": "d",
                                   "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    assert "size: Letter" in out


def test_build_html_omits_scorecard_when_evidence_absent():
    out = render.build_html(ev(), {"business_name": "E", "domain": "d",
                                   "report_date": "August 2026"},
                            section_files=["01_cover.html", "04_scorecard.html"])
    assert "Visibility Scorecard" not in out


def _scorecard_ev():
    return ev({"scorecard": {"content_quality": {"value": 28, "basis": "b"},
                             "authority": {"value": 27, "basis": "b"},
                             "user_experience": {"value": 49, "basis": "b"}}})


def _scorecard_tokens():
    return {"business_name": "E", "domain": "d", "report_date": "August 2026",
            "score_content": "28", "score_content_band": "Weak",
            "score_authority": "27", "score_authority_band": "Weak",
            "score_ux": "49", "score_ux_band": "Fair",
            "scorecard_explanations_html": "<li>x</li>"}


def test_build_html_includes_scorecard_when_evidence_present():
    out = render.build_html(_scorecard_ev(), _scorecard_tokens(),
                            section_files=["01_cover.html", "04_scorecard.html"])
    assert "Visibility Scorecard" in out
    assert "28" in out


def test_build_html_raises_on_unknown_token():
    with pytest.raises(render.RenderError):
        render.build_html(ev(), {}, section_files=["01_cover.html"])


def test_build_html_raises_when_present_section_lacks_its_tokens():
    """A section kept by the section gate must still have every token supplied."""
    with pytest.raises(render.RenderError) as e:
        render.build_html(_scorecard_ev(),
                          {"business_name": "E", "domain": "d",
                           "report_date": "August 2026"},
                          section_files=["01_cover.html", "04_scorecard.html"])
    assert "score_content" in str(e.value)


def test_build_html_names_no_vendor_tools():
    out = render.build_html(_scorecard_ev(), _scorecard_tokens(),
                            section_files=["01_cover.html", "04_scorecard.html"])
    low = out.lower()
    for vendor in ("searchatlas", "ahrefs", "semrush", "majestic", "similarweb"):
        assert vendor not in low


# --- substitution safety and HTML escaping ---------------------------------

def test_build_html_does_not_rescan_a_substituted_value():
    """A token value that itself contains a literal {{token}} must not be
    re-substituted, and must not falsely trip the token gate."""
    out = render.build_html(ev(), {"business_name": "E",
                                   "domain": "literal {{report_date}} here",
                                   "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    # `domain` appears exactly once in 01_cover.html (the .client div), so its
    # literal "{{report_date}}" text must survive unsubstituted exactly once.
    assert out.count("{{report_date}}") == 1


def test_build_html_escapes_ampersand_in_business_name():
    out = render.build_html(ev(), {"business_name": "Smith & Sons Plumbing",
                                   "domain": "d", "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    assert "Smith &amp; Sons Plumbing" in out


def test_build_html_escapes_angle_brackets_in_business_name():
    out = render.build_html(ev(), {"business_name": "<script>Evil</script>",
                                   "domain": "d", "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_build_html_html_suffixed_token_is_not_escaped():
    out = render.build_html(ev(), {"business_name": "E", "domain": "d",
                                   "report_date": "August 2026",
                                   "exec_summary_intro_html": "<p>intro</p>",
                                   "exec_summary_findings_html": "<li>x</li>",
                                   "exec_summary_close_html": "<p>close</p>"},
                            section_files=["01_cover.html", "02_exec_summary.html"])
    assert "<li>x</li>" in out
    assert "&lt;li&gt;" not in out


def test_build_html_raises_naming_missing_token():
    with pytest.raises(render.RenderError) as e:
        render.build_html(ev(), {"business_name": "E", "domain": "d"},
                          section_files=["01_cover.html"])
    assert "report_date" in str(e.value)


def test_build_html_escapes_double_quote_in_token_value():
    """quote=True: a token value carrying a double quote must not be able to
    break out of an HTML attribute in a future template (e.g. alt="{{...}}").
    In text content &quot; simply renders back as ", so the visible text is
    unaffected."""
    out = render.build_html(ev(), {"business_name": 'Joe"s Pizza',
                                   "domain": "d", "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    assert "<h1>Joe&quot;s Pizza</h1>" in out
    assert '<h1>Joe"s Pizza</h1>' not in out


# --- PDF rendering and verification ----------------------------------------

def _cover_pdf(tmp_path, name, business="Example"):
    """Render the cover to a PDF and return its path."""
    e = evidence.Evidence({"domain": "example.com", "business_name": business,
                           "generated_at": "2026-08-04T00:00:00Z"})
    html = render.build_html(e, {"business_name": business,
                                 "domain": "example.com",
                                 "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    hp = tmp_path / (name + ".html")
    hp.write_text(html, encoding="utf-8")
    pp = tmp_path / (name + ".pdf")
    render.html_to_pdf(str(hp), str(pp))
    return pp


def test_pdf_is_produced_with_brand_fonts_embedded(tmp_path):
    pp = _cover_pdf(tmp_path, "a")
    assert pp.exists() and pp.stat().st_size > 10_000

    info = render.verify_pdf(str(pp), must_contain=("Example", "riseridge.io"))
    assert info["pages"] >= 1
    joined = "".join(info["fonts"]).replace("-", "").replace(" ", "")
    assert "HankenGrotesk" in joined
    assert "CormorantGaramond" in joined


def test_verify_pdf_raises_on_missing_required_phrase(tmp_path):
    pp = _cover_pdf(tmp_path, "b")
    with pytest.raises(render.RenderError) as exc:
        render.verify_pdf(str(pp), must_contain=("ThisPhraseIsNotPresent",))
    assert "ThisPhraseIsNotPresent" in str(exc.value)


def _letter_spaced_vendor_pdf(tmp_path, name, vendor_text):
    """Render the real cover template, then place `vendor_text` inside its
    `.eyebrow` element — the one occurrence of a letter-spaced class in the
    shipped templates that a token doesn't already own.

    brand.css letter-spaces six classes (.eyebrow, .stat .k, .score .k/.band,
    th, .callout .t) at 0.1em-0.16em. Empirically, only .eyebrow's 0.16em
    reliably makes PyMuPDF insert a space between every glyph in this Chrome
    build ("AHREFS" -> "A H R E F S"); 0.1em-0.12em (.score .band, .score .k,
    th) did not reproduce the split in testing here. The whitespace-stripped
    gate is written to catch all of them regardless of that threshold, but
    .eyebrow is the element this test can prove defeats the OLD (unstripped)
    gate, which is the property this test exists to pin down."""
    e = evidence.Evidence({"domain": "example.com", "business_name": "Example",
                           "generated_at": "2026-08-04T00:00:00Z"})
    out = render.build_html(e, {"business_name": "Example", "domain": "example.com",
                                "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    marker = '<div class="eyebrow">AI Search Visibility Audit</div>'
    assert marker in out  # fail loudly if 01_cover.html ever changes shape
    out = out.replace(marker, '<div class="eyebrow">%s</div>' % vendor_text, 1)
    hp = tmp_path / (name + ".html")
    hp.write_text(out, encoding="utf-8")
    pp = tmp_path / (name + ".pdf")
    render.html_to_pdf(str(hp), str(pp))
    return pp


def test_verify_pdf_rejects_vendor_tool_names(tmp_path):
    """The audit must never name the tooling behind it, even by accident.

    business_name lands in <h1>/.client, which brand.css does NOT
    letter-space, so putting the vendor name there (as this test used to do)
    passes even a gate that only checks unstripped substrings. This rewrite
    puts the vendor name inside .eyebrow instead, which brand.css DOES
    letter-space (0.16em) and which PyMuPDF splits into single, space-joined
    glyphs on extraction -- verified directly: reverting verify_pdf to the old
    `if v in body.lower()` check and running this exact PDF through it does
    NOT raise, proving the old gate misses this and the new one is required."""
    pp = _letter_spaced_vendor_pdf(tmp_path, "c", "Ahrefs")
    with pytest.raises(render.RenderError) as exc:
        render.verify_pdf(str(pp))
    assert "ahrefs" in str(exc.value).lower()


def test_verify_pdf_rejects_spaced_vendor_spelling(tmp_path):
    """Search Atlas is how the vendor actually writes its own name. Only the
    concatenated 'searchatlas' is in FORBIDDEN_VENDORS, so the spaced spelling
    must be caught by the whitespace-stripped comparison."""
    pp = _cover_pdf(tmp_path, "d", business="Search Atlas")
    with pytest.raises(render.RenderError) as exc:
        render.verify_pdf(str(pp))
    assert "searchatlas" in str(exc.value).lower()


def test_verify_pdf_must_contain_matches_multiword_phrase_with_spaces(tmp_path):
    """Regression guard: must_contain is checked against the raw body, not the
    whitespace-stripped copy used for the vendor gate. If someone later
    "simplifies" this by reusing the stripped copy for must_contain too, a
    legitimate multi-word phrase like the phone number would stop matching,
    since the search phrase itself still has spaces in it."""
    pp = _cover_pdf(tmp_path, "e")
    info = render.verify_pdf(str(pp), must_contain=("+1 786 603 5778",))
    assert "+1 786 603 5778" in info["text"]


def test_html_to_pdf_raises_when_chrome_missing(tmp_path, monkeypatch):
    # RR_CHROME takes precedence over the module default, so a runner that sets
    # it would otherwise mask the missing-binary guard this test exists to pin.
    monkeypatch.delenv(render.CHROME_ENV, raising=False)
    monkeypatch.setattr(render, "CHROME", str(tmp_path / "nope.exe"))
    with pytest.raises(render.RenderError) as exc:
        render.html_to_pdf(str(tmp_path / "x.html"), str(tmp_path / "x.pdf"))
    assert "Chrome not found" in str(exc.value)


def test_page_overflow_flows_to_additional_pages_without_losing_content(tmp_path):
    """brand.css's `.page` used to be `height: 11in; overflow: hidden`, which
    silently clipped any section taller than one sheet. It is now
    `min-height: 11in` with no `overflow: hidden`, so a section that overflows
    must flow onto additional sheets instead of losing content. This test
    pins down the fixed, correct behaviour: every one of 60 findings and the
    closing paragraph must survive, spread across more than one PDF page."""
    e = evidence.Evidence({"domain": "example.com", "business_name": "Example",
                           "generated_at": "2026-08-04T00:00:00Z"})
    findings = "".join(
        "<li>Finding number %d: enough descriptive text to occupy real "
        "vertical space in the findings list on a printed page.</li>" % i
        for i in range(60)
    )
    tokens = {"business_name": "Example", "domain": "example.com",
              "report_date": "August 2026",
              "exec_summary_intro_html": "<p>intro</p>",
              "exec_summary_findings_html": findings,
              "exec_summary_close_html": "<p>the closing paragraph</p>"}
    out = render.build_html(e, tokens, section_files=["02_exec_summary.html"])
    hp = tmp_path / "overflow.html"
    hp.write_text(out, encoding="utf-8")
    pp = tmp_path / "overflow.pdf"
    render.html_to_pdf(str(hp), str(pp))

    info = render.verify_pdf(str(pp), must_contain=("Finding number 0",
                                                     "Finding number 59",
                                                     "the closing paragraph"))
    assert info["pages"] > 1
    for i in range(60):
        assert ("Finding number %d:" % i) in info["text"]


def test_html_to_pdf_wraps_timeout_in_render_error(tmp_path, monkeypatch):
    """A Chrome hang must surface as a RenderError the pipeline can handle,
    not a bare subprocess.TimeoutExpired escaping unwrapped."""
    fake_chrome = tmp_path / "chrome.exe"
    fake_chrome.write_text("", encoding="utf-8")
    monkeypatch.setattr(render, "CHROME", str(fake_chrome))

    def fake_run(cmd, capture_output=True, timeout=None):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(render.subprocess, "run", fake_run)

    html_path = str(tmp_path / "x.html")
    with pytest.raises(render.RenderError) as exc:
        render.html_to_pdf(html_path, str(tmp_path / "x.pdf"))
    msg = str(exc.value)
    assert "timed out" in msg.lower()
    assert html_path in msg


# --- template integrity -----------------------------------------------------

def test_section_markers_are_balanced_and_known_to_evidence():
    """TOKEN and the SECTION marker regexes only match an exact shape. A
    typo'd or uppercase marker id (e.g. `<!--SECTION:Scorecard-->` instead of
    `scorecard`) is silently never matched by strip_absent_sections's presence
    check, so the section vanishes from every audit forever with all tests
    still green. Walk every real section file and pin down that every open
    marker has a matching close, and every id is a real evidence section."""
    for name in sorted(os.listdir(render.SECTIONS)):
        path = os.path.join(render.SECTIONS, name)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        opens = re.findall(r"<!--SECTION:([^\s>-]+(?:-[^\s>-]+)*)-->", content)
        closes = re.findall(r"<!--/SECTION:([^\s>-]+(?:-[^\s>-]+)*)-->", content)
        assert sorted(opens) == sorted(closes), (
            "%s: unmatched SECTION markers, opens=%s closes=%s"
            % (name, opens, closes))
        for sid in opens:
            assert sid in evidence.SECTION_REQUIREMENTS, (
                "%s: SECTION id %r is not a key in evidence.SECTION_REQUIREMENTS "
                "(%s) -- this section would silently never render"
                % (name, sid, sorted(evidence.SECTION_REQUIREMENTS)))


# --- malformed tokens (A4) ---------------------------------------------------

def test_malformed_token_survives_build_html_but_is_caught_by_verify_pdf(
        tmp_path, monkeypatch):
    """TOKEN = r"\\{\\{([a-z0-9_]+)\\}\\}" does not match `{{ spaced_token }}`
    (internal spaces) or anything else outside that exact shape, so a template
    typo of that kind is invisible to both substitution and the missing-token
    gate in build_html: it prints verbatim into the finished PDF. verify_pdf is
    the last-mile catch: any literal '{{' surviving into extracted PDF text
    must raise. Uses a temporary section file (via a monkeypatched SECTIONS
    dir) so the real templates are never touched."""
    monkeypatch.setattr(render, "SECTIONS", str(tmp_path))
    (tmp_path / "99_bad.html").write_text(
        '<div class="page"><h3>{{business_name}}</h3><p>report date: '
        '{{ spaced_token }}</p></div>',
        encoding="utf-8")
    e = evidence.Evidence({"domain": "example.com", "business_name": "Example",
                           "generated_at": "2026-08-04T00:00:00Z"})

    out = render.build_html(e, {"business_name": "Example"},
                            section_files=["99_bad.html"])
    # This is the bug: build_html raises for nothing here, and the malformed
    # token survives verbatim rather than being flagged as missing.
    assert "{{ spaced_token }}" in out

    hp = tmp_path / "bad.html"
    hp.write_text(out, encoding="utf-8")
    pp = tmp_path / "bad.pdf"
    render.html_to_pdf(str(hp), str(pp))

    with pytest.raises(render.RenderError) as exc:
        render.verify_pdf(str(pp))
    assert "token" in str(exc.value).lower()
