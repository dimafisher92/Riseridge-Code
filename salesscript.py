"""The internal sales script: what the closer reads before the call.

Plain text, not HTML. This goes into a Slack thread, and Slack does not decode
HTML entities -- an &mdash; here prints literally as "&mdash;" in front of the
closer.

Everything here is either quoted from the lead's own funnel answers, taken from
the evidence file, or derived from the pricing recommendation. Where a fact is
unknown it says unknown, for the same reason the dossier does: a closer who
repeats an invented number on a call loses the room.
"""

import pricing

SEP = "-" * 62


def _readable(value):
    """A dossier value as the closer should read it.

    Booleans print as yes/no: a line reading "published prices   False" looks
    like a bug rather than a fact about the business.
    """
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return value


def _lead_field(lead, key, default=""):
    if lead is None:
        return default
    if isinstance(lead, dict):
        return lead.get(key) or default
    return getattr(lead, key, default) or default


# --- objection handling -----------------------------------------------------
#
# Keyed off the funnel's "what have you already tried" answer, which is a
# multi-select, so several of these can fire at once.

OBJECTIONS = (
    ("seo", "SEO agency",
     "They have been burned or underwhelmed before. Do not sell SEO. Ask what "
     "the agency actually delivered and what reporting looked like, then show "
     "the audit: this is a specific, measurable gap with a named cause, not a "
     "retainer for 'more content'."),
    ("google ads", "Google Ads",
     "They already believe search intent converts -- that argument is won. The "
     "pitch is ownership versus rental: the same searches they are bidding on "
     "can be earned, and the asset keeps returning after the spend stops."),
    ("facebook", "Facebook/Meta ads",
     "Interruption marketing against demand capture. Their buyers are already "
     "searching; the audit shows who is catching those searches instead."),
    ("social", "Organic social",
     "Effort with no compounding asset. Reframe: social builds awareness, "
     "search converts it. The audit shows the conversion half is missing."),
    ("nothing", "Nothing yet",
     "No baggage and no baseline. Lead with the competitor section -- what "
     "their rivals are already collecting -- rather than with what went wrong."),
    ("referral", "Referrals / word of mouth",
     "Their growth is capped by their network. The audit quantifies the demand "
     "sitting outside it."),
    ("myself", "DIY / in-house",
     "Respect the effort, then show the specific things that need scale: link "
     "authority, page structure and AI visibility do not respond to part-time "
     "attention."),
)


def objections_for(tried):
    out = []
    low = (tried or "").lower()
    for token, label, handling in OBJECTIONS:
        if token in low:
            out.append((label, handling))
    return out


# --- discovery --------------------------------------------------------------

BASE_DISCOVERY = (
    "Walk me through how a new customer finds you today. What is the actual "
    "path?",
    "If you had twice the enquiries next month, could you service them? What "
    "breaks first?",
    "What is a new customer worth to you over the first year?",
)

FINDING_DISCOVERY = {
    "brand_dominance": (
        "When someone who has never heard of you needs what you do, where do "
        "they end up?",
        "How much of your enquiry flow do you think comes from people who "
        "already knew your name?"),
    "ai_absence": (
        "Have you looked at what ChatGPT or Gemini say when someone asks for a "
        "recommendation in your category?",
        "If a buyer asked an assistant who to use and it named three "
        "competitors and not you, what would that be worth to fix?"),
    "near_misses": (
        "Do you know how many of your pages are ranking on page two right "
        "now?",
        "What would page-one placement on your main service terms change for "
        "you?"),
    "paid_dependence": (
        "What happens to your enquiry flow the month you pause ad spend?",
        "What are you paying per lead right now?"),
    "link_relevance": (
        "Has anyone built links for you before? Do you know where from?",
        "Who do you consider the most credible names in your industry?"),
}


def discovery_for(finding_keys, frustration=""):
    """Discovery questions, opened on what the prospect said hurts."""
    out = []
    if frustration:
        out.append('Open on their own words: "You said your biggest '
                   'frustration is %s." Ask them to expand before you present '
                   "anything." % frustration.strip().rstrip("."))
    for key in finding_keys:
        out.extend(FINDING_DISCOVERY.get(key, ()))
    out.extend(BASE_DISCOVERY)
    return out


# --- impact -----------------------------------------------------------------

def impact_lines(ev, finding_keys, anchor=None):
    """Expected business impact, stated only where a real figure supports it."""
    g = ev.get if ev is not None and hasattr(ev, "get") else (lambda p: None)
    out = []

    nonbrand = g("brand_split.nonbrand_pct")
    if "brand_dominance" in finding_keys and nonbrand is not None:
        out.append("Non-brand search is %g%% of traffic today. Every point of "
                   "that share is net-new demand they are not currently "
                   "reaching." % nonbrand)

    near = (g("position_buckets.11-20") or 0) + (g("position_buckets.21-50") or 0)
    if near:
        out.append("%s keywords rank 11-50. These already have relevance and "
                   "indexing; moving a fraction to page one is the fastest "
                   "measurable win and the easiest to report on."
                   % "{:,}".format(near))

    visits = g("traffic.monthly_organic_visits")
    value = g("traffic.traffic_value_usd")
    if value:
        # Framing matters more than the figure here. "Compare the retainer
        # against this" only works when the number is large enough to make the
        # retainer look cheap. A live run produced "$24/month" against a
        # $5,000 anchor, which reads as an argument against buying -- so a
        # near-zero value is presented as the unbuilt channel it actually is.
        if anchor and value < anchor:
            out.append(
                "Organic is worth about $%s/month at paid rates today -- "
                "effectively nothing next to what they spend acquiring "
                "customers elsewhere. Do NOT frame the retainer against this "
                "figure; the point is that the channel is unbuilt, not that "
                "it is cheap." % "{:,}".format(int(value)))
        else:
            out.append("Current organic traffic is worth about $%s/month at "
                       "paid rates. That is the number to compare the retainer "
                       "against." % "{:,}".format(int(value)))
    elif visits:
        out.append("Current organic traffic is roughly %s visits/month."
                   % "{:,}".format(int(visits)))

    if "ai_absence" in finding_keys:
        out.append("Zero AI assistant visibility today. This is the part no "
                   "competitor in their market has locked up yet, which is the "
                   "urgency argument.")

    if not out:
        out.append("No headline figure was measurable for this prospect. Lead "
                   "on the discovery answers and the competitor section rather "
                   "than on numbers.")
    return out


