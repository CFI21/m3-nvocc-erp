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


def test_denial_response_with_foreign_booking_fields_is_leakage(monkeypatch):
    def fake_request(base,path,token):
        own=(token=="a" and path.endswith("/1")) or (token=="b" and path.endswith("/2"))
        if own:
            return {"status":200,"body":'{"id":'+path.rsplit("/",1)[-1]+',"module":"booking"}'}
        return {"status":403,"body":'{"detail":"denied","agent_code":"FOREIGN_AGENT"}'}
    monkeypatch.setattr(smoke,"request_json",fake_request)
    report=smoke.run("https://m3-pr124-booking-api-preview.onrender.com",
                     "a","b","1","2",SHA)
    assert report["status"]=="BLOCKED"
    assert [item["status"] for item in report["tests"]]==[
        "PASS","BLOCKED","BLOCKED","PASS"]
    assert "FOREIGN_AGENT" not in str(report)


@pytest.mark.parametrize("denied_body", [
    '{"detail":{"agent_id":7,"booking_id":92}}',
    '{"detail":[{"booking_ref":"FOREIGN-BOOKING"}]}',
    '{"errors":[{"context":{"customer_id":33}}]}',
])
def test_nested_denied_payload_leakage_is_blocked(monkeypatch,denied_body):
    def fake_request(base,path,token):
        own=(token=="a" and path.endswith("/1")) or (token=="b" and path.endswith("/2"))
        if own:
            return {"status":200,"body":'{"id":'+path.rsplit("/",1)[-1]+',"module":"booking"}'}
        return {"status":404,"body":denied_body}
    monkeypatch.setattr(smoke,"request_json",fake_request)
    report=smoke.run("https://m3-pr124-booking-api-preview.onrender.com",
                     "a","b","1","2",SHA)
    assert report["status"]=="BLOCKED"
    assert report["tests"][1]["status"]=="BLOCKED"
    assert report["tests"][2]["status"]=="BLOCKED"


@pytest.mark.parametrize("denial", [
    '{"Detail":{"Agent_ID":17}}',
    '{"Errors":[{"Customer_Name":"Other Agent Customer"}]}',
    '{"DETAIL":{"FIELDS":{"POL":"NLRTM"}}}',
])
def test_case_variant_denial_leakage_is_blocked(monkeypatch,denial):
    def mocked(base,path,token):
        own=(token=="a" and path.endswith("/1")) or (token=="b" and path.endswith("/2"))
        return {"status":200 if own else 403,
                "body":('{"id":'+path.rsplit("/",1)[-1]+',"module":"booking"}')
                       if own else denial}
    monkeypatch.setattr(smoke,"request_json",mocked)
    out=smoke.run("https://m3-pr124-booking-api-preview.onrender.com","a","b","1","2",SHA)
    assert out["status"]=="BLOCKED"
    assert out["tests"][1]["status"]=="BLOCKED"
    assert out["tests"][2]["status"]=="BLOCKED"


def test_bulk_list_agent_isolation_passes_when_only_own_booking_visible(monkeypatch):
    def fake(base,path,token):
        assert path=="/api/v1/booking"
        own=1 if token=="a" else 2
        return {"status":200,"body":'{"module":"booking","count":1,"records":[{"id":'+str(own)+',"module":"booking"}]}'}
    monkeypatch.setattr(smoke,"request_json",fake)
    checks=smoke.check_list_scope(
        "https://m3-pr124-booking-api-preview.onrender.com","a","b","1","2")
    assert [x["id"] for x in checks]==["SEC-005","SEC-006"]
    assert all(x["status"]=="PASS" for x in checks)


@pytest.mark.parametrize("payload",[
    '{"module":"booking","count":2,"records":[{"id":1,"module":"booking"},{"id":2,"module":"booking"}]}',
    '{"module":"booking","count":1,"records":[{"id":2,"module":"booking"}]}',
    '{"module":"booking","count":1,"records":[]}',
    '<html>login</html>',
])
def test_bulk_list_foreign_ids_and_invalid_responses_block(monkeypatch,payload):
    monkeypatch.setattr(smoke,"request_json",
                        lambda *_args:{"status":200,"body":payload})
    checks=smoke.check_list_scope(
        "https://m3-pr124-booking-api-preview.onrender.com","a","b","1","2")
    assert any(x["status"]=="BLOCKED" for x in checks)
    assert all("body" not in x for x in checks)


def test_unauthenticated_booking_reads_are_denied(monkeypatch):
    def fake(base,path,token):
        assert token==""
        return {"status":401,"body":'{"detail":{"code":"SESSION_REQUIRED"}}'}
    monkeypatch.setattr(smoke,"request_json",fake)
    results=smoke.check_unauthenticated(
        "https://m3-pr124-booking-api-preview.onrender.com","1","2")
    assert [item["id"] for item in results]==["SEC-007","SEC-008"]
    assert all(item["status"]=="PASS" for item in results)


@pytest.mark.parametrize("status",[200,302,403,404,500])
def test_missing_session_requires_401_not_just_non_200(monkeypatch,status):
    monkeypatch.setattr(smoke,"request_json",
                        lambda *_args:{"status":status,"body":"redacted"})
    results=smoke.check_unauthenticated(
        "https://m3-pr124-booking-api-preview.onrender.com","1","2")
    assert all(item["status"]=="BLOCKED" for item in results)


def test_redirect_returned_as_normal_response_is_still_blocked(monkeypatch):
    class Response:
        status=302
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self,n): return b""
    class Opener:
        def open(self,request,timeout): return Response()
    monkeypatch.setattr(smoke.urllib.request,"build_opener",lambda *_args:Opener())
    with pytest.raises(ValueError,match="UNEXPECTED_AUTH_REDIRECT"):
        smoke.request_json("https://m3-pr124-booking-api-preview.onrender.com",
                           "/api/v1/booking/1","agent-a")


@pytest.mark.parametrize("left,right",[
    ("1","01"),("0","2"),("1","0"),("١","2"),("1","١")
])
def test_booking_ids_must_be_distinct_positive_ascii_canonical(left,right):
    with pytest.raises(ValueError,match="DISTINCT_NUMERIC_BOOKING_IDS_REQUIRED"):
        smoke.run("https://m3-pr124-booking-api-preview.onrender.com",
                  "a","b",left,right,SHA)
