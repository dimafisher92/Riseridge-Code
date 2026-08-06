"""Website and SEO audit computed from the pages already fetched.

The spec left the `technical` evidence block null because a full crawl needs a
Site Audit project and crawl budget. That is still true for a deep crawl, but a
great deal is measurable from the pages the dossier already retrieves, and none
of it costs anything extra: titles, meta descriptions, headings, alt text,
canonicals, structured data, mobile viewport, indexability, sitemap and robots.

Every check returns a COUNT and EXAMPLES, never a grade on its own. The rule
from the evidence contract holds: a check that could not be run is absent, not
a zero. "We fetched no pages" and "every page is missing a title" must not look
the same.

Scope, stated honestly in the output: this audits the sample of pages that were
fetched, not the whole site. It is a real finding about real pages, and it is
labelled as a sample rather than a census.
"""

import re
import urllib.parse

# Google truncates around these; the numbers are guidance, not physics, so the
# audit reports "outside the useful range" rather than pass/fail.
TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 70, 160
THIN_CONTENT_WORDS = 300


def _tag(html, name):
    m = re.search(r"<%s[^>]*>(.*?)</%s>" % (name, name), html or "",
                  re.I | re.S)
    return _text(m.group(1)) if m else ""


def _text(raw):
    raw = re.sub(r"<(script|style)\b.*?</\1>", " ", raw or "", flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _meta(html, **attrs):
    """Content of the first <meta> matching every given attribute."""
    for tag in re.findall(r"<meta\b[^>]*>", html or "", re.I):
        if all(re.search(r'%s\s*=\s*["\']%s["\']' % (k, re.escape(v)), tag, re.I)
               for k, v in attrs.items()):
            m = re.search(r'content\s*=\s*["\'](.*?)["\']', tag, re.I | re.S)
            if m:
                return _text(m.group(1))
    return ""


def _has_meta(html, **attrs):
    for tag in re.findall(r"<meta\b[^>]*>", html or "", re.I):
        if all(re.search(r'%s\s*=\s*["\']%s["\']' % (k, re.escape(v)), tag, re.I)
               for k, v in attrs.items()):
            return True
    return False


def page_facts(url, html):
    """Everything measurable about one page."""
    title = _tag(html, "title")
    desc = _meta(html, name="description")
    h1s = [_text(h) for h in re.findall(r"<h1[^>]*>(.*?)</h1>", html or "",
                                        re.I | re.S)]
    imgs = re.findall(r"<img\b[^>]*>", html or "", re.I)
    no_alt = [i for i in imgs
              if not re.search(r'\balt\s*=\s*["\'][^"\']+["\']', i, re.I)]
    canonical = ""
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]*>', html or "", re.I)
    if m:
        c = re.search(r'href\s*=\s*["\']([^"\']+)["\']', m.group(0), re.I)
        canonical = c.group(1) if c else ""

    schema_types = re.findall(r'"@type"\s*:\s*"([^"]+)"', html or "")
    robots = _meta(html, name="robots").lower()

    return {
        "url": url,
        "title": title,
        "title_length": len(title),
        "description": desc,
        "description_length": len(desc),
        "h1_count": len(h1s),
        "h1": h1s[0] if h1s else "",
        "image_count": len(imgs),
        "images_missing_alt": len(no_alt),
        "canonical": canonical,
        "has_structured_data": bool(schema_types),
        "schema_types": sorted(set(schema_types)),
        "has_viewport": _has_meta(html, name="viewport"),
        "has_open_graph": bool(re.search(r'property\s*=\s*["\']og:', html or "",
                                         re.I)),
        "noindex": "noindex" in robots,
        "word_count": len(_text(html).split()),
        "bytes": len(html or ""),
    }


def _pct(n, total):
    return round(100.0 * n / total) if total else None


