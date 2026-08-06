# RiseRidge Sales Phase 2b: The Full Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the real `evidence.json` the collector already produces into a complete, client-ready AI Search Visibility Audit PDF.

**Architecture:** Phase 1 built the render engine and 4 of 12 sections; Phase 2a built the data. This phase adds the remaining 8 section templates, fetches keywords deep enough to make the report's strongest table land, and adds the finer null-gate the spec asks for — a null value must omit its *stat tile*, not just its section. No new modules: this is templates plus targeted changes to `render.py` and `collect.py`.

**Tech Stack:** Python 3.14 stdlib, `pytest` 9.1.1, `pymupdf` for PDF verification, headless Chrome for rendering.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-riseridge-sales-audit-design.md`. Carried findings: `docs/superpowers/phase2-inbox.md` and `docs/superpowers/phase2b-inbox.md`. Read all three before starting.
- Python invoked as `python` from `D:\Claude Code\riseridge-sales`. On `master`, 316 tests passing.
- **Never name a tool in client-facing output.** `verify_pdf` fails the render if searchatlas, ahrefs, semrush, majestic or similarweb appears in the extracted text, matched against a whitespace-stripped copy (CSS letter-spacing makes PyMuPDF insert spaces between glyphs). "Domain Rating" and "Trust Flow" are retained deliberately by operator decision and are NOT vendor tool names — do not add them to `FORBIDDEN_VENDORS` or every render fails.
- **A null value omits its section OR its stat tile. Never estimated, never an em dash in an authoritative-looking tile.** These figures are read aloud to prospects.
- Section stripping runs before tile stripping, which runs before token substitution. A token inside a removed section or tile must never need a value.
- Token substitution is a SINGLE regex pass and values are HTML-escaped, except tokens whose name ends in `_html` which carry authored markup. Do not reintroduce a loop of `str.replace`.
- Logos must be inline `<svg>`. Chrome's `--print-to-pdf` does not embed external `<img src="*.svg">`.
- `.page` uses `min-height: 11in` with no `overflow: hidden`. Do NOT reintroduce `overflow: hidden` — it silently destroyed content (60 list items became 17). Accepted cost: an overflowing section's footer strands near the top of its last sheet.
- Page size US Letter. Palette pine `#1E3A2E`, brass `#A9874E`, ink `#15140F`, ivory `#F4F0E8`. Fonts Cormorant Garamond (display), Hanken Grotesk (body), Space Mono (data labels).
- Every figure printed must trace to an evidence path. Nothing is computed in a template.
- Sample-derived figures must disclose it. `position_buckets` and `brand_split` each carry `sample_rows`; the copy must not present a sample as a census.
- Do not modify `slack.py`, `leads.py`, `saprobe.py`, `sa_client.py`, `sacold.py`, `sawarm.py`, the JSON under `fixtures/api/`, or anything in `D:\Claude Code\searchatlas\`.

## Operator decisions recorded 2026-08-05

- **Scorecard stays absent** until AI-visibility and technical inputs exist. Do not construct scores from what we have.
- **Fetch 500 keywords per prospect** (5 read-only pages). Measured: 100 keywords yields 2 buried money keywords, 500 yields 90 candidates and a full 8-row table. Depth, not the volume floor, was the constraint.
- **Build the AI-visibility and paid-vs-organic templates now**, gated absent so they appear automatically when their data lands.

---

### Task 1: Fetch deep enough for the money-keywords table

**Files:**
- Modify: `collect.py`
- Modify: `derive.py`
- Modify: `tests/test_collect.py`, `tests/test_derive.py`

**Interfaces:**
- Consumes: `sacold.keyword_rows`, `derive.money_keywords`, `collect._get_ready`, `collect._usable`.
- Produces: `collect.KEYWORD_PAGES = 5`, `collect.KEYWORD_PAGE_SIZE = 100`, `collect.fetch_cold` returning an `organic_keywords` payload whose `results` is the concatenation of up to 5 pages. `derive.money_keywords` default `min_volume` becomes `100`.

Measured on getpetermd.com: buried non-branded keywords above 100 volume number 2 at one page, 19 at two, 90 at five. The reference report's table needs five.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_collect.py`:

```python
def test_fetch_cold_pages_keywords_to_the_configured_depth():
    seen = []

    class FakeSA:
        def get(self, service, path, params=None):
            if "organic-keywords" in path:
                page = (params or {}).get("page")
                seen.append(page)
                return {"results": [{"keyword": "k%d-%d" % (page, i),
                                     "search_volume": 200, "position": 30}
                                    for i in range(100)],
                        "total_count": 3705}
            return {"results": [], "total_count": 0}

    out = collect.fetch_cold(FakeSA(), "x.com")
    assert seen == [1, 2, 3, 4, 5], "must walk exactly KEYWORD_PAGES pages"
    assert len(out["organic_keywords"]["results"]) == 500
    assert out["organic_keywords"]["total_count"] == 3705, (
        "the domain total must survive concatenation, not become the row count")


def test_fetch_cold_stops_early_on_a_short_page():
    class FakeSA:
        def get(self, service, path, params=None):
            if "organic-keywords" in path:
                page = (params or {}).get("page")
                n = 100 if page == 1 else 3
                return {"results": [{"keyword": "k%d" % i} for i in range(n)],
                        "total_count": 103}
            return {"results": [], "total_count": 0}

    out = collect.fetch_cold(FakeSA(), "x.com")
    assert len(out["organic_keywords"]["results"]) == 103


def test_fetch_cold_propagates_not_ready_from_the_first_page():
    """A not-ready first page must not be silently treated as an empty corpus."""
    class FakeSA:
        def get(self, service, path, params=None):
            if "organic-keywords" in path:
                return {"results": [], "total_count": 0, "should_retry": True}
            return {"results": [], "total_count": 0}

    out = collect.fetch_cold(FakeSA(), "x.com")
    assert out["organic_keywords"].get("should_retry") is True
    assert collect._usable(out["organic_keywords"]) is False
```

Append to `tests/test_derive.py`:

```python
def test_money_keywords_default_volume_floor_is_one_hundred():
    rows = [{"keyword": "buy testosterone online", "volume": 150, "position": 31},
            {"keyword": "tiny term", "volume": 40, "position": 31}]
    toks = derive.brand_token_set("getpetermd.com", "PeterMD")
    got = [k["keyword"] for k in derive.money_keywords(rows, toks)]
    assert got == ["buy testosterone online"], (
        "150 must qualify at the new floor, 40 must not")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_collect.py -k fetch_cold_pages -v`
Expected: FAIL — `seen` is `[1]`, only one page fetched.

- [ ] **Step 3: Implement**

In `collect.py`, add the constants near the other module constants:

```python
# Measured on getpetermd.com: buried non-branded keywords above the volume floor
# number 2 at one page, 19 at two, 90 at five. Rows come back traffic-sorted, so
# the first page is the keywords already ranking WELL -- the buried money terms
# the report exists to surface only appear deeper. Five pages fills the table.
KEYWORD_PAGES = 5
KEYWORD_PAGE_SIZE = 100
```

Replace the `organic_keywords` fetch inside `fetch_cold` with a paging walk. Keep every other fetch unchanged:

```python
    pages, total, not_ready = [], None, False
    for page in range(1, KEYWORD_PAGES + 1):
        p = _get_ready(sa, "keyword",
                       "/api/v2/competitor-research/organic-keywords/",
                       params={"target": domain, "page": page,
                               "page_size": KEYWORD_PAGE_SIZE})
        if not isinstance(p, dict):
            break
        if p.get("should_retry"):
            # Only the first page decides usability; a later not-ready page just
            # ends the walk with what we already have.
            if page == 1:
                not_ready = True
            break
        rows = p.get("results") or []
        if total is None:
            total = p.get("total_count")
        pages.extend(rows)
        if len(rows) < KEYWORD_PAGE_SIZE:
            break

    merged = {"results": pages, "total_count": total}
    if not_ready:
        merged["should_retry"] = True
    out["organic_keywords"] = merged
```

In `derive.py`, change `money_keywords`'s signature default from `min_volume=500` to `min_volume=100`, and update its docstring to say the floor admits the smaller-volume commercial terms that are often the real money keywords for a local business.

- [ ] **Step 4: Run to verify passing**

Run: `python -m pytest tests/test_collect.py tests/test_derive.py -q`
Expected: PASS.

- [ ] **Step 5: Verify against the live API, read-only**

Run: `python collect.py getpetermd.com --name PeterMD`
Expected: `project: found` or `reused`, no POST. Report the real `money keywords` count — it should be 8, not 2 — and the `position_buckets` `sample_rows`, which should now be around 500 rather than 100. Report both numbers.

- [ ] **Step 6: Commit**

```bash
git add collect.py derive.py tests/test_collect.py tests/test_derive.py
git commit -m "feat: fetch 500 keywords so the money-keywords table fills"
```

---

### Task 2: Tile-granular null gating

**Files:**
- Modify: `render.py`
- Modify: `tests/test_render.py`

**Interfaces:**
- Consumes: `render.DASH`, `render.strip_absent_sections`, `render.build_html`.
- Produces: `render.strip_absent_tiles(html: str, tokens: dict) -> str`, and `build_html` calling it between section stripping and token substitution. Marker syntax `<!--TILE:token_name-->...<!--/TILE:token_name-->`.

This closes `phase2-inbox.md` item 1. The spec's rule is "a null value omits its section **or stat tile**"; only the section half is implemented, so a partially-populated section prints an em dash inside an authoritative-looking tile — verified in Phase 1 as a scorecard page showing three `—` tiles under the copy "Anything below 60 needs work." With eight more sections arriving this stops being theoretical.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py`:

```python
def test_tile_with_a_dash_value_is_removed():
    html = "A<!--TILE:x-->[<span>{{x}}</span>]<!--/TILE:x-->B"
    out = render.strip_absent_tiles(html, {"x": render.DASH})
    assert out == "AB"


def test_tile_with_a_real_value_is_kept_and_markers_removed():
    html = "A<!--TILE:x-->[{{x}}]<!--/TILE:x-->B"
    out = render.strip_absent_tiles(html, {"x": "57"})
    assert "[{{x}}]" in out
    assert "TILE:x" not in out


def test_tile_with_a_real_zero_is_kept():
    """A measured zero is a fact and must print. Only an absent value drops."""
    out = render.strip_absent_tiles("<!--TILE:x-->[{{x}}]<!--/TILE:x-->", {"x": "0"})
    assert "[{{x}}]" in out


def test_tile_with_a_missing_token_is_removed():
    out = render.strip_absent_tiles("<!--TILE:x-->[{{x}}]<!--/TILE:x-->", {})
    assert out == ""


def test_tiles_are_independent():
    html = ("<!--TILE:a-->A<!--/TILE:a--><!--TILE:b-->B<!--/TILE:b-->")
    out = render.strip_absent_tiles(html, {"a": render.DASH, "b": "9"})
    assert "A" not in out
    assert "B" in out


def test_removed_tile_takes_its_tokens_with_it():
    html = "<!--TILE:a-->{{a}} {{a_label}}<!--/TILE:a-->"
    out = render.strip_absent_tiles(html, {"a": render.DASH})
    assert "{{" not in out


def test_build_html_drops_a_dash_tile_end_to_end(tmp_path, monkeypatch):
    """A section that survives the section gate must still drop its dead tiles."""
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "99_tiles.html").write_text(
        '<div class="page"><!--SECTION:traffic-->'
        '<!--TILE:live-->LIVE {{live}}<!--/TILE:live-->'
        '<!--TILE:dead-->DEAD {{dead}}<!--/TILE:dead-->'
        '<!--/SECTION:traffic--></div>', encoding="utf-8")
    monkeypatch.setattr(render, "SECTIONS", str(sections))
    ev = evidence.Evidence({"domain": "d", "business_name": "b",
                            "generated_at": "g",
                            "traffic": {"monthly_organic_visits": {"value": 5401}}})
    out = render.build_html(ev, {"business_name": "b", "domain": "d",
                                 "report_date": "August 2026",
                                 "live": "5,401", "dead": render.DASH},
                            section_files=["99_tiles.html"])
    assert "LIVE" in out
    assert "DEAD" not in out
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_render.py -k tile -v`
Expected: FAIL — `AttributeError: module 'render' has no attribute 'strip_absent_tiles'`.

- [ ] **Step 3: Implement**

In `render.py`, add beside `strip_absent_sections`:

```python
TILE = re.compile(r"<!--TILE:([a-z0-9_]+)-->", re.I)


def _tile_re(name):
    return re.compile(
        r"<!--TILE:%s-->.*?<!--/TILE:%s-->" % (re.escape(name), re.escape(name)),
        re.S | re.I,
    )


def strip_absent_tiles(html, tokens):
    """Remove a marked tile whose token has no value, then drop the markers.

    The section gate drops a section when every metric in it is null. This is
    the finer grain the spec asks for: 'a null value omits its section OR stat
    tile'. Without it a partially-populated section prints an em dash inside a
    tile surrounded by authoritative copy -- a page that per spec should not
    exist. A measured 0 is a fact and is kept; only DASH, None and empty drop.
    """
    for name in sorted(set(TILE.findall(html))):
        v = tokens.get(name)
        if v is None or v == "" or v == DASH:
            html = _tile_re(name).sub("", html)
    return re.sub(r"<!--/?TILE:[a-z0-9_]+-->", "", html, flags=re.I)
```

In `build_html`, insert the call between section stripping and substitution:

```python
    doc = strip_absent_sections(doc, ev.present_sections())
    doc = strip_absent_tiles(doc, tokens)
```

- [ ] **Step 4: Run to verify passing**

Run: `python -m pytest tests/test_render.py -q`
Expected: PASS, including the pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add render.py tests/test_render.py
git commit -m "feat: omit a stat tile whose value is absent, not just a section"
```

---

### Task 3: Font and dependency hygiene

**Files:**
- Modify: `render.py`
- Create: `requirements.txt`
- Modify: `.gitignore`
- Modify: `tests/test_render.py`

**Interfaces:**
- Consumes: `render.REQUIRED_FONTS`, `render.verify_pdf`.
- Produces: `REQUIRED_FONTS` including `SpaceMono`; a committed `requirements.txt`; the font cache no longer git-ignored.

Closes `phase2-inbox.md` items 4 and 5. `brand.css` uses Space Mono for every eyebrow, stat key, score key, table header and `td.num` — i.e. every numeral in every data table this phase adds — yet `verify_pdf` only asserts Hanken Grotesk and Cormorant Garamond. A cache regression dropping the four Space Mono faces yields Courier numerals and passes the gate. And `state/` being ignored means a fresh clone silently downloads 18 font files instead of using the cache.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py`:

```python
def test_required_fonts_covers_every_family_the_stylesheet_uses():
    """brand.css sets Space Mono on every eyebrow, stat key, table header and
    td.num, so a cache regression dropping it yields Courier numerals. The
    embed gate must assert all three families, not two."""
    css = open(os.path.join(render.TEMPLATES, "brand.css"), encoding="utf-8").read()
    for family, token in (("Cormorant Garamond", "CormorantGaramond"),
                          ("Hanken Grotesk", "HankenGrotesk"),
                          ("Space Mono", "SpaceMono")):
        assert family in css
        assert token in render.REQUIRED_FONTS, "%s unguarded" % family
```

Add `import os` to the test file's imports if it is not already present at module level.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_render.py -k required_fonts -v`
Expected: FAIL — `SpaceMono unguarded`.

- [ ] **Step 3: Implement**

In `render.py`, extend the constant:

```python
# Every family brand.css actually uses. Space Mono styles all numerals, so
# omitting it let a cache regression ship Courier figures past the embed gate.
REQUIRED_FONTS = ("HankenGrotesk", "CormorantGaramond", "SpaceMono")
```

Create `requirements.txt`:

```
# Phase 1 + 2a runtime and test dependencies. Everything else is stdlib.
pytest==9.1.1
pymupdf==1.28.0
```

In `.gitignore`, stop ignoring the whole of `state/` and ignore only the volatile parts, so the font cache is committed and a fresh clone does not silently fetch 18 font files:

```
.env
state/prospects/
state/leads.json
out/
__pycache__/
*.pyc
```

- [ ] **Step 4: Run to verify passing, and commit the cache**

Run: `python -m pytest tests/test_render.py -q`
Expected: PASS.

Run: `python fonts.py` to ensure the cache exists, then confirm it is now tracked:

Run: `git status --short state/`
Expected: `state/fonts.css` shows as untracked/added, and nothing under `state/prospects/` appears.

- [ ] **Step 5: Commit**

```bash
git add render.py requirements.txt .gitignore state/fonts.css tests/test_render.py
git commit -m "fix: guard Space Mono, declare dependencies, commit the font cache"
```

---

### Task 4: Traffic & Rankings section

**Files:**
- Create: `templates/sections/06_traffic_rankings.html`
- Modify: `render.py` (add to `DEFAULT_SECTIONS`)
- Modify: `tests/test_render.py`

**Interfaces:**
- Consumes: `strip_absent_sections`, `strip_absent_tiles`, `fmt`.
- Produces: a section gated on `traffic`, with tiles for each of the three headline figures, the brand/non-brand split, and the money-keywords table.

Tokens this section requires: `visits`, `keyword_count`, `traffic_value`, `brand_pct`, `nonbrand_pct`, `brand_split_basis`, `money_keywords_rows_html`, `traffic_close_html`.

This is the report's analytical core — the reference version carried the finding that 95% of traffic was brand, meaning the site was not a customer-acquisition channel at all.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py`:

```python
def _traffic_ev():
    return evidence.Evidence({
        "domain": "getpetermd.com", "business_name": "PeterMD",
        "generated_at": "2026-08-05T00:00:00Z",
        "traffic": {"monthly_organic_visits": {"value": 5401},
                    "ranking_keyword_count": {"value": 3846},
                    "traffic_value_usd": {"value": 36660}},
        "brand_split": {"brand_pct": {"value": 88},
                        "nonbrand_pct": {"value": 12},
                        "sample_rows": 500},
        "money_keywords": [{"keyword": "trt", "volume": 74000, "position": 46}],
    })


def _traffic_tokens():
    return {"business_name": "PeterMD", "domain": "getpetermd.com",
            "report_date": "August 2026",
            "visits": "5.4K", "keyword_count": "3,846",
            "traffic_value": "$36.7K",
            "brand_pct": "88%", "nonbrand_pct": "12%",
            "brand_split_basis": "top 500 keywords by traffic",
            "money_keywords_rows_html":
                "<tr><td>trt</td><td class=\"num\">74,000</td>"
                "<td class=\"num\">46</td></tr>",
            "traffic_close_html": "<p>The gap is closable.</p>"}


def test_traffic_section_prints_all_three_headline_figures():
    out = render.build_html(_traffic_ev(), _traffic_tokens(),
                            section_files=["01_cover.html",
                                           "06_traffic_rankings.html"])
    for want in ("5.4K", "3,846", "$36.7K", "88%", "12%"):
        assert want in out


def test_traffic_section_discloses_the_sample_basis():
    out = render.build_html(_traffic_ev(), _traffic_tokens(),
                            section_files=["06_traffic_rankings.html"])
    assert "top 500 keywords by traffic" in out, (
        "the brand split is sample-derived and the copy must say so")


def test_traffic_section_drops_a_tile_whose_figure_is_absent():
    t = _traffic_tokens()
    t["traffic_value"] = render.DASH
    out = render.build_html(_traffic_ev(), t,
                            section_files=["06_traffic_rankings.html"])
    assert "Traffic value" not in out
    assert "5.4K" in out, "the surviving tiles must still print"


def test_traffic_section_absent_when_no_traffic_evidence():
    ev = evidence.Evidence({"domain": "d", "business_name": "b",
                            "generated_at": "g"})
    out = render.build_html(ev, {"business_name": "b", "domain": "d",
                                 "report_date": "August 2026"},
                            section_files=["01_cover.html",
                                           "06_traffic_rankings.html"])
    assert "Traffic & Rankings" not in out


def test_traffic_section_is_in_default_sections():
    assert "06_traffic_rankings.html" in render.DEFAULT_SECTIONS
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_render.py -k traffic_section -v`
Expected: FAIL — the template file does not exist.

- [ ] **Step 3: Write the template**

`templates/sections/06_traffic_rankings.html`:

```html
<!--SECTION:traffic-->
<div class="page">
  <div class="eyebrow">03 &nbsp;/&nbsp; The Numbers</div>
  <h2>Traffic &amp; Rankings</h2>

  <div class="stats">
    <!--TILE:visits-->
    <div class="stat"><div class="k">Monthly visits</div><div class="v">{{visits}}</div></div>
    <!--/TILE:visits-->
    <!--TILE:keyword_count-->
    <div class="stat"><div class="k">Ranking keywords</div><div class="v">{{keyword_count}}</div></div>
    <!--/TILE:keyword_count-->
    <!--TILE:traffic_value-->
    <div class="stat"><div class="k">Traffic value</div><div class="v">{{traffic_value}}</div></div>
    <!--/TILE:traffic_value-->
  </div>

  <!--TILE:brand_pct-->
  <h3>Who is actually searching</h3>
  <p>Separating brand searches &mdash; people who already knew the name &mdash; from
  problem searches, which are new potential customers:</p>

  <table>
    <thead><tr><th>Type of search</th><th>% of traffic</th><th>What this means</th></tr></thead>
    <tbody>
      <tr class="self"><td>Brand searches</td><td class="num">{{brand_pct}}</td>
          <td>Existing awareness converting</td></tr>
      <tr><td>Problem searches</td><td class="num">{{nonbrand_pct}}</td>
          <td>New demand captured</td></tr>
    </tbody>
  </table>
  <p style="font-size:8.5pt;color:var(--faint)">Measured across the {{brand_split_basis}}.</p>
  <!--/TILE:brand_pct-->

  <!--TILE:money_keywords_rows_html-->
  <h3>The searches that convert, and where you rank today</h3>
  <table>
    <thead><tr><th>Keyword</th><th>Searches / month</th><th>Current position</th></tr></thead>
    <tbody>
      {{money_keywords_rows_html}}
    </tbody>
  </table>
  <!--/TILE:money_keywords_rows_html-->

  {{traffic_close_html}}

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
<!--/SECTION:traffic-->
```

