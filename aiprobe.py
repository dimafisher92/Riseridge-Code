"""AI visibility: ask each engine unbranded buyer-intent questions, record who
gets named.

The spec's original method drove the operator's logged-in Chrome, because
consumer app answers are what a prospect's customers actually see. That method
cannot run on a hosted runner -- there is no logged-in browser -- so the
automated path uses the official APIs with search grounding instead. The
difference is real and is stated in the report's own provenance: API answers
approximate consumer answers, they do not equal them.

Two design rules make the result safe to print:

- **A failed engine is omitted, never scored zero.** "We could not reach
  Perplexity" and "Perplexity never mentions you" look identical in a table and
  mean opposite things. Only engines that actually answered appear.
- **Provider wiring is configuration, not a hardcoded fact.** Endpoint, model,
  auth header and response shape each come from an env-overridable default,
  because these APIs change shape faster than this repo will. Everything that
  decides what the report says is provider-independent and tested offline.
"""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state",
                     "verticals")
CACHE_TTL_DAYS = 14
TIMEOUT = 90
MAX_RETRIES = 3


class ProbeError(Exception):
    pass


# --- provider configuration -------------------------------------------------

def _env(name, default):
    return os.environ.get(name) or default


class Provider:
    """One AI engine, wired entirely from configuration.

    `text_path` is a best-effort hint. Extraction falls back to a structural
    search, so a provider changing its response envelope degrades to "still
    works" rather than "silently returns empty answers".
    """

    def __init__(self, name, key_env, endpoint, model, text_path=(),
                 auth="bearer", extra_headers=None, body=None):
        self.name = name
        self.key_env = key_env
        self.endpoint = endpoint
        self.model = model
        self.text_path = text_path
        self.auth = auth
        self.extra_headers = extra_headers or {}
        self._body = body

    def api_key(self):
        return os.environ.get(self.key_env, "")

    def available(self):
        return bool(self.api_key())

    def headers(self):
        h = {"Content-Type": "application/json"}
        key = self.api_key()
        if self.auth == "bearer":
            h["Authorization"] = "Bearer " + key
        elif self.auth == "x-api-key":
            h["x-api-key"] = key
        elif self.auth == "x-goog":
            h["x-goog-api-key"] = key
        h.update(self.extra_headers)
        return h

    def body(self, question):
        if self._body:
            return self._body(self.model, question)
        return {"model": self.model,
                "messages": [{"role": "user", "content": question}]}


def _gemini_body(model, question):
    return {"contents": [{"parts": [{"text": question}]}],
            "tools": [{"google_search": {}}]}


def _openai_body(model, question):
    # Search grounding is the whole point: without a web tool the model answers
    # from training data, which measures memorised brand fame rather than what a
    # buyer sees today.
    return {"model": model, "input": question,
            "tools": [{"type": "web_search"}]}


def _perplexity_body(model, question):
    return {"model": model,
            "messages": [{"role": "user", "content": question}]}


def default_providers():
    """The engines, with every wire-level detail overridable by env var.

    Defaults are a starting point, not a verified contract: these three APIs
    have each changed endpoint shape recently and the published docs disagree
    with each other. Override rather than edit code when one moves.
    """
    return [
        Provider(
            "ChatGPT", "OPENAI_API_KEY",
            _env("RR_OPENAI_ENDPOINT", "https://api.openai.com/v1/responses"),
            _env("RR_OPENAI_MODEL", "gpt-4o"),
            text_path=("output_text",), body=_openai_body),
        Provider(
            "Perplexity", "PERPLEXITY_API_KEY",
            _env("RR_PERPLEXITY_ENDPOINT",
                 "https://api.perplexity.ai/chat/completions"),
            _env("RR_PERPLEXITY_MODEL", "sonar"),
            text_path=("choices", 0, "message", "content"),
            body=_perplexity_body),
        Provider(
            "Gemini", "GEMINI_API_KEY",
            _env("RR_GEMINI_ENDPOINT",
                 "https://generativelanguage.googleapis.com/v1beta/models/"
                 "gemini-2.0-flash:generateContent"),
            _env("RR_GEMINI_MODEL", "gemini-2.0-flash"),
            text_path=("candidates", 0, "content", "parts", 0, "text"),
            auth="x-goog", body=_gemini_body),
    ]


# --- response extraction ----------------------------------------------------

TEXT_KEYS = ("output_text", "text", "content", "answer", "message", "parts")


def _walk_path(payload, path):
    node = payload
    for step in path:
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            return None
    return node if isinstance(node, str) else None


