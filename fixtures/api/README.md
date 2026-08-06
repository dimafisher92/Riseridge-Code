# SearchAtlas API fixtures

These are real recordings, not hand-written samples. Captured with
`saprobe.py` against the live SearchAtlas API:

```
python saprobe.py --domain getpetermd.com --warm-id 824060   # coherent cold+warm pair
python saprobe.py --domain trtnation.com                     # cold-only example
```

Every fixture file has the shape `{"service", "path", "params", "domain",
"captured_at", "payload"}`. `saprobe.load(name)` returns just `payload`.
Regenerate any fixture with the commands above; do not hand-edit these files.

## Naming and provenance (fixed after a real overwrite bug)

Fixture filenames are domain-scoped: `cold_<slug>_*` and `warm_<slug>_*`,
where `<slug>` is `re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")` —
e.g. `getpetermd.com` becomes `getpetermd_com`. `keyword_details_trt_cost.json`
is the one exception: its query text (`"trt cost"`) is fixed regardless of
`--domain`, so it isn't slugged, but every fixture — including this one —
still carries a `"domain"` key recording which `--domain` invocation wrote
it, so nothing on disk is ambiguous about its origin.

Earlier in this task, before scoping existed, `cold_*` names were flat
(`cold_backlinks.json`, no domain in the name) and `main()` always
re-captured the four cold endpoints regardless of `--warm-id`. Running the
`trtnation.com` capture followed by the `getpetermd.com` capture silently
replaced the `trtnation.com` cold data with `getpetermd.com` data under the
same filenames. That failure mode is closed now: two different `--domain`
values always produce disjoint filename sets — pinned by
`tests/test_saprobe.py::test_main_scopes_fixtures_by_domain_and_never_posts`
— and every record is self-identifying via its `domain` key even if a
filename were ever reused by mistake.

Current fixtures on disk are a coherent pair for `getpetermd.com` (cold +
warm, project `824060`, which already existed before this task ran —
nothing was created) plus a cold-only capture for `trtnation.com` (a domain
not owned by this project, chosen so the cold numbers carry no client bias).

## Shapes, one section per fixture

Top-level keys and one representative record's keys, from the actual files
currently in this directory:

