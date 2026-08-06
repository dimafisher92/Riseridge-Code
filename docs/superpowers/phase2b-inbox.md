# Phase 2b inbox

Findings carried out of Phase 2a (data collection). Sources: seven per-task reviews, one
whole-branch review on the most capable model, and controller verification against the live
API. Nothing here blocks the Phase 2a merge.

Phase 2a shipped on branch `phase2a-data-collection`. 316 tests passing.

## Loom Outreach funnel (added 2026-08-06)

A third funnel posts booked appointments and its format is materially richer than
the SEO/VSL funnels. `leads.py` now handles all three.

    :date: New Appointment Booked Loom Outreach
    Invitee: ...        Phone: ...          Website: ...
    Event: Ecom AI SEO Strategic Call       Time: 2026-08-05T22:30:00.000000Z
    Location: <zoom url>                    Company Revenue: $50K - $100K /month

Three things this unlocks that the SEO funnel could not:

- **A phone number.** Phone-based prospect research was previously impossible from
  Slack; it is available for Loom leads.
- **The real appointment time.** Call prioritisation was previously impossible;
  Loom rows can be sorted by when the call actually is.
- **Actual company revenue** instead of a self-reported budget. This is a far
  better input to the size-driven price band than "$3,000+ per month", and it
  should take precedence in `pricing.py` when present.

