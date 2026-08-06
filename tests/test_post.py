"""Slack posting.

The failure mode this guards against is unrecoverable: a wrong or duplicated
message in a prospect's thread cannot be unsent. So the tests are mostly about
refusing to write.
"""

import pytest

import post
import slack


class FakeClient:
    """Records every API call. Raises if a test did not stub the reply."""

    def __init__(self, replies=None, fail=None):
        self.calls = []
        self._replies = replies if replies is not None else []
        self._fail = fail or {}

    def api(self, method, params=None):
        self.calls.append((method, params or {}))
        if method in self._fail:
            raise slack.SlackError(self._fail[method])
        if method == "conversations.replies":
            return {"messages": self._replies}
        if method == "files.getUploadURLExternal":
            return {"upload_url": "https://files.slack.test/upload",
                    "file_id": "F123"}
        return {"ok": True}

    def methods(self):
        return [m for m, _ in self.calls]


def poster(client=None, dry_run=True, **kw):
    return post.Poster(client=client or FakeClient(), channel="C1",
                       bot_user_id="U_BOT", dry_run=dry_run,
                       upload=lambda url, data, name: 200, **kw)


@pytest.fixture(autouse=True)
def disarm(monkeypatch):
    monkeypatch.delenv(post.ARMED_ENV, raising=False)


# --- posting is off by default ---------------------------------------------

def test_dry_run_is_the_default():
    assert post.Poster(client=FakeClient(), channel="C1",
                       bot_user_id="U").dry_run is True


def test_a_dry_run_performs_no_writes():
    c = FakeClient()
    p = poster(c)
    plan = p.publish("1.1", summary="hello", dossier_text="d", script_text="s")
    assert plan["status"] == "dry-run"
    assert c.methods() == ["conversations.replies"], \
        "a dry run must read only, never write"


def test_a_dry_run_still_reports_what_it_would_do():
    p = poster()
    plan = p.publish("1.1", summary="hello", dossier_text="d", script_text="s")
    actions = [s["action"] for s in plan["planned"]]
    assert actions == ["chat.postMessage", "chat.postMessage",
                       "chat.postMessage", "reactions.add"]
    assert "POSTING PLAN" in post.format_plan(plan)


def test_writing_without_the_env_switch_is_refused(monkeypatch):
    """Two switches have to agree: the caller's dry_run=False and the operator's
    explicit arming. One of them alone is not approval."""
    p = poster(dry_run=False)
    with pytest.raises(post.PostError) as exc:
        p.post_text("1.1", "hello")
    assert post.ARMED_ENV in str(exc.value)


def test_arming_the_env_alone_does_not_post(monkeypatch):
    monkeypatch.setenv(post.ARMED_ENV, "1")
    c = FakeClient()
    poster(c).publish("1.1", summary="hello")
    assert "chat.postMessage" not in c.methods()


def test_both_switches_together_do_post(monkeypatch):
    monkeypatch.setenv(post.ARMED_ENV, "1")
    c = FakeClient()
    plan = poster(c, dry_run=False).publish("1.1", summary="hello")
    assert plan["status"] == "posted"
    assert "chat.postMessage" in c.methods()


@pytest.mark.parametrize("value,armed", [
    ("1", True), ("true", True), ("YES", True),
    ("0", False), ("", False), ("no", False),
])
def test_armed_env_parsing(monkeypatch, value, armed):
    monkeypatch.setenv(post.ARMED_ENV, value)
    assert post.posting_armed() is armed


# --- idempotency is server-side --------------------------------------------

def test_an_existing_bot_reply_stops_a_repost():
    """The real guard. A local ledger cannot work: runners are ephemeral, and
    committing one would publish prospect names and emails to a public repo."""
    c = FakeClient(replies=[{"ts": "1.1"}, {"ts": "1.2", "user": "U_BOT"}])
    plan = poster(c, dry_run=False).publish("1.1", summary="hello")
    assert plan["status"] == "skipped"
    assert "already replied" in plan["reason"]


def test_a_human_reply_does_not_block_posting():
    c = FakeClient(replies=[{"ts": "1.1"}, {"ts": "1.2", "user": "U_HUMAN"}])
    assert poster(c).already_posted("1.1") is False


def test_the_source_message_itself_is_not_mistaken_for_a_reply():
    c = FakeClient(replies=[{"ts": "1.1", "user": "U_BOT"}])
    assert poster(c).already_posted("1.1") is False


def test_an_unreadable_thread_is_treated_as_unsafe_to_post():
    """Refusing to act on unknown state is the safe direction when the failure
    mode is a duplicate in a prospect's thread."""
    c = FakeClient(fail={"conversations.replies": "channel_not_found"})
    with pytest.raises(post.PostError):
        poster(c).already_posted("1.1")


