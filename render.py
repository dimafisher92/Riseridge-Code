"""Assemble the audit HTML from templates, evidence and authored narrative.

Two gates run here. Sections whose evidence is absent are stripped before
substitution, so a missing metric drops its section instead of printing a guess.
Then the token gate refuses to emit HTML that still contains {{placeholders}}.
"""

import html
import os
import re

import fonts

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(BASE, "templates")
SECTIONS = os.path.join(TEMPLATES, "sections")
PARTIALS = os.path.join(TEMPLATES, "partials")

DEFAULT_SECTIONS = [
    "01_cover.html",
    "02_exec_summary.html",
    "03_finding.html",
    "04_scorecard.html",
    "05_ai_visibility.html",
    "06_traffic_rankings.html",
    "06b_site_audit.html",
    "07_position_buckets.html",
    "08_paid_vs_organic.html",
    "09_link_profile.html",
    "10_competitors.html",
    "11_ninety_day_plan.html",
    "12_next_steps.html",
]

TOKEN = re.compile(r"\{\{([a-z0-9_]+)\}\}", re.I)
DASH = "—"


class RenderError(Exception):
    pass


def fmt(n, kind="int"):
    """Format a metric for print. None renders as an em dash."""
    if n is None:
        return DASH
    if kind == "pct":
        return "%g%%" % n
    if kind == "k":
        return "%.1fK" % (n / 1000.0) if n >= 1000 else "%g" % n
    if kind == "usd":
        return "$%.1fK" % (n / 1000.0) if n >= 1000 else "$%g" % n
    if isinstance(n, float) and not n.is_integer():
        return "{:,.10g}".format(n)
    return "{:,}".format(int(n))


def _section_re(sid):
    return re.compile(
        r"<!--SECTION:%s-->.*?<!--/SECTION:%s-->" % (re.escape(sid), re.escape(sid)),
        re.S,
    )


def strip_absent_sections(html, present):
    """Remove every marked section whose id is not in `present`, then drop the
    markers of the ones that stay."""
    for sid in sorted(set(re.findall(r"<!--SECTION:([a-z0-9_\-]+)-->", html, re.I))):
        if sid not in present:
            html = _section_re(sid).sub("", html)
    html = re.sub(r"<!--/?SECTION:[a-z0-9_\-]+-->", "", html, flags=re.I)
    return html


PARTIAL = re.compile(r"<!--PARTIAL:([a-z0-9_]+)-->", re.I)


def expand_partials(html):
    """Inline <!--PARTIAL:name--> from templates/partials/name.html."""
    def sub(m):
        return _read(os.path.join(PARTIALS, m.group(1) + ".html"))
    return PARTIAL.sub(sub, html)


TILE = re.compile(r"<!--TILE:([a-z0-9_]+)-->", re.I)


def _tile_re(name):
    return re.compile(
        r"<!--TILE:%s-->.*?<!--/TILE:%s-->" % (re.escape(name), re.escape(name)),
        re.S | re.I,
    )


def strip_absent_tiles(html, tokens):
    """Remove a marked tile whose token has no value, then drop the markers.

    The section gate drops a section when every metric in it is null. This is the
    finer grain the spec asks for: "a null value omits its section OR stat tile".
    Without it a partially-populated section prints an em dash inside a tile
    surrounded by authoritative copy. A measured 0 is a fact and is kept; only
    DASH, None and empty drop.
    """
    for name in sorted(set(TILE.findall(html))):
        v = tokens.get(name)
        if v is None or v == "" or v == DASH:
            html = _tile_re(name).sub("", html)
    return re.sub(r"<!--/?TILE:[a-z0-9_]+-->", "", html, flags=re.I)


NUMBER_MARK = re.compile(r"<!--NUM-->")


def number_sections(html):
    """Number the section eyebrows in the order the sections survived.

    The numbers used to be written into each template. That cannot be right
    here: sections drop when their evidence is absent, so a fixed number either
    collides with a neighbour or leaves a gap -- the site audit landed as a
    second "05" sitting ahead of "04". A template now says only that it wants a
    number; which one it gets is a property of the assembled document, so the
    renderer decides it after the section gate has run.
    """
    n = [0]

    def sub(_):
        n[0] += 1
        return "%02d" % n[0]

    return NUMBER_MARK.sub(sub, html)


def assert_no_tokens(html):
    left = sorted(set(TOKEN.findall(html)))
    if left:
        raise RenderError("unreplaced tokens: %s" % ", ".join(left))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_html(ev, tokens, section_files=None):
    names = section_files or DEFAULT_SECTIONS
    body = "\n".join(_read(os.path.join(SECTIONS, n)) for n in names)

    shell = _read(os.path.join(TEMPLATES, "base.html"))
    shell = shell.replace("/*FONT_CSS*/", fonts.font_css())
    shell = shell.replace("/*BRAND_CSS*/", _read(os.path.join(TEMPLATES, "brand.css")))
    doc = shell.replace("<!--SECTIONS-->", body)

    # Partials are inlined before the gates so a mark inside a gated section is
    # stripped with it. The monogram carries no tokens -- brand.css colours it
    # per surface -- so nothing here needs a value.
    doc = expand_partials(doc)

    doc = strip_absent_sections(doc, ev.present_sections())
    doc = strip_absent_tiles(doc, tokens)
    doc = number_sections(doc)

    missing = []

    def _sub(m):
        key = m.group(1)
        if key not in tokens:
            missing.append(key)
            return m.group(0)
        v = tokens[key]
        if v is None:
            return ""
        s = str(v)
        # *_html tokens carry authored markup. Everything else is escaped:
        # business names come from prospect-typed form data and routinely
        # contain '&' (e.g. "Smith & Sons Plumbing").
        return s if key.endswith("_html") else html.escape(s, quote=True)

    doc = TOKEN.sub(_sub, doc)
    if missing:
        raise RenderError("unreplaced tokens: %s" % ", ".join(sorted(set(missing))))
    return doc


