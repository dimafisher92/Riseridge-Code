# RiseRidge Sales

Turns a booked lead in `#sales-pipeline` into an AI search visibility audit PDF,
a prospect dossier, and a sales script.

Design: `docs/superpowers/specs/2026-08-04-riseridge-sales-audit-design.md`

## Phase 1 (built)

    python -m pytest              # full suite
    python leads.py --pages 2     # parse recent booked leads from Slack
    python fonts.py               # refresh the cached brand-font CSS

- `slack.py` read-only Slack Web API client
- `leads.py` booked-appointment message -> Lead
- `evidence.py` the evidence contract; null values omit their section
- `fonts.py` base64 brand fonts, cached to `state/fonts.css`
- `render.py` templates + evidence -> HTML -> PDF, with three gates
- `templates/` `base.html`, `brand.css`, `sections/`

## Gates

1. Evidence gate: `Evidence.validate()` refuses an incomplete file.
2. Section gate: a section whose evidence is null is stripped, never estimated.
3. Token gate: HTML containing `{{tokens}}` never reaches Chrome.
4. Embed gate: `verify_pdf` fails if brand fonts did not embed or if vendor
   tooling is named in a client-facing document.

## Phase 2a (built) — SearchAtlas data collection

- `sa_client.py` bridges to the sibling SearchAtlas toolkit (`searchatlas.SearchAtlas`
  HTTP client, `prune_branded.is_branded` classifier).
- `saprobe.py` captures live API responses into the committed fixtures under
  `fixtures/api/`; read-only, honours the async not-ready retry contract.
- `sacold.py` extracts metrics from cold (any-domain, read-only) responses —
  keyword rows, keyword totals, raw backlink totals.
- `sawarm.py` owns the Site Explorer project lifecycle (`find_project`,
  `ensure_project`) and extracts warm-only metrics (traffic, authority,
  trust, native position buckets, competitors).
- `derive.py` computes metrics locally from fetched rows — brand/non-brand
  split, money keywords, row-derived position buckets — never fetched
  directly.
- `collect.py` fetches cold and warm data for one prospect domain and
  assembles a provenance-stamped `evidence.json`.

### The one write

