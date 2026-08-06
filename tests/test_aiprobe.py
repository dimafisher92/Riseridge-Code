"""AI probe logic, entirely offline through an injected transport.

The property that matters most: an engine that failed must never be reported as
an engine that found nothing. Those look identical in a table and mean opposite
things.
"""

import json

import pytest

import aiprobe


def transport_for(answers_by_question=None, fail=None, capture=None):
    """Fake http_json. `fail` raises; `capture` records the outgoing bodies."""
    def t(url, headers, body):
        if capture is not None:
            capture.append({"url": url, "headers": headers, "body": body})
        if fail:
            raise aiprobe.ProbeError(fail)
        q = json.dumps(body)
        for needle, answer in (answers_by_question or {}).items():
            if needle in q:
                return {"choices": [{"message": {"content": answer}}]}
        return {"choices": [{"message": {"content": "A generic answer."}}]}
    return t


def provider(name="ChatGPT", key_env="TEST_KEY"):
    return aiprobe.Provider(name, key_env, "https://x.test/v1", "test-model",
                            text_path=("choices", 0, "message", "content"))


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-test")
    return provider()


# --- questions must be unbranded -------------------------------------------

def test_questions_are_category_level_and_unbranded():
    qs = aiprobe.build_questions("TRT clinic", "Denver")
    assert len(qs) == len(aiprobe.QUESTION_TEMPLATES)
    assert all("Denver" in q for q in qs if "in " in q)
    for q in qs:
        assert "petermd" not in q.lower()


def test_branded_questions_are_rejected():
    """A branded question only fires for people who already know the brand and
    therefore measures nothing."""
    with pytest.raises(aiprobe.ProbeError):
        aiprobe.assert_unbranded(["best PeterMD clinic"], ["PeterMD"])


def test_unbranded_questions_pass_the_guard():
    aiprobe.assert_unbranded(aiprobe.build_questions("TRT clinic"), ["PeterMD"])


def test_category_is_required():
    with pytest.raises(aiprobe.ProbeError):
        aiprobe.build_questions("")


def test_location_is_optional_and_does_not_leave_dangling_text():
    for q in aiprobe.build_questions("plumber"):
        assert "  " not in q
        assert not q.endswith(" in")


# --- response extraction ----------------------------------------------------

def test_configured_path_extracts_the_answer():
    payload = {"choices": [{"message": {"content": "Try Acme Clinic."}}]}
    assert aiprobe.extract_text(
        payload, ("choices", 0, "message", "content")) == "Try Acme Clinic."


def test_extraction_falls_back_when_the_envelope_changes():
    """These APIs change shape faster than this repo will. A moved envelope must
    degrade to 'still works', not to silently empty answers."""
    payload = {"output": [{"content": [{"text": "Try Acme Clinic instead."}]}]}
    assert "Acme Clinic" in aiprobe.extract_text(payload, ("nope", 4, "gone"))


def test_extraction_prefers_content_over_incidental_strings():
    payload = {"model": "some-very-long-model-identifier-string",
               "choices": [{"message": {"content": "Short answer."}}]}
    assert aiprobe.extract_text(payload, ("choices", 0, "message", "content")) \
        == "Short answer."


def test_extraction_of_nothing_is_empty_not_an_exception():
    assert aiprobe.extract_text(None) == ""
    assert aiprobe.extract_text({}) == ""


# --- transport behaviour ----------------------------------------------------