import subprocess

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
# The default is the operator's Windows install, and CI runs on Windows for the
# same reason. Both are overridable so the same renderer works on a Linux
# runner: RR_CHROME points at the binary, RR_CHROME_FLAGS adds flags such as
# --no-sandbox, which Chrome requires when it runs as root in a container.
CHROME_ENV = "RR_CHROME"
CHROME_FLAGS_ENV = "RR_CHROME_FLAGS"


def chrome_path():
    return os.environ.get(CHROME_ENV) or CHROME


def chrome_extra_flags():
    return [f for f in os.environ.get(CHROME_FLAGS_ENV, "").split() if f]
# The brand families. Chrome only embeds a font a page actually USES, so a
# partial render legitimately lacks one of these -- requiring all three would
# fail a cover-only render that has no monospace text. The real failure mode is
# Chrome silently SUBSTITUTING, so the gate asserts at least one brand family is
# present and no fallback family is. The full-report acceptance test separately
# asserts all three, because the full report does use all three.
BRAND_FONTS = ("HankenGrotesk", "CormorantGaramond", "SpaceMono")
FALLBACK_FONTS = ("Arial", "TimesNewRoman", "Courier", "Helvetica")
FORBIDDEN_VENDORS = ("searchatlas", "ahrefs", "semrush", "majestic", "similarweb")


def html_to_pdf(html_path, pdf_path):
    """Print HTML to PDF with headless Chrome. Flag set is load-bearing:
    --no-pdf-header-footer removes Chrome's URL/date furniture, --no-margins
    lets @page own the geometry."""
    binary = chrome_path()
    if not os.path.exists(binary):
        raise RenderError("Chrome not found at %s" % binary)
    cmd = [
        binary, "--headless=new", "--disable-gpu",
        "--no-pdf-header-footer", "--no-margins",
    ] + chrome_extra_flags() + [
        "--print-to-pdf=%s" % pdf_path,
        html_path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise RenderError("Chrome timed out after 180s rendering %s" % html_path)
    if not os.path.exists(pdf_path):
        raise RenderError("Chrome produced no PDF: %s"
                          % r.stderr.decode("utf-8", "replace")[:400])


def verify_pdf(pdf_path, must_contain=()):
    """Assert the PDF actually embedded what it should.

    Chrome degrades silently: it substitutes Arial for fonts it failed to embed
    and leaves blanks where external SVGs were, producing a file that looks fine
    on screen and wrong in print.
    """
    import fitz

    doc = fitz.open(pdf_path)
    found_fonts, text = set(), []
    for page in doc:
        for f in page.get_fonts():
            found_fonts.add(f[3])
        text.append(page.get_text())
    doc.close()
    body = "\n".join(text)

    if len(text) == 0:
        raise RenderError("PDF has zero pages")

    flat = "".join(found_fonts).replace("-", "").replace(" ", "")
    if not any(f in flat for f in BRAND_FONTS):
        raise RenderError(
            "no brand font embedded (found: %s). Chrome fell back; check that "
            "fonts are base64-inlined one weight per call."
            % (", ".join(sorted(found_fonts)) or "none"))
    substituted = [f for f in FALLBACK_FONTS if f in flat]
    if substituted:
        raise RenderError(
            "Chrome substituted a fallback font: %s (found: %s). The brand faces "
            "did not embed." % (", ".join(substituted), ", ".join(sorted(found_fonts))))

    # CSS letter-spacing (brand.css: .eyebrow, .stat .k, .score .k/.band, th,
    # .callout .t) makes PyMuPDF insert a space between every glyph, so
    # "AHREFS" extracts as "A H R E F S". Match against a whitespace-stripped
    # copy so tracked text can't evade the gate. This also catches the vendor's
    # own spaced spelling ("Search Atlas"), which only concatenates to the
    # forbidden "searchatlas".
    flat_text = re.sub(r"\s+", "", body.lower())
    hits = [v for v in FORBIDDEN_VENDORS if v in flat_text]
    if hits:
        raise RenderError("client-facing PDF names vendor tooling: %s"
                          % ", ".join(hits))

    if "{{" in body:
        raise RenderError("client-facing PDF leaks an unsubstituted token "
                          "(literal '{{' found in extracted text)")

    # must_contain is checked against the raw body, not `flat_text`: legitimate
    # multi-word phrases like the phone number contain spaces that must survive.
    absent = [p for p in must_contain if p not in body]
    if absent:
        raise RenderError("expected text missing from PDF: %s" % ", ".join(absent))

    return {"pages": len(text), "fonts": found_fonts, "text": body}
