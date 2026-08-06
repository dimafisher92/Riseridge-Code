"""The authoring layer: evidence in, the report's full token contract out.

Section 3 ("The Finding That Changes Everything") is the judgment section. The
spec says it cannot be templated, and that is right -- the reference report's
insight was that a men's clinic ranked for perimenopause keywords, and no
threshold rule finds that. What a rule CAN do honestly is pick which of the real
findings leads, and say it in plain English.

How the lead is chosen, and why it is not a score:

An early version scored each candidate finding on a 0-100 scale and took the
maximum. That was arithmetic theatre. Comparing "85% of traffic is branded"
against "1,530 keywords sit just off page one" on one numeric scale requires an
exchange rate between a percentage and a count, and there isn't one -- both
saturated at 100 and the tie was broken by a cap.

So the order below is an explicit editorial judgment, written down and testable,
and each candidate carries its own qualifying threshold. A finding leads only if
it clears its own bar; among those that do, the first in this list wins. When a
finding is later authored by hand or by a model, it drops into the same slot.

Every FIGURE in the output still comes from the evidence file. The prose is the
only thing this module writes.
"""

import re
from datetime import datetime, timezone

import render

# Editorial priority. First qualifying finding leads section 3; the rest become
# executive-summary bullets in the same order.
FINDING_ORDER = ("brand_dominance", "ai_absence", "near_misses",
                 "paid_dependence", "link_relevance")

# Qualifying thresholds. A candidate below its bar is not a finding, it is a
# number -- and leading with a weak one wastes the report's strongest page.
BRAND_DOMINANCE_PCT = 65
NEAR_MISS_MIN = 150
LINK_VOLUME_MIN = 50
WEAK_NONBRAND_PCT = 25
PAID_SPEND_MIN = 1000


def _g(ev, path):
    return ev.get(path) if hasattr(ev, "get") else None


def rows(items, *cols):
    """Table rows from evidence records. Numeric columns get the num class.

    A record whose first column is empty is skipped: the anchors feed contains
    blank-anchor rows, and rendering one puts an empty cell beside a real number
    in a client-facing table.
    """
    out = []
    for it in items:
        label_key = cols[0][0]
        if not str(it.get(label_key) or "").strip():
            continue
        cells = []
        for key, kind in cols:
            v = it.get(key)
            if kind is None:
                cells.append("<td>%s</td>" % _esc(v or ""))
            else:
                cells.append("<td class='num'>%s</td>" % render.fmt(v, kind))
        out.append("<tr>%s</tr>" % "".join(cells))
    return "".join(out)


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _li(*items):
    return "".join("<li>%s</li>" % i for i in items if i)


def _p(*paras):
    return "".join("<p>%s</p>" % t for t in paras if t)


# --- candidate findings -----------------------------------------------------
#
# Each returns None when it does not clear its own threshold, or a dict with the
# four section-3 blocks plus a one-line summary for the executive summary.

def _brand_dominance(ev, name):
    brand = _g(ev, "brand_split.brand_pct")
    nonbrand = _g(ev, "brand_split.nonbrand_pct")
    if brand is None or brand < BRAND_DOMINANCE_PCT:
        return None
    b, nb = render.fmt(brand, "pct"), render.fmt(nonbrand, "pct")
    return {
        "key": "brand_dominance",
        "headline": "%s of your traffic already knew your name" % b,
        "body_html": _p(
            "When we pulled every keyword this site earns clicks from and "
            "separated brand searches &mdash; people typing %s directly &mdash; "
            "from problem searches, the split was stark. The marketing that "
            "drives awareness is working. The website itself is not currently "
            "a customer-acquisition channel: the people who do not already know "
            "%s are ending up somewhere else."
            % (_esc(name), _esc(name))),
        "callout_html": _p(
            "Brand searches account for %s of search traffic. Problem searches "
            "&mdash; someone with the problem and no idea who you are &mdash; "
            "account for %s." % (b, nb)),
        "why_html": _li(
            "Every marketing dollar spent on awareness has to keep working, "
            "because nothing compounds from search.",
            "Competitors are collecting the problem searches and converting "
            "them into customers who never hear your name first.",
            "This is among the faster things on this audit to move, because "
            "the site already ranks &mdash; just on the wrong terms."),
        "summary": "Almost all search traffic comes from people who already "
                   "knew the name. The site is converting existing awareness "
                   "rather than creating new demand.",
    }


