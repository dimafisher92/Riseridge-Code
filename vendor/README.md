# Vendored dependencies

Snapshots of two modules from the sibling project `D:\Claude Code\searchatlas`
(the GSM/RiseRidge client-delivery toolkit), copied in on 2026-08-06 so this
repository is self-contained and can run in CI.

| file | why it is here |
|---|---|
| `searchatlas.py` | The SearchAtlas HTTP client. Handles the Cloudflare-1010 User-Agent ban (the default `Python-urllib` UA is blocked) and the trailing-slash 301s. |
| `prune_branded.py` | `is_branded`, a precise brand-name classifier. The naive approach over-matches when a business is named after its own category: "custom golf course prints" collapses to a string containing "golfcourseprint" but is an unbranded, valuable query. |

**These are snapshots, not a live link.** Previously `sa_client.py` reached
sideways into the sibling directory via `sys.path`, which works on the author's
machine and fails everywhere else — the path does not exist in CI, so every
import downstream failed. If the originals change materially, re-copy them and
note it here.

`prune_branded.py` imports `llmvis`, `registry` and `validate_topic_maps` at
module level in its original home. Those are NOT vendored; if the import ever
starts failing, that is why.
