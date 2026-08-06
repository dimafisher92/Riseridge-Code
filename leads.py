"""Parse booked-appointment messages from #sales-pipeline into Lead records.

Message shapes verified against live channel history 2026-08-04. Only
'*Appointment booked ...' messages are leads; 'New Lead ...' is a form fill and
'Appointment stuck ...' is a follow-up nag.
"""

import argparse
import html
import re
from dataclasses import dataclass, asdict

# Three funnels post booked appointments and they do NOT share a format:
#   "*Appointment booked from the SEO Funnel*"     bold labels
#   "*Appointment booked from the VSL Funnel*"     bold labels
#   ":date: New Appointment Booked Loom Outreach"  PLAIN labels, richer fields
# Detect on the first line rather than a prefix so a new funnel with different
# wording is picked up. "Appointment stuck in booked for more than 5 days" is a
# follow-up nag and correctly does not contain the phrase "appointment booked".
BOOKED_PHRASE = "appointment booked"
# Boundaries exclude hyphens as well as word chars: a plain \b matched
# "test-drive.com" and "load-testing.io" because a hyphen is itself a word
# boundary, silently discarding those real leads.
TEST_PATTERN = re.compile(r"(?<![\w-])test(s|ing|er)?(?![\w-])|gsmgrowthagency", re.I)
SLACK_LINK = re.compile(r"<(?:https?://)([^>|]+)(?:\|[^>]*)?>")
BARE_DOMAIN = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})\b", re.I)
EMAIL_IN_TEXT = re.compile(r"mailto:([^|>\s]+)")
ECOM_ANSWERS = {"e-commerce or online-only business"}

# Answered in the "your business website" field often enough to matter. Auditing
# one of these would profile the platform instead of the prospect. Base domains
# only: _is_social_host also matches any subdomain (web.facebook.com,
# business.facebook.com, etc.), so listing both a domain and its subdomain is
# redundant except where being explicit documents a real observed case.
SOCIAL_HOSTS = frozenset({
    "facebook.com", "fb.com", "instagram.com",
    "linkedin.com", "x.com", "twitter.com", "youtube.com", "youtu.be",
    "tiktok.com", "pinterest.com", "yelp.com", "nextdoor.com",
    "google.com", "goo.gl", "maps.app.goo.gl", "linktr.ee",
    "g.page", "bit.ly", "tinyurl.com",
})


def _is_social_host(host):
    """True if the host is a social, maps or shortener domain rather than the
    prospect's own site. Suffix-aware: web.facebook.com and
    business.facebook.com must be rejected as surely as facebook.com."""
    for entry in SOCIAL_HOSTS:
        if host == entry or host.endswith("." + entry):
            return True
    return False


L_NAME = "Client's name"
L_EMAIL = "Email"
L_TZ = "Timezone"
L_MANAGER = "Manager"
L_FUNNEL = "Funnel"
L_BTYPE = "What type of business do you run?"
L_SITE = "Your business website"
L_FRUSTRATION = ("In 1-2 sentences, what's your biggest frustration with "
                 "getting new clients or patients right now?")
L_TRIED = "What have you already tried for marketing? (Select all that apply)"
L_BUDGET = ("How much are you currently spending (or willing to invest) on "
            "marketing each month?")
L_DECISION = ("When it comes to business decisions like this, how does your "
              "decision-making process work?")
L_INVITEE = "Invitee"
L_PHONE = "Phone"
L_WEBSITE_PLAIN = "Website"
L_EVENT = "Event"
L_TIME = "Time"
L_LOCATION = "Location"
L_REVENUE = "Company Revenue"

L_URGENCY = ("If this is the right fit for your business, when are you "
             "looking to get started?")


@dataclass
class Lead:
    thread_ts: str
    name: str
    email: str
    domain: str | None
    business_type: str
    track: str
    budget: str
    frustration: str
    tried: str
    decision_role: str
    urgency: str
    timezone: str
    manager: str
    funnel: str
    site_title: str = ""
    site_description: str = ""
    website_raw: str = ""
    # Present on Loom Outreach bookings only. The SEO/VSL funnels carry none of
    # these, which is why call prioritisation and phone research were previously
    # impossible from Slack alone.
    funnel_source: str = ""      # "seo" | "vsl" | "loom"
    phone: str = ""
    appointment_at: str = ""     # ISO 8601 UTC, the real call time
    meeting_url: str = ""        # Zoom link
    event_type: str = ""         # e.g. "Ecom AI SEO Strategic Call"
    revenue: str = ""            # e.g. "$50K - $100K /month"
    website_raw: str = ""

    def as_dict(self):
        return asdict(self)


def _first_link(raw):
    """Bare URL from a Slack <url|label> wrapper, or the raw text."""
    if not raw:
        return ""
    m = re.search(r"<(https?://[^>|]+)", raw)
    return m.group(1) if m else raw.strip()


def is_booked(text):
    """True if the first line announces a booked appointment, any funnel."""
    first = (text or "").splitlines()[0] if text else ""
    first = re.sub(r":[a-z0-9_+\-]+:", " ", first)      # drop :emoji: shortcodes
    first = first.replace("*", " ").lower()
    return BOOKED_PHRASE in re.sub(r"\s+", " ", first)


