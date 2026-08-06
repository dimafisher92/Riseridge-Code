"""The orchestrator.

Two properties carry the risk: both writes stay off unless explicitly enabled,
and one failing stage never silently produces a confident artefact.
"""

import json

import pytest

import leads as leads_mod
import post as post_mod
import run_pipeline


def lead(ts="100.0", domain="acme.com", **kw):
    base = dict(thread_ts=ts, name="Jordan", email="j@example.com",
                domain=domain, business_type="Home services", track="local",
                budget="$1,000 - $2,000", frustration="not enough calls",
                tried="Google Ads", decision_role="I decide", urgency="ASAP",
                timezone="MST", manager="Sam", funnel="SEO")
    base.update(kw)
    return leads_mod.Lead(**base)


class FakeSlack:
    def __init__(self, messages=None, replies=None):
        self._messages = messages or []
        self._replies = replies or []
        self.calls = []

    def history(self, channel, limit=200, pages=0):
        return self._messages

    def api(self, method, params=None):
        self.calls.append((method, params or {}))
        if method == "conversations.replies":
            return {"messages": self._replies}
        return {"ok": True}


@pytest.fixture(autouse=True)
def disarm(monkeypatch, tmp_path):
    monkeypatch.delenv(post_mod.ARMED_ENV, raising=False)
    monkeypatch.setattr(run_pipeline, "OUT", str(tmp_path / "prospects"))


# --- no backfill ------------------------------------------------------------

def test_old_bookings_are_not_backfilled():
    """Volume fell from 110/month to 1; the backlog is mostly dead and the spec
    says new bookings only."""
    now = 1000 * 3600.0
    old = lead(ts=str(now - 200 * 3600))
    new = lead(ts=str(now - 1 * 3600), domain="fresh.com")
    fresh, skipped = run_pipeline.select([old, new], max_age_hours=48, now=now)
    assert [l.domain for l in fresh] == ["fresh.com"]
    assert "no backfill" in skipped[0][1]


def test_a_lead_without_a_domain_is_reported_not_audited():
    """Only 274 of 609 non-test bookings carry a website. An audit is
    impossible without one, so it goes to the operator instead."""
    fresh, skipped = run_pipeline.select([lead(domain=None)],
                                         max_age_hours=0, now=100.0)
    assert fresh == []
    assert "no resolvable domain" in skipped[0][1]


def test_the_cutoff_can_be_disabled():
    now = 1000 * 3600.0
    fresh, _ = run_pipeline.select([lead(ts=str(now - 900 * 3600))],
                                   max_age_hours=0, now=now)
    assert len(fresh) == 1


def test_an_unparseable_timestamp_is_skipped_not_crashed():
    fresh, skipped = run_pipeline.select([lead(ts="not-a-ts")],
                                         max_age_hours=48, now=100.0)
    assert fresh == []
    assert "unparseable" in skipped[0][1]


# --- category derivation ----------------------------------------------------

@pytest.mark.parametrize("business_type,expected", [
    ("Home services", "home services company"),
    ("Medical practice", "medical clinic"),
    ("Real estate", "real estate agent"),
    ("Legal services", "law firm"),
    ("E-commerce or online-only business", "online store"),
])
def test_category_derived_from_the_funnel_answer(business_type, expected):
    assert run_pipeline.category_for(lead(business_type=business_type)) == expected


def test_an_unmappable_business_type_yields_no_category():
    """Better to omit the AI section than to probe with a guessed category --
    the questions would measure the wrong market."""
    assert run_pipeline.category_for(lead(business_type="Something else")) == ""


def test_the_probe_is_skipped_when_no_category_can_be_named(tmp_path, monkeypatch):
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence",
                        lambda ev, **k: "")
    called = []
    monkeypatch.setattr(run_pipeline.aiprobe, "probe",
                        lambda *a, **k: called.append(1))
    r = run_pipeline.process(lead(business_type="Something else"),
                             fetch=lambda u: (None, ""), chrome=False)
    assert called == []
    assert any("no category" in e for e in r["errors"])


def _evidence():
    return {"domain": "acme.com", "business_name": "Acme",
            "generated_at": "2026-08-06T00:00:00Z",
            "traffic": {"ranking_keyword_count": {"value": 400}},
            "brand_split": {"brand_pct": {"value": 90},
                            "nonbrand_pct": {"value": 10}},
            "competitors": [{"domain": "rival.com"}]}