Implementation notes: labels are PLAIN, not bold, so `field()` now tries the bold
form then a line-anchored plain form. Detection moved from a `startswith` prefix
to "does the first line contain the phrase 'appointment booked'", after stripping
`:emoji:` shortcodes and asterisks — which admits any future funnel wording while
still rejecting "Appointment stuck in booked for more than 5 days". Loom asks no
business-type question, so the event name carries the track ("Ecom AI SEO
Strategic Call" -> ecom).

**For Phase 2d:** `pricing.py` should prefer `Lead.revenue` over `Lead.budget`,
and the dossier should use `Lead.phone` when present. `Lead.funnel_source` is
"seo", "vsl" or "loom" so behaviour can branch per funnel.

## Parked with rulings

### Cold row-derived metrics rely on an invariant rather than an explicit gate

`_usable` gates the two headline cold metrics (`ranking_keyword_count`, `total_backlinks`) so a
not-ready payload yields `null` instead of `0`. But `rows` — which feeds `brand_split`,
`money_keywords` and the cold-derived `position_buckets` — is computed unconditionally.

**Ruling: park.** Safe today because the documented not-ready contract guarantees empty
`results` alongside `should_retry: true`, so `rows` is empty and every derived value falls
through to its existing null handling. If a not-ready response ever carried partial rows, those
sample-derived figures would compute over incomplete data with no not-ready signal. Worth an
explicit `_usable` gate on `rows` when someone is next in the file.

### A stale or deleted project id is never revalidated

`run` reads a recorded `searchatlas_project_id` from a prior evidence file and reuses it
directly, which is what closes the duplicate-write risk for an already-collected prospect. It
does not revalidate that the project still exists.

**Ruling: park.** If the project were deleted upstream, best case the warm payloads resolve to
nothing and metrics degrade to `null` per the null-safety rule; worst case an exception
propagates out of `run`. Either way the domain is stuck on the dead id with no fallback to
`find_project`. Add a cheap revalidation (or a `--refresh-project` flag) when Phase 2e wires
the unattended path, where a stuck prospect would go unnoticed.

## Worth deciding in Phase 2b

### The brand split contradicts the vendor's own figure by 10x

Our derived brand share for getpetermd.com is **88%**. The warm payload we already fetch
carries the vendor's own `organic_traffic_branded` = 481 and `organic_traffic_non_branded` =
4721 — i.e. **9.2% branded**.

The reconciliation, now recorded in `fixtures/api/README.md`: the vendor appears to match only
the full concatenated root (`getpetermd`), missing the spaced form `peter md` which carries the
majority of branded traffic in our sample. Our figure is the better one, and the reference
report's own headline (95% brand) corroborates the high number rather than the low one.

**But an operator quoting 88% has no answer if a prospect's own tool says 9%.** Decide in 2b
whether the audit should state the basis explicitly ("branded searches including spaced and
abbreviated forms of the brand name"), which pre-empts the objection, or stay silent.

### Position buckets and brand split are samples, and the report must say so

Both are derived from the top 100 keywords by traffic, not the full corpus. Each now carries a
`sample_rows` key and a source string naming the sample size, so the limitation is
machine-readable. `ranking_keyword_count`, by contrast, is the true domain total.

**A section that prints "3,846 ranking keywords" above a distribution summing to 100 will be
noticed.** 2b's section copy must either caption the distribution as a top-100 sample or scale
the presentation. The data now tells the author which it is; the author has to use it.

### Only 2 money keywords surfaced where the reference report had 8

`money_keywords` filters to non-branded terms above 500 volume and below position 10, over a
100-row page. For getpetermd.com that yields 2. The reference report showed 8.

Not a defect — the filter is working — but the money-keywords table is one of the report's
strongest sections and 2 rows is thin. Revisit `limit`, `min_volume`, or the page depth when
authoring that section.

### `metric_name` still hard-codes vendor metric names

`collect.py` writes `metric_name: "Domain Rating"` and `"Trust Flow"`. The operator decided on
2026-08-05 to keep them, and this matches the spec. Recorded here only because `phase2-inbox.md`
item 6 had parked it as undecided and the code has now baked it into every generated file. The
values themselves come from SearchAtlas's own proxies, not from Ahrefs or Majestic.

## Minor, fine to ship

- `leads.py` prints `(unusable: <raw>)` with the prospect's pasted text verbatim, which can
  surface a personal email in console output likely pasted into Slack. Judged Minor: `--json`
  already emitted every lead's email, and `website_raw` never reaches client-facing output. A
  one-line mask (`re.sub(r"[^@\s]+@", "***@", ...)`) is the cheap fix.
- `goo.gle` is a real Google short-link domain and is missing from `SOCIAL_HOSTS`.
- `sawarm.position_buckets` sums whichever of the three native middle buckets resolved, so a
  partial response silently understates the printed 21-50 range with no signal.
- `apply_cr_total_override` appears in every cold payload (always `False`), is unexamined, and
  its name implies it can change the meaning of `total_count` — which feeds the client-facing
  keyword count on the cold path.
- In a cold-only run, several provenance strings are stamped with the warm source even though no
  warm fetch occurred. Nulls are omitted so nothing wrong prints, but the provenance record is
  inaccurate.
- `ensure_project`'s `apply` is not keyword-only on the function itself; the guard lives on
  `collect.run`. Misbinding fails toward no write.
- `(row.get("metrics") or {})` raises `AttributeError` on a truthy non-dict `metrics`. Fails
  loud rather than masking a bad row into a false "not found".
- `ColdError` is exported and never raised.
- Re-capturing the API fixtures will break roughly six tests that pin exact real values. That is
  deliberate — the docstrings ask future maintainers not to relax them — but nothing warns that
  regenerating fixtures requires updating the pinned constants.

## Test-suite gaps worth closing

The whole-branch review named these as passing against a broken implementation:

- `tests/test_sawarm.py` position-bucket fixture test asserts only that *one* of six buckets
  resolved; the exact-value test uses a hand-built payload, so no test pins the real recording's
  bucket values.
- `tests/test_sawarm.py` competitor and anchor fixture tests assert only that a domain/anchor
  string is non-empty, so a field-name regression on `competitor_traffic`,
  `competitor_keywords` or `backlinks_num` would ship silently — competitors would print with no
  numbers.
- `tests/test_collect.py` asserts `"traffic" in present or "position_buckets" in present`, which
  passes with the entire traffic section null.
- The provenance sweep covers `traffic`, `backlinks` and `position_buckets` but omits
  `brand_split`, and is vacuous for the list sections (`money_keywords`, `competitors`,
  `top_anchors`), whose printable numbers carry no provenance at all.
