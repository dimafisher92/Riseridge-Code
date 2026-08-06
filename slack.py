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
    """Parse a KEY=VALUE .env file. Blank lines and # comments ignored.

    A missing file yields {} rather than raising. There is no .env on a hosted
    runner -- secrets arrive through the environment -- and opening it
    unconditionally turned a fully-configured run into a FileNotFoundError
    before it read a single message.

    This returns the FILE's contents only. Use `config()` to read a setting;
    it is the one that knows a real environment variable wins.
    """
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def config(key, default="", path=DEFAULT_ENV):
    """One setting: a real environment variable wins, else the .env file.

    Reading only the file meant CI secrets were invisible no matter how they
    were set, which is exactly how a hosted runner supplies them.

    An empty environment variable counts as unset, so an Actions secret that
    is declared but never given a value cannot blank out a real value from the
    file.
    """
    return os.environ.get(key) or load_env(path).get(key) or default


class SlackClient:
    def __init__(self, token=None, env_path=DEFAULT_ENV):
        if token is None:
            token = config("SLACK_BOT_TOKEN", path=env_path)
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