# --- stage isolation --------------------------------------------------------

def test_a_failing_collect_still_produces_a_script(monkeypatch):
    """A prospect whose data cannot be fetched still gets a usable brief."""
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("API down")))
    r = run_pipeline.process(lead(), fetch=lambda u: (None, ""), chrome=False)
    assert any("collect: API down" in e for e in r["errors"])
    assert "script" in r["artefacts"]
    assert "SALES SCRIPT" in r["script_text"]


def test_a_failing_dossier_does_not_stop_the_report(monkeypatch):
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence",
                        lambda ev, **k: "")
    monkeypatch.setattr(run_pipeline.dossier_mod, "build",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("dns")))
    r = run_pipeline.process(lead(), probe=False, chrome=False)
    assert any("dossier: dns" in e for e in r["errors"])
    assert "html" in r["artefacts"]


def test_errors_are_surfaced_in_the_thread_summary():
    r = {"findings": ["a finding"], "errors": ["collect: boom"]}
    text = run_pipeline.summary_for(r, lead())
    assert "Incomplete sections" in text
    assert "a finding" in text


def test_a_clean_run_does_not_mention_incomplete_sections():
    r = {"findings": ["a finding"], "errors": []}
    assert "Incomplete" not in run_pipeline.summary_for(r, lead())


# --- the two writes stay off ------------------------------------------------

def test_neither_write_is_enabled_by_default(monkeypatch):
    seen = {}

    def fake_run(domain, name, *, apply=False, sa=None):
        seen["apply"] = apply
        return _evidence(), "reused"

    monkeypatch.setattr(run_pipeline.collect, "run", fake_run)
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    client = FakeSlack(messages=[])
    report = run_pipeline.run(client=client, channel="C1", chrome=False,
                              probe=False)
    assert report["selected"] == 0
    assert not any(m == "chat.postMessage" for m, _ in client.calls)


def test_apply_is_threaded_through_to_the_one_write(monkeypatch):
    seen = {}

    def fake_run(domain, name, *, apply=False, sa=None):
        seen["apply"] = apply
        return _evidence(), "created"

    monkeypatch.setattr(run_pipeline.collect, "run", fake_run)
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    run_pipeline.process(lead(), apply=True, probe=False, chrome=False,
                         fetch=lambda u: (None, ""))
    assert seen["apply"] is True


def test_posting_stays_a_dry_run_without_the_flag(monkeypatch):
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    monkeypatch.setattr(run_pipeline.dossier_mod, "build",
                        lambda *a, **k: {"company": {}, "unknown_fields": [],
                                         "research_urls": {}, "limits": [],
                                         "pages_fetched": []})
    msg = {"ts": "100.0", "text": "*Appointment booked from the SEO Funnel*\n"
                                  "*Client's name:* Jordan\n"
                                  "*Your business website:* <https://acme.com>\n"
                                  "*What type of business do you run?:* Home services"}
    client = FakeSlack(messages=[msg])
    report = run_pipeline.run(client=client, channel="C1", chrome=False,
                              probe=False, max_age_hours=0)
    assert report["selected"] == 1
    assert report["results"][0]["posting"]["status"] == "dry-run"
    assert not any(m == "chat.postMessage" for m, _ in client.calls)


def test_the_run_report_shows_what_posting_would_have_done(monkeypatch):
    report = {"scanned": 1, "selected": 1, "skipped": [],
              "results": [{"domain": "acme.com", "name": "Jordan",
                           "artefacts": {"script": "/x/script.txt"},
                           "findings": ["a finding"], "errors": [],
                           "posting": {"status": "dry-run"}}]}
    text = run_pipeline.format_run(report)
    assert "dry-run" in text
    assert "a finding" in text
    assert "/x/script.txt" in text


def test_format_run_shows_skips_and_errors():
    report = {"scanned": 2, "selected": 0,
              "skipped": [{"domain": None, "name": "X",
                           "reason": "no resolvable domain"}],
              "results": []}
    text = run_pipeline.format_run(report)
    assert "(no domain)" in text
    assert "no resolvable domain" in text
