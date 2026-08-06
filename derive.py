"""Metrics computed locally from fetched rows, never fetched directly.

The brand/non-brand split is the most consequential figure the audit prints:
the reference report's headline was that 95% of traffic came from people who
already knew the brand, meaning the site was not a customer-acquisition channel
at all. Classification therefore uses the imported precise classifier rather
than a naive substring check, because a brand named after its own category
("Golf Course Print") would otherwise swallow every generic query.

`prune_branded.is_branded` was built for a different job: deactivating live
LLM-visibility queries, where a false positive destroys a valuable query, so
it is deliberately conservative. It requires the brand token to sit inside one
whitespace-delimited word ("petermd") or inside a run of Capitalised words
("Peter MD"); a plain lowercase two-word mention like "peter md" satisfies
neither, so it is missed even though it is plainly a brand search. For a
brand-share figure the error direction is inverted from pruning: a false
negative here understates the report's headline number, which is worse than
an occasional over-match. `brand_hit`/`_fully_generic` below add a narrow,
guarded fallback — lowercase word-run concatenation — but only when the
brand's own token is not itself decomposable into generic category words
(so "custom golf course prints" still stays unmatched for
golfcourseprint.com), and only for tokens long enough that a short generic
word can't false-positive on its own (`MIN_BRAND_TOKEN_LEN`).

Separately, `sa_client.brand_tokens` (imported from the sibling project) also
emits individual *words* out of a multi-word business name as standalone
brand tokens — "ASAP Transport Solutions" yields a bare "transport" token,
"Home Shift Team" yields "shift". That is correct for the sibling's own job
(pruning LLM-visibility queries about the client's own product, where a
generic-sounding leftover word is still worth catching), but wrong for a
brand-share figure: it makes ordinary category queries ("transport a car
across country", "shift work sleep disorder") register as branded, inflating
the split, and it is worse in `money_keywords`, which excludes branded terms —
a window company's audit would silently drop "window replacement cost" from
the one table meant to show the prospect what they are invisible for.
`brand_token_set` below replaces `sa_client.brand_tokens` as this module's
token source: a brand is the whole string (registrable domain root, that
root plus TLD, and the business name with separators stripped), never one
word plucked out of the middle.
"""

import re

import sa_client
# sa_client has already appended the sibling SearchAtlas toolkit to sys.path;
# sa_client itself does not export the generic-word vocabulary, so it is
# imported directly here rather than duplicated.
from brandmatch import GENERIC, COMMON_WORDS  # via sa_client, which puts vendor/ on sys.path

BURIED_AFTER = 10   # position 11+ is off page one

# Generic category-word vocabulary used to decide whether a brand name is
# distinctive enough for lowercase word-run matching (see _fully_generic).
_GENERIC_VOCAB = frozenset(GENERIC) | frozenset(COMMON_WORDS)

# Guards only against a very short token matching inside an unrelated word,
# and its value (5) mirrors the sibling classifier's own floor. It is NOT
# what stops a bare category word like "shift" from leaking in as a brand
# token — brand_token_set prevents that structurally, by only ever building
# whole-brand concatenations, so a single word plucked out of a multi-word
# name can never enter the token set at any length. A 6-character floor was
# tried once and rejected: applied at token-construction time it silently
# dropped a whole brand's own token when the brand name itself was five
# characters or fewer, e.g. brand_token_set("zappo.com", "Zappo") lost
# "zappo" and "zappo reviews" went unmatched — a false negative, the more
# costly error for a brand-share figure.
MIN_BRAND_TOKEN_LEN = 5


def brand_token_set(domain, business_name):
    """Brand tokens as whole-brand concatenations only.

    sa_client.brand_tokens also emits individual words from a multi-word
    name, so "ASAP Transport Solutions" yields a bare "transport" token and
    every generic transport query reads as branded. That both inflates the
    brand share and, worse, drops real money keywords out of the
    money-keywords table, because that table excludes branded terms.

    A brand is the whole string: the registrable domain name, that name plus
    its TLD, and the business name with separators removed. Never one word
    plucked out of the middle.
    """
    toks = set()
    host = re.sub(r"^www\.", "", (domain or "").strip().lower())
    host = re.sub(r"[^a-z0-9.]", "", host)
    if host:
        parts = host.split(".")
        core = parts[0] if parts else ""
        if len(core) >= MIN_BRAND_TOKEN_LEN:
            toks.add(core)
        flat = host.replace(".", "")
        if len(flat) >= MIN_BRAND_TOKEN_LEN:
            toks.add(flat)
    name = re.sub(r"[^a-z0-9]", "", (business_name or "").lower())
    if len(name) >= MIN_BRAND_TOKEN_LEN:
        toks.add(name)
    return toks


def _fully_generic(token, vocab):
    """True if the token decomposes entirely into generic category words.

    'golfcourseprint' -> golf+course+print, all generic -> True, so lowercase
    word-run matching would over-match 'custom golf course prints'.
    'petermd' -> not decomposable -> False, so word-run matching is safe.
    """
    memo = {}

    def go(t):
        if t == "":
            return True
        if t in memo:
            return memo[t]
        for i in range(2, len(t) + 1):
            if t[:i] in vocab and go(t[i:]):
                memo[t] = True
                return True
        memo[t] = False
        return False

    return go(token)