def test_idempotency_is_checked_before_any_write(monkeypatch):
    monkeypatch.setenv(post.ARMED_ENV, "1")
    c = FakeClient(replies=[{"ts": "1.2", "user": "U_BOT"}])
    poster(c, dry_run=False).publish("1.1", summary="hi", script_text="s")
    assert c.methods() == ["conversations.replies"]


# --- the reaction marker ----------------------------------------------------

def test_the_reaction_is_added_after_posting(monkeypatch):
    monkeypatch.setenv(post.ARMED_ENV, "1")
    c = FakeClient()
    poster(c, dry_run=False).publish("1.1", summary="hello")
    assert c.methods()[-1] == "reactions.add"
    assert c.calls[-1][1]["name"] == post.MARKER_REACTION


def test_an_already_present_reaction_is_not_an_error(monkeypatch):
    """A previous run got this far. That is success, not a reason to abort."""
    monkeypatch.setenv(post.ARMED_ENV, "1")
    c = FakeClient(fail={"reactions.add": "already_reacted"})
    plan = poster(c, dry_run=False).publish("1.1", summary="hello")
    assert plan["status"] == "posted"


def test_other_reaction_errors_still_raise(monkeypatch):
    monkeypatch.setenv(post.ARMED_ENV, "1")
    c = FakeClient(fail={"reactions.add": "not_in_channel"})
    with pytest.raises(slack.SlackError):
        poster(c, dry_run=False).publish("1.1", summary="hello")


# --- file upload ------------------------------------------------------------

def test_upload_uses_the_current_three_step_flow(monkeypatch, tmp_path):
    """files.upload was retired."""
    monkeypatch.setenv(post.ARMED_ENV, "1")
    pdf = tmp_path / "audit.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    c = FakeClient()
    poster(c, dry_run=False).publish("1.1", summary="here it is",
                                     pdf_path=str(pdf),
                                     business_name="Acme")
    assert c.methods() == ["conversations.replies",
                           "files.getUploadURLExternal",
                           "files.completeUploadExternal",
                           "reactions.add"]
    assert "files.upload" not in c.methods()


def test_upload_is_threaded_and_carries_the_summary(monkeypatch, tmp_path):
    monkeypatch.setenv(post.ARMED_ENV, "1")
    pdf = tmp_path / "audit.pdf"
    pdf.write_bytes(b"%PDF")
    c = FakeClient()
    poster(c, dry_run=False).publish("1.1", summary="here it is",
                                     pdf_path=str(pdf))
    params = dict(c.calls)["files.completeUploadExternal"]
    assert params["thread_ts"] == "1.1"
    assert params["initial_comment"] == "here it is"
    assert params["channel_id"] == "C1"


def test_a_missing_pdf_falls_back_to_a_text_summary(monkeypatch):
    monkeypatch.setenv(post.ARMED_ENV, "1")
    c = FakeClient()
    plan = poster(c, dry_run=False).publish("1.1", summary="no pdf today",
                                            pdf_path="/nope/missing.pdf")
    assert "summary" in plan["posted"]
    assert "chat.postMessage" in c.methods()


def test_upload_failure_when_slack_returns_no_target(monkeypatch, tmp_path):
    monkeypatch.setenv(post.ARMED_ENV, "1")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    class NoTarget(FakeClient):
        def api(self, method, params=None):
            self.calls.append((method, params or {}))
            if method == "conversations.replies":
                return {"messages": []}
            if method == "files.getUploadURLExternal":
                return {}
            return {"ok": True}

    with pytest.raises(post.PostError):
        poster(NoTarget(), dry_run=False).upload_file("1.1", str(pdf), "t")


# --- message construction ---------------------------------------------------

def test_link_unfurling_is_disabled(monkeypatch):
    """The audit names the prospect's own domain; unfurling it would put a
    preview card of their site in their own thread."""
    monkeypatch.setenv(post.ARMED_ENV, "1")
    c = FakeClient()
    poster(c, dry_run=False).post_text("1.1", "see acme.com")
    params = dict(c.calls)["chat.postMessage"]
    assert params["unfurl_links"] == "false"
    assert params["unfurl_media"] == "false"


def test_internal_artefacts_are_labelled_internal(monkeypatch):
    monkeypatch.setenv(post.ARMED_ENV, "1")
    c = FakeClient()
    poster(c, dry_run=False).publish("1.1", summary="s", dossier_text="D",
                                     script_text="S")
    texts = [p["text"] for m, p in c.calls if m == "chat.postMessage"]
    assert any("internal" in t for t in texts[1:])


def test_channel_is_required():
    with pytest.raises(post.PostError):
        post.Poster(client=FakeClient(), channel="", bot_user_id="U")