def audit(pages, *, robots_txt=None, sitemap_found=None, base_url=""):
    """Audit the fetched sample. Returns None when there is nothing to audit.

    `robots_txt` and `sitemap_found` are passed in rather than fetched here so
    this stays pure and testable; None for either means "not checked", which is
    reported as unknown rather than as a failure.
    """
    facts = [page_facts(u, h) for u, h in sorted((pages or {}).items())]
    if not facts:
        return None

    n = len(facts)
    titles = [f["title"] for f in facts if f["title"]]
    dupe_titles = len(titles) - len(set(titles))
    descs = [f["description"] for f in facts if f["description"]]

    total_images = sum(f["image_count"] for f in facts)
    missing_alt = sum(f["images_missing_alt"] for f in facts)

    checks = []

    def check(key, label, failing, detail, examples=()):
        checks.append({
            "key": key, "label": label,
            "failing": failing, "checked": n,
            "pct_failing": _pct(failing, n),
            "detail": detail,
            "examples": [e for e in examples][:3],
            "status": "ok" if failing == 0 else
                      ("warn" if failing <= n / 3.0 else "problem"),
        })

    no_title = [f for f in facts if not f["title"]]
    check("title_missing", "Page titles", len(no_title),
          "Every page needs a unique title; it is the headline in search "
          "results and the strongest on-page signal there is.",
          [f["url"] for f in no_title])

    bad_len = [f for f in facts
               if f["title"] and not TITLE_MIN <= f["title_length"] <= TITLE_MAX]
    check("title_length", "Title length", len(bad_len),
          "Titles outside roughly %d-%d characters get truncated or waste the "
          "space available." % (TITLE_MIN, TITLE_MAX),
          ["%s (%d chars)" % (f["title"][:40], f["title_length"])
           for f in bad_len])

    check("title_duplicate", "Duplicate titles", dupe_titles,
          "Pages sharing a title compete with each other and tell search "
          "engines they are the same page.", [])

    no_desc = [f for f in facts if not f["description"]]
    check("description_missing", "Meta descriptions", len(no_desc),
          "Without one, the search result snippet is chosen for you from "
          "whatever text is on the page.", [f["url"] for f in no_desc])

    bad_desc = [f for f in facts if f["description"]
                and not DESC_MIN <= f["description_length"] <= DESC_MAX]
    check("description_length", "Description length", len(bad_desc),
          "Descriptions outside roughly %d-%d characters get cut off."
          % (DESC_MIN, DESC_MAX), [f["url"] for f in bad_desc])

    no_h1 = [f for f in facts if f["h1_count"] == 0]
    check("h1_missing", "Page headings", len(no_h1),
          "A page with no H1 gives search engines and AI assistants nothing "
          "to read as its subject.", [f["url"] for f in no_h1])

    many_h1 = [f for f in facts if f["h1_count"] > 1]
    check("h1_multiple", "Multiple H1s", len(many_h1),
          "More than one H1 splits the page's stated subject.",
          ["%s (%d)" % (f["url"], f["h1_count"]) for f in many_h1])

    no_schema = [f for f in facts if not f["has_structured_data"]]
    check("structured_data", "Structured data", len(no_schema),
          "Structured data is how an AI assistant reads what a page IS -- a "
          "product, a service, a business -- rather than guessing from prose. "
          "This is the single biggest lever on AI visibility.",
          [f["url"] for f in no_schema])

    no_canonical = [f for f in facts if not f["canonical"]]
    check("canonical", "Canonical tags", len(no_canonical),
          "Without a canonical, duplicate URLs of the same page split their "
          "own ranking strength.", [f["url"] for f in no_canonical])

    no_viewport = [f for f in facts if not f["has_viewport"]]
    check("viewport", "Mobile viewport", len(no_viewport),
          "A missing viewport tag means the page is not declaring itself "
          "mobile-ready, and most of this traffic is mobile.",
          [f["url"] for f in no_viewport])

    no_og = [f for f in facts if not f["has_open_graph"]]
    check("open_graph", "Social preview tags", len(no_og),
          "Without these, a shared link renders as a bare URL.",
          [f["url"] for f in no_og])

    thin = [f for f in facts if f["word_count"] < THIN_CONTENT_WORDS]
    check("thin_content", "Thin pages", len(thin),
          "Pages under roughly %d words rarely have enough substance to rank "
          "or to be worth citing in an answer." % THIN_CONTENT_WORDS,
          ["%s (%d words)" % (f["url"], f["word_count"]) for f in thin])

    noindexed = [f for f in facts if f["noindex"]]
    check("noindex", "Blocked from search", len(noindexed),
          "A noindex tag removes the page from search results entirely.",
          [f["url"] for f in noindexed])

    if total_images:
        check("image_alt", "Image alt text", missing_alt,
              "Alt text is how an image is understood by search engines and "
              "screen readers alike.", [])
        checks[-1]["checked"] = total_images
        checks[-1]["pct_failing"] = _pct(missing_alt, total_images)

    # Site-wide checks. Only added when actually checked: `None` means the
    # request was never made, which must not read as a failure.
    if sitemap_found is not None:
        check("sitemap", "XML sitemap", 0 if sitemap_found else 1,
              "A sitemap tells search engines every page worth indexing. "
              "Without one, discovery depends entirely on internal linking.")
        checks[-1]["checked"] = 1
        checks[-1]["pct_failing"] = 0 if sitemap_found else 100
    if robots_txt is not None:
        blocks_all = bool(re.search(r"(?im)^\s*disallow:\s*/\s*$", robots_txt))
        check("robots", "robots.txt", 1 if blocks_all else 0,
              "A robots.txt that disallows everything hides the whole site "
              "from search. Present and permissive is what you want.")
        checks[-1]["checked"] = 1
        checks[-1]["pct_failing"] = 100 if blocks_all else 0

    problems = [c for c in checks if c["status"] == "problem"]
    warnings = [c for c in checks if c["status"] == "warn"]
    passed = [c for c in checks if c["status"] == "ok"]

    return {
        "pages_audited": n,
        "base_url": base_url,
        "scope_note": "Audited the %d pages reached from the homepage, not the "
                      "whole site. Findings are real for those pages and are a "
                      "sample of the rest." % n,
        "checks": checks,
        "problem_count": len(problems),
        "warning_count": len(warnings),
        "passed_count": len(passed),
        "structured_data_types": sorted({t for f in facts
                                         for t in f["schema_types"]}),
        "robots_txt": (None if robots_txt is None else bool(robots_txt)),
        "sitemap_found": sitemap_found,
        "median_words": sorted(f["word_count"] for f in facts)[n // 2],
        "pages": facts,
    }


def headline_issues(report, limit=5):
    """The problems worth putting in front of a prospect, worst first."""
    if not report:
        return []
    ranked = sorted(
        [c for c in report["checks"] if c["status"] in ("problem", "warn")],
        key=lambda c: (-(c["pct_failing"] or 0), c["label"]))
    return ranked[:limit]


def summary_line(report):
    if not report:
        return ""
    return ("%d checks run across %d pages: %d clean, %d need attention, "
            "%d are problems." % (len(report["checks"]), report["pages_audited"],
                                  report["passed_count"],
                                  report["warning_count"],
                                  report["problem_count"]))
