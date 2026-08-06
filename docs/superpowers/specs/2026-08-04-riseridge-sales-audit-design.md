# RiseRidge Sales: prospect audit and sales-prep pipeline

Design doc. Written 2026-08-04. Status: approved for planning.

## Goal

For every **newly** booked lead in the RiseRidge `#sales-pipeline` Slack channel (see Scope:
no historic backfill), produce three artefacts and post them into that lead's message thread:

1. A client-facing **AI search visibility audit PDF**, non-technical, RiseRidge-branded,
   with no mention of any tool used to produce it.
2. An internal **prospect dossier**: business-relevant background on the contact and their
   company, for the closer to read before the call.
3. An internal **sales script**: discovery questions, how to present the offer, expected
   business impact, and a recommended price tier with anchoring.

The reference artefact is `PeterMD AI Search Audit - RiseRidge (Corrected).pdf` (11 pages,
Letter, RiseRidge brand fonts). The new report follows its structure with one added section.

## Non-goals

- No fully unattended operation. AI visibility is measured by driving the real consumer
  engines in a logged-in Chrome, which needs an interactive session. The bot queues leads
  automatically; audits are produced when an operator session is available.
- No client delivery work. This pipeline stops at the sales call. Post-sale delivery stays
  in `D:\Claude Code\searchatlas\`.
- No dashboard screenshots from any third-party tool. All figures are re-rendered in
  RiseRidge branding.

## Architecture

New project directory, separate from the client-delivery toolkit:

```
D:\Claude Code\riseridge-sales\
  .env                      SLACK_BOT_TOKEN, SALES_PIPELINE_CHANNEL, SLACK_BOT_USER_ID
                            (git-ignored, never committed; SearchAtlas key stays in
                            searchatlas/.env and is read through the shim)
  README.md
  sa_client.py              sys.path shim re-exporting searchatlas.SearchAtlas
  slack.py                  Slack Web API: read history, post message, upload file to thread
  leads.py                  parse #sales-pipeline -> Lead records, dedupe ledger
  collect.py                build evidence.json for a prospect domain
  aiprobe.py                AI visibility probe + vertical cache read/write
  dossier.py                contact and company background research
  pricing.py                price matrix + tier/band recommendation
  render.py                 evidence.json + body.html -> audit.pdf
  embed_fonts.py            copied from searchatlas/reports
  post.py                   publish artefacts to the lead's Slack thread
  templates/
    audit.html              12-section skeleton with {{tokens}}
    brand.css               brand tokens and print rules
  state/
    leads.json              processed-lead ledger
    verticals/<slug>.json   cached AI probe results, 14-day TTL
    prospects/<domain>/
      evidence.json
      dossier.json
      body.html
      audit.pdf
```

### Runtime data flow

```
leads.py      #sales-pipeline -> Lead(name, email, domain, business_type, budget,
                                     frustration, tried, decision_role, urgency, thread_ts)
                  |
        +---------+---------+
        |                   |
collect.py            dossier.py          (independent, can run in parallel)
 evidence.json         dossier.json
        |                   |
        +---------+---------+
                  |
              pricing.py     needs BOTH: site/organic/paid scale from evidence,
             recommendation  company scale from dossier
                  |
aiprobe.py -> evidence.ai_visibility (vertical cache, browser)
                  |
        authored narrative -> body.html        (judgment layer)
                  |
              render.py -> audit.pdf
                  |
              post.py -> lead's Slack thread
