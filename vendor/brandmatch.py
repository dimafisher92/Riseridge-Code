r"""Brand-name matching, extracted verbatim from the sibling client-delivery toolkit.

Copied on 2026-08-06 from `D:\Claude Code\searchatlas`:
  * GENERIC, norm, brand_tokens  <- validate_topic_maps.py
  * COMMON_WORDS, is_branded     <- prune_branded.py

WHY EXTRACTED RATHER THAN VENDORED WHOLESALE: `prune_branded.py` imports
`llmvis`, `registry`, `validate_topic_maps` and `searchatlas` at module level, so
copying the file alone raised ModuleNotFoundError -- it only ever worked because
the entire sibling directory sat on sys.path. Dragging in that chain to reach one
classifier would have pulled the whole toolkit into this repo. These five
definitions are the complete surface this project uses and they need nothing but
`re`.

The bodies are UNMODIFIED. `is_branded` is deliberately subtle -- the naive check
(strip spaces, look for the brand token) over-matches when a business is named
after its own category, so "custom golf course prints" is correctly NOT branded
while "GolfCoursePrint.com" is. Do not "simplify" it.
"""

import re

GENERIC = {
    "the", "app", "shop", "store", "group", "co", "com", "net", "online", "my",
    "get", "try", "best", "pro", "pros", "legal", "law", "firm", "travel", "print",
    "catering", "cuisine", "auto", "glass", "dirt", "bag", "pack", "tax", "golf",
    "course", "haute", "solutions", "services", "company",
    # category words that double as brand words
    "chew", "chews", "tallow", "abogado", "abogados", "dropper", "bottle",
    "bottles", "ghee", "stick", "sticks", "balm", "water", "health", "flores",
    "packbag", "print", "prints", "safest",
}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def brand_tokens(domain, client_name):
    """Distinctive strings that must not appear inside any query."""
    root = domain.split(".")[0]
    tokens = {norm(domain), norm(root)}
    # split camel/word boundaries in the domain root and keep non-generic parts
    for part in re.findall(r"[a-z]+", root.lower()):
        if part not in GENERIC and len(part) >= 5:
            tokens.add(norm(part))
    if client_name:
        cn = re.sub(r"\(.*?\)", " ", client_name)
        for part in re.findall(r"[A-Za-z]{5,}", cn):
            if part.lower() not in GENERIC:
                tokens.add(norm(part))
    return {t for t in tokens if len(t) >= 5}


COMMON_WORDS = GENERIC | {
    "sharp", "glow", "galaxy", "rush", "container", "grazing", "bello", "bondi",
}


def is_branded(raw, tokens):
    """
    Precise brand detection for LIVE queries, where a false positive destroys a
    legitimate query.

    The naive check (strip all spaces, look for the brand token) over-matches when
    the brand is built from generic category words: "custom golf course prints"
    collapses to a string containing "golfcourseprint" even though the query is
    unbranded and valuable. So require one of:

      A. the brand token appears CONTIGUOUSLY (no spaces) in the text, e.g.
         "GolfCoursePrint.com", "trybello reviews"
      B. a run of consecutive Capitalized words concatenates to something
         containing the token, e.g. "Haute Cuisine Catering", "The Sharp Firm"

    A lowercase, space-separated generic phrase satisfies neither.
    """
    # Check A: the token sits inside a single whitespace-delimited word.
    # "GolfCoursePrint.com" -> word "golfcourseprintcom" contains the token.
    # "golf course prints"  -> words "golf","course","prints" do not.
    # Tokens that are ordinary English words are skipped here, because a lone
    # lowercase "sharp" in "sharp pain after a car accident" is not the brand.
    # Those must satisfy the stricter capitalised-run test below.
    for word in re.split(r"\s+", raw.lower()):
        w = re.sub(r"[^a-z0-9]", "", word)
        for tok in tokens:
            if tok and tok in w and tok not in COMMON_WORDS:
                return tok

    # Check B: a run of two or more consecutive Capitalised words spells the brand.
    # "Haute Cuisine Catering vs competitors" -> "hautecuisinecatering".
    # Leading element may be a number, so "713 Abogado" is recognised.
    for run in re.findall(r"(?:[A-Z0-9][\w.\-]*)(?:\s+[A-Z][\w.\-]*)+", raw):
        joined = norm(run)
        for tok in tokens:
            if tok and tok in joined:
                return tok
    return None