# Shared by the plain-text and .docx renderers so the two cannot drift.
PRESENT_STEPS = (
    "Play back their frustration in their own words.",
    "Show the one finding above, with the number. Let it land.",
    "Show who is winning those searches instead of them.",
    "Present the 90-day plan as sequence, not scope.",
    "Anchor on the recommended tier. Do not open with the cheapest.",
)


# --- assembly ---------------------------------------------------------------

def build(lead, *, evidence=None, dossier=None, recommendation=None,
          findings=()):
    """The full script as plain text."""
    name = _lead_field(lead, "name", "(unknown)")
    company = (evidence.data.get("business_name")
               if evidence is not None and hasattr(evidence, "data") else "") \
        or _lead_field(lead, "domain", "")
    domain = _lead_field(lead, "domain", "(no domain)")
    finding_keys = [f["key"] for f in findings]

    rec = recommendation
    lines = []
    add = lines.append

    add("SALES SCRIPT  |  %s  |  %s" % (name, company or domain))
    add(SEP)
    add("WHO YOU ARE TALKING TO")
    add("  contact      %s" % name)
    add("  company      %s (%s)" % (company or "unknown", domain))
    manager = _lead_field(lead, "manager")
    if manager:
        add("  closer       %s" % manager)
    when = _lead_field(lead, "appointment_at")
    add("  call time    %s" % (when or "not supplied by this funnel"))
    phone = _lead_field(lead, "phone")
    if phone:
        add("  phone        %s" % phone)
    add("  business     %s" % (_lead_field(lead, "business_type") or "unknown"))
    add("  track        %s" % (_lead_field(lead, "track") or "local").upper())

    if dossier:
        c = dossier.get("company") or {}
        add("")
        add("COMPANY BACKGROUND")
        for key in ("employee_count", "location_count", "years_in_business",
                    "ownership", "platform", "published_prices"):
            f = c.get(key) or {}
            add("  %-18s %s" % (key.replace("_", " "), _readable(f.get("value"))))
        unknown = dossier.get("unknown_fields") or []
        if unknown:
            add("  not established: %s" % ", ".join(unknown))
        add("  research links are in the dossier attached to this thread")

    add("")
    add("WHAT THEY TOLD US")
    for label, key in (("frustration", "frustration"), ("already tried", "tried"),
                       ("budget answer", "budget"), ("decision process",
                                                     "decision_role"),
                       ("urgency", "urgency")):
        add("  %-16s %s" % (label, _lead_field(lead, key) or "(not answered)"))

    add("")
    add("WHAT THE AUDIT FOUND")
    if findings:
        for f in findings:
            add("  * %s" % f["headline"])
    else:
        add("  No finding cleared its threshold. Do not overclaim -- run this "
            "as a discovery call.")

    add("")
    add("DISCOVERY QUESTIONS")
    for q in discovery_for(finding_keys, _lead_field(lead, "frustration")):
        add("  - %s" % q)

    add("")
    add("OBJECTION HANDLING")
    got = objections_for(_lead_field(lead, "tried"))
    if got:
        for label, handling in got:
            add("  %s" % label)
            add("    %s" % handling)
    else:
        add("  Nothing flagged in their 'already tried' answer.")

    add("")
    add("EXPECTED BUSINESS IMPACT")
    for line in impact_lines(evidence, finding_keys,
                             anchor=(rec or {}).get("anchor_price")):
        add("  - %s" % line)

    add("")
    add("HOW TO PRESENT THE OFFER")
    for i, step in enumerate(PRESENT_STEPS, 1):
        add("  %d. %s" % (i, step))

    if rec:
        add("")
        add("PRICE")
        cur = rec["currency"]
        add("  size class   %s  (%s)" % (rec["size_class"], rec["size_basis"]))
        add("  ANCHOR       %s  %s %s/month"
            % (rec["anchor_tier"].upper(), cur,
               "{:,}".format(rec["anchor_price"])))
        add("  step down    Foundation  %s %s/month"
            % (cur, "{:,}".format(rec["step_down"]["price"])))
        add("  step up      Dominate    %s %s/month"
            % (cur, "{:,}".format(rec["step_up"]["price"])))
        add("  upfront      %s -- %s %s covers three months"
            % (rec["upfront_terms"], cur,
               "{:,}".format(rec["upfront"][rec["anchor_tier"]])))
        add("  push to Dominate: %s" % rec["push_to_dominate"])
        for flag in rec["flags"]:
            add("  ! %s" % flag)
        if rec["unknown_signals"]:
            add("  size signals not established: %s"
                % ", ".join(rec["unknown_signals"]))

    add("")
    add(SEP)
    add("Every figure above is measured or quoted. Anything not established is "
        "marked unknown -- do not fill the gap on the call.")
    return "\n".join(lines)
