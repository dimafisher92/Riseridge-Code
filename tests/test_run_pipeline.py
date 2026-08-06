"""The orchestrator.

Two properties carry the risk: both writes stay off unless explicitly enabled,
and one failing stage never silently produces a confident artefact.
"""

import json
import re

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
        if method == "files.getUploadURLExternal":
            return {"upload_url": "https://files.slack.test/u", "file_id": "F1"}
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


# --- the run log is world-readable on a public repo -------------------------

def _report_with_pii():
    return {"scanned": 1, "selected": 1,
            "skipped": [{"domain": "skipped-co.com", "name": "Alex Rivera",
                         "reason": "no backfill"}],
            "results": [{"domain": "acme.com", "name": "Jordan Alvarez",
                         "artefacts": {"pdf": "/s/prospects/acme.com/audit.pdf"},
                         "findings": ["95% of your traffic already knew you"],
                         "errors": ["collect: acme.com timed out"],
                         "posting": {"status": "dry-run"}}]}


def test_the_run_log_is_redacted_by_default():
    """Actions logs and artifacts are world-readable on a public repository.
    Prospect names and domains must not appear in either."""
    text = run_pipeline.format_run(_report_with_pii())
    for secret in ("acme.com", "Jordan Alvarez", "Alex Rivera",
                   "skipped-co.com"):
        assert secret not in text, "%s leaked into the run log" % secret


def test_redaction_covers_identifiers_inside_paths_and_errors():
    text = run_pipeline.format_run(_report_with_pii())
    assert "<prospect:" in text
    assert "timed out" in text, "the error itself must survive redaction"
    assert "audit.pdf" in text


def test_the_redaction_tag_is_stable_within_a_run():
    """A reader has to be able to follow one prospect through the log."""
    text = run_pipeline.format_run(_report_with_pii())
    tags = set(re.findall(r"<prospect:[0-9a-f]{8}>", text))
    acme = run_pipeline.redact("acme.com", {"acme.com"})
    assert acme in tags
    assert text.count(acme) >= 2


def test_findings_still_readable_after_redaction():
    """The point of the log is to show what happened."""
    text = run_pipeline.format_run(_report_with_pii())
    assert "95% of your traffic already knew you" in text
    assert "dry-run" in text


def test_reveal_is_opt_in_for_local_runs():
    text = run_pipeline.format_run(_report_with_pii(), reveal=True)
    assert "Jordan Alvarez" in text


# --- the destination is the lead's own thread --------------------------------

def test_artefacts_go_to_the_leads_thread_in_the_internal_channel(monkeypatch):
    """#sales-pipeline is private and fed by a Zapier app -- the prospect is not
    in it. The spec says all three artefacts go into the lead's thread there, so
    that thread is the internal destination and needs no separate channel."""
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    monkeypatch.setattr(run_pipeline.dossier_mod, "build",
                        lambda *a, **k: {"company": {}, "unknown_fields": [],
                                         "research_urls": {}, "limits": [],
                                         "pages_fetched": []})
    msg = {"ts": "100.0", "text": "*Appointment booked from the SEO Funnel*\n"
                                  "*Your business website:* <https://acme.com>"}
    client = FakeSlack(messages=[msg])
    report = run_pipeline.run(client=client, channel="C_PIPELINE", do_post=True,
                              chrome=False, probe=False, max_age_hours=0)
    posted = [p for m, p in client.calls if m == "chat.postMessage"]
    assert posted, "the artefacts must actually be delivered"
    assert all(p["channel"] == "C_PIPELINE" for p in posted)
    assert all(p["thread_ts"] == "100.0" for p in posted), (
        "replies belong in the lead's thread, not the channel root")
    assert report["results"][0]["posting"]["status"] == "posted"


def test_the_internal_channel_does_not_require_the_arming_switch(monkeypatch):
    """RR_POSTING_ARMED guards a prospect-visible surface. This channel is not
    one, so requiring it here was guarding against nothing."""
    monkeypatch.delenv(post_mod.ARMED_ENV, raising=False)
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    msg = {"ts": "100.0", "text": "*Appointment booked from the SEO Funnel*\n"
                                  "*Your business website:* <https://acme.com>"}
    client = FakeSlack(messages=[msg])
    report = run_pipeline.run(client=client, channel="C_PIPELINE", do_post=True,
                              chrome=False, probe=False, max_age_hours=0)
    assert report["results"][0]["posting"]["status"] == "posted"
    assert not report["results"][0]["errors"]


def test_a_dry_run_still_writes_nothing(monkeypatch):
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    msg = {"ts": "100.0", "text": "*Appointment booked from the SEO Funnel*\n"
                                  "*Your business website:* <https://acme.com>"}
    client = FakeSlack(messages=[msg])
    report = run_pipeline.run(client=client, channel="C_PIPELINE", chrome=False,
                              probe=False, max_age_hours=0)
    assert report["results"][0]["posting"]["status"] == "dry-run"
    assert not any(m == "chat.postMessage" for m, _ in client.calls)
    assert any("delivered nowhere" in n for n in report["notes"])