def _allow_word_runs(tokens):
    """Decide once per brand (not per row) whether the lowercase word-run
    fallback is safe: only when the brand's shortest, most distinctive token
    does not fully decompose into generic category words."""
    core = min(tokens, key=len) if tokens else ""
    return bool(core) and not _fully_generic(core, _GENERIC_VOCAB)


def brand_hit(keyword, tokens, allow_word_runs):
    """Branded-token match, or None.

    Falls back to concatenating runs of consecutive words when the brand name
    is distinctive, because the strict classifier misses lowercase forms like
    'peter md' that are plainly branded.
    """
    hit = sa_client.is_branded(keyword or "", tokens)
    if hit or not allow_word_runs:
        return hit
    words = re.findall(r"[a-z0-9]+", (keyword or "").lower())
    for i in range(len(words)):
        joined = ""
        for j in range(i, len(words)):
            joined += words[j]
            for t in tokens:
                if t and len(t) >= MIN_BRAND_TOKEN_LEN and t in joined:
                    return t
    return None


def brand_split(rows, domain, business_name):
    """Split traffic between branded and non-branded queries.

    Returns None percentages when no row carries traffic — the split is
    unknowable then, and a fabricated 50/50 would be worse than an absent row.
    An empty token set (no usable domain or business name) is the same
    situation: nothing can be classified, so reporting 0%/100% would be a
    fabricated claim that none of the traffic is branded rather than an
    honest "we don't know."
    """
    tokens = brand_token_set(domain, business_name)
    if not tokens:
        return {"brand_pct": None, "nonbrand_pct": None, "brand_traffic": None,
                "nonbrand_traffic": None, "classified": len(rows)}
    allow_word_runs = _allow_word_runs(tokens)
    brand_t = nonbrand_t = 0.0
    seen_traffic = False

    for r in rows:
        t = r.get("traffic")
        if t is None:
            continue
        seen_traffic = True
        if brand_hit(r.get("keyword") or "", tokens, allow_word_runs):
            brand_t += float(t)
        else:
            nonbrand_t += float(t)

    total = brand_t + nonbrand_t
    if not seen_traffic or total <= 0:
        return {"brand_pct": None, "nonbrand_pct": None, "brand_traffic": None,
                "nonbrand_traffic": None, "classified": len(rows)}

    bp = int(round(brand_t / total * 100))
    return {
        "brand_pct": bp,
        "nonbrand_pct": 100 - bp,
        "brand_traffic": int(round(brand_t)),
        "nonbrand_traffic": int(round(nonbrand_t)),
        "classified": len(rows),
    }


def money_keywords(rows, tokens, limit=8, min_volume=100):
    """High-volume, non-branded terms the site does not yet rank on page one.

    These are the "every one of these searches is a customer asking Google for
    exactly what you sell, and you are invisible" table. Uses the same
    word-run-aware brand_hit as brand_split so a plainly branded term like
    "peter md trt" cannot appear here as if it were an untapped opportunity.

    Deliberately asymmetric with brand_split's empty-token-set handling: an
    empty token set here just means nothing gets excluded as branded, which
    is the safe default for this table (a branded term wrongly listed as an
    opportunity is a smaller harm than dropping a real one), so no None/empty
    special case is needed.
    """
    allow_word_runs = _allow_word_runs(tokens)
    out = []
    for r in rows:
        vol, pos = r.get("volume"), r.get("position")
        if vol is None or pos is None:
            continue
        if vol < min_volume or pos <= BURIED_AFTER:
            continue
        if brand_hit(r.get("keyword") or "", tokens, allow_word_runs):
            continue
        out.append(r)
    out.sort(key=lambda r: -float(r["volume"]))
    return out[:limit]


def bucket_rows(rows):
    """Position buckets counted from raw rows, for cold domains with no native
    buckets. Rows without a position are not counted anywhere.

    `rows` is always the cold, page-capped organic-keywords sample (up to
    100 rows) - never the full ranked-keyword corpus, which the cold
    endpoint does not expose. A band with zero observed rows in a partial
    sample means "none of the sampled rows fell here," not "the domain has
    none in this band" - the two are not distinguishable from a sample
    alone, and printing the latter as fact (e.g. "11-20: 0" beside a stated
    5,000+ ranking keywords) is a false statement about the domain. So: count
    normally, then convert any zero count to None, the same refusal
    `brand_split` already applies to an unclassifiable 0/100 split. A
    genuinely non-empty count is left untouched - that much is a real
    measured value, sample or not.
    """
    b = {"1-3": 0, "4-10": 0, "11-20": 0, "21-50": 0, "51-100": 0,
         "100_plus": 0}
    for r in rows:
        p = r.get("position")
        if p is None:
            continue
        p = float(p)
        if p <= 3:
            b["1-3"] += 1
        elif p <= 10:
            b["4-10"] += 1
        elif p <= 20:
            b["11-20"] += 1
        elif p <= 50:
            b["21-50"] += 1
        elif p <= 100:
            b["51-100"] += 1
        else:
            b["100_plus"] += 1
    return {k: (v if v else None) for k, v in b.items()}
