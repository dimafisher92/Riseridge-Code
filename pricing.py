"""Price band and tier recommendation.

Band is driven by how big the business actually is, not by the budget the
prospect self-reported at the top of the funnel. Every signal that moved the
decision is returned with its reasoning, because the operator makes the final
call live and needs to be able to defend the number out loud.

Two rules encode judgments that are easy to get wrong:

1. **Organic scale can raise a size class, never lower it.** Strong organic
   proves scale. Weak organic proves nothing — for a local service business it
   is the norm, and it is the very deficiency being sold. Treating it
   symmetrically drags a 45-staff operator into the Small band because three
   correlated organic metrics outvote the headcount.
2. **Company-scale facts are a floor, not a term in an average.** A confirmed
   headcount of 45 means the business is at least Mid. Averaging that against
   weak organic dilutes a hard fact with a soft one.
"""

import re

TRACKS = ("local", "ecom")
TIERS = ("foundation", "growth", "dominate")
ANCHOR_TIER = "growth"

# Extracted from the six price decks. Flat 10% discount for three months
# upfront, verified consistent across all six.
MATRIX = {
    ("ecom", "low"):   {"foundation": 1500, "growth": 2500, "dominate": 4000},
    ("ecom", "mid"):   {"foundation": 2500, "growth": 5000, "dominate": 8000},
    ("ecom", "high"):  {"foundation": 4000, "growth": 6500, "dominate": 9000},
    ("ecom", "euro"):  {"foundation": 1800, "growth": 2500, "dominate": 4000},
    ("local", "low"):  {"foundation": 1500, "growth": 2500, "dominate": 3500},
    ("local", "high"): {"foundation": 2500, "growth": 4000, "dominate": 6500},
}

CURRENCY = {"euro": "EUR"}
UPFRONT_MONTHS = 3
UPFRONT_DISCOUNT = 0.10

# Size classes, ordered. Index is the comparison key.
CLASSES = ("micro", "small", "mid", "large")

BAND_FOR_CLASS = {
    "local": {"micro": "low", "small": "low", "mid": "high", "large": "high"},
    "ecom": {"micro": "low", "small": "low", "mid": "mid", "large": "high"},
}


class PricingError(Exception):
    pass


def _rank(size_class):
    return CLASSES.index(size_class)


def _max_class(classes):
    """Highest of the given size classes, or None if there are none."""
    real = [c for c in classes if c]
    return max(real, key=_rank) if real else None


# --- signal definitions -----------------------------------------------------
#
# Each threshold table maps a metric to the size class it *supports*. Read
# strictly bottom-up: the first threshold the value clears wins. A value below
# every threshold yields None, meaning "this signal says nothing", which is not
# the same as "this business is Micro".

def _classify(value, thresholds):
    """Highest class whose threshold `value` clears. None if it clears none."""
    if value is None:
        return None
    for floor, size_class in thresholds:
        if value >= floor:
            return size_class
    return None


# Descending, so the first match is the strongest claim the number supports.
ORGANIC_THRESHOLDS = {
    "monthly_organic_visits": ((50000, "large"), (10000, "mid"), (1000, "small")),
    "ranking_keyword_count": ((20000, "large"), (5000, "mid"), (500, "small")),
    "traffic_value_usd": ((100000, "large"), (25000, "mid"), (2500, "small")),
    "referring_domains": ((1000, "large"), (200, "mid"), (30, "small")),
}

# Where each organic signal lives in the evidence file. Spelled out rather than
# derived from the signal name: referring_domains sits under backlinks, not
# traffic, and a name-derived path would silently read None forever.
ORGANIC_PATHS = {
    "monthly_organic_visits": "traffic.monthly_organic_visits",
    "ranking_keyword_count": "traffic.ranking_keyword_count",
    "traffic_value_usd": "traffic.traffic_value_usd",
    "referring_domains": "backlinks.referring_domains",
}

SITE_THRESHOLDS = {
    "page_count": ((2000, "large"), (500, "mid"), (50, "small")),
    "location_pages": ((40, "large"), (10, "mid"), (3, "small")),
    "product_count": ((5000, "large"), (500, "mid"), (50, "small")),
}

PAID_THRESHOLDS = {
    "estimated_monthly_spend_usd": ((50000, "large"), (10000, "mid"), (1000, "small")),
    "paid_landing_pages": ((100, "large"), (20, "mid"), (5, "small")),
}

# Company scale is the floor group: these are facts about the business itself,
# so they set a hard minimum rather than casting a vote.
COMPANY_THRESHOLDS = {
    "employee_count": ((50, "large"), (11, "mid"), (3, "small"), (1, "micro")),
    "location_count": ((10, "large"), (5, "mid"), (2, "small"), (1, "micro")),
}

# Ownership structure is a floor too: a franchise or PE-backed group is not a
# small business regardless of what one location's website looks like.
OWNERSHIP_FLOOR = {
    "franchise": "mid",
    "group": "mid",
    "pe-backed": "large",
    "private-equity": "large",
}


