"""SearchAtlas HTTP client and brand classifier, from the vendored snapshots.

Both live in `vendor/`, which makes this repository self-contained. It previously
reached sideways into a sibling directory on the author's machine via `sys.path`
-- that works nowhere else, so every import downstream failed in CI. See
`vendor/README.md` for provenance and for why the brand classifier is an
extraction rather than a whole-file copy.
"""

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(BASE, "vendor")

if not os.path.isdir(VENDOR):
    raise ImportError(
        "vendor/ not found at %s. It carries the SearchAtlas HTTP client and the "
        "brand classifier this project depends on." % VENDOR)

# APPEND, never insert(0): this repo's own modules must win. `searchatlas` and
# `brandmatch` do not collide with anything here, but fronting the path would let
# a future vendored file shadow one of ours for the whole process.
if VENDOR not in sys.path:
    sys.path.append(VENDOR)


def _seed_env_from_dotenv():
    """Put keys from the repo-root .env into os.environ if absent.

    The vendored client's own `_load_key()` falls back to a `.env` sitting NEXT
    TO ITSELF, which after vendoring means `vendor/.env` -- a path that does not
    and should not exist. Rather than patch the snapshot (it stays verbatim for
    provenance), seed the environment from the repo-root `.env` first so the
    client's env-var path succeeds. A real environment variable always wins, so
    CI secrets are unaffected.
    """
    path = os.path.join(BASE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_seed_env_from_dotenv()

from searchatlas import SearchAtlas, SearchAtlasError          # noqa: E402
from brandmatch import is_branded, brand_tokens, norm          # noqa: E402

# Retained so callers that referenced the old sibling-directory constant keep
# working; it now points at the vendored snapshot.
SA_ROOT = VENDOR

__all__ = ["SearchAtlas", "SearchAtlasError", "is_branded", "brand_tokens",
           "norm", "SA_ROOT", "VENDOR"]