def test_search_grounding_is_requested(monkeypatch):
    """Without a web tool the model answers from training data, which measures
    memorised brand fame rather than what a buyer sees today."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    by_name = {p.name: p for p in aiprobe.default_providers()}
    assert "web_search" in json.dumps(by_name["ChatGPT"].body("q"))
    assert "google_search" in json.dumps(by_name["Gemini"].body("q"))


def test_transient_failures_are_retried(keyed):
    calls = []

    def flaky(url, headers, body):
        calls.append(1)
        if len(calls) < 3:
            raise aiprobe.ProbeError("429 Too Many Requests")
        return {"choices": [{"message": {"content": "ok"}}]}

    assert aiprobe.ask(keyed, "q", transport=flaky, sleep=lambda s: None) == "ok"
    assert len(calls) == 3


def test_auth_failures_are_not_retried(keyed):
    """A 401 is a configuration error. Retrying it three times per question just
    burns wall clock on every question in the set."""
    calls = []

    def bad(url, headers, body):
        calls.append(1)
        raise aiprobe.ProbeError("401 Unauthorized")

    with pytest.raises(aiprobe.ProbeError):
        aiprobe.ask(keyed, "q", transport=bad, sleep=lambda s: None)
    assert len(calls) == 1


def test_auth_headers_by_scheme(monkeypatch):
    monkeypatch.setenv("K", "secret")
    assert aiprobe.Provider("a", "K", "u", "m").headers()["Authorization"] \
        == "Bearer secret"
    assert aiprobe.Provider("b", "K", "u", "m", auth="x-goog").headers()[
        "x-goog-api-key"] == "secret"


# --- the omission rule ------------------------------------------------------

def test_a_failed_engine_is_omitted_not_scored_zero(monkeypatch):
    """The whole point. 'We could not reach Perplexity' and 'Perplexity never
    mentions you' look identical in a table and mean opposite things."""
    monkeypatch.setenv("TEST_KEY", "sk-test")
    questions = aiprobe.build_questions("clinic")

    answers, skipped = aiprobe.gather(
        [provider("ChatGPT")], questions,
        transport=transport_for({}), sleep=lambda s: None)
    assert "ChatGPT" in answers and skipped == {}

    answers, skipped = aiprobe.gather(
        [provider("Perplexity")], questions,
        transport=transport_for(fail="503 upstream"), sleep=lambda s: None)
    assert answers == {}, "a failed engine must contribute no score at all"
    assert "503" in skipped["Perplexity"]


def test_an_engine_with_no_key_is_omitted_with_a_reason():
    answers, skipped = aiprobe.gather(
        [provider("Gemini", "MISSING_KEY_ENV")],
        ["q"], transport=transport_for({}))
    assert answers == {}
    assert "MISSING_KEY_ENV" in skipped["Gemini"]