def _metric(evidence, dotted):
    """Read a metric from an Evidence object or a plain dict."""
    if evidence is None:
        return None
    if hasattr(evidence, "get") and not isinstance(evidence, dict):
        return evidence.get(dotted)
    node = evidence
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, dict):
        node = node.get("value")
    return node


def _dossier_value(dossier, key):
    """Typed value from a dossier field, or None when it is unknown.

    Dossier fields are `{"value": x, "source": url}` and an unestablished field
    is `{"value": None, ...}` or absent. An invented headcount would silently
    move the price band, so anything not positively established reads as None.
    """
    if not dossier:
        return None
    node = (dossier.get("company") or {}).get(key)
    if isinstance(node, dict):
        return node.get("value")
    return node


def _signal(group, name, value, size_class, kind, note=""):
    return {
        "group": group,
        "signal": name,
        "value": value,
        "supports": size_class,
        "kind": kind,          # "floor" | "raise"
        "note": note,
    }


def collect_signals(evidence=None, dossier=None):
    """Every size signal found, each with the class it supports.

    A signal whose value could not be established is still returned, with
    `supports` None, so the operator can see what was looked for and not found
    rather than assuming it was never checked.
    """
    out = []

    for name, table in COMPANY_THRESHOLDS.items():
        v = _dossier_value(dossier, name)
        out.append(_signal("company", name, v, _classify(v, table), "floor",
                           "confirmed company fact; sets a minimum"))

    ownership = _dossier_value(dossier, "ownership")
    own_class = OWNERSHIP_FLOOR.get((ownership or "").strip().lower())
    out.append(_signal("company", "ownership", ownership, own_class, "floor",
                       "structure implies scale independent of one site"))

    for name, table in ORGANIC_THRESHOLDS.items():
        v = _metric(evidence, ORGANIC_PATHS[name])
        out.append(_signal("organic", name, v, _classify(v, table), "raise",
                           "strong organic proves scale; weak organic proves "
                           "nothing and cannot lower the class"))

    for name, table in SITE_THRESHOLDS.items():
        v = _dossier_value(dossier, name)
        out.append(_signal("site", name, v, _classify(v, table), "raise",
                           "site breadth is a lower bound on operation size"))

    for name, table in PAID_THRESHOLDS.items():
        path = ("paid.estimated_monthly_spend_usd" if name.startswith("estimated")
                else "paid.landing_pages")
        v = _metric(evidence, path)
        if isinstance(v, list):
            v = len(v)
        out.append(_signal("paid", name, v, _classify(v, table), "raise",
                           "paid budget in market is a spend-capacity signal"))

    return out


def size_class_from(signals):
    """Resolve signals into one size class, plus how it was reached.

    Ordered priority, not a mean:
      1. Floors (confirmed company facts) set the minimum.
      2. Raise-only signals lift the class if they clear the floor.
      3. Nothing established at all yields "unknown".
    """
    floors = [s["supports"] for s in signals if s["kind"] == "floor"]
    raises = [s["supports"] for s in signals if s["kind"] == "raise"]
    floor = _max_class(floors)
    lift = _max_class(raises)

    if floor is None and lift is None:
        return "unknown", {
            "basis": "no size signal could be established",
            "floor": None, "raised_to": None,
        }

    if floor is None:
        return lift, {
            "basis": "no confirmed company facts; class inferred from "
                     "site, organic and paid scale alone",
            "floor": None, "raised_to": lift,
        }

    if lift and _rank(lift) > _rank(floor):
        return lift, {
            "basis": "company facts set a %s floor; site/organic/paid scale "
                     "raised it to %s" % (floor, lift),
            "floor": floor, "raised_to": lift,
        }

    return floor, {
        "basis": "company facts set a %s floor; nothing raised it "
                 "(weak organic cannot lower a class)" % floor,
        "floor": floor, "raised_to": None,
    }


# --- budget parsing ---------------------------------------------------------

_MONEY = re.compile(r"\$?\s*([\d,]+)\s*(k)?", re.I)


def budget_ceiling(answer):
    """Upper bound in dollars from the funnel's budget answer, or None.

    The answer caps at "$3,000+", so a "+" bound is returned as the number with
    `open_ended` true — it cannot distinguish a $3k business from a $30k one and
    must never be read as a cap.
    """
    if not answer:
        return None
    text = answer.strip().lower()
    nums = []
    for m in _MONEY.finditer(text):
        raw = m.group(1).replace(",", "")
        if not raw.isdigit():
            continue
        n = int(raw)
        if m.group(2):
            n *= 1000
        nums.append(n)
    if not nums:
        return None
    open_ended = "+" in text or "more" in text or "above" in text
    return {"amount": max(nums), "open_ended": open_ended,
            "under": bool(re.search(r"less than|under|below", text))}


# --- the recommendation -----------------------------------------------------

def band_for(track, size_class, currency="USD"):
    """Band key into MATRIX for a track and size class."""
    if track not in TRACKS:
        raise PricingError("unknown track: %r" % (track,))
    if track == "ecom" and currency.upper() == "EUR":
        # The euro deck ships a single band, so a European ECOM prospect gets
        # that column and the size class cannot move the price. Surfaced in the
        # recommendation rather than silently ignored.
        return "euro"
    if size_class == "unknown":
        return "low"
    return BAND_FOR_CLASS[track][size_class]