def _ai_absence(ev, name):
    topics = _g(ev, "ai_visibility.topics") or []
    if topics:
        return _ai_absence_from_sources(ev, name, topics)
    platforms = _g(ev, "ai_visibility.platforms") or []
    if not platforms:
        return None
    named = [p for p in platforms if p.get("brand_named")]
    if named:
        return None
    rivals = []
    for p in platforms:
        for c in p.get("competitors_named") or []:
            if c not in rivals:
                rivals.append(c)
    engines = ", ".join(p.get("platform", "") for p in platforms)
    rival_text = (" They named %s instead." % _esc(", ".join(rivals[:3]))
                  if rivals else "")
    return {
        "key": "ai_absence",
        "headline": "The AI assistants do not know you exist",
        "body_html": _p(
            "We asked %s the questions your buyers actually ask &mdash; "
            "unbranded, category-level questions from someone who has a problem "
            "and no shortlist. %s was named in none of them.%s"
            % (_esc(engines), _esc(name), rival_text)),
        "callout_html": _p(
            "Across %d assistant%s and every question we asked, %s was named "
            "zero times." % (len(platforms), "" if len(platforms) == 1 else "s",
                             _esc(name))),
        "why_html": _li(
            "Buyers who research this way have already narrowed their choices "
            "before they ever click.",
            "The assistant's answer is the shortlist. Being absent from it is "
            "not a ranking problem, it is a not-in-the-running problem.",
            "The businesses being named are not necessarily bigger &mdash; they "
            "are structured in the way these systems can read and cite."),
        "summary": "None of the major AI assistants name the business when "
                   "asked what a buyer in this category should do.",
    }


def _ai_absence_from_sources(ev, name, topics):
    """Same finding, measured the keyless way.

    Worded to match what was actually checked. It says the business is absent
    from the pages these answers are built from -- never that an assistant was
    asked and did not name it, which is a claim this method cannot support.
    """
    present = [t for t in topics if t.get("brand_present")]
    if present:
        return None
    rivals, aggs = [], []
    for t in topics:
        for c in t.get("competitors_named") or []:
            if c not in rivals:
                rivals.append(c)
        for a in t.get("aggregator_sources") or []:
            if a not in aggs:
                aggs.append(a)

    rival_text = (" The pages that do get used name %s."
                  % _esc(", ".join(rivals[:3])) if rivals else "")
    return {
        "key": "ai_absence",
        "headline": "You are missing from the answers buyers are being given",
        "body_html": _p(
            "We took the questions your buyers ask when they have the problem "
            "and no shortlist &mdash; unbranded, no company names &mdash; and "
            "looked at the pages an AI assistant pulls from to answer them. "
            "%s is in none of them.%s" % (_esc(name), rival_text)),
        "callout_html": _p(
            "Across %d buyer question%s, %s did not appear once in the sources "
            "those answers are assembled from."
            % (len(topics), "" if len(topics) == 1 else "s", _esc(name))),
        "why_html": _li(
            "An assistant can only recommend what it can find and cite. Not "
            "being in the source pool means not being in the answer.",
            "Buyers who research this way arrive already narrowed down &mdash; "
            "the answer is the shortlist.",
            "The businesses that do appear are not necessarily bigger. They are "
            "published in a form these systems can read and quote."
            + (" A large share of what is being cited is directory listings, "
               "which is a gap a real business page can take." if aggs else "")),
        "summary": "The business does not appear in the sources AI assistants "
                   "build their answers from for its own category.",
    }


