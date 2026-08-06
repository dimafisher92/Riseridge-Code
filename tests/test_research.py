"""Company and contact research.

The boundary being defended: LinkedIn and the aggregators are never REQUESTED,
but their search snippets -- already published by the search engine -- are read.
Getting that wrong in either direction is a problem: scraping them gets the
runner blocked, and refusing to read snippets throws away the only role
information available.
"""

import research


COMPANY_ROWS = [
    {"rank": 1, "title": "CLEAN Nutritionals launches new isolate range",
     "url": "https://ausbiz.example/news/clean", "domain": "ausbiz.example",
     "snippet": "The brand will launch a new range this quarter.",
     "aggregator": False},
    {"rank": 2, "title": "CLEAN Nutritionals Reviews",
     "url": "https://www.trustpilot.com/review/clean", "domain": "trustpilot.com",
     "snippet": "Rated 4.5 from 300 reviews.", "aggregator": True},
    {"rank": 3, "title": "CLEAN Nutritionals",
     "url": "https://cleannutritionals.com.au/", "domain": "cleannutritionals.com.au",
     "snippet": "Official store.", "aggregator": False},
]

PERSON_ROWS = [
    {"rank": 1,
     "title": "Mary-Louise Condon - Founder & Director - CLEAN Nutritionals | LinkedIn",
     "url": "https://www.linkedin.com/in/mlcondon", "domain": "linkedin.com",
     "snippet": "Founder & Director at CLEAN Nutritionals. Sydney, Australia.",
     "aggregator": True},
]


def searcher(company=COMPANY_ROWS, person=PERSON_ROWS):
    def search(q, limit=10):
        return person if "Condon" in q else company
    return search


def test_blocked_hosts_are_never_fetched():
    """LinkedIn and friends prohibit automated access and block the IP of
    anything that tries."""
    requested = []

    def fetch(url):
        requested.append(url)
        return 200, "<html><body>page</body></html>"

    research.run("CLEAN Nutritionals", "cleannutritionals.com.au",
                 "Mary-Louise Condon", search=searcher(), fetch=fetch)
    for url in requested:
        assert "linkedin.com" not in url
        assert "trustpilot.com" not in url


def test_the_role_comes_from_a_snippet_without_fetching_the_profile():
    """Reading a search result ABOUT a LinkedIn page is not scraping LinkedIn,
    and it is the only place a role is reliably available."""
    got = research.run("CLEAN Nutritionals", "cleannutritionals.com.au",
                       "Mary-Louise Condon", search=searcher())
    assert got["role"]["value"].lower().startswith("founder")
    assert "linkedin.com" in got["role"]["source"]
    assert got["role"]["evidence"]


def test_no_role_when_no_snippet_supports_one():
    """Never guessed: a wrong title on a call is worse than no title."""
    rows = [{"rank": 1, "title": "Some unrelated page", "url": "https://x.test",
             "domain": "x.test", "snippet": "nothing here", "aggregator": False}]
    got = research.run("Acme", "acme.com", "Jordan Alvarez",
                       search=searcher(person=rows))
    assert got["role"] is None


def test_the_role_must_belong_to_the_named_person():
    other = [{"rank": 1, "title": "Someone Else - CEO - Acme", "url": "https://x.test",
              "domain": "x.test", "snippet": "Someone Else is CEO of Acme.",
              "aggregator": False}]
    got = research.run("Acme", "acme.com", "Jordan Alvarez",
                       search=searcher(person=other))
    assert got["role"] is None


def test_results_are_bucketed_for_a_closer():
    got = research.run("CLEAN Nutritionals", "cleannutritionals.com.au",
                       "Mary-Louise Condon", search=searcher())
    assert any(r["domain"] == "ausbiz.example" for r in got["press"])
    assert any(r["domain"] == "trustpilot.com" for r in got["reviews"])
    assert not any(r["domain"] == "cleannutritionals.com.au"
                   for r in got["press"]), "their own site is not press"


def test_followed_pages_carry_an_extract_not_just_a_link():
    """The whole point: return findings, not homework."""
    def fetch(url):
        return 200, "<html><body><p>CLEAN Nutritionals will open a second " \
                    "facility in Melbourne next year.</p></body></html>"

    got = research.run("CLEAN Nutritionals", "cleannutritionals.com.au", "",
                       search=searcher(), fetch=fetch)
    assert got["followed"]
    assert "Melbourne" in got["followed"][0]["extract"]


def test_max_follow_caps_the_requests():
    calls = []

    def fetch(url):
        calls.append(url)
        return 200, "<html><body>x</body></html>"

    rows = [{"rank": i, "title": "launch %d" % i, "url": "https://x%d.test" % i,
             "domain": "x%d.test" % i, "snippet": "will launch soon",
             "aggregator": False} for i in range(10)]
    research.run("Acme", "acme.com", "", search=searcher(company=rows),
                 fetch=fetch, max_follow=2)
    assert len(calls) <= 2


def test_a_failing_search_does_not_crash_the_research():
    def boom(q, limit=10):
        raise RuntimeError("blocked")

    assert research.run("Acme", "acme.com", "Jordan", search=boom) is None


def test_no_results_at_all_returns_none():
    assert research.run("Acme", "acme.com", "", search=lambda q, limit=10: []) is None


def test_a_failing_fetch_does_not_lose_the_search_results():
    def boom(url):
        raise OSError("dns")

    got = research.run("CLEAN Nutritionals", "cleannutritionals.com.au", "",
                       search=searcher(), fetch=boom)
    assert got is not None
    assert got["press"]
    assert got["followed"] == []


def test_the_limits_are_stated_in_the_output():
    got = research.run("Acme", "acme.com", "", search=searcher())
    assert any("prohibit automated access" in x for x in got["limits"])


def test_person_queries_are_skipped_when_there_is_no_contact():
    seen = []

    def search(q, limit=10):
        seen.append(q)
        return COMPANY_ROWS

    research.run("Acme", "acme.com", "", search=search)
    assert all("linkedin" not in q.lower() for q in seen)