In `render.py`, add `"06_traffic_rankings.html"` to `DEFAULT_SECTIONS` after `"04_scorecard.html"`.

- [ ] **Step 4: Run to verify passing**

Run: `python -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/sections/06_traffic_rankings.html render.py tests/test_render.py
git commit -m "feat: add the Traffic & Rankings section"
```

---

### Task 5: Link Profile section

**Files:**
- Create: `templates/sections/09_link_profile.html`
- Modify: `render.py` (`DEFAULT_SECTIONS`)
- Modify: `tests/test_render.py`

**Interfaces:**
- Produces: a section gated on `backlinks`, with tiles for referring domains, total backlinks, authority and trust, plus an anchors table.

Tokens: `referring_domains`, `total_backlinks`, `authority`, `authority_label`, `trust`, `trust_label`, `anchor_rows_html`, `link_profile_findings_html`.

`authority_label` and `trust_label` come from the evidence's `metric_name` fields ("Domain Rating", "Trust Flow") which the operator decided to retain.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py`:

```python
def _links_ev():
    return evidence.Evidence({
        "domain": "getpetermd.com", "business_name": "PeterMD",
        "generated_at": "2026-08-05T00:00:00Z",
        "backlinks": {"referring_domains": {"value": 3021},
                      "total_backlinks": {"value": 27955},
                      "authority": {"value": 57, "metric_name": "Domain Rating"},
                      "trust": {"value": 25, "metric_name": "Trust Flow"}},
    })


def _links_tokens():
    return {"business_name": "PeterMD", "domain": "getpetermd.com",
            "report_date": "August 2026",
            "referring_domains": "3,021", "total_backlinks": "27,955",
            "authority": "57", "authority_label": "Domain Rating",
            "trust": "25", "trust_label": "Trust Flow",
            "anchor_rows_html": "<tr><td>visit</td><td class=\"num\">5,183</td></tr>",
            "link_profile_findings_html": "<li>Authority is not in the right category.</li>"}


def test_link_profile_prints_the_link_figures():
    out = render.build_html(_links_ev(), _links_tokens(),
                            section_files=["09_link_profile.html"])
    for want in ("3,021", "27,955", "57", "25", "Domain Rating", "Trust Flow"):
        assert want in out


def test_link_profile_drops_the_trust_tile_when_absent():
    t = _links_tokens()
    t["trust"] = render.DASH
    out = render.build_html(_links_ev(), t,
                            section_files=["09_link_profile.html"])
    assert "Trust Flow" not in out
    assert "57" in out


def test_link_profile_absent_without_backlink_evidence():
    ev = evidence.Evidence({"domain": "d", "business_name": "b",
                            "generated_at": "g"})
    out = render.build_html(ev, {"business_name": "b", "domain": "d",
                                 "report_date": "August 2026"},
                            section_files=["01_cover.html",
                                           "09_link_profile.html"])
    assert "Link Profile" not in out


def test_link_profile_is_in_default_sections():
    assert "09_link_profile.html" in render.DEFAULT_SECTIONS
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_render.py -k link_profile -v`
Expected: FAIL — template missing.

- [ ] **Step 3: Write the template**

`templates/sections/09_link_profile.html`:

```html
<!--SECTION:backlinks-->
<div class="page">
  <div class="eyebrow">05 &nbsp;/&nbsp; Authority</div>
  <h2>The Link Profile</h2>

  <p>Other sites vouching for {{business_name}} is one of the strongest signals both
  Google and the AI answer engines use. Here is what that looks like today.</p>

  <div class="stats">
    <!--TILE:referring_domains-->
    <div class="stat"><div class="k">Referring domains</div><div class="v">{{referring_domains}}</div></div>
    <!--/TILE:referring_domains-->
    <!--TILE:total_backlinks-->
    <div class="stat"><div class="k">Total links</div><div class="v">{{total_backlinks}}</div></div>
    <!--/TILE:total_backlinks-->
  </div>

  <div class="scores">
    <!--TILE:authority-->
    <div class="score"><div class="k">{{authority_label}}</div><div class="v">{{authority}}</div></div>
    <!--/TILE:authority-->
    <!--TILE:trust-->
    <div class="score"><div class="k">{{trust_label}}</div><div class="v">{{trust}}</div></div>
    <!--/TILE:trust-->
  </div>

  <!--TILE:anchor_rows_html-->
  <h3>What those links actually say</h3>
  <table>
    <thead><tr><th>Anchor text</th><th>Links</th></tr></thead>
    <tbody>
      {{anchor_rows_html}}
    </tbody>
  </table>
  <!--/TILE:anchor_rows_html-->

  <h3>What this means</h3>
  <ul class="findings">
    {{link_profile_findings_html}}
  </ul>

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
<!--/SECTION:backlinks-->
```

Add `"09_link_profile.html"` to `DEFAULT_SECTIONS` after `"07_position_buckets.html"`.

- [ ] **Step 4: Run to verify passing**

Run: `python -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/sections/09_link_profile.html render.py tests/test_render.py
git commit -m "feat: add the Link Profile section"
```

---

### Task 6: Who's Winning Right Now section

**Files:**
- Create: `templates/sections/10_competitors.html`
- Modify: `render.py` (`DEFAULT_SECTIONS`)
- Modify: `tests/test_render.py`

**Interfaces:**
- Produces: a section gated on `competitors`.

Tokens: `competitor_rows_html`, `competitor_pattern_html`.

The evidence carries 100 competitor rows; the narrative layer selects and formats the handful worth showing, so this template takes pre-rendered rows.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py`:

```python
def _comp_ev():
    return evidence.Evidence({
        "domain": "getpetermd.com", "business_name": "PeterMD",
        "generated_at": "2026-08-05T00:00:00Z",
        "competitors": [{"domain": "trtnation.com", "monthly_visits": 21200,
                         "ranking_keywords": 5500}],
    })


def _comp_tokens():
    return {"business_name": "PeterMD", "domain": "getpetermd.com",
            "report_date": "August 2026",
            "competitor_rows_html":
                "<tr class=\"self\"><td>PeterMD (you)</td><td class=\"num\">5.4K</td>"
                "<td class=\"num\">3,846</td></tr>"
                "<tr><td>trtnation.com</td><td class=\"num\">21.2K</td>"
                "<td class=\"num\">5,500</td></tr>",
            "competitor_pattern_html": "<li>They point content at real buyers.</li>"}


