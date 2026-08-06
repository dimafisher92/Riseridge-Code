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
