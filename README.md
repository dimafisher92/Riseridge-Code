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

## Not yet built (Phase 2b/2c)

`aiprobe.py`, `dossier.py`, `pricing.py`, `post.py`, and the remaining eight
report sections.

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