def test_competitor_section_prints_the_table():
    out = render.build_html(_comp_ev(), _comp_tokens(),
                            section_files=["10_competitors.html"])
    assert "trtnation.com" in out
    assert "21.2K" in out


def test_competitor_section_marks_the_client_row():
    out = render.build_html(_comp_ev(), _comp_tokens(),
                            section_files=["10_competitors.html"])
    assert 'class="self"' in out, "the client's own row must be visually distinct"


def test_competitor_section_absent_without_competitor_evidence():
    ev = evidence.Evidence({"domain": "d", "business_name": "b",
                            "generated_at": "g"})
    out = render.build_html(ev, {"business_name": "b", "domain": "d",
                                 "report_date": "August 2026"},
                            section_files=["01_cover.html",
                                           "10_competitors.html"])
    assert "Who's Winning" not in out


def test_competitor_section_is_in_default_sections():
    assert "10_competitors.html" in render.DEFAULT_SECTIONS
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_render.py -k competitor_section -v`
Expected: FAIL — template missing.

- [ ] **Step 3: Write the template**

`templates/sections/10_competitors.html`:

```html
<!--SECTION:competitors-->
<div class="page">
  <div class="eyebrow">06 &nbsp;/&nbsp; The Field</div>
  <h2>Who&rsquo;s Winning Right Now</h2>

  <p>Here is how {{business_name}} stacks up against the businesses competing for
  the same customers online.</p>

  <table>
    <thead><tr><th>Competitor</th><th>Monthly traffic</th><th>Ranking keywords</th></tr></thead>
    <tbody>
      {{competitor_rows_html}}
    </tbody>
  </table>

  <h3>The pattern across all of them</h3>
  <ul class="findings">
    {{competitor_pattern_html}}
  </ul>

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
<!--/SECTION:competitors-->
```

Add `"10_competitors.html"` to `DEFAULT_SECTIONS` after `"09_link_profile.html"`.

- [ ] **Step 4: Run to verify passing**

Run: `python -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/sections/10_competitors.html render.py tests/test_render.py
git commit -m "feat: add the Who's Winning Right Now section"
```

---

### Task 7: The three narrative sections

**Files:**
- Create: `templates/sections/03_finding.html`
- Create: `templates/sections/11_ninety_day_plan.html`
- Create: `templates/sections/12_next_steps.html`
- Modify: `render.py` (`DEFAULT_SECTIONS`)
- Modify: `tests/test_render.py`

**Interfaces:**
- Produces: three ungated sections. `03_finding.html` and `11_ninety_day_plan.html` are authored per prospect; `12_next_steps.html` is fixed copy with no data tokens beyond identity.

Tokens: `finding_headline`, `finding_body_html`, `finding_data_callout_html`, `finding_why_html`; `plan_days_1_30_html`, `plan_days_31_60_html`, `plan_days_61_90_html`, `plan_outcome_html`.

Section 3 is the judgment layer and cannot be templated — in the reference report it was the discovery that a men's clinic ranked for perimenopause keywords. No threshold rule finds that. It is authored per prospect from the evidence.

These three are **ungated** on purpose: they carry no fetched metrics, so there is no evidence key to gate on, and a report should never lose its opening finding or its call to action.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py`:

```python
def _narrative_tokens():
    return {"business_name": "PeterMD", "domain": "getpetermd.com",
            "report_date": "August 2026",
            "finding_headline": "95% of your traffic already knew your name",
            "finding_body_html": "<p>The site converts existing awareness.</p>",
            "finding_data_callout_html": "<p>Brand 88%, problem searches 12%.</p>",
            "finding_why_html": "<li>The website is not an acquisition channel.</li>",
            "plan_days_1_30_html": "<li>Technical cleanup.</li>",
            "plan_days_31_60_html": "<li>Rebuild the top 20 pages.</li>",
            "plan_days_61_90_html": "<li>Authority and compounding growth.</li>",
            "plan_outcome_html": "<p>25,000 to 35,000 monthly visits.</p>"}


def _bare_ev():
    return evidence.Evidence({"domain": "getpetermd.com",
                              "business_name": "PeterMD",
                              "generated_at": "2026-08-05T00:00:00Z"})


def test_finding_section_renders_with_no_metric_evidence():
    """The judgment section carries no fetched metrics, so it must render even
    when every evidence group is absent."""
    out = render.build_html(_bare_ev(), _narrative_tokens(),
                            section_files=["03_finding.html"])
    assert "95% of your traffic already knew your name" in out
    assert "not an acquisition channel" in out


def test_ninety_day_plan_renders_all_three_windows():
    out = render.build_html(_bare_ev(), _narrative_tokens(),
                            section_files=["11_ninety_day_plan.html"])
    for want in ("Technical cleanup", "Rebuild the top 20 pages",
                 "Authority and compounding growth", "25,000"):
        assert want in out


def test_next_steps_carries_the_contact_details():
    out = render.build_html(_bare_ev(), _narrative_tokens(),
                            section_files=["12_next_steps.html"])
    assert "riseridge.io" in out
    assert "+1 786 603 5778" in out


def test_all_three_narrative_sections_are_in_default_sections():
    for f in ("03_finding.html", "11_ninety_day_plan.html", "12_next_steps.html"):
        assert f in render.DEFAULT_SECTIONS


def test_next_steps_is_the_last_default_section():
    """brand.css targets .page:last-of-type; a section after the CTA would
    leave the CTA page with page-break-after and add a trailing blank sheet."""
    assert render.DEFAULT_SECTIONS[-1] == "12_next_steps.html"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_render.py -k "finding_section or ninety_day or next_steps" -v`
Expected: FAIL — templates missing.

- [ ] **Step 3: Write the three templates**

`templates/sections/03_finding.html`:

```html
<div class="page">
  <div class="eyebrow">02 &nbsp;/&nbsp; The Headline</div>
  <h2>The Finding That Changes Everything</h2>

  <h3>{{finding_headline}}</h3>
  {{finding_body_html}}

  <div class="callout">
    <div class="t">What this looks like in the data</div>
    {{finding_data_callout_html}}
  </div>

  <h3>Why this matters</h3>
  <ul class="findings">
    {{finding_why_html}}
  </ul>

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
```

`templates/sections/11_ninety_day_plan.html`:

```html
<div class="page">
  <div class="eyebrow">07 &nbsp;/&nbsp; The Work</div>
  <h2>The 90-Day Plan</h2>

  <p>Here is what the first three months look like when {{business_name}} partners
  with RiseRidge.</p>

  <h3>Days 1&ndash;30 &mdash; Foundation</h3>
  <ul class="findings">{{plan_days_1_30_html}}</ul>

  <h3>Days 31&ndash;60 &mdash; Content That Wins In AI Search</h3>
  <ul class="findings">{{plan_days_31_60_html}}</ul>

  <h3>Days 61&ndash;90 &mdash; Authority &amp; Compounding Growth</h3>
  <ul class="findings">{{plan_days_61_90_html}}</ul>

  <div class="callout">
    <div class="t">The realistic outcome</div>
    {{plan_outcome_html}}
  </div>

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
```

