# Phase 2 inbox

Findings carried out of Phase 1. Sources: seven per-task reviews, one whole-branch review
(most-capable model, 17 commits), and controller verification. Nothing here blocks Phase 1;
everything here should be resolved before or during Phase 2.

Phase 1 shipped on branch `phase1-leads-and-report-engine`. 123 tests passing.

## Blocking for Phase 2

### 1. The section gate is section-granular; the spec asks for tile-granular

`Evidence.present_sections()` uses `any(...)`, so one live metric keeps a whole section, and
`fmt(None)` then prints an em dash into every dead tile beside it. Verified: a scorecard with
all three pillars null but `ai_visibility` populated renders the full "Visibility Scorecard"
page with three `—` tiles under the copy "Anything below 60 needs work. Anything below 40 is
costing revenue every month."

The spec's rule is "a null value omits its section **or stat tile**". Only the first half is
implemented. Not an invented figure, but a page that per spec should not exist, wrapped in
authoritative prose. Needs a per-tile gate and a test for a partially-populated section.

Related: `04_scorecard.html` renders 3 of the 4 scorecard metrics. `scorecard.ai_visibility`
is hardcoded-absent from the template rather than data-gated, so an evidence file that does
score it silently never prints it.

### 4. `REQUIRED_FONTS` omits Space Mono

`brand.css` uses Space Mono in six rules covering every eyebrow, stat key, score key, table
header and `td.num` — i.e. every numeral in the position-buckets table. `verify_pdf` only
asserts Hanken Grotesk and Cormorant Garamond, so a cache regression dropping the four Space
Mono faces yields Courier numerals and passes the embed gate. Latent, not live: the cache
currently holds 18 faces.

### 5. The font cache is git-ignored and there is no dependency manifest

`state/` is ignored, so `state/fonts.css` (377 KB, 18 base64 faces) is not in the repo.
`build_html` calls `fonts.font_css()` with no argument, falling through to `fonts.CACHE`. On a
fresh clone every PDF-producing test, including the acceptance test, silently issues 18 Google
Fonts CSS requests plus 18 woff2 downloads instead of using a cache. `test_font_css_uses_cache_without_network`
does not catch this because it passes an explicit temp cache.

There is also no `requirements.txt` / `pyproject.toml` declaring PyMuPDF or pytest, so
"123 tests pass" is environment-dependent. Either commit the cache or make the suite fail
loudly rather than fetch.

### 6. Decide whether "Domain Rating" and "Trust Flow" are disclosures

`fixtures/petermd_evidence.json` carries `metric_name: "Domain Rating"` and
`metric_name: "Trust Flow"`, transcribed from the reference report and enshrined in the spec.
These are Ahrefs' and Majestic's proprietary metric names. The `metric_name` field exists to
label the printed number, and printing it tells any SEO-literate prospect which tools the
agency uses — which is exactly what the no-vendor-names rule exists to prevent.
`FORBIDDEN_VENDORS` does not catch them and no test looks.

**This is a client-facing copy decision for the operator, not a code fix.** Either relabel to
neutral language ("link authority", "link trust") or consciously accept the disclosure. The
fixture currently teaches the wrong pattern to whoever authors the remaining eight sections.

## Fixed during Phase 2a (2a Task 6: lead-quality fixes)

### `normalize_domain` accepted social profiles and the `wwww.` typo

Was: `https://www.facebook.com/mybiz` -> `facebook.com`; `https://instagram.com/mybiz` ->
`instagram.com`; `https://wwww.getpetermd.com` -> `wwww.getpetermd.com` (the `www.` strip did
not match the 4-w typo present in the live channel). Fixed in `leads.py`: `normalize_domain`
now runs a social-host blocklist (`_is_social_host`) returning `None`, and strips any run of
two or more leading `w`s (`re.sub(r"^w{2,}\.", "", host)`) rather than only the literal `www.`.

### `is_test_lead` substring-matched `test`, discarding real leads

Was: `ann@bestestates.com` -> `True`. `info@contestwinners.com` -> `True`. Name `Tester Brown`
-> `True`. Fixed in `leads.py`: `is_test_lead` now uses a word-boundary `TEST_PATTERN`
(`\btest\b`-style) against name and email instead of a bare substring check.