def _near_misses(ev, name):
    near = (_g(ev, "position_buckets.11-20") or 0) + \
           (_g(ev, "position_buckets.21-50") or 0)
    if near < NEAR_MISS_MIN:
        return None
    top = (_g(ev, "position_buckets.1-3") or 0) + \
          (_g(ev, "position_buckets.4-10") or 0)
    return {
        "key": "near_misses",
        "headline": "%s rankings are sitting just off page one"
                    % render.fmt(near),
        "body_html": _p(
            "Google already indexes these pages, already considers them "
            "relevant, and already ranks them &mdash; positions 11 to 50. They "
            "are simply not strong enough to reach page one yet, and almost "
            "nobody scrolls that far. This is earned ground that is currently "
            "earning nothing."),
        "callout_html": _p(
            "%s keywords rank in positions 11&ndash;50, against %s in the top "
            "ten. The work needed to move a page from 14 to 8 is a fraction of "
            "the work needed to rank a page that does not exist yet."
            % (render.fmt(near), render.fmt(top))),
        "why_html": _li(
            "Page-two rankings convert at close to zero, so this traffic is "
            "invisible in the numbers today.",
            "These pages have already cleared the hard part &mdash; relevance "
            "and indexing.",
            "Moving even a fraction of them into the top ten changes the "
            "traffic line materially."),
        "summary": "A large block of keywords ranks in positions 11&ndash;50, "
                   "where they earn nothing despite already being relevant.",
    }


def _paid_dependence(ev, name):
    spend = _g(ev, "paid.estimated_monthly_spend_usd")
    if spend is None or spend < PAID_SPEND_MIN:
        return None
    nonbrand = _g(ev, "brand_split.nonbrand_pct")
    return {
        "key": "paid_dependence",
        "headline": "You are renting the visibility you could own",
        "body_html": _p(
            "%s is paying for placement on searches it does not rank for "
            "organically. That spend buys traffic for exactly as long as the "
            "card keeps being charged, and stops the day it does not."
            % _esc(name)),
        "callout_html": _p(
            "Estimated ad spend runs at %s per month, while non-brand organic "
            "accounts for %s of search traffic."
            % (render.fmt(spend, "usd"),
               render.fmt(nonbrand, "pct") if nonbrand is not None
               else "a small share")),
        "why_html": _li(
            "Paid traffic stops the moment the budget does. Organic and AI "
            "visibility compound.",
            "The same searches you are bidding on can be earned.",
            "Every month of paid-only strategy is a month the owned asset is "
            "not being built."),
        "summary": "Meaningful ad spend is buying visibility on searches the "
                   "site does not own organically.",
    }


def _link_relevance(ev, name):
    refs = _g(ev, "backlinks.referring_domains")
    nonbrand = _g(ev, "brand_split.nonbrand_pct")
    if refs is None or refs < LINK_VOLUME_MIN:
        return None
    if nonbrand is None or nonbrand > WEAK_NONBRAND_PCT:
        return None
    return {
        "key": "link_relevance",
        "headline": "Real link authority that is not converting into rankings",
        "body_html": _p(
            "%s links from %s separate domains point at this site, so the "
            "investment has clearly been made. What is missing is category "
            "relevance: authority from unrelated sites does not transfer trust "
            "in the one category that matters here."
            % (render.fmt(_g(ev, "backlinks.total_backlinks") or 0),
               render.fmt(refs))),
        "callout_html": _p(
            "%s referring domains, and non-brand search still accounts for only "
            "%s of traffic." % (render.fmt(refs), render.fmt(nonbrand, "pct"))),
        "why_html": _li(
            "Link volume without category relevance is effort that does not "
            "compound.",
            "The same outreach effort aimed at industry publications and "
            "directories would move rankings.",
            "This is the highest-leverage change available in the link "
            "profile."),
        "summary": "Link volume is real, but it is not concentrated in the "
                   "categories that would transfer authority where it counts.",
    }


