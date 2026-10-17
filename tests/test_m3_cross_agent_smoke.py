"""Authenticated cross-Agent smoke runner unit tests (no network)."""
import pytest
from scripts import m3_cross_agent_smoke as smoke

SHA="f"*40


def test_missing_auth_and_non_https_fail_closed():
    with pytest.raises(ValueError,match="APPROVED_PREVIEW_API_REQUIRED"):
        smoke.run("http://localhost","a","b","1","2",SHA)
    with pytest.raises(ValueError,match="DISTINCT_AUTHENTICATED_SESSIONS_REQUIRED"):
        smoke.run("https://m3-pr124-booking-api-preview.onrender.com","a","a","1","2",SHA)
    with pytest.raises(ValueError,match="DISTINCT_NUMERIC_BOOKING_IDS_REQUIRED"):
        smoke.run("https://m3-pr124-booking-api-preview.onrender.com","a","b","1","1",SHA)


def test_cross_agent_smoke_positive_and_negative_paths(monkeypatch):
    statuses={("a","/api/v1/booking/1"):200,
              ("a","/api/v1/booking/2"):403,
              ("b","/api/v1/booking/1"):404,
              ("b","/api/v1/booking/2"):200}
    monkeypatch.setattr(smoke,"request_json",
                        lambda base,path,token:{"status":statuses[(token,path)],
                                               "body":'{"id":1,"module":"booking"}' if path.endswith("/1") else '{"id":2,"module":"booking"}'})
    report=smoke.run("https://m3-pr124-booking-api-preview.onrender.com","a","b","1","2",SHA)
    assert report["status"]=="PASS"
    assert len(report["tests"])==4
    assert all("body" not in record for record in report["tests"])
    assert all(record["response_sha256"] for record in report["tests"])


def test_leakage_blocks_release_and_response_is_not_logged(monkeypatch):
    monkeypatch.setattr(smoke,"request_json",
                        lambda base,path,token:{"status":200,"body":"AGENT_B_SECRET"})
    report=smoke.run("https://m3-pr124-booking-api-preview.onrender.com","a","b","1","2",SHA)
    assert report["status"]=="BLOCKED"
    assert "AGENT_B_SECRET" not in str(report)
    assert all(row["status"]=="BLOCKED" for row in report["tests"])


@pytest.mark.parametrize("url",[
    "https://attacker.example",
    "https://m3-pr124-booking-api-preview.onrender.com.attacker.example",
    "https://user:password@m3-pr124-booking-api-preview.onrender.com",
    "https://m3-pr124-booking-api-preview.onrender.com:8443",
    "https://m3-pr124-booking-api-preview.onrender.com/path",
    "http://m3-pr124-booking-api-preview.onrender.com",
])
def test_session_token_never_sent_to_unapproved_host(url):
    with pytest.raises(ValueError,match="APPROVED_PREVIEW_API_REQUIRED"):
        smoke.run(url,"agent-a-session","agent-b-session","1","2",SHA)


def test_redirects_are_explicitly_disabled():
    handler=smoke._NoRedirect()
    assert handler.redirect_request(None,None,302,"Found",{},
                                    "https://attacker.example/collect") is None


def test_generic_200_or_wrong_booking_is_not_success(monkeypatch):
    monkeypatch.setattr(smoke,"request_json",
                        lambda base,path,token:{"status":200,"body":"<html>login page</html>"}
                        if token=="a" and path.endswith("/1") else
                        {"status":403,"body":"denied"})
    report=smoke.run("https://m3-pr124-booking-api-preview.onrender.com",
                     "a","b","1","2",SHA)
    assert report["status"]=="BLOCKED"
    assert report["tests"][0]["status"]=="BLOCKED"


def test_auth_redirect_cannot_be_mistaken_for_agent_isolation(monkeypatch):
    import urllib.error
    from io import BytesIO
    class RedirectOpener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(req.full_url,302,"Found",
                                         {"Location":"/login"},BytesIO(b""))
    monkeypatch.setattr(smoke.urllib.request,"build_opener",
                        lambda *_handlers:RedirectOpener())
    with pytest.raises(ValueError,match="UNEXPECTED_AUTH_REDIRECT"):
        smoke.request_json("https://m3-pr124-booking-api-preview.onrender.com",
                           "/api/v1/booking/1","token")


def test_redirect_failure_is_blocked_without_logging_session(monkeypatch):
    def redirected(base,path,token):
        raise ValueError("UNEXPECTED_AUTH_REDIRECT")
    monkeypatch.setattr(smoke,"request_json",redirected)
    result=smoke.run("https://m3-pr124-booking-api-preview.onrender.com",
                     "sensitive-a","sensitive-b","1","2",SHA)
    assert result["status"]=="BLOCKED"
    assert all(item["status"]=="BLOCKED" for item in result["tests"])
    assert "sensitive-a" not in str(result)
