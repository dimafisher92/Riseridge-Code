"""End to end: a new booking in #sales-pipeline becomes three artefacts.

Dry-run in two independent dimensions, both off by default:

  --apply   allows collect.py's one write, creating a Site Explorer project
            against the operator's paid quota. One per unique domain.
  --post    allows writing into the lead's thread in #sales-pipeline, which
            is a private internal channel -- the prospect is not in it.

Scope is new bookings only, per the spec: no backfill. That is enforced by
`--max-age-hours` rather than by a ledger, because a hosted runner has no
durable local state -- see post.py for why the ledger moved server-side.

Every stage is allowed to fail without taking down the ones after it. A
prospect whose site is unreachable still gets a dossier full of honest
unknowns, a pricing recommendation flagged as unconfirmed, and a script that
tells the closer to run discovery.
"""

import argparse
import hashlib
import json
import os
import time
import traceback
from datetime import datetime, timezone

import aiprobe
import collect
import dossier as dossier_mod
import evidence as evidence_mod
import leads as leads_mod
import narrative
import post as post_mod
import pricing
import render
import salesscript
import slack

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state",
                   "prospects")

# Unbranded category nouns for the AI probe, keyed off the funnel's own
# business-type answer. The probe questions must be category-level: a branded
# question only fires for people who already know the brand.
CATEGORY_FOR = (
    ("home service", "home services company"),
    ("medical", "medical clinic"),
    ("wellness", "wellness clinic"),
    ("therapy", "therapy practice"),
    ("real estate", "real estate agent"),
    ("legal", "law firm"),
    ("law", "law firm"),
    ("restaurant", "restaurant"),
    ("e-commerce", "online store"),
    ("ecommerce", "online store"),
    # The Loom funnel names the call "Ecom AI SEO Strategic Call" and
    # carries no business-type answer at all, so without this every
    # Loom booking silently lost its AI section.
    ("ecom", "online store"),
    ("dental", "dentist"),
    ("fitness", "gym"),
    ("automotive", "auto repair shop"),
)


def category_for(lead):
    """A category noun for the probe, or '' when we cannot name one honestly."""
    text = " ".join(filter(None, [
        getattr(lead, "business_type", "") or "",
        getattr(lead, "site_title", "") or "",
        getattr(lead, "event_type", "") or "",
    ])).lower()
    for token, category in CATEGORY_FOR:
        if token in text:
            return category
    return ""


def vertical_for(lead):
    category = category_for(lead)
    return category.replace(" ", "-") if category else ""


# Page-title segments that name a page rather than the business. The unfurl
# title is a <title> tag, so "Home | Acme Plumbing" and "Acme Plumbing -
# Denver's Best Plumber" are both common, and both print on the audit cover.
GENERIC_TITLE_PARTS = frozenset({
    "home", "homepage", "welcome", "index", "official site",
    "official website", "site", "website", "landing page", "main",
})
TITLE_SPLIT = " | "


def business_name_for(lead):
    """A business name fit for the cover of a client-facing report.

    The funnel gives us a page <title>, not a company name. Printing it raw
    puts "Home | Acme Plumbing - Denver's Best Plumber" on the cover of a
    document the prospect is meant to take seriously.
    """
    raw = (getattr(lead, "site_title", "") or "").strip()
    if not raw:
        return getattr(lead, "domain", "") or ""
    parts = []
    for chunk in raw.replace("—", "|").replace("–", "|").replace(" - ", "|") \
                    .replace(":", "|").split("|"):
        chunk = chunk.strip()
        if chunk and chunk.lower() not in GENERIC_TITLE_PARTS:
            parts.append(chunk)
    if not parts:
        return getattr(lead, "domain", "") or raw
    # The company name is normally the shortest surviving segment: the others
    # are taglines. Ties keep document order, so a leading brand still wins.
    return min(parts, key=len)


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_hours(thread_ts, now=None):
    try:
        when = float(thread_ts)
    except (TypeError, ValueError):
        return None
    now = now if now is not None else time.time()
    return (now - when) / 3600.0


def select(all_leads, *, max_age_hours, now=None):
    """New bookings with a usable domain. No backfill, per the spec."""
    fresh, skipped = [], []
    for lead in all_leads:
        if not lead.domain:
            skipped.append((lead, "no resolvable domain; audit is impossible"))
            continue
        age = _age_hours(lead.thread_ts, now)
        if age is None:
            skipped.append((lead, "unparseable timestamp"))
            continue
        if max_age_hours and age > max_age_hours:
            skipped.append((lead, "older than %dh (no backfill)"
                            % max_age_hours))
            continue
        fresh.append(lead)
    return fresh, skipped


def _outdir(domain):
    d = os.path.join(OUT, collect._safe_domain_component(domain))
    os.makedirs(d, exist_ok=True)
    return d


