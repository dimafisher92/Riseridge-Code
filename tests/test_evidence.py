import json

import pytest

import evidence


def base():
    return {
        "domain": "example.com",
        "business_name": "Example",
        "generated_at": "2026-08-04T00:00:00Z",
        "traffic": {
            "monthly_organic_visits": {"value": 10800, "source": "x", "pulled_at": "y"},
            "ranking_keyword_count": {"value": None},
        },
    }


def test_get_returns_metric_value():
    e = evidence.Evidence(base())
    assert e.get("traffic.monthly_organic_visits") == 10800


def test_get_returns_none_for_null_value():
    assert evidence.Evidence(base()).get("traffic.ranking_keyword_count") is None


def test_get_returns_none_for_missing_path():
    assert evidence.Evidence(base()).get("backlinks.total_backlinks") is None


def test_get_returns_none_for_missing_intermediate():
    assert evidence.Evidence(base()).get("nope.nothing.here") is None


def test_has_is_false_for_null_value():
    assert evidence.Evidence(base()).has("traffic.ranking_keyword_count") is False


def test_has_is_true_for_real_value():
    assert evidence.Evidence(base()).has("traffic.monthly_organic_visits") is True


def test_get_handles_plain_scalar_not_wrapped_in_metric():
    e = evidence.Evidence({"domain": "d", "business_name": "b",
                           "generated_at": "g", "vertical": "dentists"})
    assert e.get("vertical") == "dentists"


def test_validate_passes_with_required_fields():
    evidence.Evidence(base()).validate()


@pytest.mark.parametrize("missing", ["domain", "business_name", "generated_at"])
def test_validate_rejects_missing_required_field(missing):
    d = base()
    del d[missing]
    with pytest.raises(evidence.EvidenceError) as e:
        evidence.Evidence(d).validate()
    assert missing in str(e.value)


@pytest.mark.parametrize("empty_field", ["domain", "business_name", "generated_at"])
def test_validate_rejects_empty_required_field(empty_field):
    d = base()
    d[empty_field] = ""
    with pytest.raises(evidence.EvidenceError):
        evidence.Evidence(d).validate()


def test_present_sections_includes_satisfied_section():
    d = base()
    d["traffic"]["traffic_value_usd"] = {"value": 61900}
    assert "traffic" in evidence.Evidence(d).present_sections()


def test_present_sections_excludes_section_with_no_data():
    assert "paid" not in evidence.Evidence(base()).present_sections()


def test_present_sections_excludes_section_with_all_null():
    d = base()
    d["paid"] = {"estimated_monthly_spend_usd": {"value": None}, "paid_keywords": []}
    assert "paid" not in evidence.Evidence(d).present_sections()


def test_present_sections_includes_list_backed_section():
    d = base()
    d["competitors"] = [{"domain": "rival.com", "monthly_visits": 21200}]
    assert "competitors" in evidence.Evidence(d).present_sections()


def test_load_reads_json_file(tmp_path):
    p = tmp_path / "e.json"
    p.write_text(json.dumps(base()), encoding="utf-8")
    assert evidence.Evidence.load(p).get("domain") == "example.com"


def test_get_returns_zero_metric_value():
    e = evidence.Evidence({"domain": "d", "business_name": "b", "generated_at": "g",
                           "metric": {"value": 0}})
    assert e.get("metric") == 0


def test_has_true_for_zero_value():
    e = evidence.Evidence({"domain": "d", "business_name": "b", "generated_at": "g",
                           "metric": {"value": 0}})
    assert e.has("metric") is True


def test_get_returns_false_metric_value():
    e = evidence.Evidence({"domain": "d", "business_name": "b", "generated_at": "g",
                           "metric": {"value": False}})
    assert e.get("metric") is False


def test_has_true_for_false_value():
    e = evidence.Evidence({"domain": "d", "business_name": "b", "generated_at": "g",
                           "metric": {"value": False}})
    assert e.has("metric") is True


def test_present_sections_includes_zero_metric():
    d = base()
    d["scorecard"] = {"content_quality": {"value": 0}}
    assert "scorecard" in evidence.Evidence(d).present_sections()


def test_get_returns_none_for_dict_without_value_key():
    e = evidence.Evidence({"domain": "d", "business_name": "b", "generated_at": "g",
                           "metric": {"no_value_key": 42}})
    assert e.get("metric") is None


def test_get_returns_none_for_empty_dict():
    e = evidence.Evidence({"domain": "d", "business_name": "b", "generated_at": "g",
                           "metric": {}})
    assert e.get("metric") is None
