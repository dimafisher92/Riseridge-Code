import json
import os
import sys
import types

import pytest

import saprobe


def test_capture_writes_named_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"a": 1}, {"a": 2}], "total_count": 2}

    out = saprobe.capture(FakeSA(), "demo", "keyword", "/x/")
    assert out["total_count"] == 2
    written = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert written["payload"]["total_count"] == 2


def test_capture_records_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))

    class FakeSA:
        def get(self, service, path, params=None):
            return {"ok": True}

    saprobe.capture(FakeSA(), "demo", "keyword", "/x/", params={"target": "d.com"})
    rec = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert rec["service"] == "keyword"
    assert rec["path"] == "/x/"
    assert rec["params"] == {"target": "d.com"}
    assert rec["captured_at"]


def test_capture_trims_result_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [{"i": i} for i in range(500)], "total_count": 500}

    out = saprobe.capture(FakeSA(), "big", "keyword", "/x/", trim=3)
    assert len(out["results"]) == 3
    assert out["total_count"] == 500, "trimming must not touch the real total"


def test_capture_retries_when_response_says_should_retry(tmp_path, monkeypatch):
    """organic-keywords is async-warmed: an empty first response with
    should_retry must not be read as 'no data'."""
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))
    monkeypatch.setattr(saprobe.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class FakeSA:
        def get(self, service, path, params=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"results": [], "total_count": 0,
                        "should_retry": True, "retry_after": 1}
            return {"results": [{"k": "trt cost"}], "total_count": 1}

    out = saprobe.capture(FakeSA(), "warmed", "keyword", "/x/")
    assert calls["n"] == 2
    assert out["total_count"] == 1


def test_capture_gives_up_after_max_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))
    monkeypatch.setattr(saprobe.time, "sleep", lambda s: None)

    class FakeSA:
        def get(self, service, path, params=None):
            return {"results": [], "total_count": 0, "should_retry": True,
                    "retry_after": 1}

    out = saprobe.capture(FakeSA(), "never", "keyword", "/x/", max_retries=2)
    assert out["should_retry"] is True, "returns the last response rather than hanging"


def test_capture_records_domain(tmp_path, monkeypatch):
    """A fixture must be self-identifying even when its filename is
    domain-independent (e.g. the keyword-details lookup)."""
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))

    class FakeSA:
        def get(self, service, path, params=None):
            return {"ok": True}

    saprobe.capture(FakeSA(), "demo", "keyword", "/x/", domain="d.com")
    rec = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert rec["domain"] == "d.com"


def test_main_scopes_fixtures_by_domain_and_never_posts(tmp_path, monkeypatch):
    """Pins the naming contract that fixed the real overwrite bug: two
    different --domain runs must never collide on a filename, cold names
    must carry the domain slug, every fixture must record its own domain,
    warm captures must only happen when --warm-id is given, and saprobe must
    never issue a write (POST) against the live API.

    Exercises saprobe.main() directly (rather than a name-building helper
    extracted for the purpose) because the bug that prompted this test was
    in main()'s wiring itself -- a helper tested in isolation could pass
    while main() still called it wrong. sa_client is faked by pre-seeding
    sys.modules, since main() does `import sa_client` internally; no network
    call is possible because FakeSA.get returns canned data and FakeSA.post
    raises if it is ever called.
    """
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))
    calls = {"post": 0}

    class FakeSA:
        def __init__(self, timeout=60):
            self.timeout = timeout

        def get(self, service, path, params=None):
            return {"results": [{"x": 1}], "total_count": 1}

        def post(self, *a, **kw):
            calls["post"] += 1
            raise AssertionError("saprobe must never POST")

    fake_sa_client = types.SimpleNamespace(SearchAtlas=FakeSA)
    monkeypatch.setitem(sys.modules, "sa_client", fake_sa_client)

    monkeypatch.setattr(sys, "argv", ["saprobe.py", "--domain", "trtnation.com"])
    saprobe.main()
    cold_only_files = set(os.listdir(str(tmp_path)))

    monkeypatch.setattr(sys, "argv", ["saprobe.py", "--domain", "getpetermd.com",
                                       "--warm-id", "824060"])
    saprobe.main()
    all_files = set(os.listdir(str(tmp_path)))

    assert calls["post"] == 0, "no POST may ever be issued"

    # Nothing the first run wrote was removed or overwritten-into-absence by
    # the second run -- both domains' files coexist.
    assert cold_only_files <= all_files
    assert "cold_trtnation_com_organic_keywords.json" in cold_only_files
    assert "cold_getpetermd_com_organic_keywords.json" in all_files

    # Cold names carry the domain slug.
    for fname in ("organic_keywords", "backlinks", "brand_signal"):
        assert "cold_trtnation_com_%s.json" % fname in cold_only_files
        assert "cold_getpetermd_com_%s.json" % fname in all_files

    # Warm captures only happen when --warm-id is supplied.
    assert not any(f.startswith("warm_") for f in cold_only_files)
    warm_files = {f for f in all_files if f.startswith("warm_")}
    assert warm_files, "warm-id run should have produced warm_* fixtures"
    assert all(f.startswith("warm_getpetermd_com_") for f in warm_files)

    # Every fixture records the domain it was actually captured for.
    rec_cold = json.loads(
        (tmp_path / "cold_trtnation_com_organic_keywords.json")
        .read_text(encoding="utf-8"))
    assert rec_cold["domain"] == "trtnation.com"
    rec_warm = json.loads(
        (tmp_path / "warm_getpetermd_com_project_detail.json")
        .read_text(encoding="utf-8"))
    assert rec_warm["domain"] == "getpetermd.com"


def test_load_reads_payload_only(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))
    (tmp_path / "z.json").write_text(json.dumps(
        {"service": "keyword", "path": "/x/", "params": {}, "captured_at": "t",
         "payload": {"hello": "world"}}), encoding="utf-8")
    assert saprobe.load("z") == {"hello": "world"}


def test_load_raises_actionable_error_for_missing_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(saprobe, "FIXTURES", str(tmp_path))
    with pytest.raises(FileNotFoundError) as e:
        saprobe.load("nope")
    assert "saprobe.py" in str(e.value), "error must say how to regenerate it"
