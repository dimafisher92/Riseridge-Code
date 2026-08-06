"""Publish the three artefacts into the lead's Slack thread.

This is the only module in the pipeline that writes to Slack, and it is off by
default. The spec's rule stands: nothing reaches a prospect thread without
explicit approval.

**Idempotency is server-side, deliberately.** The spec put the ledger in
`state/leads.json`. That cannot work here. Runners are ephemeral, so the file
does not survive between runs, and committing it back would publish prospect
names, emails and domains into a public repository. Slack itself already holds
the authoritative answer to "did we post in this thread", so this asks Slack:

1. `conversations.replies` -- has this bot already replied in the thread? This
   is the real guard, and it is correct even for a first run on a fresh runner.
2. A reaction on the source message -- the visible marker, so a human scanning
   the channel can see which leads have been handled.

The reaction alone would not be enough: it can be removed by any member, and
adding it is a separate call that can fail after the post succeeded.
"""

import argparse
import json
import os
import urllib.error
import urllib.request

import slack

MARKER_REACTION = "white_check_mark"
ARMED_ENV = "RR_POSTING_ARMED"


class PostError(Exception):
    pass


def posting_armed():
    """True only when the operator has explicitly armed posting.

    Two independent switches have to agree -- this env var and the caller's own
    `dry_run=False` -- because the failure mode is unrecoverable: a wrong
    message in a prospect thread cannot be unsent.
    """
    return os.environ.get(ARMED_ENV, "").strip().lower() in ("1", "true", "yes")