def test_format_run_shows_skips_and_errors():
    report = {"scanned": 2, "selected": 0,
              "skipped": [{"domain": None, "name": "X",
                           "reason": "no resolvable domain"}],
              "results": []}
    text = run_pipeline.format_run(report)
    assert "(no domain)" in text
    assert "no resolvable domain" in text


# --- the name that goes on the client-facing cover --------------------------

@pytest.mark.parametrize("title,expected", [
    ("Home | Acme Plumbing", "Acme Plumbing"),
    ("Acme Plumbing | Denver's Best Plumber", "Acme Plumbing"),
    ("Acme Plumbing - Home", "Acme Plumbing"),
    ("Welcome | Acme Plumbing | Official Site", "Acme Plumbing"),
    ("Acme Plumbing", "Acme Plumbing"),
    ("Acme Plumbing: Trusted Since 1998", "Acme Plumbing"),
])
def test_the_cover_name_is_cleaned_from_the_page_title(title, expected):
    """The funnel gives a <title> tag, not a company name. Printing it raw puts
    'Home | Acme Plumbing - Denver's Best Plumber' on the cover of a document
    the prospect is meant to take seriously."""
    assert run_pipeline.business_name_for(lead(site_title=title)) == expected


def test_a_title_of_only_generic_parts_falls_back_to_the_domain():
    assert run_pipeline.business_name_for(
        lead(site_title="Home | Welcome")) == "acme.com"


def test_no_title_falls_back_to_the_domain():
    assert run_pipeline.business_name_for(lead(site_title="")) == "acme.com"


def test_the_cleaned_name_reaches_the_report_and_the_thread_summary(monkeypatch):
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda d, name, **k: (dict(_evidence(),
                                                   business_name=name), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    l = lead(site_title="Home | Acme Plumbing")
    r = run_pipeline.process(l, probe=False, chrome=False,
                             fetch=lambda u: (None, ""))
    html = open(r["artefacts"]["html"], encoding="utf-8").read()
    assert "Acme Plumbing" in html
    assert "Home |" not in html
    assert "Acme Plumbing" in run_pipeline.summary_for(r, l)


def test_building_artefacts_with_nowhere_to_deliver_them_is_flagged(monkeypatch):
    """On a hosted runner they are discarded with the runner. Silently doing
    all the work and delivering nothing is the confusing failure."""
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    msg = {"ts": "100.0", "text": "*Appointment booked from the SEO Funnel*\n"
                                  "*Your business website:* <https://acme.com>"}
    report = run_pipeline.run(client=FakeSlack(messages=[msg]), channel="C1",
                              chrome=False, probe=False, max_age_hours=0)
    assert any("delivered nowhere" in n for n in report["notes"])
    assert "--post" in run_pipeline.format_run(report)


def test_posting_produces_no_delivered_nowhere_note(monkeypatch):
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    msg = {"ts": "100.0", "text": "*Appointment booked from the SEO Funnel*\n"
                                  "*Your business website:* <https://acme.com>"}
    report = run_pipeline.run(client=FakeSlack(messages=[msg]), channel="C1",
                              do_post=True, chrome=False, probe=False,
                              max_age_hours=0)
    assert report["notes"] == []


# --- surviving a runner that dies -------------------------------------------

def test_every_stage_announces_itself(monkeypatch, capsys):
    """A runner-loss killed a 45-minute run and left zero logs, so there was no
    way to tell which stage hung. Every stage must leave a trail."""
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    run_pipeline.process(lead(), probe=False, chrome=False,
                         fetch=lambda u: (None, ""))
    out = capsys.readouterr().out
    for stage in ("collect", "dossier", "render", "lead"):
        assert stage in out, "%s left no trace in the log" % stage


def test_a_failing_stage_says_so_in_the_log(monkeypatch, capsys):
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    run_pipeline.process(lead(), probe=False, chrome=False,
                         fetch=lambda u: (None, ""))
    assert "FAILED" in capsys.readouterr().out


def test_the_probe_is_skipped_once_the_budget_is_spent(monkeypatch, capsys):
    """A report without the AI section beats a job killed by a runner timeout."""
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    monkeypatch.setattr(run_pipeline, "LEAD_BUDGET_SECONDS", 0)
    called = []
    monkeypatch.setattr(run_pipeline.aiprobe, "probe_sources",
                        lambda *a, **k: called.append(1))
    r = run_pipeline.process(lead(), chrome=False, fetch=lambda u: (None, ""))
    assert called == [], "the probe must not run past the budget"
    assert any("budget" in e for e in r["errors"])
    assert "SKIPPED" in capsys.readouterr().out


def test_the_budget_does_not_skip_anything_when_there_is_time(monkeypatch):
    monkeypatch.setattr(run_pipeline.collect, "run",
                        lambda *a, **k: (_evidence(), "reused"))
    monkeypatch.setattr(run_pipeline.collect, "write_evidence", lambda ev, **k: "")
    called = []
    monkeypatch.setattr(run_pipeline.aiprobe, "probe_sources",
                        lambda *a, **k: called.append(1) or None)
    run_pipeline.process(lead(), chrome=False, fetch=lambda u: (None, ""))
    assert called == [1]
