"""End-to-end HTTP parsing checks using a loopback server only.

No external requests, production endpoints, database or real secrets.
"""
import importlib.util
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

p = Path(__file__).resolve().parents[1] / "scripts" / "m3_isolated_atomic_bulk_uat.py"
spec = importlib.util.spec_from_file_location("m3_http_uat", p)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@contextmanager
def fake_server(status=200, response=None, content_type="application/json"):
    seen = []
    if response is None:
        response = {"ok": True}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append({"path": self.path,
                         "uat": self.headers.get("X-M3-UAT-Token"),
                         "session": self.headers.get("X-M3-Session"),
                         "idempotency": self.headers.get("Idempotency-Key"),
                         "data": json.loads(body.decode())})
            payload = (json.dumps(response).encode() if isinstance(response, (dict, list))
                       else str(response).encode())
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:" + str(httpd.server_port), seen
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=3)


def test_valid_http_login_preserves_token_and_json(monkeypatch):
    with fake_server(response={"session_token": "fixture-session"}) as (url, seen):
        monkeypatch.setattr(runner, "API", url)
        status, result = runner.call(
            "/api/admin/auth/login", method="POST", token="abc+def=",
            body={"username": "md.maker", "password": "fixture", "mfa_code": "000000"})
    assert status == 200 and result["session_token"] == "fixture-session"
    assert seen[0]["uat"] == "abc+def="
    assert seen[0]["data"]["username"] == "md.maker"


def test_bulk_idempotency_header(monkeypatch):
    with fake_server() as (url, seen):
        monkeypatch.setattr(runner, "API", url)
        status, _ = runner.call(
            "/api/masterdata/bulk/submit-atomic",
            method="POST", token="fixture-access", session="fixture-session",
            extra_headers={"Idempotency-Key": "fixture-key-001"},
            body={"domain": "agent", "rows": []})
    assert status == 200
    assert seen[0]["session"] == "fixture-session"
    assert seen[0]["idempotency"] == "fixture-key-001"


@pytest.mark.parametrize(("status", "body", "ctype"), [
    (400, "<html>Bad request</html>", "text/html"),
    (502, {"detail": "gateway error"}, "application/json"),
    (503, ["unexpected", "json"], "application/json"),
])
def test_gateway_errors_are_never_reflected(monkeypatch, status, body, ctype):
    with fake_server(status=status, response=body, content_type=ctype) as (url, _):
        monkeypatch.setattr(runner, "API", url)
        code, info = runner.call("/api/admin/auth/login", method="POST",
                                 token="fixture-token", body={"username": "md.maker"})
    assert code == status
    assert info["_http_status"] == status
    message = runner.safe_error(code, info)
    assert "HTTP " + str(status) in message
    assert "gateway error" not in message
    assert "<html>" not in message