## Parked with rulings

### Whitespace-stripped vendor matching can false-positive

To close the letter-spacing evasion, `verify_pdf` matches `FORBIDDEN_VENDORS` against a
whitespace-stripped copy of the extracted text. Consequence: the legitimate phrase
"similar web" collapses to `similarweb` and trips the gate. Verified.

**Ruling: keep it.** It fails closed — generation stops with a clear message naming the
vendor, rather than leaking. In this domain "a similar web presence" is a plausible sentence,
so expect to hit it eventually; the remedy is to reword the narrative. Punctuation is not
stripped, so "similar. Web" and "similar-web" are safe. Trading a reworded sentence for a
closed disclosure hole is the right side of that bet.

### The letter-spacing test proves a mechanism with no current live exploit path

The `.eyebrow` test injects a vendor name by string-replacing rendered markup, because no
shipped template routes a token into any of the six letter-spaced classes except
`.score .band`, and `.score .band` at `0.1em` does not reproduce PyMuPDF glyph-splitting in
this Chrome build (only `.eyebrow` at `0.16em` does).

**Ruling: keep it.** The fix itself is unconditional across all six classes, and Phase 2
authors eight more sections that may well put data into a table header or stat key. The other
half of the finding, the spaced `Search Atlas` spelling, is reachable today through the real
`business_name` field and is tested through that path.

### `.page-footer` on overflowing sections

Fixing silent content clipping (`.page` moved from `height: 11in` to `min-height`) traded a
cosmetic regression. Measured on a 3-sheet overflowing section: sheets 1 and 2 have no footer,
and the footer that prints lands about 0.76in from the **top** of sheet 3 with roughly ten
inches of blank page below it.

**Ruling: accepted.** Content preservation beats footer placement. Do NOT reintroduce
`overflow: hidden`. Harmless for the shipping 4-section layout, where footers verifiably sit
at the page bottom on all four pages. Phase 2 must design its eight sections knowing this,
and should add a test pinning footers-on-every-page for the normal render.

### `.page:last-of-type` matches by tag, not class

Harmless today (all top-level elements are `.page` divs; the acceptance PDF is exactly 4
pages, no trailing blank). Becomes a trailing-blank-page bug the moment a non-`.page` div is
appended. `.page:not(:last-child)` or `.page + .page { page-break-before: always }` is the
robust form.

### Unclosed or mistyped `<!--SECTION:id-->` markers

A mismatched close tag leaves an absent section's content and its live token in place. Today
that fails loudly on the missing token, but Phase 2 will supply every token unconditionally,
at which point an absent section renders with em-dash values — a section that should have been
omitted, printed as if real. Same class: a typo'd or uppercase marker id never matches
`present_sections()`, so the page silently vanishes from every audit with all tests green.

Partly mitigated: `test_section_markers_are_balanced_and_known_to_evidence` now walks
`templates/sections/` and asserts every marker is balanced and every id is a key of
`SECTION_REQUIREMENTS`. That test must keep passing as sections are added.

## Minor, fine to ship

- `load_env` strips neither inline comments nor surrounding quotes. If an operator ever quotes
  the token, the failure is a confusing `invalid_auth`.
- `history(pages=0)` loops forever if Slack returns a constant cursor, and there is no
  429 / `Retry-After` handling, so a full-history walk can die mid-scan on an uncaught
  `HTTPError`.
- `leads.main()` does `env["SALES_PIPELINE_CHANNEL"]`, so a missing key raises a bare
  `KeyError` rather than a readable error.
- `fmt` uses `"{:,.10g}"` for non-integral floats, which switches to scientific notation past
  10 significant digits. Not reachable today; large metrics use `kind="k"`/`"usd"`.
- `verify_pdf` does not scan PDF `/Title` metadata, so a vendor name reaching only `<title>`
  would evade. Low risk: the title is built from `business_name`, which also appears on-page.
- `README.md` says render.py has "three gates"; the list below it has four.
- `strip_absent_sections` and `assert_no_tokens` take a parameter named `html`, shadowing the
  stdlib module inside their own scope. Inert today.
- `import subprocess` sits mid-file in `render.py`.