def _raw_upload(url, data, filename):
    """POST file bytes to the URL Slack handed back. Separate from the JSON API
    because it is not an api.slack.com call and takes no token."""
    boundary = "----riseridge-upload-boundary"
    body = (
        ("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
         "filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n"
         % (boundary, filename)).encode("utf-8")
        + data
        + ("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status
    except (urllib.error.URLError, OSError) as e:
        raise PostError("file upload POST failed: %s" % e)


class Poster:
    """Writes to one channel. `dry_run` is the default and performs no writes."""

    def __init__(self, client=None, channel=None, bot_user_id=None,
                 dry_run=True, upload=None, internal=False):
        env = {}
        if channel is None or bot_user_id is None:
            try:
                env = slack.load_env()
            except OSError:
                env = {}
        self.client = client or slack.SlackClient()
        self.channel = channel or env.get("SALES_PIPELINE_CHANNEL")
        self.bot_user_id = bot_user_id or env.get("SLACK_BOT_USER_ID", "")
        self.dry_run = dry_run
        # An internal review channel is the operator's own space, not a
        # prospect's thread. The arming switch guards the irreversible case --
        # a wrong message in front of a prospect -- so it does not apply here.
        # dry_run still does.
        self.internal = internal
        self._upload = upload or _raw_upload
        self.planned = []
        if not self.channel:
            raise PostError("SALES_PIPELINE_CHANNEL is not set")

    # --- guards -------------------------------------------------------------

    def _guard(self, action, detail):
        """Record the intent. Returns True when the write must be skipped."""
        self.planned.append({"action": action, "detail": detail})
        if self.dry_run:
            return True
        if not self.internal and not posting_armed():
            raise PostError(
                "posting was requested but %s is not set. Arm the bot "
                "explicitly; nothing reaches a prospect thread otherwise."
                % ARMED_ENV)
        return False

    def already_posted(self, thread_ts):
        """True if this bot already replied in the thread.

        Authoritative and server-side, so it is correct on a fresh runner with
        no local state. A thread we cannot read is treated as already posted:
        refusing to act on unknown state is the safe direction when the failure
        mode is a duplicate message in a prospect's thread.
        """
        try:
            r = self.client.api("conversations.replies",
                                {"channel": self.channel, "ts": thread_ts,
                                 "limit": "200"})
        except slack.SlackError as e:
            raise PostError("cannot read thread %s: %s" % (thread_ts, e))
        for msg in r.get("messages", []):
            if msg.get("ts") == thread_ts:
                continue
            if self.bot_user_id and msg.get("user") == self.bot_user_id:
                return True
            if not self.bot_user_id and msg.get("bot_id"):
                return True
        return False

    # --- writes -------------------------------------------------------------

    def post_text(self, thread_ts, text):
        """Post to a thread, or to the channel itself when thread_ts is None."""
        if self._guard("chat.postMessage",
                       "%d chars to %s" % (len(text),
                                           "thread %s" % thread_ts if thread_ts
                                           else "channel %s" % self.channel)):
            return None
        params = {"channel": self.channel, "text": text,
                  "unfurl_links": "false", "unfurl_media": "false"}
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self.client.api("chat.postMessage", params)

    def upload_file(self, thread_ts, path, title, comment=""):
        """The current three-step external upload. files.upload was retired."""
        with open(path, "rb") as fh:
            data = fh.read()
        filename = os.path.basename(path)
        if self._guard("files.upload",
                       "%s (%d bytes) to %s"
                       % (filename, len(data),
                          "thread %s" % thread_ts if thread_ts
                          else "channel %s" % self.channel)):
            return None
        got = self.client.api("files.getUploadURLExternal",
                              {"filename": filename, "length": str(len(data))})
        url, file_id = got.get("upload_url"), got.get("file_id")
        if not url or not file_id:
            raise PostError("getUploadURLExternal returned no upload target")
        self._upload(url, data, filename)
        params = {
            "files": json.dumps([{"id": file_id, "title": title}]),
            "channel_id": self.channel,
            "initial_comment": comment,
        }
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self.client.api("files.completeUploadExternal", params)

    def mark(self, thread_ts):
        """Visible marker on the source message. Never the primary guard."""
        if self._guard("reactions.add",
                       "%s on %s" % (MARKER_REACTION, thread_ts)):
            return None
        try:
            return self.client.api("reactions.add", {
                "channel": self.channel, "timestamp": thread_ts,
                "name": MARKER_REACTION})
        except slack.SlackError as e:
            # already_reacted means a previous run got this far. That is a
            # successful outcome, not a failure worth aborting the run for.
            if "already_reacted" in str(e):
                return None
            raise

    # --- the published bundle ----------------------------------------------

    def publish(self, thread_ts, *, summary, pdf_path=None, dossier_text="",
                script_text="", business_name=""):
        """Post the three artefacts into one thread, once.

        Order matters: the PDF goes first so that if a later call fails, the
        thread already carries the artefact the closer most needs, and the
        idempotency check will stop a retry from duplicating it.
        """
        if self.already_posted(thread_ts):
            return {"status": "skipped", "reason": "bot already replied in "
                                                   "this thread",
                    "planned": self.planned}

        did = []
        if pdf_path and os.path.exists(pdf_path):
            self.upload_file(thread_ts, pdf_path,
                             "AI Search Visibility Audit -- %s"
                             % (business_name or ""), summary)
            did.append("audit pdf")
        else:
            self.post_text(thread_ts, summary)
            did.append("summary")

        for label, body in (("dossier", dossier_text), ("script", script_text)):
            if body:
                self.post_text(thread_ts, _chunk_header(label) + body)
                did.append(label)

        self.mark(thread_ts)
        return {"status": "dry-run" if self.dry_run else "posted",
                "posted": did, "planned": self.planned}


    # --- internal review ----------------------------------------------------

    def already_reviewed(self, marker, pages=1):
        """True if this marker already appears in the review channel.

        The prospect-thread guard cannot be used here: a review post is a new
        top-level message, so there is no thread to inspect. The marker is a
        non-reversible tag derived from the lead, embedded in the message, so a
        re-run finds its own previous post without the channel ever carrying
        the prospect's name.
        """
        try:
            for msg in self.client.history(self.channel, pages=pages):
                if marker in (msg.get("text") or ""):
                    return True
        except slack.SlackError as e:
            raise PostError("cannot read review channel: %s" % e)
        return False

    def publish_review(self, marker, *, summary, pdf_path=None,
                       dossier_text="", script_text="", business_name=""):
        """Post the bundle to an internal channel instead of a prospect thread.

        This is what makes the operator's review run possible when the
        repository is public: build logs and artifacts are world-readable
        there, so the artefacts have to reach the operator through Slack.
        """
        if self.already_reviewed(marker):
            return {"status": "skipped",
                    "reason": "already in the review channel",
                    "planned": self.planned}

        head = "%s\n_Review copy -- not posted to the prospect._ `%s`" % (
            summary, marker)
        did = []
        if pdf_path and os.path.exists(pdf_path):
            self.upload_file(None, pdf_path,
                             "AI Search Visibility Audit -- %s"
                             % (business_name or ""), head)
            did.append("audit pdf")
        else:
            self.post_text(None, head)
            did.append("summary")

        for label, body in (("dossier", dossier_text), ("script", script_text)):
            if body:
                self.post_text(None, _chunk_header(label) + body)
                did.append(label)

        return {"status": "dry-run" if self.dry_run else "review-posted",
                "posted": did, "planned": self.planned}


def _chunk_header(label):
    return {"dossier": "*Prospect dossier (internal)*\n```\n",
            "script": "*Sales script (internal)*\n```\n"}.get(label, "")


def format_plan(plan):
    """What a dry run would have done, for the operator's review."""
    out = ["POSTING PLAN (%s)" % plan.get("status", "?")]
    if plan.get("reason"):
        out.append("  reason: %s" % plan["reason"])
    for step in plan.get("planned", []):
        out.append("  %-24s %s" % (step["action"], step["detail"]))
    if not plan.get("planned"):
        out.append("  (nothing to do)")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Post artefacts to a lead's thread. Dry-run by default.")
    ap.add_argument("thread_ts")
    ap.add_argument("--summary", required=True)
    ap.add_argument("--pdf", default="")
    ap.add_argument("--post", action="store_true",
                    help="actually write to Slack (also needs %s=1)" % ARMED_ENV)
    a = ap.parse_args()

    poster = Poster(dry_run=not a.post)
    print(format_plan(poster.publish(a.thread_ts, summary=a.summary,
                                     pdf_path=a.pdf or None)))


if __name__ == "__main__":
    main()