def test_omitted_engines_are_reported_in_the_evidence(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-test")
    got = aiprobe.probe(
        "Acme", "acme.com", vertical="v", category="clinic",
        providers=[provider("ChatGPT"), provider("Perplexity", "NO_KEY")],
        transport=transport_for({}), use_cache=False, sleep=lambda s: None)
    assert [p["platform"] for p in got["platforms"]] == ["ChatGPT"]
    assert "Perplexity" in got["engines_omitted"]


def test_no_engine_answering_yields_none_not_an_empty_scorecard():
    """A null ai_visibility block omits the section. No measurement is not a
    finding, and an empty table in front of a prospect implies it is one."""
    got = aiprobe.probe("Acme", "acme.com", vertical="v", category="clinic",
                        providers=[provider("ChatGPT", "NO_KEY")],
                        transport=transport_for({}), use_cache=False)
    assert got is None


# --- analysis ---------------------------------------------------------------

def test_brand_named_and_topic_count():
    answers = ["Acme Clinic is a good option.", "Try Beta Health.",
               "Acme Clinic and others."]
    got = aiprobe.analyse(answers, ["Acme Clinic"], [])
    assert got["brand_named"] is True
    assert got["topics_present"] == 2
    assert got["topics_total"] == 3


def test_brand_not_named_is_a_real_zero():
    got = aiprobe.analyse(["Try Beta Health."], ["Acme"], [])
    assert got["brand_named"] is False
    assert got["topics_present"] == 0


def test_competitors_are_matched_from_the_known_set_only():
    """Pulling capitalised runs out of prose invents company names. Only
    competitors already collected from real data are reported."""
    got = aiprobe.analyse(
        ["Consider Trtnation or some other place in Denver."],
        ["Acme"], ["trtnation", "marek"])
    assert got["competitors_named"] == ["trtnation"]


def test_verbatim_excerpt_is_captured_as_proof():
    got = aiprobe.analyse(
        ["For men in Denver, Acme Clinic is widely recommended. Others exist."],
        ["Acme Clinic"], [])
    assert "Acme Clinic is widely recommended" in got["verbatim_excerpt"]


def test_excerpt_falls_back_to_a_competitor_sentence_when_brand_is_absent():
    got = aiprobe.analyse(["Most people go to Trtnation for this."],
                          ["Acme"], ["trtnation"])
    assert "Trtnation" in got["verbatim_excerpt"]


def test_substring_matches_do_not_count_as_a_mention():
    """'Acme' must not match inside 'Acmentor'."""
    got = aiprobe.analyse(["Acmentor Health is great."], ["Acme"], [])
    assert got["brand_named"] is False


def test_brand_tokens_cover_the_domain_label():
    tokens = aiprobe.brand_tokens_for("PeterMD", "getpetermd.com")
    assert "PeterMD" in tokens
    assert "petermd" in [t.lower() for t in tokens]


def test_competitor_names_come_from_collected_evidence():
    names = aiprobe.competitor_names_from_evidence(
        {"competitors": [{"domain": "trtnation.com"}, {"domain": "marek.com"}]})
    assert names == ["trtnation", "marek"]


# --- vertical cache ---------------------------------------------------------

def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-test")
    base = str(tmp_path)
    first = aiprobe.probe("Acme", "acme.com", vertical="TRT Clinic",
                          category="clinic", providers=[provider()],
                          transport=transport_for({}), cache_base=base,
                          sleep=lambda s: None)
    assert first["source"] == "live probe"

    calls = []
    second = aiprobe.probe("Beta", "beta.com", vertical="TRT Clinic",
                           category="clinic", providers=[provider()],
                           transport=transport_for(capture=calls),
                           cache_base=base)
    assert second["source"].startswith("vertical cache")
    assert calls == [], "a cached vertical must not re-hit the engines"


def test_stale_cache_is_ignored(tmp_path):
    from datetime import datetime, timedelta, timezone
    base = str(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=aiprobe.CACHE_TTL_DAYS + 1)
           ).strftime("%Y-%m-%dT%H:%M:%SZ")
    aiprobe.save_cache("v", {"probed_at": old, "questions": [], "answers": {}},
                       base)
    assert aiprobe.load_cache("v", base) is None


def test_fresh_cache_is_returned(tmp_path):
    base = str(tmp_path)
    aiprobe.save_cache("v", {"probed_at": aiprobe._now(), "questions": ["q"],
                             "answers": {"ChatGPT": ["a"]}}, base)
    assert aiprobe.load_cache("v", base)["questions"] == ["q"]


def test_corrupt_cache_is_ignored_not_fatal(tmp_path):
    base = str(tmp_path)
    path = aiprobe.cache_path("v", base)
    import os
    os.makedirs(base, exist_ok=True)
    with open(path, "w") as fh:
        fh.write("{not json")
    assert aiprobe.load_cache("v", base) is None


def test_a_failed_probe_is_not_cached(tmp_path, monkeypatch):
    """Caching a total failure would suppress retries for fourteen days."""
    base = str(tmp_path)
    monkeypatch.setenv("TEST_KEY", "sk-test")
    got = aiprobe.probe("Acme", "acme.com", vertical="v", category="clinic",
                        providers=[provider()],
                        transport=transport_for(fail="503 upstream"),
                        cache_base=base, sleep=lambda s: None)
    assert got is None
    assert aiprobe.load_cache("v", base) is None


def test_empty_vertical_slug_is_rejected(tmp_path):
    with pytest.raises(aiprobe.ProbeError):
        aiprobe.cache_path("!!!", str(tmp_path))


# --- provenance -------------------------------------------------------------

def test_the_method_and_its_caveat_are_recorded(monkeypatch):
    """The spec's method drove a logged-in browser because consumer answers
    differ from API output. The automated path cannot, and must say so."""
    monkeypatch.setenv("TEST_KEY", "sk-test")
    got = aiprobe.probe("Acme", "acme.com", vertical="v", category="clinic",
                        providers=[provider()], transport=transport_for({}),
                        use_cache=False, sleep=lambda s: None)
    assert "search grounding" in got["method"]
    assert "not identical" in got["method_caveat"]
    assert got["questions"]