def _deep_text(node, depth=0):
    """Longest plausible answer string anywhere in the payload.

    The fallback when a provider changes its envelope. Prefers text under known
    content keys, so a model id or a request echo does not win over the answer.
    """
    if depth > 8:
        return ""
    best = ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        for key in TEXT_KEYS:
            if key in node:
                got = _deep_text(node[key], depth + 1)
                if len(got) > len(best):
                    best = got
        if best:
            return best
        for v in node.values():
            got = _deep_text(v, depth + 1)
            if len(got) > len(best):
                best = got
    elif isinstance(node, list):
        for v in node:
            got = _deep_text(v, depth + 1)
            if len(got) > len(best):
                best = got
    return best


def extract_text(payload, text_path=()):
    """Answer text from a provider payload. Configured path first, then a
    structural search."""
    if payload is None:
        return ""
    direct = _walk_path(payload, text_path) if text_path else None
    if direct:
        return direct.strip()
    return _deep_text(payload).strip()


# --- transport --------------------------------------------------------------

def http_json(url, headers, body, timeout=TIMEOUT):
    """POST JSON, return the decoded payload, or raise ProbeError."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers,
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ProbeError("%s %s: %s" % (e.code, e.reason, detail))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        raise ProbeError(str(e))


RETRYABLE = ("429", "500", "502", "503", "504", "timed out", "timeout")


def ask(provider, question, *, transport=None, sleep=time.sleep):
    """One question to one engine. Returns text, or raises ProbeError.

    Retries only the transient statuses. A 401 is a configuration error and
    retrying it three times just burns wall clock on every question.
    """
    transport = transport or http_json
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            payload = transport(provider.endpoint, provider.headers(),
                                provider.body(question))
            return extract_text(payload, provider.text_path)
        except ProbeError as e:
            last = e
            if not any(t in str(e).lower() for t in RETRYABLE):
                raise
            if attempt < MAX_RETRIES - 1:
                sleep(2 ** attempt)
    raise last


# --- questions --------------------------------------------------------------

# Unbranded and category-level on purpose. A branded question only fires for
# people who already know the brand and therefore measures nothing -- the exact
# flaw in auto-generated topic sets.
QUESTION_TEMPLATES = (
    "best {category}{where}",
    "where should I go for {category}{where}",
    "how do I choose a good {category}",
    "who are the top rated {category}{where}",
    "what should I look for when hiring a {category}",
)


def build_questions(category, location=""):
    """Unbranded buyer-intent questions for a vertical."""
    if not category:
        raise ProbeError("category is required to build questions")
    where = (" in " + location) if location else ""
    return [t.format(category=category, where=where) for t in QUESTION_TEMPLATES]


def assert_unbranded(questions, brand_tokens):
    """Guard: a branded question measures brand recall, not discovery."""
    bad = [q for q in questions
           if any(t and t.lower() in q.lower() for t in brand_tokens)]
    if bad:
        raise ProbeError("questions must be unbranded, these name the brand: %s"
                         % "; ".join(bad))


# --- answer analysis --------------------------------------------------------

SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def _mentions(text, token):
    if not token:
        return False
    return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(token.lower()),
                     text.lower()) is not None


def brand_tokens_for(business_name, domain):
    """Match tokens for the prospect: the business name, and the domain's
    registrable label."""
    out = []
    if business_name:
        out.append(business_name.strip())
        compact = re.sub(r"[^a-z0-9]", "", business_name.lower())
        if compact and compact != business_name.strip().lower():
            out.append(compact)
    if domain:
        label = domain.split(".")[0]
        label = re.sub(r"^(get|try|the|my|go)", "", label) or label
        if len(label) >= 4:
            out.append(label)
    return [t for t in dict.fromkeys(out) if t]


def find_excerpt(text, tokens):
    """The sentence that proves the finding, trimmed for the report."""
    for sentence in SENTENCE.findall(text or ""):
        if any(_mentions(sentence, t) for t in tokens):
            return re.sub(r"\s+", " ", sentence).strip()[:300]
    return ""


def analyse(answers, brand_tokens, competitor_names=()):
    """Per-engine reading over that engine's answers.

    `competitor_names` are matched exactly -- they come from the collected
    competitor set, so a hit is a fact rather than a guess. Names the engine
    raised that are not in that set are NOT reported as competitors, because
    pulling capitalised runs out of prose invents company names.
    """
    named_in, competitors, excerpt = 0, [], ""
    brand_excerpt = ""
    for text in answers:
        if any(_mentions(text, t) for t in brand_tokens):
            named_in += 1
            if not brand_excerpt:
                brand_excerpt = find_excerpt(text, brand_tokens)
        for c in competitor_names:
            if _mentions(text, c) and c not in competitors:
                competitors.append(c)
        if not excerpt and competitors:
            excerpt = find_excerpt(text, competitors)

    return {
        "brand_named": named_in > 0,
        "topics_present": named_in,
        "topics_total": len(answers),
        "competitors_named": competitors,
        "verbatim_excerpt": brand_excerpt or excerpt,
    }


# --- vertical cache ---------------------------------------------------------

def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def cache_path(vertical, base=None):
    slug = _slug(vertical)
    if not slug:
        raise ProbeError("vertical slug is empty")
    return os.path.join(base or STATE, slug + ".json")


def load_cache(vertical, base=None, now=None):
    """Cached questions and per-engine answers, or None when absent or stale.

    A second prospect in a known vertical reuses the answers and only has to
    check its own brand against them, which is what makes this affordable to run
    per booking.
    """
    path = cache_path(vertical, base)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    stamped = data.get("probed_at")
    if not stamped:
        return None
    try:
        when = datetime.strptime(stamped, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    if now - when > timedelta(days=CACHE_TTL_DAYS):
        return None
    return data


def save_cache(vertical, data, base=None):
    path = cache_path(vertical, base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    return path


# --- the probe --------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gather(providers, questions, *, transport=None, sleep=time.sleep):
    """Raw answers per engine. An engine that fails is omitted with its reason.

    Returns ({engine: [answers]}, {engine: reason}). The second map is what
    keeps a transport failure from being printed as a visibility score of zero.
    """
    answers, skipped = {}, {}
    for p in providers:
        if not p.available():
            skipped[p.name] = "no API key configured (%s)" % p.key_env
            continue
        got = []
        try:
            for q in questions:
                got.append(ask(p, q, transport=transport, sleep=sleep))
        except ProbeError as e:
            skipped[p.name] = str(e)[:200]
            continue
        if not any(t.strip() for t in got):
            skipped[p.name] = "engine returned no usable text"
            continue
        answers[p.name] = got
    return answers, skipped


def probe(business_name, domain, *, vertical, category, location="",
          competitors=(), providers=None, transport=None, cache_base=None,
          use_cache=True, sleep=time.sleep, now=None):
    """The ai_visibility evidence block for one prospect.

    Returns None when no engine answered. A null block omits the report section
    entirely, which is the correct outcome: no measurement is not a finding.
    """
    providers = providers if providers is not None else default_providers()
    brand = brand_tokens_for(business_name, domain)

    cached = load_cache(vertical, cache_base, now) if use_cache else None
    if cached and cached.get("answers"):
        questions = cached["questions"]
        answers = cached["answers"]
        skipped = cached.get("skipped", {})
        source = "vertical cache (%s)" % vertical
        probed_at = cached["probed_at"]
    else:
        questions = build_questions(category, location)
        assert_unbranded(questions, brand)
        answers, skipped = gather(providers, questions, transport=transport,
                                  sleep=sleep)
        probed_at = _now()
        source = "live probe"
        if answers and use_cache:
            save_cache(vertical, {"vertical": vertical, "category": category,
                                  "location": location, "probed_at": probed_at,
                                  "questions": questions, "answers": answers,
                                  "skipped": skipped}, cache_base)

    if not answers:
        return None

    competitor_names = [c for c in competitors if c]
    platforms = []
    for engine, texts in sorted(answers.items()):
        reading = analyse(texts, brand, competitor_names)
        reading["platform"] = engine
        platforms.append(reading)

    return {
        "probed_at": probed_at,
        "vertical_cache": vertical,
        "source": source,
        "method": "official provider APIs with search grounding",
        "method_caveat": "API answers approximate what a consumer sees in the "
                         "chat apps; they are not identical to them",
        "questions": questions,
        "platforms": platforms,
        "engines_omitted": skipped,
    }


def competitor_names_from_evidence(ev):
    """Readable competitor names from the collected competitor set.

    The engines name businesses, not domains, so 'trtnation.com' has to be
    matched as 'TRTNation' too.
    """
    out = []
    for row in (ev or {}).get("competitors") or []:
        domain = (row.get("domain") or "").strip().lower()
        if not domain:
            continue
        label = domain.split(".")[0]
        if len(label) >= 4:
            out.append(label)
    return list(dict.fromkeys(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--name", required=True)
    ap.add_argument("--vertical", required=True)
    ap.add_argument("--category", required=True,
                    help="unbranded category noun, e.g. 'TRT clinic'")
    ap.add_argument("--location", default="")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    got = probe(a.name, a.domain, vertical=a.vertical, category=a.category,
                location=a.location, use_cache=not a.no_cache)
    print(json.dumps(got, indent=2) if got else
          "no engine answered; ai_visibility stays null and its section is omitted")


if __name__ == "__main__":
    main()