```

`pricing.py` is the one hard ordering constraint: it cannot run until both `evidence.json` and
`dossier.json` exist, because band selection reads company scale from one and site scale from
the other.

Rationale for a sibling directory rather than a subpackage of `searchatlas/`: this is a
different business function (prospect acquisition, not client delivery), it carries its own
Slack credential and its own state, and mixing the two would muddy both. It reuses the
SearchAtlas HTTP client by import, so there is one client implementation.

## The evidence contract

The central design decision. One `evidence.json` per prospect. Every metric the report can
display exists as a key here, and every key carries provenance.

```json
{
  "domain": "getpetermd.com",
  "business_name": "PeterMD",
  "generated_at": "2026-08-04T12:00:00Z",
  "vertical": "mens-trt-clinic",
  "business_type": "ecom | local",
  "traffic": {
    "monthly_organic_visits": {"value": 10800, "source": "...", "pulled_at": "..."},
    "ranking_keyword_count": {"value": 3800, "source": "...", "pulled_at": "..."},
    "traffic_value_usd": {"value": 61900, "source": "...", "pulled_at": "..."}
  },
  "brand_split": {
    "brand_pct": {"value": 95, "source": "derived", "method": "brand-token match over ranking keywords"},
    "nonbrand_pct": {"value": 5, "source": "derived"}
  },
  "position_buckets": {
    "1-3": {"value": 77}, "4-10": {"value": 171}, "11-20": {"value": 268},
    "21-50": {"value": 834}, "51-100": {"value": 1700},
    "source": "...", "pulled_at": "..."
  },
  "money_keywords": [
    {"keyword": "enclomiphene", "volume": 90500, "position": 41, "cpc": null}
  ],
  "backlinks": {
    "referring_domains": {"value": 50}, "total_backlinks": {"value": 214},
    "authority": {"value": 71.8, "metric_name": "Domain Rating"},
    "trust": {"value": 5, "metric_name": "Trust Flow"},
    "top_anchors": [{"anchor": "...", "count": 1500}],
    "referring_domain_categories": ["B2B reviews", "personal finance"],
    "source": "...", "pulled_at": "..."
  },
  "paid": {
    "estimated_monthly_spend_usd": {"value": null},
    "paid_keywords": [], "landing_pages": [],
    "source": "...", "pulled_at": "..."
  },
  "competitors": [
    {"domain": "trtnation.com", "monthly_visits": 21200, "ranking_keywords": 5500}
  ],
  "ai_visibility": {
    "probed_at": "...", "vertical_cache": "mens-trt-clinic",
    "questions": ["where should I get TRT online"],
    "platforms": [
      {"platform": "ChatGPT", "brand_named": false, "topics_present": 1,
       "competitors_named": ["TRTNation", "Marek"], "verbatim_excerpt": "..."}
    ]
  },
  "technical": {
    "ai_crawler_access": {"value": null}, "structured_data": {"value": null},
    "core_web_vitals": {"value": null}, "source": "...", "pulled_at": "..."
  },
  "scorecard": {
    "content_quality": {"value": 28, "basis": "..."},
    "authority": {"value": 27, "basis": "..."},
    "user_experience": {"value": 49, "basis": "..."},
    "ai_visibility": {"value": null, "basis": "..."}
  }
}
```

**Enforced rule: a null value omits its section or stat tile. It is never estimated,
interpolated, or filled from memory.** Every scorecard number must state its `basis`. This
is what makes the numbers safe to quote out loud on a call.

## Data sources

Primary source is the SearchAtlas API via the existing client (`X-API-Key`, 12 service
subdomains, custom User-Agent required to avoid Cloudflare 1010).

### Verified capability (spike completed 2026-08-04)

All endpoints below were called live. The OpenAPI spec is inlined in the docs HTML as
`const __redoc_state`, not served separately; 798 paths, extracted for reference.

Everything splits on **cold** (works for any domain, read-only) versus **warm** (requires a
Site Explorer project, created by a POST):

| Metric | Cold? | Endpoint |
|---|---|---|
| Ranking keywords with volume, position, CPC, difficulty, intent, SERP features | **cold** | `keyword` `GET /api/v2/competitor-research/organic-keywords/?target=<domain>&page=N&page_size=100` |
| Backlink row list + total count | **cold** | `keyword` `GET /api/v2/competitor-research/backlinks/?target=<domain>` |
| Keyword research (volume, CPC, difficulty) | **cold** | `keyword` `GET /api/v1/keyword_details?query=<kw>&country_code=us` |
| Brand signal score + branded/navigational volume | **cold** | `keyword` `GET /api/v4/brand-signal-score/retrieve?domains=<domain>` |
| Domain overview (traffic, keyword count, traffic value) | warm | `keyword` `GET /api/v2/competitor-research/?search=<domain>` |
| **Position buckets, native** | warm | project detail, or `.../{id}/data-extended/?context=organic` |
| Authority (`domain_power`, `domain_rating`, `authority_score`), `trust_flow`, `citation_flow`, `spam_score` | warm | project detail |
| Top anchors | warm | `.../{id}/view-more/?context=anchors` |
| Referring domains list | warm | `.../{id}/view-more/?context=refdomains` |
| Organic competitors with traffic and keyword counts | warm | `.../{id}/view-more/?context=organic_competitors` |
| Paid keywords / paid positions | warm | project detail only |
| Technical / on-page audit | **no** | needs a Site Audit project and crawl budget |

Warming a domain is `POST /api/v2/competitor-research/ {"url", "country_code"}`. Since most
of the report's credibility figures are warm-only, the pipeline must create a Site Explorer
project per prospect. This is a **write**, and it is the one write `collect.py` performs.

**Operator authorised this write 2026-08-04: one Site Explorer project per booked prospect.**
Constraints on it, so it cannot run away: one project per unique normalised domain, never
per booked message (the same person can book twice); check for an existing project before
creating one (getpetermd.com already had project 824060 from prior use); record the project
id in `state/prospects/<domain>/evidence.json` so a re-run reuses it; and `collect.py`
performs no other write of any kind.

Two undocumented `?target=` collection routes are the entire reason cold prospects work at
all: `organic-keywords` and `backlinks`. No sibling exists; every other name falls through to
the `/{id}/` detail route and 400s.

**Position buckets are natively 1-3 / 4-10 / 11-20 / 21-30 / 31-40 / 41-50 / 51-100 / 100+.**
The report's 21-50 row is the sum of the 21-30, 31-40 and 41-50 buckets. That is an
aggregation of real values, not an estimate, and is allowed. Cold domains have no native
buckets, so bucketing then requires paging every keyword row (38 calls at 100/page for a
3,700-keyword site).

Doc bugs found, where the live API differs from the published spec. Trust these, not the docs:

- `/api/v1/keyword_details` takes `query`, not `keyword`.
- `/api/v4/brand-signal-score/retrieve` takes `domains` (plural).
- Site Auditor is under `/api/v2/site-audit/`, not the documented `/api/site-auditor/`.
- `/api/v2/competitor-research/{id}/data-extended/` needs an undocumented `context`; only
  `organic` and `backlinks` are valid.

Known dead ends, do not spend time on them:

- **`backlink.searchatlas.com` returns 401 "Bad API key" on every path.** Either it needs
  separate credentials or the subscription does not include it. All backlink data must come
  from the `keyword` service instead.
- `POST /api/v1/bulk-url-metrics/`, the documented one-shot domain overview, returns a 500.
- `organic-keywords` silently ignores every filter and sort parameter (`order_by`,
  `filters`, `position_from/to`, `mode`). Paging is the only option.
- `/api/v1/website-competitors-*` and `/search-console/*` are bound to properties we own and
  return nothing for a prospect.

Two operational rules for `collect.py`:

- `organic-keywords?target=` is **async-warmed**. It can return
  `{"results": [], "total_count": 0, "should_retry": true, "retry_after": 10}`. Retry on
  `should_retry`; never read an empty first response as "no data". The cold `backlinks?target=`
  call took about two minutes, so the client's 60-second default timeout must be raised.
- **Figures are volatile.** Two reads of the same project twenty minutes apart returned
  traffic 10,796 then 5,401, keywords 3,783 then 3,846, DR 58 then 57, and
  `domain_authority` 54 then `null`. A 2x swing in a headline number is a credibility risk on
  a live call. `collect.py` therefore snapshots once per prospect, stamps `pulled_at`, and the
  report prints the date alongside the figure. Never re-pull mid-engagement to "check";
  `recrawl_available_at` gates real refreshes at roughly two days.

Also available cold, and useful: the `llmvis` service answered
`GET /api/v1/se/llm-visibility-overview/?domain=` with real per-platform data for a
non-project domain. This does **not** change the decision to measure AI visibility by browser
probe, for the reasons below, but it is a free cross-check on the probe results.

Optional client improvement, not required: the MCP server at
`mcp.searchatlas.com/mcp/site-explorer/` exposes 25 read-only Site Explorer tools that are
strictly more capable than the REST surface, including pre-bucketed position tables. The
existing client cannot reach it because it sends `Accept: application/json` and the server
requires `application/json, text/event-stream`, returning 406. Adding an Accept override
would unlock it.

**SearchAtlas LLM Visibility is not used.** It is known broken for this purpose: it reports
against brand-anchored auto-generated topics, and it has misidentified businesses outright
in production. AI visibility comes from the probe below instead.

Two derived metrics are computed locally, not fetched:

- **brand vs non-brand split**: classify every ranking keyword by whether it contains a
  brand token, then split clicks or estimated traffic accordingly. This was the single
  strongest finding in the reference report, so the classifier needs care with brands whose
  name is also their category (compare `prune_branded.py` in the searchatlas project, which
  solves exactly this and should be reused rather than reinvented).
- **position buckets**: bucket the ranking-keyword list.

## AI visibility probe

Method: ask each engine a fixed set of unbranded, buyer-intent questions for the prospect's
vertical, and record which brands get named.

Engines: ChatGPT, Perplexity, Gemini, Copilot, Google AI Mode. Driven in the operator's
logged-in Chrome, since consumer app answers are what the prospect's customers actually see
and they differ from API output.

Per platform, record: whether the prospect was named, how many of the question topics it
appeared in, which competitors were named, and a verbatim excerpt as proof for the report.

**Vertical cache.** Questions and per-engine competitor sets are stored in
`state/verticals/<slug>.json` with a 14-day TTL. A second prospect in a known vertical
reuses the cache and only probes for its own brand. First prospect in a vertical costs
roughly 20 minutes of browser work; subsequent ones a few minutes.

Questions must be unbranded and category-level. Branded questions only fire for people who
already know the brand and therefore measure nothing, which is the exact flaw in the
SearchAtlas auto topics.

## Report

Letter (612x792pt) to match the reference. HTML plus CSS rendered by headless Chrome
(`chrome --headless=new --disable-gpu --no-pdf-header-footer --no-margins --print-to-pdf`),
Chrome at `C:\Program Files\Google\Chrome\Application\chrome.exe`.

Brand: pine `#1E3A2E`, brass `#A9874E`, ink `#15140F`, ivory `#F4F0E8`. Fonts Cormorant
Garamond (display), Hanken Grotesk (body), Space Mono (data labels), base64-embedded.

Sections, in order:

| # | Section | Evidence it needs |
|---|---|---|
| 1 | Cover | business name, domain, date |
| 2 | Executive Summary | traffic, brand split, top 3-4 findings, lead competitor |
| 3 | The Finding That Changes Everything | authored from evidence; the one insight |
| 4 | Visibility Scorecard | scorecard, each with basis |
| 5 | The AI Search Opportunity | ai_visibility per platform |
| 6 | Traffic & Rankings | traffic, brand_split, money_keywords |
| 7 | Ranking Distribution | position_buckets |
| 8 | Paid vs Organic | paid |
| 9 | Link Profile | backlinks |
| 10 | Who's Winning Right Now | competitors |
| 11 | The 90-Day Plan | derived from the findings |
| 12 | What Happens Next | fixed CTA |

Section 3 is the judgment layer and cannot be templated. In the reference report it was the
discovery that a men's clinic was ranking for perimenopause keywords. No threshold rule
finds that. It is authored per prospect from the evidence file.

Section 8 is new, added on request: contrast what the prospect rents in paid traffic against
the organic and AI gap.

Language constraint: no tool names, no jargon without an immediate plain-English gloss, no
dashboard screenshots. The reference report is the style benchmark.

**Metric labels: "Domain Rating" and "Trust Flow" are retained. Operator decision, 2026-08-05.**
Raised twice during review that these are Ahrefs' and Majestic's proprietary metric names, so
printing them lets an SEO-literate prospect infer the tooling. The operator weighed that and
chose to keep them, because the familiar names carry more credibility with the buyers who
recognise them. Settled — do not re-raise. They are metric names, not vendor tool names, so
they are correctly absent from `FORBIDDEN_VENDORS` and must stay absent or every render will
fail.

## Dossier

Scope boundary, deliberate: **business-relevant public information only.** Company, role,
tenure, LinkedIn, company size, funding or ownership, other ventures, tech stack, recent
press, and how they likely found RiseRidge. Not personal-life material, not family, not
home address, not anything unrelated to the commercial conversation. This is both the
correct line and the more useful output for a closer.

The dossier is also a **hard input to pricing**, so it must return the company-scale fields
`pricing.py` needs as first-class, typed values rather than prose: employee count, number of
locations, years in business, ownership structure (independent, franchise, group, PE-backed),
other ventures owned by the contact, and the contact's decision authority. Each carries its
source URL. Any field it cannot establish is returned as unknown, never inferred, since an
invented headcount would silently move the price band.

Order dependency: `dossier.py` runs before `pricing.py`.

## Pricing

Matrix extracted from the six decks in `Price Deck Folder-20260804T140911Z-1-001.zip`.
Three tiers (Foundation, Growth, Dominate) across ECOM (3 USD bands plus a EUR variant) and
LOCAL (2 bands). Flat 10% discount for three months upfront, verified consistent across all
six decks.

| Track / band | Foundation | Growth | Dominate |
|---|---|---|---|
| ECOM low | $1,500 | $2,500 | $4,000 |
| ECOM mid | $2,500 | $5,000 | $8,000 |
| ECOM high | $4,000 | $6,500 | $9,000 |
| ECOM euro | EUR 1,800 | EUR 2,500 | EUR 4,000 |
| LOCAL low | $1,500 | $2,500 | $3,500 |
| LOCAL high | $2,500 | $4,000 | $6,500 |

**LOCAL is the default track, not ECOM.** Across 609 non-test bookings the business-type
answers are overwhelmingly local service businesses: home services 84, other local service
77, medical practice 33, wellness and therapy 21, real estate 17, legal 16, restaurant 11,
totalling 259, against just 19 e-commerce. The ECOM decks are the exception case.

Track selection from the `What type of business do you run?` answer:

| Questionnaire answer | Track |
|---|---|
| E-commerce or online-only business | ECOM |
| everything else (home services, medical, wellness, real estate, legal, restaurant, other local) | LOCAL |

### Band selection: company size, not stated budget

**Band is driven by how big the business actually is, discovered from the website review and
the contact/company investigation. The bigger the company, the higher the band.** The
questionnaire budget answer is a secondary signal only.

The reason for not leading with the stated budget: it is self-reported at the top of a funnel
by someone who has not yet seen what they are missing, it caps at "$3,000+" so it cannot
distinguish a $3k business from a $30k one, and 325 of 609 bookings do not answer it at all.
A 40-van HVAC operator and a solo handyman both tick "$1,000 - $2,000".

`pricing.py` scores the prospect into a size class from signals collected by `collect.py`
(site and SEO scale) and `dossier.py` (company and ownership scale):

| Signal group | Evidence used |
|---|---|
| Site scale | indexed page count, number of location or service-area pages, product/SKU count (ecom), platform tier (e.g. Shopify Plus vs basic), whether prices are published |
| Organic scale | monthly organic visits, ranking keyword count, traffic value, referring domains |
| Paid scale | estimated monthly paid spend, count of paid landing pages |
| Company scale | staff or team-page headcount, LinkedIn employee count, number of physical locations, fleet size, years in business, franchise or multi-brand structure, PE or group ownership |
| Market scale | competitor traffic in the same vertical, single-city vs regional vs national footprint |
| Contact seniority | owner/founder vs marketing manager vs agency intermediary |

Size classes and the band each maps to:

| Size class | Rough shape | LOCAL band | ECOM band |
|---|---|---|---|
| Micro | solo or 1-2 staff, one location, negligible organic | low | low |
| Small | 3-10 staff, 1-2 locations, some organic footprint | low | low |
| Mid | 11-50 staff, multi-location or regional, real organic and paid presence | high | mid |
| Large | 50+ staff, multi-region or national, franchise/group/PE-backed | high | high |

Currency: EUR variant of the ECOM band for European prospects, USD otherwise.

Tier within the band always opens on **Growth** (see Anchor tier below), with Foundation as
the step-down and Dominate as the step-up. Urgency and competitive gap, from the
`when are you looking to get started` and `what have you already tried` answers plus the size
of the organic gap the audit found, decide how hard to push toward Dominate. The stated budget
acts as a **sanity check, not a cap**: if the size-derived band anchors well above what they
stated, `pricing.py` flags the gap explicitly so the operator can decide whether to lead with
the audit findings and justify the number, or step down to Foundation.

Every quote carries the 3-month-upfront option at 10% off, verified consistent across all six
decks.

`pricing.py` returns a recommendation **with every signal and its reasoning shown**, never a
silent choice, because the operator makes the final call live. Where a size signal could not
be found it is reported as unknown rather than assumed, per the evidence rule.

### Anchor tier

**Growth is the anchor tier on both tracks.** Resolved 2026-08-04: the ECOM decks currently
tag Foundation as RECOMMENDED, which is inconsistent with LOCAL and is to be treated as
Growth for scripting purposes. Every script opens on Growth, presents Foundation as the
step-down and Dominate as the step-up.

Resulting anchor price by size class:

| Size class | LOCAL anchor (Growth) | ECOM anchor (Growth) |
|---|---|---|
| Micro / Small | $2,500 | $2,500 (EUR 2,500 in Europe) |
| Mid | $4,000 | $5,000 |
| Large | $4,000 | $6,500 |

The ECOM sales decks should be re-tagged to mark Growth as RECOMMENDED so the printed
material matches the script. Flagged as a follow-up outside this pipeline.

## Slack integration

Bot: "RiseRidge Sales", installed to the workspace, invited to `#sales-pipeline`. Bot scopes:
`chat:write`, `files:write`, `channels:history`, `groups:history`, `channels:read`,
`groups:read`, `users:read`, `reactions:read`, `reactions:write`.

`#sales-pipeline` is confirmed internal, so all three artefacts go into the lead's thread.

File upload uses the current three-step flow (`files.upload` was retired):

1. `files.getUploadURLExternal` with `filename` and `length` returns an upload URL and file id.
2. POST the PDF bytes to that URL.
3. `files.completeUploadExternal` with `files`, `channel_id`, `thread_ts` and
   `initial_comment` shares it as a threaded reply.

Both steps require `files:write`. Verified against Slack docs 2026-08-04.

Channel: `#sales-pipeline` = `C09PLHVBHRC` (private, bot is a member). Bot user
`riseridge_sales` = `U0BMUDY9TM3`, workspace "GSM team". Verified with `auth.test` 2026-08-04.

### Lead source data (verified against live channel 2026-08-04)

A Zapier app (`B09TSBSMFJL`) posts structured messages. Message types, by volume over full
history:

| Count | First line | Use |
|---|---|---|
| 469 | `*New Lead from the SEO Funnel*` | form fill, not booked. Ignore. |
| 361 | `*Appointment stuck in booked for more than 5 days*` | nag. Ignore. |
| 242 | `*Appointment booked from the SEO Funnel*` | **target** |
| 50 | `*Appointment booked from the VSL Funnel*` | **target** |
| 12 | `*New Lead from Facebook Form*` | not booked. Ignore. |
| 9 | `*New Lead from the Typeform Funnel*` | not booked. Ignore. |

Trigger is a message whose text starts with `*Appointment booked`. Its `ts` is the
`thread_ts` for all replies.

Parsed fields, all `*Label:*` prefixed in the message text:

- `Client's name`, `Email`, `Timezone`, `Manager` (the assigned closer), `Funnel`, `Created on`
- utm block: `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`
- Questionnaire block:
  - `What type of business do you run?` -> selects ECOM vs LOCAL track
  - `Your business website` -> **the audit domain**
  - `In 1-2 sentences, what's your biggest frustration with getting new clients or patients right now?` -> script discovery hook
  - `What have you already tried for marketing? (Select all that apply)` -> objection handling
  - `How much are you currently spending (or willing to invest) on marketing each month?` -> **selects price band**
  - `When it comes to business decisions like this, how does your decision-making process work?` -> single vs multi decision-maker
  - `If this is the right fit for your business, when are you looking to get started?` -> urgency

The message also carries a link-unfurl attachment for the prospect's site with its `title`,
`text` (meta description) and `service_name`. Free business context; use it to seed the
vertical guess before crawling.

Parsing notes, all observed in live data and all of which must be handled:

- Websites appear as Slack links (`<https://Www.exampleRealty.com>`) with inconsistent case
  and `www.`; normalise to a bare lowercase registrable domain.
- Some entries put an email in the website field, or free text before the link.
- A malformed message can collapse two questionnaire answers onto one line, so field
  extraction must be anchored per-label and tolerate a missing label rather than
  offset-parsing the block.
- Test leads must be excluded: name or email matching `/test|gsmgrowthagency/i`.
- The same person can book twice (Lou Lobel, 07-20 and 07-23). Dedupe on normalised domain,
  not on message.
- Older bookings predate the questionnaire: only 274 of 609 non-test bookings carry a
  website. A booked message with no resolvable domain is reported to the operator rather
  than audited, since the audit is impossible without a domain.

### Scope

**New bookings only. No backfill.** The bot processes `*Appointment booked` messages that
arrive after it goes live. Historic bookings are left alone; volume has fallen from 110/month
in February 2026 to 11 in July and 1 in early August, so the backlog is mostly dead.

One exception, for validation only: the most recent real booking
(Jordan Alvarez, examplerealty.com, 2026-08-02) is used as the end-to-end test case. It is
rendered locally with **posting disabled** so the operator can judge output quality before
the bot is armed. Nothing is posted to a prospect thread without explicit approval.

Note: booked messages carry no appointment date/time, only lead creation time. The pipeline
therefore cannot tell which calls are still upcoming and does not try to.

## Error handling

Four gates, three of them learned from the OLASBET report pipeline:

1. **Evidence gate.** `render.py` refuses to run if a required field is missing. Optional
   fields that are null drop their section.
2. **Token gate.** Renderer asserts zero unreplaced `{{tokens}}` in the final HTML.
3. **Embed gate.** After rendering, rasterize the PDF with pymupdf and assert the brand
   fonts and logo are actually embedded. Chrome silently drops external `<img src="*.svg">`
   and variable fonts, producing a PDF that looks correct on screen and blank in print.
   Logos must therefore be inline `<svg>` elements and fonts requested one weight per call.
4. **Idempotency gate.** Posting is guarded by `state/leads.json` plus a reaction on the
   source message. A re-run must never double-post into a prospect thread.

## Testing

Acceptance test: hand-build `evidence.json` for getpetermd.com from the reference PDF's own
figures, render it, and diff the result against the reference PDF for structure, section
order, and figure fidelity. Reproducing that report is the bar for the engine being correct.

Unit level: brand/non-brand classifier against known-tricky cases (brands whose name is
their category); position bucketing; pricing band selection; evidence gate rejecting an
incomplete file; token gate catching an unreplaced token.

## Build order

1. **Done.** Slack layer verified read-only: token works, channel resolved, lead message
   shape and field labels confirmed against live data.
2. `leads.py` — parse `*Appointment booked` messages into Lead records, with the
   normalisation and edge cases listed above. Unit-tested against real message text.
3. `render.py` plus `templates/audit.html`, validated by the PeterMD acceptance test. Built
   before the collector so the evidence schema is exercised by a real render early.
4. `collect.py` against the spike's verified endpoints, for examplerealty.com.
5. `aiprobe.py` plus the first vertical cache entry (real estate / local agent).
6. `dossier.py`, then `pricing.py` (which depends on it plus `collect.py`).
7. Validation run on Jordan Alvarez / examplerealty.com with **posting disabled**. Operator
   reviews all three artefacts.
8. `post.py` and the idempotency ledger. Arm the bot for new bookings only after step 7 is
   approved.

## Risks

- **Resolved.** SearchAtlas does serve prospect-domain data, but most credibility figures
  require warming the domain with a Site Explorer project POST. See Verified capability.
- **Figure volatility is the top remaining risk to the sales artefact.** A headline traffic
  number that halves between two reads twenty minutes apart will not survive a prospect
  checking it. Mitigated by snapshot-once plus a visible `pulled_at` date, but the report
  should lean on the metrics that proved stable (keyword counts, position distribution,
  anchors) and treat estimated traffic as directional.
- **No technical/on-page audit for prospects without spending crawl budget.** The spec's
  `technical` evidence block will stay null, so any technical section stays absent, unless the
  operator authorises a Site Audit project per prospect.
- **Consumer AI engines may rate-limit or gate automated interaction.** Mitigation is the
  vertical cache, which cuts probe volume sharply.
- **The judgment section does not scale.** Section 3 needs a real read of each prospect's
  business. This is a deliberate cost, not a defect.
