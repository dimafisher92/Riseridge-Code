# RiseRidge Sales Phase 1: Lead Ingestion and Report Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a booked-lead Slack message into a parsed `Lead`, and turn an `evidence.json` into a brand-correct RiseRidge audit PDF, proven by reproducing the existing PeterMD report.

**Architecture:** Two independent halves that meet in Phase 2. `slack.py` + `leads.py` read `#sales-pipeline` and emit typed `Lead` records. `evidence.py` + `render.py` + `templates/` turn an evidence file plus an authored narrative into a PDF via headless Chrome. Nothing in Phase 1 calls SearchAtlas, so it is unblocked by the pending capability spike.

**Tech Stack:** Python 3.14 stdlib (`urllib`, `json`, `re`, `dataclasses`, `subprocess`), `pytest` 9.1.1 for tests, `pymupdf` (`fitz`) 1.28 for PDF verification, headless Chrome for PDF generation.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-riseridge-sales-audit-design.md`. Read it before starting.
- Python invoked as `python` from `D:\Claude Code\riseridge-sales`.
- **Never name a tool in client-facing output.** No "SearchAtlas", "Ahrefs", "Semrush", "Majestic", "SimilarWeb" anywhere in the PDF or its template. This is test-enforced in Task 7.
- **A null evidence value omits its section. It is never estimated, interpolated, or invented.**
- Chrome is at `C:\Program Files\Google\Chrome\Application\chrome.exe`. Render flags, exactly: `--headless=new --disable-gpu --no-pdf-header-footer --no-margins --print-to-pdf=OUT IN`.
- Fonts must be base64-inlined, **one weight per Google Fonts CSS call** (the `css2` API serves variable fonts that Chrome silently drops), and every `@font-face` must keep its `unicode-range`.
- Logos must be inline `<svg>` elements. Chrome's `--print-to-pdf` does not embed external `<img src="*.svg">`.
- Page size is US Letter: `@page { size: Letter; margin: 0; }`.
- Brand palette: pine `#1E3A2E`, brass `#A9874E`, ink `#15140F`, ivory `#F4F0E8`. Muted greys `#56514a`, `#8a8276`. Fonts: Cormorant Garamond (display), Hanken Grotesk (body), Space Mono (data labels).
- `.env` holds `SLACK_BOT_TOKEN`, `SALES_PIPELINE_CHANNEL=C09PLHVBHRC`, `SLACK_BOT_USER_ID=U0BMUDY9TM3`. It is git-ignored. Never commit it, never print the token.
- Phase 1 makes **no Slack writes**. No `chat.postMessage`, no file upload, no reactions.

---

### Task 1: Slack read-only client

**Files:**
- Create: `slack.py`
- Create: `tests/test_slack.py`
- Create: `conftest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SlackClient()` with `.auth_test() -> dict`, `.history(channel: str, limit: int = 200, pages: int = 0) -> list[dict]` (pages=0 means all pages), and `.api(method: str, params: dict) -> dict`. Raises `SlackError(str)` on `ok: false`. `load_env(path=".env") -> dict[str, str]`.

- [ ] **Step 1: Write the failing test**

`tests/test_slack.py`:

```python
import pytest
import slack


def test_load_env_reads_key_values(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\nB=two\n\n# comment\n", encoding="utf-8")
    got = slack.load_env(p)
    assert got == {"A": "1", "B": "two"}


def test_load_env_ignores_inline_comment_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# lead\nTOKEN=xoxb-abc\n", encoding="utf-8")
    assert slack.load_env(p) == {"TOKEN": "xoxb-abc"}


def test_slack_error_raised_on_not_ok(monkeypatch):
    c = slack.SlackClient(token="xoxb-fake")
    monkeypatch.setattr(c, "_post", lambda m, p: {"ok": False, "error": "channel_not_found"})
    with pytest.raises(slack.SlackError) as e:
        c.api("conversations.history", {"channel": "C0"})
    assert "channel_not_found" in str(e.value)


def test_history_paginates_and_concatenates(monkeypatch):
    c = slack.SlackClient(token="xoxb-fake")
    pages = [
        {"ok": True, "messages": [{"ts": "1"}, {"ts": "2"}],
         "response_metadata": {"next_cursor": "CUR"}},
        {"ok": True, "messages": [{"ts": "3"}], "response_metadata": {"next_cursor": ""}},
    ]
    seen = []

    def fake_post(method, params):
        seen.append(params.get("cursor", ""))
        return pages[len(seen) - 1]

    monkeypatch.setattr(c, "_post", fake_post)
    assert [m["ts"] for m in c.history("C0")] == ["1", "2", "3"]
    assert seen == ["", "CUR"]


def test_history_stops_at_page_limit(monkeypatch):
    c = slack.SlackClient(token="xoxb-fake")

    def fake_post(method, params):
        return {"ok": True, "messages": [{"ts": "1"}],
                "response_metadata": {"next_cursor": "MORE"}}

    monkeypatch.setattr(c, "_post", fake_post)
    assert len(c.history("C0", pages=2)) == 2
```

`conftest.py` (so tests import project modules without packaging):

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_slack.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'slack'`

- [ ] **Step 3: Write minimal implementation**

`slack.py`:

```python
"""Slack Web API client for the RiseRidge Sales pipeline.

Read-only in Phase 1. Write methods (chat.postMessage, the three-step file
upload, reactions) arrive with post.py in Phase 2.
"""

import json
import os
import urllib.parse
import urllib.request

API = "https://slack.com/api/"
DEFAULT_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


class SlackError(Exception):
    pass