```
## cold_getpetermd_com_backlinks.json (domain=getpetermd.com)
  top: ['apply_cr_total_override', 'enriched', 'results', 'total_count']
  record: ['anchor', 'author_id', 'author_name', 'backlinks_count', 'canonical', 'canonical_resolved', 'domain_ascore', 'domains', 'domains_count', 'external_link_num', 'first_seen', 'form', 'frame', 'image', 'image_alt', 'image_url', 'internal_link_num', 'ip', 'lang', 'last_seen', 'lost_due', 'lostlink', 'mobile', 'newlink', 'nofollow', 'page_ascore', 'platform', 'position', 'redirect', 'redirect_code', 'redirect_url', 'response_code', 'sitewide', 'source_size', 'source_title', 'source_url', 'sponsored', 'target_title', 'target_url', 'text', 'traffic', 'ugc', 'urlanchor']
## cold_getpetermd_com_brand_signal.json (domain=getpetermd.com)
  top: ['results', 'total_errors', 'total_found', 'total_requested']
  record: ['brand_signal_recommendations', 'brand_signal_score_processing_status', 'domain', 'score', 'status', 'total_processed']
## cold_getpetermd_com_organic_keywords.json (domain=getpetermd.com)
  top: ['apply_cr_total_override', 'results', 'total_count']
  record: ['change_of_traffic', 'click_pontential_pct', 'click_potential', 'cmp', 'cpc', 'intents', 'keyword', 'keyword_difficulty', 'position', 'position_change', 'position_difference', 'position_is_serp_feature', 'position_serp_features', 'previous_position', 'ranking_url', 'search_volume', 'serp_features', 'traffic', 'traffic_cost', 'traffic_cost_pct', 'traffic_pct', 'trends']
## cold_trtnation_com_backlinks.json (domain=trtnation.com)
  top: ['apply_cr_total_override', 'enriched', 'results', 'total_count']
  record: (same shape as cold_getpetermd_com_backlinks.json above)
## cold_trtnation_com_brand_signal.json (domain=trtnation.com)
  top: ['results', 'total_errors', 'total_found', 'total_requested']
  record: (same shape as cold_getpetermd_com_brand_signal.json above)
## cold_trtnation_com_organic_keywords.json (domain=trtnation.com)
  top: ['apply_cr_total_override', 'results', 'total_count']
  record: (same shape as cold_getpetermd_com_organic_keywords.json above)
## keyword_details_trt_cost.json (domain=trtnation.com, but the query itself is domain-independent)
  top: ['results']
  results is a DICT, not a list: ['ads', 'autocomplete', 'bolded_terms', 'cpc', 'difficulty', 'enhanced_keyword_metrics', 'global_volume', 'keyword', 'last_serp_pulled_at', 'public_share_hash', 'questions', 'related_keywords', 'search_volume', 'search_volume_data', 'serp_features', 'serp_overview', 'serps_fetched_at', 'task_status', 'top_countries_search_volume', 'total_results']
## warm_getpetermd_com_anchors.json (domain=getpetermd.com)
  top: ['last_processed_at', 'processing_status', 'results', 'source', 'total_count']
  record: ['anchor', 'backlinks_num', 'domains_num', 'first_seen', 'last_seen', 'urlanchor']
## warm_getpetermd_com_organic.json (domain=getpetermd.com)
  top (37 keys, all scalar/list, no results list — see full values below):
  ['all_position_changes', 'commercial_keywords_count', 'commercial_keywords_traffic', 'commercial_keywords_traffic_cost', 'declined_position_changes', 'improved_position_changes', 'informational_keywords_count', 'informational_keywords_traffic', 'informational_keywords_traffic_cost', 'is_processing', 'last_processed_at', 'lost_position_changes', 'navigational_keywords_count', 'navigational_keywords_traffic', 'navigational_keywords_traffic_cost', 'new_position_changes', 'organic_competitors', 'organic_keywords', 'organic_keywords_100_plus', 'organic_keywords_11_to_20', 'organic_keywords_21_to_30', 'organic_keywords_31_to_40', 'organic_keywords_41_to_50', 'organic_keywords_4_to_10', 'organic_keywords_51_to_100', 'organic_keywords_top_3', 'organic_position_changes', 'organic_traffic', 'organic_traffic_branded', 'organic_traffic_cost', 'organic_traffic_non_branded', 'processing_status', 'serps_features', 'transactional_keywords_count', 'transactional_keywords_traffic', 'transactional_keywords_traffic_cost', 'trend']
## warm_getpetermd_com_organic_competitors.json (domain=getpetermd.com)
  top: ['last_processed_at', 'processing_status', 'results', 'source', 'total_count']
  record: ['common_keywords', 'competition_level', 'competitor', 'competitor_keywords', 'competitor_traffic', 'domain_power', 'domain_rating', 'traffic_cost']
## warm_getpetermd_com_project_detail.json (domain=getpetermd.com)
  top: ['customer_id', 'data', 'id', 'is_saved', 'public_share_hash', 'searched_on']
  the useful content is nested under `data` and `data.competitor_research` — see full paths below
## warm_getpetermd_com_refdomains.json (domain=getpetermd.com)
  top: ['enriched', 'enrichment_status', 'last_processed_at', 'processing_status', 'results', 'source', 'total_count']
  record: ['backlinks_count', 'backlinks_num', 'category', 'country', 'domain', 'domain_ascore', 'domains', 'domains_count', 'first_seen', 'ip', 'is_follow', 'last_seen', 'lost', 'new', 'traffic']
```

A naive top-level-only key dump (as a first derivation pass did) hides where
the real numbers live in three of these files. Full dotted paths, walked and
verified against the actual bytes on disk, follow.

### `warm_getpetermd_com_project_detail.json` — full verified paths