`templates/sections/12_next_steps.html`:

```html
<div class="page">
  <div class="eyebrow">08 &nbsp;/&nbsp; Next</div>
  <h2>What Happens Next</h2>

  <p>This audit is a snapshot of where {{business_name}} is today and where the
  biggest opportunities sit. The next step is a working session where we walk
  through the specific pages, keywords and priorities together &mdash; and answer
  the question every founder actually wants answered: what does this cost, how
  fast does it move the needle, and what does month six look like.</p>

  <p>Book the call. If we are not the right fit, you will still walk away with a
  clearer picture of what to do.</p>

  <div class="callout">
    <div class="t">RiseRidge</div>
    <p>AI Search Visibility for Brands That Deserve to Be Found.<br>
    riseridge.io &nbsp;·&nbsp; info@riseridge.io &nbsp;·&nbsp; +1 786 603 5778</p>
  </div>

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
```

In `render.py`, set `DEFAULT_SECTIONS` to the full ordered list:

```python
DEFAULT_SECTIONS = [
    "01_cover.html",
    "02_exec_summary.html",
    "03_finding.html",
    "04_scorecard.html",
    "05_ai_visibility.html",
    "06_traffic_rankings.html",
    "07_position_buckets.html",
    "08_paid_vs_organic.html",
    "09_link_profile.html",
    "10_competitors.html",
    "11_ninety_day_plan.html",
    "12_next_steps.html",
]
```

Sections 05 and 08 are created in Task 8. If you are executing tasks in order, add them to the list in Task 8 rather than here, and keep this list without them until then — a missing template file raises on read.

- [ ] **Step 4: Run to verify passing**

Run: `python -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/sections/03_finding.html templates/sections/11_ninety_day_plan.html templates/sections/12_next_steps.html render.py tests/test_render.py
git commit -m "feat: add the finding, 90-day plan and next-steps sections"
```

---

### Task 8: AI Visibility and Paid vs Organic, gated absent

**Files:**
- Create: `templates/sections/05_ai_visibility.html`
- Create: `templates/sections/08_paid_vs_organic.html`
- Modify: `render.py` (`DEFAULT_SECTIONS`)
- Modify: `tests/test_render.py`

**Interfaces:**
- Produces: two sections gated on `ai_visibility` and `paid`, which are null in every evidence file today and therefore stay absent until the browser probe (Phase 2c) and paid collection land.

Tokens: `ai_platform_rows_html`, `ai_gap_html`; `paid_spend`, `paid_keyword_count`, `paid_landing_pages_html`, `paid_vs_organic_html`.

Building them now means no wasted work later and the gate is exercised in both directions immediately. The AI-visibility section is the report's differentiator, so its markup should exist before its data does.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py`:

```python
def test_ai_visibility_absent_with_todays_evidence():
    """ai_visibility.platforms is null in every evidence file until the browser
    probe lands, so the section must not render."""
    ev = evidence.Evidence.load(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "fixtures", "petermd_evidence.json"))
    out = render.build_html(ev, _petermd_min_tokens(),
                            section_files=["05_ai_visibility.html"])
    assert "The AI Search Opportunity" not in out


def test_ai_visibility_renders_once_platforms_exist():
    ev = evidence.Evidence({
        "domain": "d", "business_name": "b", "generated_at": "g",
        "ai_visibility": {"platforms": [
            {"platform": "ChatGPT", "visibility_pct": 20, "sentiment_pct": 65,
             "reading": "Barely visible"}]}})
    out = render.build_html(ev, {
        "business_name": "b", "domain": "d", "report_date": "August 2026",
        "ai_platform_rows_html":
            "<tr><td>ChatGPT</td><td class=\"num\">20%</td>"
            "<td class=\"num\">65%</td><td>Barely visible</td></tr>",
        "ai_gap_html": "<p>Competitors are named; you are not.</p>"},
        section_files=["05_ai_visibility.html"])
    assert "The AI Search Opportunity" in out
    assert "ChatGPT" in out


def test_paid_section_absent_with_todays_evidence():
    ev = evidence.Evidence({"domain": "d", "business_name": "b",
                            "generated_at": "g",
                            "paid": {"estimated_monthly_spend_usd": {"value": None},
                                     "paid_keywords": [], "landing_pages": []}})
    out = render.build_html(ev, {"business_name": "b", "domain": "d",
                                 "report_date": "August 2026"},
                            section_files=["08_paid_vs_organic.html"])
    assert "Paid vs Organic" not in out


def test_paid_section_renders_once_spend_exists():
    ev = evidence.Evidence({"domain": "d", "business_name": "b",
                            "generated_at": "g",
                            "paid": {"estimated_monthly_spend_usd": {"value": 12000}}})
    out = render.build_html(ev, {
        "business_name": "b", "domain": "d", "report_date": "August 2026",
        "paid_spend": "$12.0K", "paid_keyword_count": "84",
        "paid_landing_pages_html": "<li>/trt-online</li>",
        "paid_vs_organic_html": "<p>You are renting this traffic.</p>"},
        section_files=["08_paid_vs_organic.html"])
    assert "Paid vs Organic" in out
    assert "$12.0K" in out


def test_both_future_sections_are_in_default_sections():
    assert "05_ai_visibility.html" in render.DEFAULT_SECTIONS
    assert "08_paid_vs_organic.html" in render.DEFAULT_SECTIONS


def test_default_sections_are_in_numeric_order():
    """A section rendered out of order would put the CTA mid-document."""
    nums = [int(f.split("_")[0]) for f in render.DEFAULT_SECTIONS]
    assert nums == sorted(nums)
```

`_petermd_min_tokens()` is a helper you add alongside: it returns the minimum token set the PeterMD fixture's sections need — reuse the token dict already built in `tests/test_acceptance_petermd.py` if convenient, or define the identity tokens plus `ai_platform_rows_html` and `ai_gap_html` set to empty strings.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_render.py -k "ai_visibility or paid_section" -v`
Expected: FAIL — templates missing.

- [ ] **Step 3: Write the templates**

`templates/sections/05_ai_visibility.html`:

```html
<!--SECTION:ai_visibility-->
<div class="page">
  <div class="eyebrow">04 &nbsp;/&nbsp; The Opportunity</div>
  <h2>The AI Search Opportunity</h2>

  <p>Every major AI assistant now recommends businesses to people asking for
  exactly what {{business_name}} sells. When someone asks one of them where to go,
  they see an answer &mdash; and one business gets the click.</p>

  <table>
    <thead><tr><th>AI platform</th><th>Visibility</th><th>Sentiment</th><th>Reading</th></tr></thead>
    <tbody>
      {{ai_platform_rows_html}}
    </tbody>
  </table>

  <div class="callout">
    <div class="t">Why this matters right now</div>
    <p>Buyers who use these tools to research are higher intent &mdash; they have
    already narrowed their choices before they ever click. Being invisible there
    means losing the best customers to whoever the AI names first.</p>
  </div>

  <h3>The specific gap</h3>
  {{ai_gap_html}}

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
<!--/SECTION:ai_visibility-->
```

`templates/sections/08_paid_vs_organic.html`:

```html
<!--SECTION:paid-->
<div class="page">
  <div class="eyebrow">04b &nbsp;/&nbsp; Rented vs Owned</div>
  <h2>Paid vs Organic</h2>

  <p>What {{business_name}} is currently paying for, set against what the same
  visibility would cost to own.</p>

  <div class="stats">
    <!--TILE:paid_spend-->
    <div class="stat"><div class="k">Estimated monthly ad spend</div><div class="v">{{paid_spend}}</div></div>
    <!--/TILE:paid_spend-->
    <!--TILE:paid_keyword_count-->
    <div class="stat"><div class="k">Paid keywords</div><div class="v">{{paid_keyword_count}}</div></div>
    <!--/TILE:paid_keyword_count-->
  </div>

  <!--TILE:paid_landing_pages_html-->
  <h3>Where that spend is pointed</h3>
  <ul class="findings">{{paid_landing_pages_html}}</ul>
  <!--/TILE:paid_landing_pages_html-->

  {{paid_vs_organic_html}}

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
<!--/SECTION:paid-->
```

Now set `DEFAULT_SECTIONS` in `render.py` to the full twelve in numeric order, as listed in Task 7 Step 3.

- [ ] **Step 4: Run to verify passing**

Run: `python -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/sections/05_ai_visibility.html templates/sections/08_paid_vs_organic.html render.py tests/test_render.py
git commit -m "feat: add AI visibility and paid sections, gated until their data lands"
```

---

### Task 9: Full-report acceptance test on real collected evidence

**Files:**
- Create: `tests/test_acceptance_full_report.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above, plus `render.html_to_pdf` and `render.verify_pdf`.

This is the bar for Phase 2b: the engine must render a complete, correct PDF from the evidence file the collector actually produced, not from a hand-built fixture. That file lives at `state/prospects/getpetermd.com/evidence.json`.

**Regenerate it before running this task**, with `python collect.py getpetermd.com --name PeterMD` — dry-run by default, no write. Two reasons it will otherwise be stale: the file currently on disk was written before `brand_split.sample_rows` existed, so `brand_split_basis` would render as an em dash and the sample-disclosure test would fail; and Task 1 changes the fetch depth from 100 to 500 keywords, which changes `money_keywords` and `position_buckets.sample_rows`. Task 1 Step 5 already regenerates it, so executing in order is enough — this note is for an out-of-order run.

Verify before starting: `python -c "import json;d=json.load(open('state/prospects/getpetermd.com/evidence.json',encoding='utf-8'));print(d['brand_split'].get('sample_rows'), len(d.get('money_keywords') or []))"` should print a row count around 500 and a money-keyword count of at least 5. If it prints `None`, the file is stale.

- [ ] **Step 1: Write the failing acceptance test**

`tests/test_acceptance_full_report.py`:

```python
"""Acceptance: a complete report from REAL collected evidence.

Phase 1's acceptance test rendered a hand-built fixture transcribed from the
reference PDF. This one renders what the collector actually produced from the
live API, which is the only thing that proves the two halves fit together.
"""

import os
import pathlib

import pytest

import evidence
import render

REAL = (pathlib.Path(__file__).parent.parent / "state" / "prospects" /
        "getpetermd.com" / "evidence.json")

pytestmark = pytest.mark.skipif(
    not REAL.exists(),
    reason="run `python collect.py getpetermd.com --name PeterMD` first "
           "(dry-run, read-only)")


def _tokens(ev):
    """Identity plus the authored narrative. Every figure comes from evidence."""
    g = ev.get
    return {
        "business_name": "PeterMD",
        "domain": ev.get("domain"),
        "report_date": "August 2026",

        "exec_summary_intro_html": "<p>PeterMD has built real momentum.</p>",
        "exec_summary_findings_html":
            "<li>Almost all traffic comes from people who already knew the name.</li>"
            "<li>The money keywords sit well off page one.</li>",
        "exec_summary_close_html": "<p>The gap is closable.</p>",

        "finding_headline": "%s%% of your traffic already knew your name"
                            % render.fmt(g("brand_split.brand_pct")),
        "finding_body_html": "<p>The site converts existing awareness rather than "
                             "creating new demand.</p>",
        "finding_data_callout_html":
            "<p>Brand searches %s of traffic; problem searches %s.</p>"
            % (render.fmt(g("brand_split.brand_pct"), "pct"),
               render.fmt(g("brand_split.nonbrand_pct"), "pct")),
        "finding_why_html": "<li>The website is not currently an acquisition "
                            "channel.</li>",

        "visits": render.fmt(g("traffic.monthly_organic_visits"), "k"),
        "keyword_count": render.fmt(g("traffic.ranking_keyword_count")),
        "traffic_value": render.fmt(g("traffic.traffic_value_usd"), "usd"),
        "brand_pct": render.fmt(g("brand_split.brand_pct"), "pct"),
        "nonbrand_pct": render.fmt(g("brand_split.nonbrand_pct"), "pct"),
        "brand_split_basis": "top %s keywords by traffic"
                             % render.fmt(g("brand_split.sample_rows")),
        "money_keywords_rows_html": "".join(
            "<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td></tr>"
            % (k["keyword"], render.fmt(k.get("volume")),
               render.fmt(k.get("position")))
            for k in (ev.data.get("money_keywords") or [])),
        "traffic_close_html": "<p>Every one of those searches is a customer "
                              "asking for exactly what you sell.</p>",

        "pos_1_3": render.fmt(g("position_buckets.1-3")),
        "pos_4_10": render.fmt(g("position_buckets.4-10")),
        "pos_11_20": render.fmt(g("position_buckets.11-20")),
        "pos_21_50": render.fmt(g("position_buckets.21-50")),
        "pos_51_100": render.fmt(g("position_buckets.51-100")),
        "position_buckets_close_html":
            "<p>Move a fraction of these into the top ten and non-brand traffic "
            "climbs sharply.</p>",

        "referring_domains": render.fmt(g("backlinks.referring_domains")),
        "total_backlinks": render.fmt(g("backlinks.total_backlinks")),
        "authority": render.fmt(g("backlinks.authority")),
        "authority_label": (ev.data.get("backlinks", {})
                            .get("authority", {}).get("metric_name", "Authority")),
        "trust": render.fmt(g("backlinks.trust")),
        "trust_label": (ev.data.get("backlinks", {})
                        .get("trust", {}).get("metric_name", "Trust")),
        "anchor_rows_html": "".join(
            "<tr><td>%s</td><td class='num'>%s</td></tr>"
            % (a.get("anchor", ""), render.fmt(a.get("count")))
            for a in (ev.data.get("backlinks", {}).get("top_anchors") or [])[:8]),
        "link_profile_findings_html":
            "<li>Authority is real but not concentrated in the right category.</li>",

        "competitor_rows_html": "".join(
            "<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td></tr>"
            % (c.get("domain", ""), render.fmt(c.get("monthly_visits"), "k"),
               render.fmt(c.get("ranking_keywords")))
            for c in (ev.data.get("competitors") or [])[:6]),
        "competitor_pattern_html":
            "<li>They point content at what buyers actually search.</li>",

        "plan_days_1_30_html": "<li>Technical cleanup and link audit.</li>",
        "plan_days_31_60_html": "<li>Rebuild the highest-value pages.</li>",
        "plan_days_61_90_html": "<li>Authority and compounding growth.</li>",
        "plan_outcome_html": "<p>Materially more non-brand traffic within six "
                             "months.</p>",
    }


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    ev = evidence.Evidence.load(REAL)
    ev.validate()
    html = render.build_html(ev, _tokens(ev))
    d = tmp_path_factory.mktemp("full")
    hp, pp = d / "audit.html", d / "audit.pdf"
    hp.write_text(html, encoding="utf-8")
    render.html_to_pdf(str(hp), str(pp))
    return render.verify_pdf(str(pp)), pp, ev