def load_env(path=DEFAULT_ENV):
    """Parse a KEY=VALUE .env file. Blank lines and # comments ignored."""
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class SlackClient:
    def __init__(self, token=None, env_path=DEFAULT_ENV):
        if token is None:
            token = load_env(env_path).get("SLACK_BOT_TOKEN")
        if not token:
            raise SlackError("SLACK_BOT_TOKEN missing")
        self.token = token

    def _post(self, method, params):
        req = urllib.request.Request(
            API + method,
            data=urllib.parse.urlencode(params).encode(),
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def api(self, method, params=None):
        r = self._post(method, params or {})
        if not r.get("ok"):
            raise SlackError("%s failed: %s" % (method, r.get("error")))
        return r

    def auth_test(self):
        return self.api("auth.test")

    def history(self, channel, limit=200, pages=0):
        """Return messages newest-first. pages=0 fetches every page."""
        out, cursor, n = [], "", 0
        while True:
            p = {"channel": channel, "limit": str(limit)}
            if cursor:
                p["cursor"] = cursor
            r = self.api("conversations.history", p)
            out.extend(r.get("messages", []))
            cursor = (r.get("response_metadata") or {}).get("next_cursor", "")
            n += 1
            if not cursor or (pages and n >= pages):
                return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_slack.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Verify against the live API**

Run: `python -c "import slack; print(slack.SlackClient().auth_test()['user'])"`
Expected: prints `riseridge_sales`

- [ ] **Step 6: Commit**

```bash
git add slack.py tests/test_slack.py conftest.py
git commit -m "feat: add read-only Slack Web API client"
```

---

### Task 2: Booked-lead parsing

**Files:**
- Create: `leads.py`
- Create: `tests/test_leads.py`
- Create: `fixtures/booked_marcio.txt`
- Create: `fixtures/booked_collapsed.txt`

**Interfaces:**
- Consumes: `slack.SlackClient.history` from Task 1.
- Produces: `Lead` dataclass with fields `thread_ts, name, email, domain, business_type, track, budget, frustration, tried, decision_role, urgency, timezone, manager, funnel, site_title, site_description`. Functions `normalize_domain(raw: str) -> str | None`, `is_test_lead(name: str, email: str) -> bool`, `field(text: str, label: str) -> str`, `track_for(business_type: str) -> str`, `parse_booked_message(msg: dict) -> Lead | None`, `scan(client, channel, pages=1) -> list[Lead]`.

`parse_booked_message` returns `None` for any message that is not a booked appointment or is a test lead.

- [ ] **Step 1: Create the real-message fixtures**

`fixtures/booked_marcio.txt` — exact text of the 2026-08-02 booking:

```
*Appointment booked from the SEO Funnel*

*Client's name:* Jordan alvarez Alvarez
*Email:* <mailto:owner@example.com|owner@example.com>

*Timezone:* America/New_York
*Manager:* Sample Manager
*Funnel:* seo

*Created on:* 2026-08-02T10:20:17.176Z

*utm_source:* Instagram_Reels
*utm_medium:* [18.07] | 35  | US | 2 new bold cuption

*Questionnaire:*

*What type of business do you run?:* Real estate (agent, broker, property management)
*Your business website:* <https://Www.exampleRealty.com>

*In 1-2 sentences, what's your biggest frustration with getting new clients or patients right now?* We need. More 

*What have you already tried for marketing? (Select all that apply):* SEO (agency, freelancer, or in-house),Facebook/Instagram ads

*How much are you currently spending (or willing to invest) on marketing each month?:* $3,000+ per month

*When it comes to business decisions like this, how does your decision-making process work?:* I'm the sole decision-maker

*If this is the right fit for your business, when are you looking to get started?:* I could start within the next 2 weeks

#GSMAppointment #GSMSEO
```

`fixtures/booked_collapsed.txt` — the real malformed case where the business-type answer is empty and the next label collapses onto its line, plus an email in the website field:

```
*Appointment booked from the SEO Funnel*

*Client's name:* Pat Owner
*Email:* <mailto:pat@example.org|pat@example.org>

*Timezone:* America/Denver
*Manager:* Sergey
*Funnel:* seo

*Questionnaire:*

*What type of business do you run?:* *Your business website:* <mailto:Info@example.org|Info@example.org>

*How much are you currently spending (or willing to invest) on marketing each month?:* $1,000 - $2,000 per month
```

- [ ] **Step 2: Write the failing test**

`tests/test_leads.py`:

```python
import pathlib

import leads

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


def msg(fixture, ts="1785666152.880169", attachments=None):
    return {
        "ts": ts,
        "text": (FIX / fixture).read_text(encoding="utf-8"),
        "attachments": attachments or [],
    }


# --- normalize_domain -------------------------------------------------------

def test_normalize_strips_scheme_www_and_case():
    assert leads.normalize_domain("<https://Www.exampleRealty.com>") == "examplerealty.com"


def test_normalize_handles_slack_link_with_label():
    assert leads.normalize_domain("<http://Beauty4everslc.com|Beauty4everslc.com>") == "beauty4everslc.com"


def test_normalize_ignores_leading_free_text():
    assert leads.normalize_domain("Wild <http://removals.co.uk|removals.co.uk>") == "removals.co.uk"


def test_normalize_rejects_email_in_website_field():
    assert leads.normalize_domain("<mailto:Info@example.com|Info@example.com>") is None


def test_normalize_reads_bare_domain_without_link():
    assert leads.normalize_domain("goodfellowsauto.com") == "goodfellowsauto.com"


def test_normalize_drops_path_and_query():
    assert leads.normalize_domain("<https://example.com/pricing?a=1>") == "example.com"


def test_normalize_returns_none_on_empty():
    assert leads.normalize_domain("") is None
    assert leads.normalize_domain("n/a") is None


# --- is_test_lead ----------------------------------------------------------

def test_test_lead_detected_by_name():
    assert leads.is_test_lead("Anatoliy Test Labinskiy", "a@real.com") is True


def test_test_lead_detected_by_internal_email():
    assert leads.is_test_lead("Real Person", "dmitriy@gsmgrowthagency.com") is True


def test_real_lead_not_flagged():
    assert leads.is_test_lead("Jordan alvarez Alvarez", "owner@example.com") is False


# --- field ----------------------------------------------------------------

def test_field_handles_colon_inside_bold():
    t = "*Client's name:* Jordan alvarez Alvarez"
    assert leads.field(t, "Client's name") == "Jordan alvarez Alvarez"


def test_field_handles_question_mark_label_without_colon():
    t = "*In 1-2 sentences, what's your biggest frustration?* We need more"
    assert leads.field(t, "In 1-2 sentences, what's your biggest frustration?") == "We need more"


def test_field_returns_empty_when_next_label_collapsed_onto_line():
    t = "*What type of business do you run?:* *Your business website:* <https://x.com>"
    assert leads.field(t, "What type of business do you run?") == ""


def test_field_unescapes_html_entities():
    t = "*What type of business do you run?:* Wellness &amp; therapy (spa)"
    assert leads.field(t, "What type of business do you run?") == "Wellness & therapy (spa)"


def test_field_missing_label_returns_empty():
    assert leads.field("*A:* 1", "Nope") == ""


# --- track_for ------------------------------------------------------------

def test_ecommerce_answer_selects_ecom_track():
    assert leads.track_for("E-commerce or online-only business") == "ecom"


def test_home_services_answer_selects_local_track():
    assert leads.track_for("Home services (contractor, HVAC, plumber, electrician, etc.)") == "local"


def test_unknown_business_type_defaults_to_local():
    assert leads.track_for("") == "local"


# --- parse_booked_message -------------------------------------------------

def test_parses_real_booking_end_to_end():
    lead = leads.parse_booked_message(msg("booked_marcio.txt"))
    assert lead.name == "Jordan alvarez Alvarez"
    assert lead.email == "owner@example.com"
    assert lead.domain == "examplerealty.com"
    assert lead.business_type.startswith("Real estate")
    assert lead.track == "local"
    assert lead.budget == "$3,000+ per month"
    assert lead.decision_role == "I'm the sole decision-maker"
    assert lead.urgency == "I could start within the next 2 weeks"
    assert lead.manager == "Sample Manager"
    assert lead.thread_ts == "1785666152.880169"


def test_collapsed_message_yields_no_domain_and_empty_business_type():
    lead = leads.parse_booked_message(msg("booked_collapsed.txt"))
    assert lead.domain is None
    assert lead.business_type == ""
    assert lead.track == "local"
    assert lead.budget == "$1,000 - $2,000 per month"


def test_non_booked_message_returns_none():
    assert leads.parse_booked_message({"ts": "1", "text": "*New Lead from the SEO Funnel*\n"}) is None


def test_stuck_appointment_message_returns_none():
    t = "*Appointment stuck in booked for more than 5 days*\n\nClient's name: X\n"
    assert leads.parse_booked_message({"ts": "1", "text": t}) is None


def test_test_lead_returns_none():
    t = (FIX / "booked_marcio.txt").read_text(encoding="utf-8").replace(
        "owner@example.com", "anatolii@gsmgrowthagency.com")
    assert leads.parse_booked_message({"ts": "1", "text": t}) is None


def test_attachment_supplies_site_title_and_description():
    att = [{"title": "Example Realty Team: Houses for Sale",
            "text": "Find Houses for Sale &amp; Real Estate"}]
    lead = leads.parse_booked_message(msg("booked_marcio.txt", attachments=att))
    assert lead.site_title == "Example Realty Team: Houses for Sale"
    assert "Real Estate" in lead.site_description


def test_email_is_not_taken_from_the_website_field():
    """Anchored per label: with no Email label, a mailto: in the website field
    must NOT become the lead's email."""
    t = ("*Appointment booked from the SEO Funnel*\n\n"
         "*Client's name:* Pat Owner\n\n"
         "*Your business website:* <mailto:info@somebiz.com|info@somebiz.com>\n")
    lead = leads.parse_booked_message({"ts": "1", "text": t})
    assert lead is not None
    assert lead.email != "info@somebiz.com"
    assert lead.email == ""


def test_vsl_funnel_booking_is_also_parsed():
    t = (FIX / "booked_marcio.txt").read_text(encoding="utf-8").replace(
        "*Appointment booked from the SEO Funnel*",
        "*Appointment booked from the VSL Funnel*")
    assert leads.parse_booked_message({"ts": "1", "text": t}) is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_leads.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'leads'`

- [ ] **Step 4: Write minimal implementation**

`leads.py`:

```python
"""Parse booked-appointment messages from #sales-pipeline into Lead records.

Message shapes verified against live channel history 2026-08-04. Only
'*Appointment booked ...' messages are leads; 'New Lead ...' is a form fill and
'Appointment stuck ...' is a follow-up nag.
"""

import argparse
import html
import re
from dataclasses import dataclass, asdict

BOOKED_PREFIX = "*Appointment booked"
TEST_PATTERN = re.compile(r"test|gsmgrowthagency", re.I)
SLACK_LINK = re.compile(r"<(?:https?://)([^>|]+)(?:\|[^>]*)?>")
BARE_DOMAIN = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})\b", re.I)
EMAIL_IN_TEXT = re.compile(r"mailto:([^|>\s]+)")
ECOM_ANSWERS = {"e-commerce or online-only business"}

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

    def as_dict(self):
        return asdict(self)


def field(text, label):
    """Extract a *Label:* value. Anchored per label so a malformed line in one
    field cannot shift another. Returns '' if absent or if the next label
    collapsed onto this line."""
    pat = re.compile(r"\*" + re.escape(label) + r":?\*:?[ \t]*(.*)")
    m = pat.search(text)
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
    if host.startswith("www."):
        host = host[4:]
    if "." not in host or host.endswith(".") or host.startswith("."):
        return None
    return host


def is_test_lead(name, email):
    return bool(TEST_PATTERN.search(name or "") or TEST_PATTERN.search(email or ""))


def track_for(business_type):
    return "ecom" if (business_type or "").strip().lower() in ECOM_ANSWERS else "local"


def parse_booked_message(msg):
    text = msg.get("text") or ""
    if not text.startswith(BOOKED_PREFIX):
        return None

    name = field(text, L_NAME)
    email_raw = field(text, L_EMAIL)
    # Anchored to the Email label ONLY. Never scan the whole message: a booked
    # message with no Email label but a mailto: in the website field would
    # otherwise yield the business's contact address as the lead's email, and
    # could corrupt is_test_lead in both directions.
    m = EMAIL_IN_TEXT.search(email_raw)
    email = m.group(1) if m else email_raw
    if is_test_lead(name, email):
        return None

    business_type = field(text, L_BTYPE)
    atts = msg.get("attachments") or []
    att = atts[0] if atts else {}

    return Lead(
        thread_ts=msg.get("ts", ""),
        name=name,
        email=email,
        domain=normalize_domain(field(text, L_SITE)),
        business_type=business_type,
        track=track_for(business_type),
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

    env = slack.load_env()
    got = scan(slack.SlackClient(), env["SALES_PIPELINE_CHANNEL"], pages=a.pages)
    if a.json:
        print(json.dumps([x.as_dict() for x in got], indent=2))
        return
    print("%d booked leads" % len(got))
    for x in got:
        # Do not truncate the domain: a clipped domain reads as a parse bug.
        print("  %-24s %-40s %-6s %s" % (
            x.name[:24], x.domain or "(no domain)", x.track, x.budget))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_leads.py -v`
Expected: PASS, 25 passed

- [ ] **Step 6: Verify against the live channel**

Run: `python leads.py --pages 2`
Expected: prints booked leads with domains and tracks; `Jordan alvarez Alvarez` / `examplerealty.com` / `local` / `$3,000+ per month` appears.

- [ ] **Step 7: Commit**

```bash
git add leads.py tests/test_leads.py fixtures/booked_marcio.txt fixtures/booked_collapsed.txt
git commit -m "feat: parse booked-appointment messages into Lead records"
```

---

### Task 3: Evidence schema and gate

**Files:**
- Create: `evidence.py`
- Create: `tests/test_evidence.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Evidence` class with `.load(path) -> Evidence` (classmethod), `.get(dotted: str) -> object | None` returning the `value` of a metric or `None`, `.has(dotted: str) -> bool`, `.present_sections() -> set[str]`, `.validate() -> None` raising `EvidenceError`. Module constant `SECTION_REQUIREMENTS: dict[str, tuple[str, ...]]` mapping a section id to the dotted paths it needs. `EvidenceError(Exception)`.

`get("traffic.monthly_organic_visits")` reads `data["traffic"]["monthly_organic_visits"]["value"]` and returns `None` if any level is missing or the value is `None`.

- [ ] **Step 1: Write the failing test**

`tests/test_evidence.py`:

```python
import json

import pytest

import evidence


def base():
    return {
        "domain": "example.com",
        "business_name": "Example",
        "generated_at": "2026-08-04T00:00:00Z",
        "traffic": {
            "monthly_organic_visits": {"value": 10800, "source": "x", "pulled_at": "y"},
            "ranking_keyword_count": {"value": None},
        },
    }


def test_get_returns_metric_value():
    e = evidence.Evidence(base())
    assert e.get("traffic.monthly_organic_visits") == 10800


def test_get_returns_none_for_null_value():
    assert evidence.Evidence(base()).get("traffic.ranking_keyword_count") is None


def test_get_returns_none_for_missing_path():
    assert evidence.Evidence(base()).get("backlinks.total_backlinks") is None


def test_get_returns_none_for_missing_intermediate():
    assert evidence.Evidence(base()).get("nope.nothing.here") is None


def test_has_is_false_for_null_value():
    assert evidence.Evidence(base()).has("traffic.ranking_keyword_count") is False


def test_has_is_true_for_real_value():
    assert evidence.Evidence(base()).has("traffic.monthly_organic_visits") is True


def test_get_handles_plain_scalar_not_wrapped_in_metric():
    e = evidence.Evidence({"domain": "d", "business_name": "b",
                           "generated_at": "g", "vertical": "dentists"})
    assert e.get("vertical") == "dentists"


def test_validate_passes_with_required_fields():
    evidence.Evidence(base()).validate()


@pytest.mark.parametrize("missing", ["domain", "business_name", "generated_at"])
def test_validate_rejects_missing_required_field(missing):
    d = base()
    del d[missing]
    with pytest.raises(evidence.EvidenceError) as e:
        evidence.Evidence(d).validate()
    assert missing in str(e.value)


@pytest.mark.parametrize("blank", ["domain", "business_name", "generated_at"])
def test_validate_rejects_empty_required_field(blank):
    d = base()
    d[blank] = ""
    with pytest.raises(evidence.EvidenceError):
        evidence.Evidence(d).validate()


# --- falsy-but-real values (load-bearing) ---------------------------------
# has() and present_sections() must test `is not None`, never truthiness. A
# scorecard pillar legitimately scoring 0 must keep its section.

def test_get_returns_zero_as_a_real_value():
    e = evidence.Evidence({"domain": "d", "business_name": "b",
                           "generated_at": "g",
                           "scorecard": {"content_quality": {"value": 0}}})
    assert e.get("scorecard.content_quality") == 0
    assert e.has("scorecard.content_quality") is True


def test_get_returns_false_as_a_real_value():
    e = evidence.Evidence({"domain": "d", "business_name": "b",
                           "generated_at": "g",
                           "technical": {"ai_crawler_access": {"value": False}}})
    assert e.get("technical.ai_crawler_access") is False
    assert e.has("technical.ai_crawler_access") is True


def test_section_with_only_a_zero_metric_still_renders():
    e = evidence.Evidence({"domain": "d", "business_name": "b",
                           "generated_at": "g",
                           "scorecard": {"content_quality": {"value": 0}}})
    assert "scorecard" in e.present_sections()


def test_get_returns_none_for_dict_leaf_without_value_key():
    e = evidence.Evidence({"domain": "d", "business_name": "b",
                           "generated_at": "g",
                           "technical": {"structured_data": {"schema_types": ["FAQPage"]}}})
    assert e.get("technical.structured_data") is None


def test_get_returns_none_for_empty_dict_leaf():
    e = evidence.Evidence({"domain": "d", "business_name": "b",
                           "generated_at": "g", "technical": {"structured_data": {}}})
    assert e.get("technical.structured_data") is None


def test_present_sections_includes_satisfied_section():
    d = base()
    d["traffic"]["traffic_value_usd"] = {"value": 61900}
    assert "traffic" in evidence.Evidence(d).present_sections()


def test_present_sections_excludes_section_with_no_data():
    assert "paid" not in evidence.Evidence(base()).present_sections()


def test_present_sections_excludes_section_with_all_null():
    d = base()
    d["paid"] = {"estimated_monthly_spend_usd": {"value": None}, "paid_keywords": []}
    assert "paid" not in evidence.Evidence(d).present_sections()


def test_present_sections_includes_list_backed_section():
    d = base()
    d["competitors"] = [{"domain": "rival.com", "monthly_visits": 21200}]
    assert "competitors" in evidence.Evidence(d).present_sections()


def test_load_reads_json_file(tmp_path):
    p = tmp_path / "e.json"
    p.write_text(json.dumps(base()), encoding="utf-8")
    assert evidence.Evidence.load(p).get("domain") == "example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_evidence.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'evidence'`

- [ ] **Step 3: Write minimal implementation**

`evidence.py`:

```python
"""The evidence contract.

Every number the audit can print lives in an evidence file and carries its own
source. The rule the renderer enforces: a null value omits its section. Nothing
is estimated, interpolated, or invented, because these figures get quoted out
loud on a sales call.
"""

import json

REQUIRED = ("domain", "business_name", "generated_at")

# A section renders only if at least one of its dotted paths has a real value.
SECTION_REQUIREMENTS = {
    "traffic": ("traffic.monthly_organic_visits", "traffic.ranking_keyword_count",
                "traffic.traffic_value_usd"),
    "brand_split": ("brand_split.brand_pct", "brand_split.nonbrand_pct"),
    "position_buckets": ("position_buckets.1-3", "position_buckets.4-10",
                         "position_buckets.11-20", "position_buckets.21-50",
                         "position_buckets.51-100"),
    "money_keywords": ("money_keywords",),
    "backlinks": ("backlinks.referring_domains", "backlinks.total_backlinks",
                  "backlinks.authority", "backlinks.trust"),
    "paid": ("paid.estimated_monthly_spend_usd", "paid.paid_keywords",
             "paid.landing_pages"),
    "competitors": ("competitors",),
    "ai_visibility": ("ai_visibility.platforms",),
    "scorecard": ("scorecard.content_quality", "scorecard.authority",
                  "scorecard.user_experience", "scorecard.ai_visibility"),
    "technical": ("technical.ai_crawler_access", "technical.structured_data",
                  "technical.core_web_vitals"),
}


class EvidenceError(Exception):
    pass


class Evidence:
    def __init__(self, data):
        self.data = data

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def get(self, dotted):
        """Value at a dotted path. Unwraps {'value': x} metrics. None if absent.

        A dict leaf without a 'value' key returns None rather than the raw dict.
        Absent is the safe failure: a renderer handed a dict would print its repr
        into a client-facing PDF. Non-empty lists pass through unchanged, since
        money_keywords, competitors, top_anchors and ai_visibility.platforms are
        legitimately list-valued.

        Falsy-but-real values survive. get() returns 0 and False as themselves,
        and has() tests `is not None`, so a pillar legitimately scoring 0 keeps
        its section instead of silently vanishing.
        """
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        if isinstance(node, dict):
            if "value" not in node:
                return None
            node = node["value"]
        if isinstance(node, (list, tuple, dict)) and not node:
            return None
        return node

    def has(self, dotted):
        return self.get(dotted) is not None

    def present_sections(self):
        return {s for s, paths in SECTION_REQUIREMENTS.items()
                if any(self.has(p) for p in paths)}

    def validate(self):
        for key in REQUIRED:
            if not self.data.get(key):
                raise EvidenceError("required field missing or empty: %s" % key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_evidence.py -v`
Expected: PASS, 16 passed

- [ ] **Step 5: Commit**

```bash
git add evidence.py tests/test_evidence.py
git commit -m "feat: add evidence contract with null-omits-section gate"
```

---

### Task 4: Brand stylesheet and cached font embedding

**Files:**
- Create: `templates/brand.css`
- Create: `fonts.py`
- Create: `tests/test_fonts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `fonts.font_css(cache_path=None, refresh=False) -> str` returning a string of `@font-face` rules with base64 `woff2` data, cached to `state/fonts.css` after the first call. `fonts.CACHE` default path constant.

- [ ] **Step 1: Write the failing test**

`tests/test_fonts.py`:

```python
import fonts


def test_font_css_uses_cache_without_network(tmp_path, monkeypatch):
    cache = tmp_path / "fonts.css"
    cache.write_text("@font-face{font-family:'Hanken Grotesk';}", encoding="utf-8")

    def boom(url):
        raise AssertionError("network hit despite warm cache")

    monkeypatch.setattr(fonts, "get", boom)
    assert "Hanken Grotesk" in fonts.font_css(cache_path=cache)


def test_font_css_writes_cache_on_first_call(tmp_path, monkeypatch):
    cache = tmp_path / "fonts.css"
    css = ("@font-face{font-family:'Hanken Grotesk';font-style:normal;"
           "font-weight:400;src:url(https://x/a.woff2) format('woff2');"
           "unicode-range:U+0000-00FF;}")
    monkeypatch.setattr(fonts, "get",
                        lambda u: css.encode() if "googleapis" in u else b"FONTBYTES")
    out = fonts.font_css(cache_path=cache)
    assert "base64," in out
    assert cache.exists()
    assert "base64," in cache.read_text(encoding="utf-8")


def test_font_css_keeps_unicode_range(tmp_path, monkeypatch):
    cache = tmp_path / "fonts.css"
    css = ("@font-face{font-family:'Space Mono';font-style:normal;font-weight:400;"
           "src:url(https://x/a.woff2) format('woff2');unicode-range:U+0000-00FF;}")
    monkeypatch.setattr(fonts, "get",
                        lambda u: css.encode() if "googleapis" in u else b"B")
    assert "unicode-range:U+0000-00FF" in fonts.font_css(cache_path=cache)


def test_font_css_skips_unwanted_unicode_ranges(tmp_path, monkeypatch):
    cache = tmp_path / "fonts.css"
    css = ("@font-face{font-family:'Space Mono';font-style:normal;font-weight:400;"
           "src:url(https://x/cyr.woff2) format('woff2');unicode-range:U+0400-045F;}")
    monkeypatch.setattr(fonts, "get",
                        lambda u: css.encode() if "googleapis" in u else b"B")
    assert fonts.font_css(cache_path=cache).strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fonts.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'fonts'`

- [ ] **Step 3: Write minimal implementation**

`fonts.py`. Adapted from `D:\Claude Code\searchatlas\reports\embed_fonts.py`, which already targets these three families. Two changes: it returns a string instead of mutating a report in place, and it caches so a render does not re-download nine woff2 files.

```python
"""Base64-inline the RiseRidge brand fonts as @font-face rules.

Chrome --print-to-pdf will not fetch remote Google Fonts in headless mode and
silently falls back to Times/Arial. Two rules, both learned the hard way on the
OLASBET and Trybello report pipelines:

  1. Request ONE weight per CSS call. Multi-weight calls (and the whole css2
     API) return VARIABLE fonts, which Chrome fails to embed.
  2. Keep `unicode-range` on every rule. Several subsets for one weight without
     it makes the last rule shadow the others and silently breaks the font.

All three families are SIL Open Font License.
"""

import base64
import os
import re
import urllib.request

FAMILIES = {
    "Cormorant+Garamond": [500, 600, 700],
    "Hanken+Grotesk": [400, 500, 600, 700],
    "Space+Mono": [400, 700],
}
CSS_URLS = [
    "https://fonts.googleapis.com/css?family=%s:%d&display=swap" % (fam, w)
    for fam, ws in FAMILIES.items()
    for w in ws
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Keep a face if its unicode-range covers basic Latin or the start of Latin
# Extended-A. Decided by codepoint membership, NOT by substring-matching
# Google's range strings: that approach silently dropped every latin-ext face
# once Google's latin-ext range changed from U+0100-024F to U+0100-02BA.
# Verified 2026-08-04: css?family=... returns cyrillic-ext, vietnamese,
# latin-ext and latin blocks, identically with or without a subset= param.
# Vietnamese starts at U+0102 so it matches neither probe and is correctly
# rejected.
WANTED_CODEPOINTS = (0x0041, 0x0100)
BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "state", "fonts.css")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _range_covers(unicode_range, codepoints):
    """True if the CSS unicode-range string covers any of the given codepoints."""
    for part in unicode_range.split(","):
        part = part.strip()
        if not part.upper().startswith("U+"):
            continue
        body = part[2:]
        try:
            if "-" in body:
                lo_s, hi_s = body.split("-", 1)
                lo, hi = int(lo_s, 16), int(hi_s, 16)
            else:
                lo = hi = int(body, 16)
        except ValueError:
            continue
        if any(lo <= cp <= hi for cp in codepoints):
            return True
    return False


def _build():
    blocks = []
    for u in CSS_URLS:
        blocks += re.findall(r"@font-face\s*\{[^}]+\}", get(u).decode("utf-8"))

    out, seen = [], set()
    for b in blocks:
        fam = re.search(r"font-family:\s*'([^']+)'", b)
        wgt = re.search(r"font-weight:\s*([\d ]+)", b)
        sty = re.search(r"font-style:\s*(\w+)", b)
        url = re.search(r"url\((https://[^)]+\.woff2)\)", b)
        rng = re.search(r"unicode-range:\s*([^;]+);", b)
        if not (fam and url and rng):
            continue
        ur = rng.group(1).strip()
        if not _range_covers(ur, WANTED_CODEPOINTS):
            continue
        key = (fam.group(1), (wgt.group(1) if wgt else "400").strip(),
               sty.group(1) if sty else "normal", ur)
        if key in seen:
            continue
        seen.add(key)
        b64 = base64.b64encode(get(url.group(1))).decode("ascii")
        out.append(
            "@font-face{font-family:'%s';font-style:%s;font-weight:%s;"
            "font-display:block;src:url(data:font/woff2;base64,%s) format('woff2');"
            "unicode-range:%s;}" % (key[0], key[2], key[1], b64, ur)
        )
    return "\n".join(out)


def _usable(path):
    """A cache is trustworthy only if it exists, is non-empty, and holds a face.

    Without this, an empty or truncated cache is returned forever with no error,
    which is the same silent-wrong-font failure this module exists to prevent.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return "@font-face" in fh.read()
    except OSError:
        return False


def font_css(cache_path=None, refresh=False):
    path = str(cache_path or CACHE)
    if not refresh and _usable(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    css = _build()
    if "@font-face" not in css:
        raise RuntimeError(
            "font build produced no @font-face rules; refusing to cache. "
            "Google Fonts response shape or unicode-range filter has changed.")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Atomic: a process killed mid-write must not leave a truncated cache that
    # reads as valid next time.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(css)
    os.replace(tmp, path)
    return css


if __name__ == "__main__":
    print("%.1f KB cached to %s" % (len(font_css(refresh=True)) / 1024, CACHE))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fonts.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Warm the real cache**

Run: `python fonts.py`
Expected: prints a size over 200 KB and writes `state/fonts.css`.

- [ ] **Step 6: Write the brand stylesheet**

`templates/brand.css`:

```css
/* RiseRidge audit report. US Letter, print-first. */
@page { size: Letter; margin: 0; }

:root {
  --pine:  #1E3A2E;
  --brass: #A9874E;
  --ink:   #15140F;
  --ivory: #F4F0E8;
  --mute:  #56514a;
  --faint: #8a8276;
  --rule:  #d4cfc4;
  --bad:   #8c3b2f;
}

* { box-sizing: border-box; }

html, body { margin: 0; padding: 0; }

body {
  font-family: 'Hanken Grotesk', Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.55;
  color: var(--ink);
  background: #fff;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

/* One .page per printed sheet. */
.page {
  position: relative;
  width: 8.5in;
  height: 11in;
  padding: 0.72in 0.8in 0.62in;
  page-break-after: always;
  overflow: hidden;
}
.page:last-of-type { page-break-after: auto; }

.page-footer {
  position: absolute;
  left: 0.8in; right: 0.8in; bottom: 0.38in;
  font-size: 7.5pt;
  letter-spacing: 0.04em;
  color: var(--faint);
  border-top: 1px solid var(--rule);
  padding-top: 6px;
}

h1, h2, h3 { font-family: 'Cormorant Garamond', serif; font-weight: 600; margin: 0; }
h1 { font-size: 30pt; line-height: 1.12; color: var(--pine); }
h2 { font-size: 21pt; line-height: 1.18; color: var(--pine); margin-bottom: 14px; }
h3 { font-size: 12pt; color: var(--ink); margin: 18px 0 6px; }

.eyebrow {
  font-family: 'Space Mono', monospace;
  font-size: 7.5pt;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--brass);
  margin-bottom: 10px;
}

p { margin: 0 0 10px; }
strong { font-weight: 600; }

/* Cover */
.cover { background: var(--pine); color: var(--ivory); }
.cover h1 { color: var(--ivory); font-size: 34pt; }
.cover .brandmark { height: 34px; margin-bottom: 0.9in; }
.cover .client { font-size: 15pt; color: var(--brass); margin-top: 6px; }
.cover .lede { max-width: 5.4in; margin-top: 22px; color: #cfd8d0; font-size: 10.5pt; }
.cover .meta { position: absolute; left: 0.8in; bottom: 1.0in; font-size: 9pt; color: #a9b6ac; }
.cover .page-footer { color: #7d8c82; border-top-color: #34513f; }

/* Findings list */
ul.findings { list-style: none; margin: 0; padding: 0; }
ul.findings > li {
  position: relative;
  padding-left: 20px;
  margin-bottom: 11px;
}
ul.findings > li::before {
  content: ""; position: absolute; left: 0; top: 7px;
  width: 7px; height: 7px; background: var(--brass);
}

/* Stat tiles */
.stats { display: flex; gap: 14px; margin: 16px 0 4px; }
.stat { flex: 1; background: var(--ivory); padding: 14px 14px 12px; }
.stat .k {
  font-family: 'Space Mono', monospace;
  font-size: 7pt; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--mute);
}
.stat .v {
  font-family: 'Cormorant Garamond', serif;
  font-size: 26pt; line-height: 1.05; color: var(--pine); margin-top: 4px;
}

/* Scorecard */
.scores { display: flex; gap: 14px; margin: 16px 0; }
.score { flex: 1; border-top: 3px solid var(--brass); padding-top: 10px; }
.score .k {
  font-family: 'Space Mono', monospace; font-size: 7pt;
  letter-spacing: 0.12em; text-transform: uppercase; color: var(--mute);
}
.score .v { font-family: 'Cormorant Garamond', serif; font-size: 30pt; color: var(--pine); }
.score .band { font-size: 8pt; letter-spacing: 0.1em; text-transform: uppercase; color: var(--bad); }

/* Data tables */
table { width: 100%; border-collapse: collapse; margin: 14px 0; }
th {
  font-family: 'Space Mono', monospace;
  font-size: 7pt; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--mute); text-align: left;
  border-bottom: 1.5px solid var(--pine);
  padding: 0 8px 6px 0;
}
td {
  font-size: 9.5pt; padding: 7px 8px 7px 0;
  border-bottom: 1px solid var(--rule); vertical-align: top;
}
td.num { font-family: 'Space Mono', monospace; white-space: nowrap; }
tr.self td { background: var(--ivory); font-weight: 600; }

/* Callout */
.callout {
  background: var(--ivory);
  border-left: 3px solid var(--brass);
  padding: 13px 16px;
  margin: 15px 0;
  font-size: 9.5pt;
}
.callout .t {
  font-family: 'Space Mono', monospace; font-size: 7pt;
  letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--brass); margin-bottom: 5px;
}
```

- [ ] **Step 7: Commit**

```bash
git add fonts.py tests/test_fonts.py templates/brand.css
git commit -m "feat: add brand stylesheet and cached font embedding"
```

---

### Task 5: Template assembly and token gate

**Files:**
- Create: `templates/base.html`
- Create: `templates/sections/01_cover.html`
- Create: `templates/sections/02_exec_summary.html`
- Create: `templates/sections/04_scorecard.html`
- Create: `templates/sections/07_position_buckets.html`
- Create: `render.py`
- Create: `tests/test_render.py`

**Interfaces:**
- Consumes: `evidence.Evidence` from Task 3, `fonts.font_css` from Task 4.
- Produces: `render.build_html(ev: Evidence, tokens: dict, section_files: list[str] | None = None) -> str`, `render.strip_absent_sections(html: str, present: set[str]) -> str`, `render.assert_no_tokens(html: str) -> None`, `render.RenderError(Exception)`, `render.fmt(n, kind="int") -> str` where kind is one of `int`, `k`, `usd`, `pct`.

Section files carry `<!--SECTION:id-->...<!--/SECTION:id-->` markers matching `evidence.SECTION_REQUIREMENTS` keys. Sections whose id is absent from `present` are removed before the token gate runs, so their tokens never need values.

This task delivers the three *kinds* of section — narrative (`02`), stat-grid (`04`), data-table (`07`) — plus the cover. Task 8 (Phase 2) adds the remaining eight sections, each an instance of one of these kinds.

- [ ] **Step 1: Write the failing test**

`tests/test_render.py`:

```python
import pytest

import evidence
import render


def ev(extra=None):
    d = {"domain": "example.com", "business_name": "Example",
         "generated_at": "2026-08-04T00:00:00Z"}
    d.update(extra or {})
    return evidence.Evidence(d)


# --- fmt ------------------------------------------------------------------

def test_fmt_int_adds_thousands_separator():
    assert render.fmt(90500) == "90,500"


def test_fmt_k_abbreviates_thousands():
    assert render.fmt(10800, "k") == "10.8K"


def test_fmt_usd_prefixes_and_abbreviates():
    assert render.fmt(61900, "usd") == "$61.9K"


def test_fmt_pct_appends_sign():
    assert render.fmt(95, "pct") == "95%"


def test_fmt_none_returns_placeholder_dash():
    assert render.fmt(None) == "\u2014"


# --- section stripping ----------------------------------------------------

def test_absent_section_is_removed():
    h = "A<!--SECTION:paid-->PAID<!--/SECTION:paid-->B"
    assert render.strip_absent_sections(h, present=set()) == "AB"


def test_present_section_is_kept():
    h = "A<!--SECTION:paid-->PAID<!--/SECTION:paid-->B"
    out = render.strip_absent_sections(h, present={"paid"})
    assert "PAID" in out
    assert "SECTION:paid" not in out


def test_stripping_handles_two_sections_independently():
    h = ("<!--SECTION:paid-->P<!--/SECTION:paid-->"
         "<!--SECTION:traffic-->T<!--/SECTION:traffic-->")
    out = render.strip_absent_sections(h, present={"traffic"})
    assert "T" in out and "P" not in out


def test_stripping_removes_tokens_inside_absent_section():
    h = "<!--SECTION:paid-->{{paid_spend}}<!--/SECTION:paid-->"
    assert "{{" not in render.strip_absent_sections(h, present=set())


# --- token gate -----------------------------------------------------------

def test_assert_no_tokens_passes_on_clean_html():
    render.assert_no_tokens("<p>done</p>")


def test_assert_no_tokens_raises_and_names_the_token():
    with pytest.raises(render.RenderError) as e:
        render.assert_no_tokens("<p>{{business_name}}</p>")
    assert "business_name" in str(e.value)


def test_assert_no_tokens_reports_every_leftover():
    with pytest.raises(render.RenderError) as e:
        render.assert_no_tokens("{{a}} {{b}}")
    msg = str(e.value)
    assert "a" in msg and "b" in msg


# --- build_html -----------------------------------------------------------

def test_build_html_substitutes_tokens():
    out = render.build_html(ev(), {"business_name": "Example",
                                   "domain": "example.com",
                                   "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    assert "Example" in out
    assert "{{" not in out


def test_build_html_inlines_font_css_not_a_remote_link():
    out = render.build_html(ev(), {"business_name": "E", "domain": "d",
                                   "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    assert "fonts.googleapis.com" not in out
    assert "@font-face" in out


def test_build_html_sets_letter_page_size():
    out = render.build_html(ev(), {"business_name": "E", "domain": "d",
                                   "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    assert "size: Letter" in out


def test_build_html_omits_scorecard_when_evidence_absent():
    out = render.build_html(ev(), {"business_name": "E", "domain": "d",
                                   "report_date": "August 2026"},
                            section_files=["01_cover.html", "04_scorecard.html"])
    assert "Visibility Scorecard" not in out


def _scorecard_ev():
    return ev({"scorecard": {"content_quality": {"value": 28, "basis": "b"},
                             "authority": {"value": 27, "basis": "b"},
                             "user_experience": {"value": 49, "basis": "b"}}})


def _scorecard_tokens():
    return {"business_name": "E", "domain": "d", "report_date": "August 2026",
            "score_content": "28", "score_content_band": "Weak",
            "score_authority": "27", "score_authority_band": "Weak",
            "score_ux": "49", "score_ux_band": "Fair",
            "scorecard_explanations_html": "<li>x</li>"}


def test_build_html_includes_scorecard_when_evidence_present():
    out = render.build_html(_scorecard_ev(), _scorecard_tokens(),
                            section_files=["01_cover.html", "04_scorecard.html"])
    assert "Visibility Scorecard" in out
    assert "28" in out


def test_build_html_raises_on_unknown_token():
    with pytest.raises(render.RenderError):
        render.build_html(ev(), {}, section_files=["01_cover.html"])


def test_build_html_raises_when_present_section_lacks_its_tokens():
    """A section kept by the section gate must still have every token supplied."""
    with pytest.raises(render.RenderError) as e:
        render.build_html(_scorecard_ev(),
                          {"business_name": "E", "domain": "d",
                           "report_date": "August 2026"},
                          section_files=["01_cover.html", "04_scorecard.html"])
    assert "score_content" in str(e.value)


def test_build_html_names_no_vendor_tools():
    out = render.build_html(_scorecard_ev(), _scorecard_tokens(),
                            section_files=["01_cover.html", "04_scorecard.html"])
    low = out.lower()
    for vendor in ("searchatlas", "ahrefs", "semrush", "majestic", "similarweb"):
        assert vendor not in low
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'render'`

- [ ] **Step 3: Write the base template**

`templates/base.html`:

```html
<meta charset="utf-8">
<title>{{business_name}} — AI Search Visibility Audit</title>
<style>
/*FONT_CSS*/
/*BRAND_CSS*/
</style>
<!--SECTIONS-->
```

- [ ] **Step 4: Write the cover section**

`templates/sections/01_cover.html`:

```html
<div class="page cover">
  <svg class="brandmark" viewBox="0 0 190 34" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="RiseRidge">
    <path d="M2 26 L11 8 L20 26 Z" fill="#A9874E"/>
    <path d="M18 26 L27 8 L36 26 Z" fill="#F4F0E8"/>
    <text x="46" y="24" font-family="Cormorant Garamond, serif" font-size="21" fill="#F4F0E8" letter-spacing="1.4">RiseRidge</text>
  </svg>

  <div class="eyebrow">AI Search Visibility Audit</div>
  <h1>{{business_name}}</h1>
  <div class="client">{{domain}}</div>

  <p class="lede">A visibility and growth review of how {{business_name}} is being found —
  or missed — across Google and the new AI answer engines: ChatGPT, Perplexity,
  Gemini, Copilot and Google AI Mode.</p>

  <div class="meta">
    Prepared by RiseRidge<br>
    riseridge.io &nbsp;·&nbsp; info@riseridge.io &nbsp;·&nbsp; +1 786 603 5778<br>
    {{report_date}}
  </div>

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
```

Note the brandmark is an inline `<svg>`, not an `<img>`. Chrome does not embed external SVGs into a PDF.

- [ ] **Step 5: Write the narrative-kind section**

`templates/sections/02_exec_summary.html`:

```html
<div class="page">
  <div class="eyebrow">01 &nbsp;/&nbsp; Overview</div>
  <h2>Executive Summary</h2>

  {{exec_summary_intro_html}}

  <ul class="findings">
    {{exec_summary_findings_html}}
  </ul>

  {{exec_summary_close_html}}

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
```

`{{exec_summary_intro_html}}`, `{{exec_summary_findings_html}}` and `{{exec_summary_close_html}}` are authored HTML fragments supplied by the narrative layer, not derived from evidence. This is the judgment section.

- [ ] **Step 6: Write the stat-grid-kind section**

`templates/sections/04_scorecard.html`:

```html
<!--SECTION:scorecard-->
<div class="page">
  <div class="eyebrow">02 &nbsp;/&nbsp; Diagnosis</div>
  <h2>The Visibility Scorecard</h2>

  <p>Here is how {{business_name}} scores across the pillars that decide whether a
  site wins in modern search. Anything below 60 needs work. Anything below 40 is
  costing revenue every month.</p>

  <div class="scores">
    <div class="score">
      <div class="k">Content Quality</div>
      <div class="v">{{score_content}}</div>
      <div class="band">{{score_content_band}}</div>
    </div>
    <div class="score">
      <div class="k">Authority</div>
      <div class="v">{{score_authority}}</div>
      <div class="band">{{score_authority_band}}</div>
    </div>
    <div class="score">
      <div class="k">User Experience</div>
      <div class="v">{{score_ux}}</div>
      <div class="band">{{score_ux_band}}</div>
    </div>
  </div>

  <h3>What this means in plain English</h3>
  <ul class="findings">
    {{scorecard_explanations_html}}
  </ul>

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
<!--/SECTION:scorecard-->
```

- [ ] **Step 7: Write the data-table-kind section**

`templates/sections/07_position_buckets.html`:

```html
<!--SECTION:position_buckets-->
<div class="page">
  <div class="eyebrow">04 &nbsp;/&nbsp; Opportunity</div>
  <h2>Where the Rankings Already Sit</h2>

  <p>Google already indexes and ranks these pages. They are simply not strong
  enough to reach page one yet. Moving even a fraction of them into the top ten
  is the fastest available gain.</p>

  <table>
    <thead>
      <tr><th>Ranking position</th><th># of keywords</th><th>What it means</th></tr>
    </thead>
    <tbody>
      <tr><td>Top 3 (positions 1&ndash;3)</td><td class="num">{{pos_1_3}}</td><td>Money terms already won</td></tr>
      <tr><td>Positions 4&ndash;10</td><td class="num">{{pos_4_10}}</td><td>One push and these hit the top 3</td></tr>
      <tr><td>Positions 11&ndash;20</td><td class="num">{{pos_11_20}}</td><td>Page 2 &mdash; very fixable</td></tr>
      <tr><td>Positions 21&ndash;50</td><td class="num">{{pos_21_50}}</td><td>Buried but recoverable</td></tr>
      <tr><td>Positions 51&ndash;100</td><td class="num">{{pos_51_100}}</td><td>The biggest short-term opportunity</td></tr>
    </tbody>
  </table>

  {{position_buckets_close_html}}

  <div class="page-footer">RiseRidge &nbsp;·&nbsp; AI Search Visibility Audit for {{business_name}} &nbsp;·&nbsp; Confidential</div>
</div>
<!--/SECTION:position_buckets-->
```

- [ ] **Step 8: Write the renderer**

`render.py`:

```python
"""Assemble the audit HTML from templates, evidence and authored narrative.

Two gates run here. Sections whose evidence is absent are stripped before
substitution, so a missing metric drops its section instead of printing a guess.
Then the token gate refuses to emit HTML that still contains {{placeholders}}.
"""

import html
import os
import re

import fonts

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(BASE, "templates")
SECTIONS = os.path.join(TEMPLATES, "sections")

DEFAULT_SECTIONS = [
    "01_cover.html",
    "02_exec_summary.html",
    "04_scorecard.html",
    "07_position_buckets.html",
]

TOKEN = re.compile(r"\{\{([a-z0-9_]+)\}\}", re.I)
DASH = "\u2014"


class RenderError(Exception):
    pass


def fmt(n, kind="int"):
    """Format a metric for print. None renders as an em dash."""
    if n is None:
        return DASH
    if kind == "pct":
        return "%g%%" % n
    if kind == "k":
        return "%.1fK" % (n / 1000.0) if n >= 1000 else "%g" % n
    if kind == "usd":
        return "$%.1fK" % (n / 1000.0) if n >= 1000 else "$%g" % n
    return "{:,}".format(int(n))


def _section_re(sid):
    return re.compile(
        r"<!--SECTION:%s-->.*?<!--/SECTION:%s-->" % (re.escape(sid), re.escape(sid)),
        re.S,
    )


def strip_absent_sections(html, present):
    """Remove every marked section whose id is not in `present`, then drop the
    markers of the ones that stay."""
    for sid in sorted(set(re.findall(r"<!--SECTION:([a-z0-9_\-]+)-->", html, re.I))):
        if sid not in present:
            html = _section_re(sid).sub("", html)
    html = re.sub(r"<!--/?SECTION:[a-z0-9_\-]+-->", "", html, flags=re.I)
    return html


def assert_no_tokens(html):
    left = sorted(set(TOKEN.findall(html)))
    if left:
        raise RenderError("unreplaced tokens: %s" % ", ".join(left))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_html(ev, tokens, section_files=None):
    """Assemble the report HTML.

    Substitution is a SINGLE regex pass, not a loop of str.replace calls: a
    substituted value must never be rescanned, or a token value containing the
    literal text {{other}} would silently pick up other's real data.

    Values are HTML-escaped by default because business names come from
    prospect-typed form data and routinely contain '&' ("Smith & Sons
    Plumbing"). Tokens named *_html carry authored markup and pass through raw.
    """
    names = section_files or DEFAULT_SECTIONS
    body = "\n".join(_read(os.path.join(SECTIONS, n)) for n in names)

    shell = _read(os.path.join(TEMPLATES, "base.html"))
    shell = shell.replace("/*FONT_CSS*/", fonts.font_css())
    shell = shell.replace("/*BRAND_CSS*/", _read(os.path.join(TEMPLATES, "brand.css")))
    doc = shell.replace("<!--SECTIONS-->", body)

    # Strip before substituting, so tokens inside a removed section never need
    # values.
    doc = strip_absent_sections(doc, ev.present_sections())

    missing = []

    def _sub(m):
        key = m.group(1)
        if key not in tokens:
            missing.append(key)
            return m.group(0)
        v = tokens[key]
        if v is None:
            return ""
        s = str(v)
        return s if key.endswith("_html") else html.escape(s, quote=False)

    doc = TOKEN.sub(_sub, doc)
    if missing:
        raise RenderError("unreplaced tokens: %s" % ", ".join(sorted(set(missing))))

    # Deliberately NOT calling assert_no_tokens here. Escaping leaves braces
    # intact, so a prospect whose business name contained {{x}} would falsely
    # trip it. The single pass plus `missing` is the gate.
    return doc
```

- [ ] **Step 9: Run test to verify it passes**

Run: `python -m pytest tests/test_render.py -v`
Expected: PASS, 20 passed

- [ ] **Step 10: Commit**

```bash
git add render.py tests/test_render.py templates/base.html templates/sections/
git commit -m "feat: assemble audit HTML with section and token gates"
```

---

### Task 6: PDF generation and embed verification

**Files:**
- Modify: `render.py` (append functions; do not change existing ones)
- Modify: `tests/test_render.py` (append tests)

**Interfaces:**
- Consumes: `render.build_html` from Task 5.
- Produces: `render.html_to_pdf(html_path: str, pdf_path: str) -> None`, `render.verify_pdf(pdf_path: str, must_contain: tuple[str, ...] = ()) -> dict` returning `{"pages": int, "fonts": set[str], "text": str}` and raising `RenderError` on a missing brand font, zero pages, or a missing required phrase. `render.FORBIDDEN_VENDORS` tuple.

`verify_pdf` exists because Chrome fails silently: it will happily emit a PDF with Arial substituted for the brand fonts and blank boxes where external SVGs were.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py`. `evidence`, `render` and `pytest` are already imported at the top of that file from Task 5; do not re-import them.

```python
def _cover_pdf(tmp_path, name, business="Example"):
    """Render the cover to a PDF and return its path."""
    e = evidence.Evidence({"domain": "example.com", "business_name": business,
                           "generated_at": "2026-08-04T00:00:00Z"})
    html = render.build_html(e, {"business_name": business,
                                 "domain": "example.com",
                                 "report_date": "August 2026"},
                            section_files=["01_cover.html"])
    hp = tmp_path / (name + ".html")
    hp.write_text(html, encoding="utf-8")
    pp = tmp_path / (name + ".pdf")
    render.html_to_pdf(str(hp), str(pp))
    return pp


def test_pdf_is_produced_with_brand_fonts_embedded(tmp_path):
    pp = _cover_pdf(tmp_path, "a")
    assert pp.exists() and pp.stat().st_size > 10_000

    info = render.verify_pdf(str(pp), must_contain=("Example", "riseridge.io"))
    assert info["pages"] >= 1
    joined = "".join(info["fonts"]).replace("-", "").replace(" ", "")
    assert "HankenGrotesk" in joined
    assert "CormorantGaramond" in joined


def test_verify_pdf_raises_on_missing_required_phrase(tmp_path):
    pp = _cover_pdf(tmp_path, "b")
    with pytest.raises(render.RenderError) as exc:
        render.verify_pdf(str(pp), must_contain=("ThisPhraseIsNotPresent",))
    assert "ThisPhraseIsNotPresent" in str(exc.value)


def test_verify_pdf_rejects_vendor_tool_names(tmp_path):
    """The audit must never name the tooling behind it, even by accident."""
    pp = _cover_pdf(tmp_path, "c", business="Powered by Ahrefs")
    with pytest.raises(render.RenderError) as exc:
        render.verify_pdf(str(pp))
    assert "ahrefs" in str(exc.value).lower()


def test_html_to_pdf_raises_when_chrome_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "CHROME", str(tmp_path / "nope.exe"))
    with pytest.raises(render.RenderError) as exc:
        render.html_to_pdf(str(tmp_path / "x.html"), str(tmp_path / "x.pdf"))
    assert "Chrome not found" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render.py -k pdf -v`
Expected: FAIL, `AttributeError: module 'render' has no attribute 'html_to_pdf'`

- [ ] **Step 3: Append the implementation to `render.py`**

```python
import subprocess

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
REQUIRED_FONTS = ("HankenGrotesk", "CormorantGaramond")
FORBIDDEN_VENDORS = ("searchatlas", "ahrefs", "semrush", "majestic", "similarweb")


def html_to_pdf(html_path, pdf_path):
    """Print HTML to PDF with headless Chrome. Flag set is load-bearing:
    --no-pdf-header-footer removes Chrome's URL/date furniture, --no-margins
    lets @page own the geometry."""
    if not os.path.exists(CHROME):
        raise RenderError("Chrome not found at %s" % CHROME)
    cmd = [
        CHROME, "--headless=new", "--disable-gpu",
        "--no-pdf-header-footer", "--no-margins",
        "--print-to-pdf=%s" % pdf_path,
        html_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    if not os.path.exists(pdf_path):
        raise RenderError("Chrome produced no PDF: %s"
                          % r.stderr.decode("utf-8", "replace")[:400])


def verify_pdf(pdf_path, must_contain=()):
    """Assert the PDF actually embedded what it should.

    Chrome degrades silently: it substitutes Arial for fonts it failed to embed
    and leaves blanks where external SVGs were, producing a file that looks fine
    on screen and wrong in print.
    """
    import fitz

    doc = fitz.open(pdf_path)
    found_fonts, text = set(), []
    for page in doc:
        for f in page.get_fonts():
            found_fonts.add(f[3])
        text.append(page.get_text())
    doc.close()
    body = "\n".join(text)

    if len(text) == 0:
        raise RenderError("PDF has zero pages")

    flat = "".join(found_fonts).replace("-", "").replace(" ", "")
    missing = [f for f in REQUIRED_FONTS if f not in flat]
    if missing:
        raise RenderError(
            "brand fonts not embedded: %s (found: %s). Chrome fell back; check "
            "that fonts are base64-inlined one weight per call."
            % (", ".join(missing), ", ".join(sorted(found_fonts)) or "none"))

    low = body.lower()
    hits = [v for v in FORBIDDEN_VENDORS if v in low]
    if hits:
        raise RenderError("client-facing PDF names vendor tooling: %s"
                          % ", ".join(hits))

    absent = [p for p in must_contain if p not in body]
    if absent:
        raise RenderError("expected text missing from PDF: %s" % ", ".join(absent))

    return {"pages": len(text), "fonts": found_fonts, "text": body}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render.py -v`
Expected: PASS, 23 passed

- [ ] **Step 5: Commit**

```bash
git add render.py tests/test_render.py
git commit -m "feat: render PDF via headless Chrome with embed verification"
```

---

### Task 7: PeterMD acceptance test

**Files:**
- Create: `fixtures/petermd_evidence.json`
- Create: `tests/test_acceptance_petermd.py`
- Create: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 3-6.
- Produces: nothing new. This task proves the engine reproduces the reference report.

The evidence file is hand-built from the figures printed in `C:\Users\dimaf\Downloads\PeterMD AI Search Audit - RiseRidge (Corrected).pdf`. Sources read `reference-report` because these numbers are transcribed from an existing artefact, not pulled live.

- [ ] **Step 1: Create the evidence fixture**

`fixtures/petermd_evidence.json`:

```json
{
  "domain": "getpetermd.com",
  "business_name": "PeterMD",
  "generated_at": "2026-07-01T00:00:00Z",
  "vertical": "mens-trt-clinic",
  "business_type": "local",
  "traffic": {
    "monthly_organic_visits": {"value": 10800, "source": "reference-report", "pulled_at": "2026-07-01"},
    "ranking_keyword_count": {"value": 3800, "source": "reference-report", "pulled_at": "2026-07-01"},
    "traffic_value_usd": {"value": 61900, "source": "reference-report", "pulled_at": "2026-07-01"}
  },
  "brand_split": {
    "brand_pct": {"value": 95, "source": "derived", "method": "brand-token match over ranking keywords"},
    "nonbrand_pct": {"value": 5, "source": "derived", "method": "brand-token match over ranking keywords"}
  },
  "position_buckets": {
    "1-3": {"value": 77},
    "4-10": {"value": 171},
    "11-20": {"value": 268},
    "21-50": {"value": 834},
    "51-100": {"value": 1700},
    "source": "reference-report",
    "pulled_at": "2026-07-01"
  },
  "money_keywords": [
    {"keyword": "enclomiphene", "volume": 90500, "position": 41},
    {"keyword": "at home testosterone test", "volume": 6600, "position": 54},
    {"keyword": "buy testosterone online", "volume": 3600, "position": 24},
    {"keyword": "trt cost", "volume": 2900, "position": 42},
    {"keyword": "how much does trt cost", "volume": 2900, "position": 51},
    {"keyword": "testosterone test kit", "volume": 2900, "position": 58},
    {"keyword": "online testosterone therapy", "volume": 2400, "position": 71},
    {"keyword": "how to get trt", "volume": 2400, "position": 56}
  ],
  "backlinks": {
    "referring_domains": {"value": 50, "source": "reference-report"},
    "total_backlinks": {"value": 214, "source": "reference-report"},
    "authority": {"value": 71.8, "metric_name": "Domain Rating", "source": "reference-report"},
    "trust": {"value": 5, "metric_name": "Trust Flow", "source": "reference-report"},
    "top_anchors": [
      {"anchor": "telegram @seo_anomaly - seo backlinks, pbn, traffic boost, link indexing", "count": 1500}
    ],
    "referring_domain_categories": [
      "B2B agency reviews", "personal finance", "PR industry",
      "market research", "graduation frames", "travel history", "affiliate software"
    ]
  },
  "competitors": [
    {"domain": "trtnation.com", "monthly_visits": 21200, "ranking_keywords": 5500},
    {"domain": "maleexcel.com", "monthly_visits": 1500, "ranking_keywords": 52},
    {"domain": "novagenix.org", "monthly_visits": 1300, "ranking_keywords": 3600},
    {"domain": "keeps.com", "monthly_visits": 12400, "ranking_keywords": 95},
    {"domain": "thencginstitute.com", "monthly_visits": 2400, "ranking_keywords": 9100}
  ],
  "ai_visibility": {
    "probed_at": "2026-07-01T00:00:00Z",
    "vertical_cache": "mens-trt-clinic",
    "questions": [
      "where should I get TRT online",
      "what's the cheapest online TRT clinic",
      "where can I get enclomiphene prescribed online"
    ],
    "platforms": [
      {"platform": "Gemini", "brand_named": true, "visibility_pct": 40, "sentiment_pct": 73, "topics_present": 2, "reading": "Present but shallow", "competitors_named": ["TRTNation", "Marek"]},
      {"platform": "Google AI Mode", "brand_named": true, "visibility_pct": 40, "sentiment_pct": 75, "topics_present": 2, "reading": "Present but shallow", "competitors_named": ["TRTNation"]},
      {"platform": "ChatGPT", "brand_named": false, "visibility_pct": 20, "sentiment_pct": 65, "topics_present": 1, "reading": "Barely visible", "competitors_named": ["TRTNation", "Marek"]},
      {"platform": "Perplexity", "brand_named": false, "visibility_pct": 20, "sentiment_pct": 67, "topics_present": 1, "reading": "Barely visible", "competitors_named": ["TRTNation", "Marek"]},
      {"platform": "Copilot", "brand_named": false, "visibility_pct": 20, "sentiment_pct": 71, "topics_present": 1, "reading": "Barely visible", "competitors_named": ["TRTNation"]}
    ]
  },
  "paid": {
    "estimated_monthly_spend_usd": {"value": null},
    "paid_keywords": [],
    "landing_pages": []
  },
  "scorecard": {
    "content_quality": {"value": 28, "basis": "pages do not answer the questions customers ask; no topical depth signal"},
    "authority": {"value": 27, "basis": "DR 71.8 across 50 domains, none in health or medical categories; trust flow 5"},
    "user_experience": {"value": 49, "basis": "site functional but slow; several technical fixes outstanding"},
    "ai_visibility": {"value": null, "basis": "not scored in the reference report"}
  },
  "technical": {
    "ai_crawler_access": {"value": null},
    "structured_data": {"value": null},
    "core_web_vitals": {"value": null}
  }
}
```

- [ ] **Step 2: Write the failing acceptance test**

`tests/test_acceptance_petermd.py`:

```python
"""Acceptance: the engine must reproduce the reference PeterMD audit.

Bar for correctness. The reference PDF was hand-built in Google Docs; this
renders the same figures through the pipeline and checks they survive.
"""

import pathlib

import pytest

import evidence
import render

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    ev = evidence.Evidence.load(FIX / "petermd_evidence.json")
    ev.validate()
    tokens = {
        "business_name": "PeterMD",
        "domain": "getpetermd.com",
        "report_date": "July 2026",
        "exec_summary_intro_html": "<p>PeterMD has built real momentum.</p>",
        "exec_summary_findings_html":
            "<li>95% of every visit comes from people who already knew the name.</li>"
            "<li>Content is ranking for the wrong audience.</li>"
            "<li>A spam link network is attached to the domain.</li>"
            "<li>The money keywords sit on page 4 through 6.</li>",
        "exec_summary_close_html": "<p>The gap is closable. This audit lays out how.</p>",
        "score_content": render.fmt(ev.get("scorecard.content_quality")),
        "score_content_band": "Weak",
        "score_authority": render.fmt(ev.get("scorecard.authority")),
        "score_authority_band": "Weak",
        "score_ux": render.fmt(ev.get("scorecard.user_experience")),
        "score_ux_band": "Fair",
        "scorecard_explanations_html":
            "<li><strong>Content Quality:</strong> pages are not answering what customers ask.</li>"
            "<li><strong>Authority:</strong> high authority from the wrong categories carries no topical trust.</li>"
            "<li><strong>User Experience:</strong> the site works but is slower than it should be.</li>",
        "pos_1_3": render.fmt(ev.get("position_buckets.1-3")),
        "pos_4_10": render.fmt(ev.get("position_buckets.4-10")),
        "pos_11_20": render.fmt(ev.get("position_buckets.11-20")),
        "pos_21_50": render.fmt(ev.get("position_buckets.21-50")),
        "pos_51_100": render.fmt(ev.get("position_buckets.51-100")),
        "position_buckets_close_html":
            "<p>Move even a fraction into the top 10 and non-brand traffic doubles.</p>",
    }
    html = render.build_html(ev, tokens)
    d = tmp_path_factory.mktemp("petermd")
    hp, pp = d / "audit.html", d / "audit.pdf"
    hp.write_text(html, encoding="utf-8")
    render.html_to_pdf(str(hp), str(pp))
    return render.verify_pdf(str(pp)), pp


def test_pdf_has_expected_page_count(rendered):
    info, _ = rendered
    assert 4 <= info["pages"] <= 14


def test_brand_fonts_embedded(rendered):
    info, _ = rendered
    flat = "".join(info["fonts"]).replace("-", "").replace(" ", "")
    assert "HankenGrotesk" in flat
    assert "CormorantGaramond" in flat


@pytest.mark.parametrize("figure", ["77", "171", "268", "834", "1,700"])
def test_position_bucket_figures_survive_to_pdf(rendered, figure):
    info, _ = rendered
    assert figure in info["text"]


@pytest.mark.parametrize("figure", ["28", "27", "49"])
def test_scorecard_figures_survive_to_pdf(rendered, figure):
    info, _ = rendered
    assert figure in info["text"]


def test_client_identity_present(rendered):
    info, _ = rendered
    assert "PeterMD" in info["text"]
    assert "getpetermd.com" in info["text"]


def test_contact_details_present(rendered):
    info, _ = rendered
    assert "riseridge.io" in info["text"]
    assert "+1 786 603 5778" in info["text"]


def test_no_vendor_tooling_named(rendered):
    """verify_pdf already enforces this; asserted explicitly as the requirement."""
    info, _ = rendered
    low = info["text"].lower()
    for vendor in render.FORBIDDEN_VENDORS:
        assert vendor not in low


def test_null_evidence_blocks_are_absent_from_present_sections():
    """paid.* and technical.* are all null in the fixture, so the section gate
    must not offer them. Asserted on present_sections rather than on absent PDF
    text, because absent text would also pass if the section simply did not
    exist yet."""
    ev = evidence.Evidence.load(FIX / "petermd_evidence.json")
    present = ev.present_sections()
    assert "paid" not in present
    assert "technical" not in present
    assert {"scorecard", "position_buckets", "traffic", "backlinks",
            "competitors", "money_keywords", "brand_split",
            "ai_visibility"} <= present


def test_evidence_gate_rejects_incomplete_file():
    bad = evidence.Evidence({"domain": "x.com"})
    with pytest.raises(evidence.EvidenceError):
        bad.validate()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_acceptance_petermd.py -v`
Expected: FAIL, the fixture or a token is missing. Fix whatever it names until green. Do not weaken an assertion to make it pass.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS, all tests across all five test files.

- [ ] **Step 5: Render the PDF for human inspection**

```bash
python -c "import pathlib,evidence,render; ev=evidence.Evidence.load('fixtures/petermd_evidence.json'); print('sections:', sorted(ev.present_sections()))"
```

Expected: prints the sections the fixture supports, which must include `scorecard` and `position_buckets` and must exclude `paid` and `technical`.

- [ ] **Step 6: Write the README**

`README.md`:

```markdown
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

## Not yet built (Phase 2)

`collect.py`, `aiprobe.py`, `dossier.py`, `pricing.py`, `post.py`, and the
remaining eight report sections. Phase 2 is blocked on the SearchAtlas
capability spike.

## Constraints

- Never name the tooling in client-facing output.
- `.env` is git-ignored and holds the Slack bot token. Never commit it.
- Phase 1 performs no Slack writes.
```

- [ ] **Step 7: Commit**

```bash
git add fixtures/petermd_evidence.json tests/test_acceptance_petermd.py README.md
git commit -m "test: reproduce the reference PeterMD audit end-to-end"
```

---

## Self-Review

**Spec coverage.** Phase 1 covers, from the spec: the architecture and directory layout, the evidence contract and its null-omits-section rule, the Slack channel and lead message shape including every parsing edge case listed (casing, `www.`, email-in-website-field, leading free text, collapsed labels, test leads), Letter page size, brand palette and fonts, inline-SVG logo, the one-weight-per-call font rule, and three of the four error-handling gates (evidence, token, embed).

Deliberately deferred to Phase 2, and why: `collect.py` and the SearchAtlas data mapping (blocked on the capability spike), `aiprobe.py` and the vertical cache (needs the browser), `dossier.py` and `pricing.py` (pricing depends on dossier), `post.py` and the idempotency gate (the fourth gate; Phase 1 makes no writes by design), and report sections 3, 5, 6, 8, 9, 10, 11, 12. Section 8 (Paid vs Organic) is exercised negatively in Task 7, which asserts it stays absent while its evidence is null.

**Placeholder scan.** No TBD, TODO, "implement later", or "similar to Task N". Every code step carries runnable code. Task 5 states plainly that it delivers three section *kinds* and names which eight sections remain, rather than gesturing at "the rest".

**Type consistency.** Checked across tasks: `Evidence.get`/`.has`/`.present_sections`/`.validate` as used in Tasks 5, 6, 7 match Task 3. `render.fmt`, `build_html`, `strip_absent_sections`, `assert_no_tokens`, `RenderError` as used in Tasks 6 and 7 match Task 5. `html_to_pdf`/`verify_pdf`/`FORBIDDEN_VENDORS` in Task 7 match Task 6. `fonts.font_css(cache_path=, refresh=)` in Task 5 matches Task 4. `slack.SlackClient.history(channel, limit, pages)` used by `leads.scan` matches Task 1. Section ids in `evidence.SECTION_REQUIREMENTS` (`scorecard`, `position_buckets`, `paid`) match the `<!--SECTION:id-->` markers in Task 5's templates.

One deliberate deviation from the spec's build order: `render.py` is built before `collect.py`, so the evidence schema is exercised by a real render early rather than being designed against nothing.