def field(text, label):
    """Extract a *Label:* value. Anchored per label so a malformed line in one
    field cannot shift another. Returns '' if absent or if the next label
    collapsed onto this line."""
    # Bold form first: "*Client's name:*" (SEO and VSL funnels). Then the plain
    # line-anchored form: "Invitee:" (Loom Outreach). Line-anchored so a label
    # word appearing mid-sentence cannot be mistaken for a field.
    m = re.search(r"\*" + re.escape(label) + r":?\*:?[ \t]*(.*)", text)
    if not m:
        m = re.search(r"(?:^|\n)[ \t]*" + re.escape(label) + r"[ \t]*:[ \t]*(.*)",
                      text)
    if not m:
        return ""
    val = m.group(1).strip()
    if val.startswith("*"):
        return ""
    return html.unescape(val).strip()


def normalize_domain(raw):
    """Bare lowercase registrable domain, or None."""
    if not raw:
        return None
    if "mailto:" in raw:
        return None
    m = SLACK_LINK.search(raw)
    host = m.group(1) if m else None
    if host is None:
        m2 = BARE_DOMAIN.search(raw)
        host = m2.group(1) if m2 else None
    if not host:
        return None
    host = host.split("/")[0].split("?")[0].split("#")[0].strip().lower()
    # Live data contains "wwww." with four w's; strip any run of two or more.
    # A single leading "w." is a legitimate host and is left alone.
    host = re.sub(r"^w{2,}\.", "", host)
    if "." not in host or host.endswith(".") or host.startswith("."):
        return None
    if _is_social_host(host):
        return None
    return host


def is_test_lead(name, email):
    return bool(TEST_PATTERN.search(name or "") or TEST_PATTERN.search(email or ""))


def track_for(business_type):
    return "ecom" if (business_type or "").strip().lower() in ECOM_ANSWERS else "local"


def parse_booked_message(msg):
    text = msg.get("text") or ""
    if not is_booked(text):
        return None

    name = field(text, L_NAME)
    email_raw = field(text, L_EMAIL)
    m = EMAIL_IN_TEXT.search(email_raw)
    email = m.group(1) if m else email_raw
    if is_test_lead(name, email):
        return None

    # Loom Outreach uses different label names and plain (non-bold) labels.
    loom = bool(field(text, L_INVITEE))
    if loom:
        name = field(text, L_INVITEE) or name
        source = "loom"
    elif "vsl funnel" in (text.splitlines()[0] if text else "").lower():
        source = "vsl"
    else:
        source = "seo"

    business_type = field(text, L_BTYPE)
    atts = msg.get("attachments") or []
    att = atts[0] if atts else {}
    website_raw = field(text, L_SITE) or field(text, L_WEBSITE_PLAIN)

    # Loom Outreach names the call rather than asking a business-type question,
    # and the event name carries the track: "Ecom AI SEO Strategic Call".
    event_type = field(text, L_EVENT)
    track = track_for(business_type)
    if not business_type and "ecom" in event_type.lower():
        track = "ecom"

    return Lead(
        thread_ts=msg.get("ts", ""),
        name=name,
        email=email,
        domain=normalize_domain(website_raw),
        business_type=business_type,
        track=track,
        budget=field(text, L_BUDGET),
        frustration=field(text, L_FRUSTRATION),
        tried=field(text, L_TRIED),
        decision_role=field(text, L_DECISION),
        urgency=field(text, L_URGENCY),
        timezone=field(text, L_TZ),
        manager=field(text, L_MANAGER),
        funnel=field(text, L_FUNNEL),
        site_title=html.unescape(att.get("title", "") or ""),
        site_description=html.unescape(att.get("text", "") or ""),
        website_raw=website_raw,
        funnel_source=source,
        # Loom Outreach only. The SEO and VSL funnels carry none of these, which
        # is why phone research and call prioritisation were previously
        # impossible from Slack alone.
        phone=field(text, L_PHONE),
        appointment_at=field(text, L_TIME),
        meeting_url=_first_link(field(text, L_LOCATION)),
        event_type=event_type,
        revenue=field(text, L_REVENUE),
    )


def scan(client, channel, pages=1):
    """Newest-first Leads from the channel. pages=0 walks all history."""
    out = []
    for m in client.history(channel, pages=pages):
        lead = parse_booked_message(m)
        if lead:
            out.append(lead)
    return out


def main():
    import json
    import slack

    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=1,
                    help="history pages to scan; 0 = all")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    got = scan(slack.SlackClient(), slack.config("SALES_PIPELINE_CHANNEL"),
               pages=a.pages)
    if a.json:
        print(json.dumps([x.as_dict() for x in got], indent=2))
        return
    print("%d booked leads" % len(got))
    print("  %-3s %-22s %-30s %-5s %-16s %s" % (
        "src", "name", "domain", "trk", "call (UTC)", "money signal"))
    for x in got:
        if x.domain:
            domain_col = x.domain
        elif x.website_raw:
            domain_col = "(unusable: %s)" % x.website_raw
        else:
            domain_col = "(no domain)"
        # The funnels disagree on which money question they ask: the SEO and VSL
        # funnels capture a self-reported budget, Loom Outreach captures actual
        # company revenue. Show whichever exists rather than a blank column.
        money = x.revenue or x.budget or ""
        # Only Loom carries the real appointment time, so only Loom rows can be
        # prioritised by when the call actually is.
        when = (x.appointment_at or "")[:16].replace("T", " ")
        print("  %-3s %-22s %-30s %-5s %-16s %s" % (
            x.funnel_source or "?", x.name[:22], domain_col[:30], x.track,
            when, money[:34]))


if __name__ == "__main__":
    main()