`collect.py` is dry-run by default: every run searches for an existing Site
Explorer project and reads from it, but never creates one unless invoked
with `--apply`. `--apply` creates a Site Explorer project — a POST against
the operator's paid SearchAtlas quota — for domains that don't have one
yet. It is one project per unique normalised domain: an existing project
(found by search, or read back from a prospect's own prior evidence file)
is always reused, never re-created.

    python collect.py trtnation.com --name "TRT Nation"              # dry run
    python collect.py getpetermd.com --name PeterMD --apply           # may create a paid project

## Phase 2b/2c (built) — automation

A booked lead in `#sales-pipeline` becomes three artefacts without an operator
present.

    python run_pipeline.py               # dry run: no writes at all
    python run_pipeline.py --post        # deliver into the lead's thread
    python run_pipeline.py --post --apply  # and create the Site Explorer project

- `dossier.py` company scale from the prospect's own public site
- `websearch.py` keyless web search (no API key, no account)
- `aiprobe.py` AI visibility: answer-source method by default, provider
  APIs when keys are set; vertical cache on both
- `pricing.py` size class → band → tier, with every signal shown
- `narrative.py` evidence → the report's full token contract
- `salesscript.py` the closer's brief, plain text for Slack
- `post.py` the only module that writes to Slack
- `run_pipeline.py` the orchestrator

### Three things the automated path does differently from the spec

The spec assumed an operator at a keyboard. Three of its assumptions do not
survive an unattended runner, and each is handled explicitly rather than
quietly approximated.

**AI visibility.** The spec drove the operator's logged-in Chrome, because
consumer app answers are what a prospect's customers actually see. A hosted
runner has no logged-in browser, so there are two methods, and the default
needs no API key at all.

`probe_sources()` measures the **answer-source pool**: search-grounded
assistants do not answer from memory, they retrieve web results for the
question and synthesise from those, so the pages ranking for a buyer's
question are the raw material of the answer. Whether a business appears in
that pool is measurable with open web search and no account. This supports
"you are absent from the sources these answers are built from"; it does not
support "ChatGPT did not name you", and nothing in the report says that.

`probe()` asks the provider APIs directly and is used only when keys are
configured — an upgrade, not the baseline, since it costs money per question
and is still not the consumer app.

Under both, a failed engine or a blocked search is **omitted, never scored
zero**: "we could not measure" and "you do not appear" look identical in a
table and mean opposite things. If nothing can be measured the section drops.

The report states which method produced its figures, and the cover names no
engine it did not individually test.

**The idempotency ledger.** The spec put it in `state/leads.json`. Runners are
ephemeral so it would not survive, and committing it back would publish
prospect names, emails and domains to this public repository. The guard is now
server-side: `conversations.replies` answers "has this bot already replied in
this thread", which is authoritative even on a fresh runner. The reaction is
kept as the visible marker for humans scanning the channel.

Not a difference, but worth stating because an earlier version of this code got
it wrong: **`#sales-pipeline` is private and internal.** It is fed by a Zapier
app and the prospect is not in it, so the lead's thread is an internal surface
and is exactly where the spec says all three artefacts belong. There is no
separate review channel and no prospect-visible destination anywhere in this
pipeline.

**Section 3, the judgment section.** The spec is right that it cannot be
templated. What a rule can do honestly is choose which real finding leads.
Each candidate carries its own qualifying threshold, and among those that
clear it a documented editorial order decides — not a score. Scoring them
0–100 required an exchange rate between "85% of traffic is branded" and "1,530
keywords just off page one"; there isn't one. A hand- or model-authored
finding drops into the same slot later.

### The two switches

Both writes are off by default and are independent.

| switch | guards | needs |
|---|---|---|
| `--apply` | creating a Site Explorer project (paid quota) | the flag |
| `--post` | delivering artefacts into the lead's thread | the flag |

`post.py` also supports `RR_POSTING_ARMED`, a second switch it requires before
writing to a channel the prospect can see. Nothing in this pipeline targets
one, so it is unused here; the guard stays in place for any future surface
that is genuinely prospect-facing.

**Scheduled runs pass `--post` and `--apply`; a manual dispatch has to opt in
to each.** A schedule that built the artefacts and discarded them with the
runner would be pointless, and a hand-run stays a dry run by default. Traffic,
traffic value, authority, trust, position buckets and competitors are all
warm-only metrics, so without a project a first-time prospect's report drops
most of its credibility figures. `collect.py` bounds the spend: one project
per unique normalised domain, and an existing project — found by search or
read back from a prior evidence file — is always reused.

### This repository is public

That constrains where prospect data may go, and the pipeline is built around
it:

- The run log is **redacted by default**. Names, emails and domains are
  replaced by a stable `<prospect:xxxxxxxx>` tag. `--reveal` is for a local
  machine only.
- The pipeline workflow uploads **no build artifact**. Artifacts on a public
  repository are downloadable by anyone with the run URL, and
  `state/prospects/` holds real prospect data.
- Artefacts leave the runner through Slack or not at all, into the lead's
  thread in the private `#sales-pipeline` channel. Slack is the only private
  channel available to a public-repo runner.

`state/prospects/` and `state/verticals/` stay git-ignored.

### Scheduling

`.github/workflows/pipeline.yml` polls hourly on `windows-latest`. Polling
rather than a webhook: a webhook needs a public endpoint to receive Slack's
callback and there isn't one, while the bot already has `channels:history`.
Windows for the same reason as the test workflow — the renderer shells out to
Chrome. `RR_CHROME` and `RR_CHROME_FLAGS` override the Chrome path if the
pipeline is ever moved to a Linux runner.

### Reviewing it

The hourly schedule delivers into each lead's thread on its own. To see the
output before trusting it, dispatch by hand with `post: true` and read the
three artefacts in the thread under that booking. Re-running is safe: the
already-replied check means a lead is never posted twice.

## Constraints

- Never name the tooling in client-facing output.
- `.env` is git-ignored and holds the Slack bot token. Never commit it.
- Phase 1 performs no Slack writes.


## Running it elsewhere (GitHub, CI, a server)

The repository is self-contained. It previously imported the SearchAtlas HTTP
client and the brand classifier from a sibling directory on one machine via
`sys.path`; both now live in `vendor/` (see `vendor/README.md` for provenance).
Verified: a clean checkout with no sibling directory present runs 326 tests and
skips only the ones guarded on git-ignored local data.

### Secrets

Nothing secret is committed. `.env` is git-ignored; copy `.env.example` and fill
it in. On a hosted runner set these as repository secrets and write them into
`.env` at run time — never paste them into a workflow file.

| variable | used by |
|---|---|
| `SLACK_BOT_TOKEN` | reading `#sales-pipeline` |
| `SALES_PIPELINE_CHANNEL` | which channel to read |
| `SLACK_BOT_USER_ID` | skipping the bot's own messages |
| `SEARCHATLAS_API_KEY` | all prospect data collection |

`sa_client` seeds `os.environ` from the repo-root `.env` on import, so a real
environment variable always wins and CI secrets work without a `.env` file.

### CI

`.github/workflows/tests.yml` runs on `windows-latest`, not Linux, deliberately:
the PDF renderer shells out to Chrome, and Chrome is preinstalled on the Windows
image. On Linux every PDF test would skip — quietly removing coverage from the
part of this pipeline most likely to break without anyone noticing.

The workflow also asserts three things beyond the test suite: that the repo
imports from a clean checkout (catching any return of the sibling-directory
dependency), that `state/fonts.css` is committed (without it, renders silently
download 18 font files and Chrome substitutes Arial), and that no Slack token
appears in tracked files.

### The one write

`collect.py` is dry-run by default. `--apply` creates a SearchAtlas Site Explorer
project, which consumes paid quota. One project per unique normalised domain, and
an existing project is always reused. Nothing else in the pipeline writes
anywhere. Be deliberate about granting `--apply` to any unattended automation.

### Test data

Fixtures under `fixtures/` use synthetic contacts at `example.com`-family
domains. Real prospect names, emails and phone numbers are never committed —
`state/prospects/` is git-ignored for the same reason.