def process(lead, *, apply=False, probe=True, sa=None, fetch=None,
            chrome=True):
    """All artefacts for one lead. Never raises; failures land in `errors`."""
    result = {"domain": lead.domain, "name": lead.name,
              "thread_ts": lead.thread_ts, "errors": [], "artefacts": {},
              "started_at": _stamp()}
    outdir = _outdir(lead.domain)
    business_name = business_name_for(lead)

    # --- evidence -----------------------------------------------------------
    ev_dict, ev = None, None
    try:
        ev_dict, action = collect.run(lead.domain, business_name,
                                      apply=apply, sa=sa)
        collect.write_evidence(ev_dict)
        ev = evidence_mod.Evidence(ev_dict)
        ev.validate()
        result["project_action"] = action
        result["artefacts"]["evidence"] = os.path.join(outdir, "evidence.json")
    except Exception as e:
        result["errors"].append("collect: %s" % e)

    # --- dossier ------------------------------------------------------------
    dos = None
    try:
        dos = dossier_mod.build(lead.domain, business_name=business_name,
                                contact_name=lead.name,
                                decision_answer=lead.decision_role,
                                fetch=fetch)
        path = os.path.join(outdir, "dossier.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(dos, fh, indent=1)
        result["artefacts"]["dossier"] = path
    except Exception as e:
        result["errors"].append("dossier: %s" % e)

    # --- AI visibility ------------------------------------------------------
    if probe and ev_dict is not None:
        category = category_for(lead)
        if not category:
            result["errors"].append(
                "aiprobe: no category could be named for business type %r; "
                "the AI section is omitted rather than probed with a guess"
                % (lead.business_type or ""))
        else:
            try:
                # Keyless by default. The provider APIs are used only when keys
                # are configured: they cost money per question and still are not
                # the consumer app, so they are an upgrade rather than the
                # baseline.
                rivals = aiprobe.competitor_names_from_evidence(ev_dict)
                if any(p.available() for p in aiprobe.default_providers()):
                    block = aiprobe.probe(
                        business_name, lead.domain,
                        vertical=vertical_for(lead), category=category,
                        competitors=rivals)
                else:
                    block = aiprobe.probe_sources(
                        business_name, lead.domain,
                        vertical=vertical_for(lead), category=category,
                        competitors=rivals)
                if block:
                    ev_dict["ai_visibility"] = block
                    collect.write_evidence(ev_dict)
                    ev = evidence_mod.Evidence(ev_dict)
                else:
                    result["errors"].append(
                        "aiprobe: no source could be measured; the AI section is "
                        "omitted rather than shown as an empty result")
            except Exception as e:
                result["errors"].append("aiprobe: %s" % e)

    # --- pricing ------------------------------------------------------------
    rec = None
    try:
        rec = pricing.recommend(
            lead.track or "local", evidence=ev_dict, dossier=dos,
            budget_answer=lead.budget, urgency=lead.urgency,
            revenue=getattr(lead, "revenue", ""), currency="USD")
        path = os.path.join(outdir, "pricing.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1)
        result["artefacts"]["pricing"] = path
    except Exception as e:
        result["errors"].append("pricing: %s" % e)

    # --- report -------------------------------------------------------------
    findings = []
    if ev is not None:
        try:
            findings = narrative.findings_for(ev, business_name)
            html = render.build_html(ev, narrative.build_tokens(ev))
            hp = os.path.join(outdir, "audit.html")
            with open(hp, "w", encoding="utf-8") as fh:
                fh.write(html)
            result["artefacts"]["html"] = hp
            if chrome:
                pp = os.path.join(outdir, "audit.pdf")
                render.html_to_pdf(hp, pp)
                info = render.verify_pdf(pp)
                result["artefacts"]["pdf"] = pp
                result["pdf_pages"] = info["pages"]
        except Exception as e:
            result["errors"].append("render: %s" % e)

    # --- sales script -------------------------------------------------------
    try:
        text = salesscript.build(lead, evidence=ev, dossier=dos,
                                 recommendation=rec, findings=findings)
        path = os.path.join(outdir, "script.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        result["artefacts"]["script"] = path
        result["script_text"] = text
    except Exception as e:
        result["errors"].append("script: %s" % e)

    if dos:
        result["dossier_text"] = dossier_mod.format_dossier(dos)
    result["findings"] = [f["headline"] for f in findings]
    result["finished_at"] = _stamp()
    return result


def summary_for(result, lead):
    """The message that accompanies the PDF in the thread."""
    lines = ["*AI Search Visibility Audit -- %s*" % business_name_for(lead),
             "Prepared for the call with %s." % (lead.name or "this lead")]
    if result.get("findings"):
        lines.append("")
        lines.append("What it found:")
        for f in result["findings"][:3]:
            lines.append("  - %s" % f)
    if result.get("errors"):
        lines.append("")
        lines.append("Incomplete sections: %d stage(s) could not run; details "
                     "in the internal notes below." % len(result["errors"]))
    return "\n".join(lines)


def run(*, pages=1, max_age_hours=48, apply=False, do_post=False, probe=True,
        client=None, channel=None, chrome=True, limit=0):
    client = client or slack.SlackClient()
    channel = channel or slack.config("SALES_PIPELINE_CHANNEL")
    if not channel:
        raise SystemExit("SALES_PIPELINE_CHANNEL is not set")

    found = leads_mod.scan(client, channel, pages=pages)
    fresh, skipped = select(found, max_age_hours=max_age_hours)
    if limit:
        fresh = fresh[:limit]

    # #sales-pipeline is a private, internal channel fed by a Zapier app -- the
    # prospect is not in it, and the spec's own instruction is that all three
    # artefacts go into the lead's thread there. So the thread IS the internal
    # destination, and an earlier separate "review channel" was solving a
    # problem that does not exist.
    poster = post_mod.Poster(client=client, channel=channel,
                             bot_user_id=slack.config("SLACK_BOT_USER_ID"),
                             dry_run=not do_post, internal=True)

    results = []
    for lead in fresh:
        try:
            r = process(lead, apply=apply, probe=probe, chrome=chrome)
        except Exception:
            r = {"domain": lead.domain, "name": lead.name,
                 "thread_ts": lead.thread_ts,
                 "errors": ["fatal: " + traceback.format_exc(limit=3)],
                 "artefacts": {}, "findings": []}
        try:
            r["posting"] = poster.publish(
                lead.thread_ts,
                summary=summary_for(r, lead),
                pdf_path=r["artefacts"].get("pdf"),
                dossier_text=r.get("dossier_text", ""),
                script_text=r.get("script_text", ""),
                business_name=business_name_for(lead))
        except Exception as e:
            r["errors"].append("post: %s" % e)
        results.append(r)

    notes = []
    if results and not do_post:
        notes.append(
            "Artefacts were built but delivered nowhere: this was a dry run. "
            "On a hosted runner they are discarded with the runner -- pass "
            "--post so they reach the lead's thread.")

    return {"scanned": len(found), "selected": len(fresh),
            "skipped": [{"domain": l.domain, "name": l.name, "reason": why}
                        for l, why in skipped],
            "results": results, "notes": notes}


def _identifiers(report):
    """Every string in the report that identifies a prospect."""
    out = set()
    for group in (report.get("results", []), report.get("skipped", [])):
        for r in group:
            for key in ("domain", "name", "email"):
                v = r.get(key)
                if v:
                    out.add(str(v))
    return out


def redact(text, identifiers):
    """Replace prospect identifiers with a stable non-reversible tag.

    The run log and any build artifact are world-readable on a public
    repository, so prospect names, emails and domains must not appear in
    either. The tag is stable within a run, so a reader can still follow one
    prospect through the log and match it to the Slack thread.
    """
    for value in sorted(identifiers, key=len, reverse=True):
        tag = "<prospect:%s>" % hashlib.sha256(
            value.lower().encode("utf-8")).hexdigest()[:8]
        text = text.replace(value, tag)
    return text


def format_run(report, *, reveal=False):
    """The run log.

    Redacted by default. `reveal=True` is for an operator running this on their
    own machine, never for a hosted runner whose logs are public.
    """
    out = ["PIPELINE RUN  scanned %d, selected %d"
           % (report["scanned"], report["selected"])]
    for note in report.get("notes", []):
        out.append("  NOTE     %s" % note)
    for s in report["skipped"]:
        out.append("  skipped  %-28s %s" % (s["domain"] or "(no domain)",
                                            s["reason"]))
    for r in report["results"]:
        out.append("")
        out.append("  %s  (%s)" % (r["domain"], r.get("name", "")))
        for key, path in sorted(r.get("artefacts", {}).items()):
            out.append("    %-10s %s" % (key, path))
        if r.get("pdf_pages"):
            out.append("    %-10s %d pages" % ("pdf pages", r["pdf_pages"]))
        for f in r.get("findings", []):
            out.append("    finding   %s" % f)
        for e in r.get("errors", []):
            out.append("    ERROR     %s" % e)
        posting = r.get("posting") or {}
        out.append("    posting   %s%s" % (posting.get("status", "not attempted"),
                                           (" (%s)" % posting["reason"])
                                           if posting.get("reason") else ""))
    text = "\n".join(out)
    return text if reveal else redact(text, _identifiers(report))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--max-age-hours", type=int, default=48,
                    help="new bookings only; 0 disables the cutoff")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true",
                    help="allow creating a Site Explorer project (paid quota)")
    ap.add_argument("--post", action="store_true",
                    help="allow writing to Slack (also needs RR_POSTING_ARMED)")
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--no-chrome", action="store_true",
                    help="build HTML but skip the PDF render")
    ap.add_argument("--reveal", action="store_true",
                    help="print prospect names and domains. Local use only: "
                         "hosted run logs are world-readable on a public repo")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    report = run(pages=a.pages, max_age_hours=a.max_age_hours, apply=a.apply,
                 do_post=a.post, probe=not a.no_probe, chrome=not a.no_chrome,
                 limit=a.limit)
    if a.json:
        text = json.dumps(report, indent=2, default=str)
        print(text if a.reveal else redact(text, _identifiers(report)))
    else:
        print(format_run(report, reveal=a.reveal))


if __name__ == "__main__":
    main()
