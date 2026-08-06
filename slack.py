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