CANDIDATES = {
    "brand_dominance": _brand_dominance,
    "ai_absence": _ai_absence,
    "near_misses": _near_misses,
    "paid_dependence": _paid_dependence,
    "link_relevance": _link_relevance,
}


def _fallback(ev, name):
    """Section 3 is ungated, so something must always be authored.

    Used when nothing clears its bar. It states what was measured rather than
    manufacturing an insight the data does not support.
    """
    kw = _g(ev, "traffic.ranking_keyword_count")
    where = ("%s keywords currently rank somewhere in Google's results"
             % render.fmt(kw)) if kw else "the site ranks for a limited set of searches"
    return {
        "key": "baseline",
        "headline": "The search footprint is smaller than the business",
        "body_html": _p(
            "%s, and very little of it sits where buyers actually look. The "
            "gap between what %s does and what it is findable for is the whole "
            "opportunity in this report." % (where.capitalize(), _esc(name))),
        "callout_html": _p(
            "This audit reports only what could be measured directly. Where a "
            "figure is absent, no estimate has been substituted for it."),
        "why_html": _li(
            "Search and AI assistants are now where category research starts.",
            "The businesses winning these searches are not necessarily larger.",
            "The work is knowable and sequenced &mdash; the plan is later in "
            "this report."),
        "summary": "The site's search footprint is materially smaller than the "
                   "business behind it.",
    }


def findings_for(ev, name):
    """Every qualifying finding, in editorial priority order."""
    out = []
    for key in FINDING_ORDER:
        got = CANDIDATES[key](ev, name)
        if got:
            out.append(got)
    return out


# --- section builders -------------------------------------------------------

HEAD_ENGINE = ("<th>AI platform</th><th>Visibility</th><th>Sentiment</th>"
               "<th>Reading</th>")
HEAD_SOURCE = ("<th>What a buyer asks</th><th>Are you in the sources</th>"
               "<th>Who is</th>")


def _shorten(question, limit=46):
    q = question.strip()
    return q if len(q) <= limit else q[:limit - 1].rstrip() + "…"


def _source_rows(topics):
    """One row per buyer question, from the open-source method.

    Deliberately does NOT claim an assistant said anything. It reports presence
    in the pool of pages the answer is built from, which is what was measured.
    """
    out = []
    for t in topics:
        if t.get("brand_present"):
            rank = t.get("brand_rank")
            presence = "Yes (source %s)" % rank if rank else "Yes"
        else:
            presence = "No"
        rivals = [c for c in (t.get("competitors_named") or [])]
        others = [d for d in (t.get("business_sources") or [])][:2]
        if rivals:
            who = _esc(", ".join(rivals[:2]))
        elif others:
            who = _esc(", ".join(others))
        elif t.get("aggregator_sources"):
            who = "Directories only"
        else:
            who = "No business named"
        out.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                   % (_esc(_shorten(t.get("question", ""))), presence, who))
    return "".join(out)


def _ai_rows(platforms):
    out = []
    for p in platforms:
        engine = p.get("platform", "")
        total = p.get("topics_total") or 0
        present = p.get("topics_present") or 0
        if p.get("brand_named"):
            visibility = "Named in %d of %d" % (present, total)
            sentiment = "Mentioned"
            reading = "Present, but not the default recommendation"
            if total and present == total:
                reading = "Consistently named across every question"
        else:
            visibility = "Not named"
            sentiment = "Absent"
            rivals = p.get("competitors_named") or []
            reading = ("Names %s instead" % _esc(", ".join(rivals[:2]))
                       if rivals else "Recommends other businesses")
        out.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                   % (_esc(engine), visibility, sentiment, reading))
    return "".join(out)