def upfront_quote(monthly):
    """Three months upfront at 10% off, rounded to the dollar."""
    return round(monthly * UPFRONT_MONTHS * (1 - UPFRONT_DISCOUNT))


def recommend(track, *, evidence=None, dossier=None, budget_answer=None,
              urgency="", currency="USD"):
    """A defensible price recommendation with every signal shown."""
    if track not in TRACKS:
        raise PricingError("unknown track: %r" % (track,))

    signals = collect_signals(evidence, dossier)
    size_class, how = size_class_from(signals)
    band = band_for(track, size_class, currency)
    prices = MATRIX[(track, band)]
    symbol = CURRENCY.get(band, "USD")

    anchor = prices[ANCHOR_TIER]
    ceiling = budget_ceiling(budget_answer)

    flags = []
    if size_class == "unknown":
        flags.append(
            "No size signal could be established. The band defaults to the "
            "bottom of the matrix; confirm company size on the call before "
            "quoting.")
    if band == "euro":
        flags.append(
            "European ECOM deck has a single band, so the size class (%s) is "
            "reported but does not move the price." % size_class)
    if ceiling and not ceiling["open_ended"] and anchor > ceiling["amount"]:
        flags.append(
            "Anchor (%s %s) is above the stated budget (%s). The stated budget "
            "is a sanity check, not a cap: lead with the audit findings and "
            "justify the number, or step down to Foundation at %s %s."
            % (symbol, anchor, budget_answer, symbol, prices["foundation"]))

    unknown = [s["signal"] for s in signals if s["supports"] is None]

    return {
        "track": track,
        "currency": symbol,
        "band": band,
        "size_class": size_class,
        "size_basis": how["basis"],
        "size_floor": how["floor"],
        "size_raised_to": how["raised_to"],
        "anchor_tier": ANCHOR_TIER,
        "anchor_price": anchor,
        "step_down": {"tier": "foundation", "price": prices["foundation"]},
        "step_up": {"tier": "dominate", "price": prices["dominate"]},
        "prices": dict(prices),
        "upfront": {tier: upfront_quote(p) for tier, p in prices.items()},
        "upfront_terms": "%d months upfront, %d%% off"
                         % (UPFRONT_MONTHS, int(UPFRONT_DISCOUNT * 100)),
        "push_to_dominate": _push_level(urgency, size_class),
        "stated_budget": budget_answer or "",
        "budget_ceiling": ceiling,
        "signals": signals,
        "unknown_signals": unknown,
        "flags": flags,
    }


# Checked before the urgency tokens, because "just researching for now" reads
# as urgent to any substring match on "now" while meaning the exact opposite.
BROWSING = ("just researching", "just looking", "not sure", "exploring",
            "no rush", "no timeline", "someday", "next year")
URGENT = ("asap", "immediately", "right away", "this week", "right now",
          "as soon as")
SOON = ("this month", "30 days", "next month", "1-3 months", "few weeks")


def _push_level(urgency, size_class):
    """How hard to push toward the step-up tier."""
    u = (urgency or "").strip().lower()
    if not u:
        level = "unknown"
    elif any(t in u for t in BROWSING):
        level = "light"
    elif any(t in u for t in URGENT):
        level = "hard"
    elif any(t in u for t in SOON):
        level = "moderate"
    else:
        level = "light"
    if level in ("hard", "moderate") and size_class in ("mid", "large"):
        return "hard"
    return level


def format_recommendation(rec):
    """Operator-readable summary. Every number traceable to a signal."""
    cur = rec["currency"]
    out = ["PRICE RECOMMENDATION",
           "  track        %s" % rec["track"].upper(),
           "  size class   %s" % rec["size_class"],
           "  basis        %s" % rec["size_basis"],
           "  band         %s" % rec["band"],
           "",
           "  ANCHOR ON    %s  %s %s/mo"
           % (rec["anchor_tier"].upper(), cur, "{:,}".format(rec["anchor_price"])),
           "  step down    Foundation  %s %s/mo"
           % (cur, "{:,}".format(rec["step_down"]["price"])),
           "  step up      Dominate    %s %s/mo"
           % (cur, "{:,}".format(rec["step_up"]["price"])),
           "  upfront      %s" % rec["upfront_terms"],
           ]
    for tier in TIERS:
        out.append("    %-11s %s %s for %d months"
                   % (tier, cur, "{:,}".format(rec["upfront"][tier]), UPFRONT_MONTHS))
    out += ["", "  push to Dominate: %s" % rec["push_to_dominate"],
            "  stated budget:    %s" % (rec["stated_budget"] or "(not answered)"),
            "", "  SIGNALS"]
    for s in rec["signals"]:
        got = "unknown" if s["value"] is None else s["value"]
        supports = s["supports"] or "-"
        out.append("    %-8s %-28s %-14s -> %-7s (%s)"
                   % (s["group"], s["signal"], got, supports, s["kind"]))
    if rec["flags"]:
        out.append("")
        out.append("  FLAGS")
        for f in rec["flags"]:
            out.append("    ! " + f)
    return "\n".join(out)
