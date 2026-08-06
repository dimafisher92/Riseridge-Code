"""Configuration loading.

Every one of these is a regression test for the same production failure: the
first live run on a hosted runner crashed with FileNotFoundError on a .env that
does not exist there, with all four required secrets correctly set in the
environment the whole time.
"""

import pytest

import post
import slack


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("SLACK_BOT_TOKEN", "SALES_PIPELINE_CHANNEL",
                "SLACK_BOT_USER_ID"):
        monkeypatch.delenv(key, raising=False)


def test_a_missing_dotenv_is_not_an_error(tmp_path):
    """There is no .env on a runner; secrets arrive through the environment.
    Opening it unconditionally killed the run before it read a message."""
    got = slack.load_env(str(tmp_path / "nope.env"))
    assert isinstance(got, dict)
    assert "SLACK_BOT_TOKEN" not in got


def test_environment_variables_are_read_when_there_is_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")
    assert slack.config("SLACK_BOT_TOKEN",
                        path=str(tmp_path / "nope.env")) == "xoxb-from-env"


def test_a_real_environment_variable_beats_the_file(monkeypatch, tmp_path):
    """The README already promised this. Only sa_client actually did it."""
    env = tmp_path / ".env"
    env.write_text("SLACK_BOT_TOKEN=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "from-env")
    assert slack.config("SLACK_BOT_TOKEN", path=str(env)) == "from-env"
    assert slack.load_env(str(env))["SLACK_BOT_TOKEN"] == "from-file", (
        "load_env stays a pure file parser; config() owns the precedence")


def test_the_file_is_still_read_when_the_variable_is_unset(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SLACK_BOT_TOKEN=from-file\nSALES_PIPELINE_CHANNEL=C1\n",
                   encoding="utf-8")
    got = slack.load_env(str(env))
    assert got["SLACK_BOT_TOKEN"] == "from-file"
    assert got["SALES_PIPELINE_CHANNEL"] == "C1"


def test_a_blank_actions_secret_does_not_shadow_the_file(monkeypatch, tmp_path):
    """An Actions secret that is declared but never set arrives as an empty
    string. Letting that win would blank a value the file supplies."""
    env = tmp_path / ".env"
    env.write_text("SALES_PIPELINE_CHANNEL=C_FROM_FILE\n", encoding="utf-8")
    monkeypatch.setenv("SALES_PIPELINE_CHANNEL", "")
    assert slack.config("SALES_PIPELINE_CHANNEL",
                        path=str(env)) == "C_FROM_FILE"


def test_comments_and_blank_lines_are_still_ignored(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# a comment\n\nSLACK_BOT_TOKEN=t\nnot-a-pair\n",
                   encoding="utf-8")
    got = slack.load_env(str(env))
    assert got["SLACK_BOT_TOKEN"] == "t"
    assert "not-a-pair" not in got


def test_the_client_builds_from_the_environment_alone(monkeypatch, tmp_path):
    """The exact production crash: SlackClient() on a runner with no .env."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")
    monkeypatch.setattr(slack, "DEFAULT_ENV", str(tmp_path / "nope.env"))
    assert slack.SlackClient().token == "xoxb-from-env"


def test_the_client_still_errors_when_the_token_is_genuinely_absent(monkeypatch,
                                                                    tmp_path):
    monkeypatch.setattr(slack, "DEFAULT_ENV", str(tmp_path / "nope.env"))
    with pytest.raises(slack.SlackError):
        slack.SlackClient()


def test_the_poster_reads_its_channel_from_the_environment(monkeypatch, tmp_path):
    """Even past the crash, reading only the file left the channel unset and
    the run exited claiming SALES_PIPELINE_CHANNEL was missing."""
    monkeypatch.setenv("SALES_PIPELINE_CHANNEL", "C_FROM_ENV")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "U_FROM_ENV")
    monkeypatch.setattr(slack, "DEFAULT_ENV", str(tmp_path / "nope.env"))

    class FakeClient:
        pass

    p = post.Poster(client=FakeClient())
    assert p.channel == "C_FROM_ENV"
    assert p.bot_user_id == "U_FROM_ENV"


def test_the_pipeline_resolves_its_channel_from_the_environment(monkeypatch,
                                                                tmp_path):
    import run_pipeline

    monkeypatch.setenv("SALES_PIPELINE_CHANNEL", "C_PIPELINE")
    monkeypatch.setattr(slack, "DEFAULT_ENV", str(tmp_path / "nope.env"))

    class FakeSlack:
        def __init__(self):
            self.channels = []

        def history(self, channel, limit=200, pages=0):
            self.channels.append(channel)
            return []

        def api(self, method, params=None):
            return {"ok": True, "messages": []}

    client = FakeSlack()
    report = run_pipeline.run(client=client, chrome=False, probe=False)
    assert client.channels == ["C_PIPELINE"], "channel came from the environment"
    assert report["selected"] == 0