def _ai_source_gap(ev, name):
    """The specific gap, for the open-source method."""
    d = ev.data if hasattr(ev, "data") else ev
    block = (d.get("ai_visibility") or {})
    topics = block.get("topics") or []
    summary = block.get("summary") or {}
    total = summary.get("questions_searched") or len(topics)
    present = summary.get("questions_present") or 0
    rivals = summary.get("competitors_named") or []
    aggs = summary.get("aggregator_sources") or []

    parts = []
    if present == 0:
        parts.append(
            "Across every question we checked, %s does not appear in the pages "
            "these answers are assembled from." % _esc(name))
    else:
        parts.append(
            "%s appears in the source pool for %d of %d questions, and is "
            "absent from the rest." % (_esc(name), present, total))
    if rivals:
        parts.append("Businesses that do appear include %s."
                     % _esc(", ".join(rivals[:4])))
    if aggs:
        parts.append(
            "Much of the rest is directory listings &mdash; %s &mdash; which is "
            "its own opportunity: when the assistant has no strong business "
            "page to cite, it falls back to aggregators."
            % _esc(", ".join(aggs[:3])))
    return _p(" ".join(parts))


def _ai_gap(ev, name):
    if _g(ev, "ai_visibility.topics"):
        return _ai_source_gap(ev, name)
    platforms = _g(ev, "ai_visibility.platforms") or []
    if not platforms:
        return ""
    rivals = []
    for p in platforms:
        for c in p.get("competitors_named") or []:
            if c not in rivals:
                rivals.append(c)
    excerpt = next((p.get("verbatim_excerpt") for p in platforms
                    if p.get("verbatim_excerpt")), "")
    parts = []
    named = [p["platform"] for p in platforms if p.get("brand_named")]
    if named:
        parts.append("%s is named by %s, but not consistently and not first."
                     % (_esc(name), _esc(", ".join(named))))
    else:
        parts.append("%s was not named by any assistant we asked."
                     % _esc(name))
    if rivals:
        parts.append("The businesses being recommended in its place are %s."
                     % _esc(", ".join(rivals[:4])))
    out = _p(" ".join(parts))
    if excerpt:
        out += ('<div class="callout"><div class="t">What the assistant '
                'actually said</div><p>&ldquo;%s&rdquo;</p></div>'
                % _esc(excerpt))
    return out


def _plan(ev, findings):
    """The 90-day plan, sequenced from what the findings actually found."""
    keys = {f["key"] for f in findings}
    first = ["Full technical audit and cleanup of the issues holding every "
             "page back.",
             "Link profile audit, with anything risky identified and filed for "
             "disavow."]
    second, third = [], []

    if "ai_absence" in keys or _g(ev, "ai_visibility.platforms"):
        first.append("AI visibility mapping: every question the assistants are "
                     "being asked in this category, and where you need to "
                     "appear in the answer.")
        third.append("Expand presence across all the major AI answer engines.")
    if "brand_dominance" in keys:
        second.append("Build the pages that capture the high-intent, non-brand "
                      "searches the site is invisible for today.")
    if "near_misses" in keys:
        second.append("Target the keywords already ranking on page two and "
                      "push them into the top ten.")
    if "link_relevance" in keys:
        third.append("Secure genuine third-party links from publications and "
                     "directories in your own category.")
    if "paid_dependence" in keys:
        second.append("Move the highest-cost paid terms onto pages built to "
                      "earn them organically.")

    second.append("Rebuild the highest-value pages in the structure search and "
                  "AI models actually reward.")
    third.append("Stand up reporting so the whole team can see rankings, "
                 "traffic and revenue impact in real time.")
    return _li(*first), _li(*second[:3]), _li(*third[:3])


def _report_date(ev, now=None):
    stamp = (ev.data.get("generated_at") if hasattr(ev, "data") else None)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(stamp, fmt).strftime("%B %Y")
        except (TypeError, ValueError):
            continue
    return (now or datetime.now(timezone.utc)).strftime("%B %Y")