def test_report_has_more_pages_than_phase_one(rendered):
    info, _, _ = rendered
    assert info["pages"] >= 7, "six evidence-backed sections plus narrative"


def test_all_three_brand_fonts_embedded(rendered):
    info, _, _ = rendered
    flat = "".join(info["fonts"]).replace("-", "").replace(" ", "")
    for f in ("HankenGrotesk", "CormorantGaramond", "SpaceMono"):
        assert f in flat, "%s missing" % f


def test_every_present_section_appears_in_the_pdf(rendered):
    info, _, ev = rendered
    headings = {"traffic": "Traffic & Rankings",
                "position_buckets": "Where the Rankings Already Sit",
                "backlinks": "The Link Profile",
                "competitors": "Who", }
    for key, heading in headings.items():
        if key in ev.present_sections():
            assert heading in info["text"], "%s present but %r missing" % (key, heading)


def test_absent_sections_do_not_appear(rendered):
    info, _, ev = rendered
    assert "ai_visibility" not in ev.present_sections()
    assert "The AI Search Opportunity" not in info["text"]
    assert "paid" not in ev.present_sections()
    assert "Paid vs Organic" not in info["text"]
    assert "scorecard" not in ev.present_sections()
    assert "The Visibility Scorecard" not in info["text"]


def test_no_em_dash_placeholder_survives_into_the_pdf(rendered):
    """A dash in a stat tile means the tile gate failed. Every printed figure
    must be a real number."""
    info, _, _ = rendered
    assert render.DASH not in info["text"]


def test_no_unsubstituted_token_and_no_vendor_name(rendered):
    """verify_pdf already enforces both; asserted explicitly as the requirement."""
    info, _, _ = rendered
    assert "{{" not in info["text"]
    low = info["text"].lower()
    for vendor in render.FORBIDDEN_VENDORS:
        assert vendor not in low


def test_the_sample_basis_is_disclosed(rendered):
    info, _, _ = rendered
    assert "keywords by traffic" in info["text"], (
        "the brand split is sample-derived and must say so")


def test_money_keywords_table_is_not_thin(rendered):
    info, _, ev = rendered
    n = len(ev.data.get("money_keywords") or [])
    assert n >= 5, ("only %d money keywords; the 500-keyword fetch should yield "
                    "a full table" % n)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_acceptance_full_report.py -v`
Expected: FAIL, or SKIP if the evidence file is absent. If it skips, run the collector first as described above, then re-run. Fix whatever the failures name. **Do not weaken an assertion to get green** — in particular `test_no_em_dash_placeholder_survives_into_the_pdf` and `test_money_keywords_table_is_not_thin` are the two that prove this phase worked.

- [ ] **Step 3: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS across every file.

- [ ] **Step 4: Render for human inspection and verify visually**

```bash
python -c "
import sys, pathlib; sys.path.insert(0,'.')
import evidence, render
sys.path.insert(0, 'tests')
from test_acceptance_full_report import _tokens
ev = evidence.Evidence.load('state/prospects/getpetermd.com/evidence.json')
pathlib.Path('out').mkdir(exist_ok=True)
open('out/full.html','w',encoding='utf-8').write(render.build_html(ev, _tokens(ev)))
render.html_to_pdf(str(pathlib.Path('out/full.html').resolve()), str(pathlib.Path('out/full-report.pdf').resolve()))
print(render.verify_pdf('out/full-report.pdf')['pages'], 'pages')
"
```

Then rasterise and inspect every page:

```bash
python -c "
import fitz, os
d = fitz.open('out/full-report.pdf')
os.makedirs('out/png', exist_ok=True)
for i, p in enumerate(d):
    p.get_pixmap(dpi=110).save('out/png/p%d.png' % (i+1))
print('pages:', d.page_count)
"
```

Report the page count, and confirm by looking at the images: no clipped content, no overlapping elements, no empty tiles, no table running off the page, and the footer present on the pages where it should be. Report anything visually wrong rather than only what the tests assert — the text-extraction tests prove content is present, not that it is laid out correctly.

- [ ] **Step 5: Update the README**

Add a Phase 2b section listing the twelve report sections and which evidence group gates each, noting that `05_ai_visibility` and `08_paid_vs_organic` stay absent until their data lands and that `04_scorecard` stays absent by operator decision until AI-visibility and technical inputs exist.

- [ ] **Step 6: Commit**

```bash
git add tests/test_acceptance_full_report.py README.md
git commit -m "test: render a complete report from real collected evidence"
```

---

## Self-Review

**Spec coverage.** This plan implements the spec's report section: all twelve sections exist after Task 8, in the spec's order, each gated on the evidence group the spec assigns it. The tile-granular gate in Task 2 closes the spec's "omits its section **or stat tile**" requirement, which Phase 1 implemented only half of. The sample-disclosure rule is honoured in Task 4's copy and asserted in Task 9. The no-vendor-names and no-unsubstituted-token gates are re-asserted in Task 9 against a real render.

Deliberately not in Phase 2b, with the phase that owns each: the browser AI probe (2c), the dossier and pricing and sales script (2d), Slack posting (2e). `scorecard` stays absent by operator decision. `technical` needs a crawl nobody has authorised.

**Placeholder scan.** No TBD or "similar to Task N". Every code step carries runnable code and every template is complete markup. Task 7 Step 3 explicitly warns that `DEFAULT_SECTIONS` must not list the two Task 8 templates before they exist, because a missing file raises on read.

**Type consistency.** `render.strip_absent_tiles(html, tokens)` as added in Task 2 is called by `build_html` and used by Tasks 4, 5, 8. `render.DASH` is the sentinel in both Task 2's gate and Task 9's assertion. `render.fmt(value, kind)` with kinds `int|k|usd|pct` is used consistently in Task 9's token builder. Evidence dotted paths used in Task 9 match those written by `collect.build_evidence` and keyed in `evidence.SECTION_REQUIREMENTS`: `traffic.*`, `brand_split.*`, `position_buckets.{1-3,4-10,11-20,21-50,51-100}`, `money_keywords`, `backlinks.{referring_domains,total_backlinks,authority,trust,top_anchors}`, `competitors`, `ai_visibility.platforms`, `paid.estimated_monthly_spend_usd`. Section marker ids in every new template are keys of `SECTION_REQUIREMENTS`, which the pre-existing `test_section_markers_are_balanced_and_known_to_evidence` enforces automatically as sections are added.

One risk worth naming: `brand_split.sample_rows` is read by Task 9's `brand_split_basis` token but is a bare int rather than a wrapped metric, so `Evidence.get` returns it directly. That is the same shape as `position_buckets.sample_rows` and was verified working in Phase 2a.
