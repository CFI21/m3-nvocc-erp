"""Authenticated cross-Agent smoke runner unit tests (no network)."""
import pytest
from scripts import m3_cross_agent_smoke as smoke

SHA="f"*40


def test_missing_auth_and_non_https_fail_closed():
    with pytest.raises(ValueError,match="HTTPS_API_REQUIRED"):
        smoke.run("http://localhost","a","b","1","2",SHA)
    with pytest.raises(ValueError,match="DISTINCT_AUTHENTICATED_SESSIONS_REQUIRED"):
        smoke.run("https://preview.example","a","a","1","2",SHA)
    with pytest.raises(ValueError,match="DISTINCT_NUMERIC_BOOKING_IDS_REQUIRED"):
        smoke.run("https://preview.example","a","b","1","1",SHA)


def test_cross_agent_smoke_positive_and_negative_paths(monkeypatch):
    statuses={("a","/api/v1/booking/1"):200,
              ("a","/api/v1/booking/2"):403,
              ("b","/api/v1/booking/1"):404,
              ("b","/api/v1/booking/2"):200}
    monkeypatch.setattr(smoke,"request_json",
                        lambda base,path,token:{"status":statuses[(token,path)],
                                               "body":"private fixture"})
    report=smoke.run("https://preview.example","a","b","1","2",SHA)
    assert report["status"]=="PASS"
    assert len(report["tests"])==4
    assert all("body" not in record for record in report["tests"])
    assert all(record["response_sha256"] for record in report["tests"])


def test_leakage_blocks_release_and_response_is_not_logged(monkeypatch):
    monkeypatch.setattr(smoke,"request_json",
                        lambda base,path,token:{"status":200,"body":"AGENT_B_SECRET"})
    report=smoke.run("https://preview.example","a","b","1","2",SHA)
    assert report["status"]=="BLOCKED"
    assert "AGENT_B_SECRET" not in str(report)
    assert {row["status"] for row in report["tests"]}=={"PASS","BLOCKED"}