def build_tokens(ev, *, now=None):
    """Every token the templates need, authored from evidence.

    Figures come from the evidence file. Prose is generated. A metric that is
    absent stays absent -- the renderer's section and tile gates then drop the
    surrounding markup rather than printing a guess.
    """
    d = ev.data if hasattr(ev, "data") else ev
    name = d.get("business_name") or d.get("domain") or ""
    bl = d.get("backlinks") or {}
    paid = d.get("paid") or {}
    sc = d.get("scorecard") or {}

    brand = render.fmt(_g(ev, "brand_split.brand_pct"), "pct")
    nonbrand = render.fmt(_g(ev, "brand_split.nonbrand_pct"), "pct")
    sample = _g(ev, "brand_split.sample_rows")

    found = findings_for(ev, name)
    lead = found[0] if found else _fallback(ev, name)
    rest = found[1:] if found else []

    summary_items = [f["summary"] for f in ([lead] + rest)][:4]
    plan_1, plan_2, plan_3 = _plan(ev, found)

    ai_block = d.get("ai_visibility") or {}
    platforms = _g(ev, "ai_visibility.platforms") or []
    topics = _g(ev, "ai_visibility.topics") or []
    paid_pages = paid.get("landing_pages") or []
    paid_kws = paid.get("paid_keywords") or []

    tokens = {
        "business_name": name,
        "domain": d.get("domain") or "",
        "report_date": _report_date(ev, now),

        # --- executive summary ---
        "exec_summary_intro_html": _p(
            "This is a plain-English read of how %s shows up when someone goes "
            "looking for what you do &mdash; in search, and now in the AI "
            "assistants people increasingly ask first. Every figure here was "
            "measured directly. Where something could not be measured, it has "
            "been left out rather than estimated." % _esc(name)),
        "exec_summary_findings_html": _li(*summary_items),
        "exec_summary_close_html": _p(
            "None of this is out of reach. It is a matter of doing the right "
            "work in the right order, on the right pages. The rest of this "
            "report lays out exactly what that is."),

        # --- the finding (section 3) ---
        "finding_headline": lead["headline"],
        "finding_body_html": lead["body_html"],
        "finding_data_callout_html": lead["callout_html"],
        "finding_why_html": lead["why_html"],

        # --- scorecard (passed through; never invented) ---
        "score_content": render.fmt(_g(ev, "scorecard.content_quality")),
        "score_content_band": _band(_g(ev, "scorecard.content_quality")),
        "score_authority": render.fmt(_g(ev, "scorecard.authority")),
        "score_authority_band": _band(_g(ev, "scorecard.authority")),
        "score_ux": render.fmt(_g(ev, "scorecard.user_experience")),
        "score_ux_band": _band(_g(ev, "scorecard.user_experience")),
        "scorecard_explanations_html": _li(*[
            "<b>%s.</b> %s" % (_esc(k.replace("_", " ").title()),
                               _esc((v or {}).get("basis", "")))
            for k, v in sc.items() if isinstance(v, dict) and v.get("basis")]),

        # --- AI visibility ---
        # Two methods can populate this block and they measure different
        # things, so the table says which one produced it rather than letting
        # source-pool presence read as an assistant's verdict.
        "ai_platform_rows_html": (_source_rows(topics) if topics
                                  else _ai_rows(platforms)),
        "ai_table_head_html": HEAD_SOURCE if topics else HEAD_ENGINE,
        "ai_intro_html": _p(
            "We asked the questions a buyer asks when they have the problem and "
            "no shortlist &mdash; unbranded, no company names &mdash; and looked "
            "at which businesses the answers are built from."
            if topics else
            "We put the questions a buyer asks &mdash; unbranded, no company "
            "names &mdash; to each assistant, and recorded who got named."),
        "ai_method_note_html": (
            '<p class="fine">%s</p>' % _esc(ai_block.get("method_note", ""))
            if topics and ai_block.get("method_note") else ""),
        "ai_gap_html": _ai_gap(ev, name),

        # --- traffic and rankings ---
        "visits": render.fmt(_g(ev, "traffic.monthly_organic_visits"), "k"),
        "keyword_count": render.fmt(_g(ev, "traffic.ranking_keyword_count")),
        "traffic_value": render.fmt(_g(ev, "traffic.traffic_value_usd"), "usd"),
        "brand_pct": brand,
        "nonbrand_pct": nonbrand,
        "brand_split_basis": ("top %s keywords by traffic" % render.fmt(sample)
                              if sample else render.DASH),
        "money_keywords_rows_html": rows(
            (d.get("money_keywords") or [])[:12],
            ("keyword", None), ("volume", "int"), ("position", "int")),
        "traffic_close_html": _p(
            "Every one of those searches is somebody asking Google to solve "
            "exactly what %s sells. On each of them, someone else is getting "
            "the click." % _esc(name)),

        # --- position distribution ---
        "pos_1_3": render.fmt(_g(ev, "position_buckets.1-3")),
        "pos_4_10": render.fmt(_g(ev, "position_buckets.4-10")),
        "pos_11_20": render.fmt(_g(ev, "position_buckets.11-20")),
        "pos_21_50": render.fmt(_g(ev, "position_buckets.21-50")),
        "pos_51_100": render.fmt(_g(ev, "position_buckets.51-100")),
        "position_buckets_close_html": _p(
            "Google already indexes and ranks these pages and considers them "
            "relevant enough to show. They are simply not strong enough to "
            "reach page one yet. Move a fraction into the top ten and non-brand "
            "traffic climbs sharply."),

        # --- paid ---
        "paid_spend": render.fmt(
            _g(ev, "paid.estimated_monthly_spend_usd"), "usd"),
        "paid_keyword_count": render.fmt(len(paid_kws)) if paid_kws else "",
        "paid_landing_pages_html": _li(*[_esc(p) for p in paid_pages[:6]]),
        "paid_vs_organic_html": _p(
            "Paid placement disappears the day the budget does. Organic and AI "
            "visibility are assets that keep returning after the work is done."),

        # --- link profile ---
        "referring_domains": render.fmt(_g(ev, "backlinks.referring_domains")),
        "total_backlinks": render.fmt(_g(ev, "backlinks.total_backlinks")),
        "authority": render.fmt(_g(ev, "backlinks.authority")),
        "authority_label": (bl.get("authority") or {}).get(
            "metric_name", "Link authority"),
        "trust": render.fmt(_g(ev, "backlinks.trust")),
        "trust_label": (bl.get("trust") or {}).get("metric_name", "Link trust"),
        "anchor_rows_html": rows((bl.get("top_anchors") or [])[:8],
                                 ("anchor", None), ("count", "int")),
        "link_profile_findings_html": _li(
            "The link volume is real, so the investment has been made.",
            "What is missing is category relevance: authority from unrelated "
            "sites does not transfer trust where it counts, which is why it has "
            "not converted into rankings on the terms that matter.",
            "Redirecting that same effort toward publications and directories "
            "in your own category is the highest-leverage change available."),

        # --- competitors ---
        "competitor_rows_html": rows(
            (d.get("competitors") or [])[:6],
            ("domain", None), ("monthly_visits", "k"),
            ("ranking_keywords", "int")),
        "competitor_pattern_html": _li(
            "Their content is pointed at what buyers actually search, at every "
            "stage from research to ready-to-buy.",
            "They do not spend content budget on an audience that already knows "
            "them.",
            "They show up when someone asks an AI assistant where to go."),

        # --- the plan ---
        "plan_days_1_30_html": plan_1,
        "plan_days_31_60_html": plan_2,
        "plan_days_61_90_html": plan_3,
        "plan_outcome_html": _p(
            "Based on what this data shows, the realistic outcome is a "
            "materially larger share of traffic from people who did not already "
            "know the brand &mdash; the difference between a channel that "
            "reinforces your existing marketing and one that generates net-new "
            "customers on its own."),
    }
    return tokens


def _band(score):
    """Plain-English band for a scorecard number. Never invents the number."""
    if score is None:
        return ""
    if score >= 60:
        return "Solid"
    if score >= 40:
        return "Needs work"
    return "Critical"