```
payload.data.domain_rating                                   = 57
payload.data.domain_power                                    = 35
payload.data.spam_score                                       = null   (this capture)
payload.data.domain_authority                                 = null   (this capture)
payload.data.competitor_research.authority_score              = 35
payload.data.competitor_research.trust_flow                   = 25
payload.data.competitor_research.citation_flow                = 35
payload.data.competitor_research.backlink_count                = 27955
payload.data.competitor_research.dofollow_pages                = 18612
payload.data.competitor_research.nofollow_pages                = 9138
payload.data.competitor_research.referring_domains              = 896   (int)
payload.data.competitor_research.reffering_domains              = 3021  (int, doubled f — real, see warning below)
payload.data.competitor_research.referring_domain_type_direct   = "896"  (string)
payload.data.competitor_research.referring_domain_type_follow   = "790"  (string)
payload.data.competitor_research.referring_ips                  = 472
payload.data.competitor_research.referring_subnets              = 326
payload.data.competitor_research.backlinks_trend  = list of 44 {date, ascore,
    new_links, lost_links, total_links, new_ref_domains, lost_ref_domains, total_refdomains}
    — most recent entry (2026-08-01): total_links=27955, total_refdomains=3021
payload.data.competitor_research.backlinks.reffering_domains = list of per-domain records
    (a THIRD, differently-shaped occurrence of the same key name, nested one level
    deeper under competitor_research.backlinks — do not confuse with the scalar above)
```

`spam_score` and `domain_authority` both returned `null` in this capture.
Consistent with the documented volatility of these figures (see the module
docstring in `saprobe.py`) — treat `null` as "not populated for this domain
at capture time," not as "field doesn't exist," and re-check a fresh capture
before assuming either field is dead.

### `warm_getpetermd_com_organic.json` — flat, no nesting, real values this capture

Every key here is a top-level scalar or short list (`trend`,
`organic_competitors`, `organic_position_changes`); there is no `results`
array. The position-bucket family and its actual values in this capture:

```
payload.organic_keywords_top_3      = 77
payload.organic_keywords_4_to_10    = 162
payload.organic_keywords_11_to_20   = 253
payload.organic_keywords_21_to_30   = 370
payload.organic_keywords_31_to_40   = 454
payload.organic_keywords_41_to_50   = 453
payload.organic_keywords_51_to_100  = 1783
payload.organic_keywords_100_plus   = 294
payload.organic_keywords            = 3846   (roughly the sum of the buckets above)
payload.organic_traffic             = 5401
payload.organic_traffic_branded     = 481
payload.organic_traffic_non_branded = 4721
payload.organic_traffic_cost        = 36660
```

### `warm_getpetermd_com_organic_competitors.json` — competitor rows

`payload.results[i]` is flat, one level deep, six competitors in this
capture. Sample row (`petermdportal.com`):

```
payload.results[0].competitor          = "petermdportal.com"
payload.results[0].domain_rating       = 26.0
payload.results[0].domain_power        = 27.0
payload.results[0].competition_level   = 33.0
payload.results[0].common_keywords     = 29
payload.results[0].competitor_keywords = 64
payload.results[0].competitor_traffic  = 6510
payload.results[0].traffic_cost        = 46086
```

## `referring_domains` vs `reffering_domains` — the most dangerous field pair in this dataset

Both spellings are real, both appear in
`warm_getpetermd_com_project_detail.json["data"]["competitor_research"]`,
and in this capture they hold materially different values:

- `referring_domains` = **896** (correctly spelled)
- `reffering_domains` = **3021** (doubled f — not a typo to normalize away)

This was checked twice during this task. A first manual check missed the
doubled-f key because a plain substring search for `"ferring"` does not
match `"reffering"` — searching for the correct spelling alone will make the
misspelled key look absent. A full recursive walk of every key in the file
confirms both are present with the values above, in both the original
capture and an independently re-captured `warm_getpetermd_com_project_detail.json`.

Which one is "the" referring-domain count depends on what the number is
meant to represent, and there is corroborating evidence for both readings:

- **3021 is the total.** `competitor_research.backlinks_trend` is a
  44-point time series of `{total_links, total_refdomains, ...}`; its most
  recent entry (`date: 2026-08-01`) has `total_refdomains = 3021`, matching
  `reffering_domains` exactly, and `total_links = 27955`, matching
  `backlink_count` exactly. This corroborates `reffering_domains` as the
  full referring-domains total.
- **896 is a subset.** `referring_domain_type_direct = "896"` (a string)
  matches `referring_domains = 896` exactly, suggesting `referring_domains`
  specifically counts *direct* referring domains, as opposed to
  `referring_domain_type_follow = "790"`, a third, still smaller number in
  the same family.

Net: use `reffering_domains` (3021, doubled f) as the total referring-domain
count reported to a client, and treat `referring_domains` (896) as a
narrower "direct" subset, not a lower-precision duplicate of the total.
Getting this backwards understates a prospect's link profile by roughly
3.4x. `cold_getpetermd_com_backlinks.json` and
`cold_trtnation_com_backlinks.json` (top-level keys exactly
`apply_cr_total_override`, `enriched`, `results`, `total_count`) carry no
referring-domain count of any kind — that number only exists in the warm
project-detail payload, not in the cold backlinks list.

## Fixture sizes (this capture)

| File | Domain | Size |
|---|---|---|
| `cold_getpetermd_com_backlinks.json` | getpetermd.com | 30.0 KB |
| `cold_getpetermd_com_brand_signal.json` | getpetermd.com | 1.1 KB |
| `cold_getpetermd_com_organic_keywords.json` | getpetermd.com | 20.0 KB |
| `cold_trtnation_com_backlinks.json` | trtnation.com | 32.2 KB |
| `cold_trtnation_com_brand_signal.json` | trtnation.com | 1.1 KB |
| `cold_trtnation_com_organic_keywords.json` | trtnation.com | 20.5 KB |
| `keyword_details_trt_cost.json` | trtnation.com (query is domain-independent) | 29.7 KB |
| `warm_getpetermd_com_anchors.json` | getpetermd.com | 5.3 KB |
| `warm_getpetermd_com_organic.json` | getpetermd.com | 269.7 KB |
| `warm_getpetermd_com_organic_competitors.json` | getpetermd.com | 6.7 KB |
| `warm_getpetermd_com_project_detail.json` | getpetermd.com | 367.6 KB |
| `warm_getpetermd_com_refdomains.json` | getpetermd.com | 10.4 KB |

No fixture returned `should_retry: true` on first attempt during either live
capture session — the async-warm contract on the organic-keywords cold
endpoint (encoded in `saprobe.capture`'s retry loop) was not exercised live.
It remains tested via the fake client in `tests/test_saprobe.py`.

## Vendor's own branded/non-branded split contradicts ours — record, don't reconcile

`warm_getpetermd_com_organic.json` carries two fields the vendor itself
computed:

```
payload.organic_traffic_branded      = 481
payload.organic_traffic_non_branded  = 4721
```

That is 481 / (481 + 4721) = **9.2% branded** by the vendor's own
classification — starkly against this pipeline's own figure of roughly
**88% branded** (`derive.brand_split`, corroborated by
`test_brand_split_real_fixture_reports_higher_brand_share` asserting
`brand_pct > 80` against this same organic-keywords sample). An operator
will be asked about this gap on a sales call, so it is recorded here rather
than silently reconciled.

Likely explanation, not confirmed: the vendor's classifier appears to match
only the full concatenated root, `getpetermd` (and `petermd`), against each
keyword. `derive.brand_hit`'s word-run fallback additionally catches the
spaced lowercase form `peter md` — plainly a brand search for this business,
but a form the vendor's stricter matching seems to miss — and that spaced
form carries the majority of the branded traffic in our sample (see
`derive.py`'s module docstring on why `prune_branded.is_branded` alone,
which only recognises a token inside one whitespace-delimited word or a
run of Capitalised words, also misses it and why the word-run fallback
exists). Do not change our figure to match the vendor's: our 88% is the one
methodology documented and tested in this repo, and the vendor's own two
fields are transcribed here unmodified as the source of the contradiction,
not as a correction.
