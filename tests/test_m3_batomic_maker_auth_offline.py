"""Offline Maker-only safety checks. No network, credentials or DB writes."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

path=Path(__file__).resolve().parents[1]/"scripts/m3_batomic_maker_auth_only.py"
spec=importlib.util.spec_from_file_location("maker_gate",path)
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

HOST=gate.HOST

@pytest.mark.parametrize("url,good",[
    (f"postgresql://tester:fake@{HOST}/neondb?sslmode=require",True),
    (f"postgresql://tester:fake@{HOST}/neondb?sslmode=disable",False),
    (f"postgresql://tester:fake@{HOST}/other?sslmode=require",False),
    (f"postgresql://tester:fake@{HOST.replace('.c-','-pooler.c-')}/neondb?sslmode=require",False),
    (f"postgresql://tester:fake@{HOST}/neondb?sslmode=require&pgbouncer=true",False),
    ("postgresql://tester:fake@wrong.neon.tech/neondb?sslmode=require",False),
])
def test_exact_database_host(monkeypatch,url,good):
    monkeypatch.setenv("DATABASE_URL",url)
    monkeypatch.setenv("M3_UAT_DB_HOST",HOST)
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA","1")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC","OFF")
    monkeypatch.setenv("M3_LIVE_PROVIDERS","OFF")
    monkeypatch.setenv("M3_REAL_MONEY","OFF")
    monkeypatch.setenv("M3_MAKER_PASSWORD","dummy")
    monkeypatch.setenv("M3_MAKER_MFA","123456")
    if good: gate.assert_target()
    else:
        with pytest.raises(RuntimeError): gate.assert_target()

def test_missing_preservation_flag(monkeypatch):
    monkeypatch.setenv("DATABASE_URL",f"postgresql://tester:fake@{HOST}/neondb?sslmode=require")
    monkeypatch.setenv("M3_UAT_DB_HOST",HOST)
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA","0")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC","OFF")
    monkeypatch.setenv("M3_LIVE_PROVIDERS","OFF")
    monkeypatch.setenv("M3_REAL_MONEY","OFF")
    monkeypatch.setenv("M3_MAKER_PASSWORD","dummy")
    monkeypatch.setenv("M3_MAKER_MFA","123456")
    with pytest.raises(RuntimeError,match="M3_UAT_ISOLATION_FLAGS_INVALID"):
        gate.assert_target()

def test_no_login_credential_leak_to_app_process():
    content=path.read_text()
    assert "env.pop('M3_MAKER_PASSWORD', None)" in content
    assert "env.pop('M3_MAKER_MFA', None)" in content
    assert "/bulk/submit" not in content

def test_pinned_revision_fail_closed(monkeypatch,tmp_path):
    monkeypatch.setattr(gate.subprocess,"run",lambda *args,**kwargs: SimpleNamespace(stdout=gate.PINNED_SHA))
    gate.assert_revision(tmp_path)
    monkeypatch.setattr(gate.subprocess,"run",lambda *args,**kwargs: SimpleNamespace(stdout="0"*40))
    with pytest.raises(RuntimeError,match="PINNED_SHA_MISMATCH"):
        gate.assert_revision(tmp_path)

def test_credential_preflight_no_login_without_valid_hash(monkeypatch):
    import hashlib
    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *args): pass
        def fetchone(self): return ("invalid-hash",)
    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return FakeCursor()
        def rollback(self): pass
    monkeypatch.setenv("M3_MAKER_PASSWORD","nonmatching")
    monkeypatch.setenv("DATABASE_URL","postgresql://placeholder")
    monkeypatch.setattr(gate.psycopg,"connect",lambda *args,**kwargs: FakeConn())
    with pytest.raises(RuntimeError,match="MAKER_CREDENTIAL_PRECHECK_FAILED"):
        gate.assert_maker_password_matches_database()

def test_credential_preflight_accepts_matching_hash(monkeypatch):
    import hashlib
    password="nonsecret-fixture"
    expected=hashlib.pbkdf2_hmac("sha256",password.encode(),b"m3-test-salt",120000).hex()
    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *args): pass
        def fetchone(self): return (expected,)
    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return FakeCursor()
        def rollback(self): pass
    monkeypatch.setenv("M3_MAKER_PASSWORD",password)
    monkeypatch.setenv("DATABASE_URL","postgresql://placeholder")
    monkeypatch.setattr(gate.psycopg,"connect",lambda *args,**kwargs: FakeConn())
    gate.assert_maker_password_matches_database()

def test_full_master_and_iam_snapshot_coverage():
    assert {"md_records", "md_versions", "md_aliases", "md_change_requests",
            "md_audit", "md_bulk_idempotency"} <= set(gate.MD_TABLES)
    assert "iam_audit_events" in gate.OTHER_TABLES
    source = path.read_text()
    assert "for t in MD_TABLES + OTHER_TABLES" in source
    assert "for table in MD_TABLES + AUTH_TABLES + OTHER_TABLES" in source

@pytest.mark.parametrize("unsafe_variable", ["M3_LIVE_PROVIDERS", "M3_REAL_MONEY"])
def test_provider_or_real_money_switch_fail_closed(monkeypatch, unsafe_variable):
    monkeypatch.setenv("DATABASE_URL", f"postgresql://tester:fake@{HOST}/neondb?sslmode=require")
    monkeypatch.setenv("M3_UAT_DB_HOST", HOST)
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA", "1")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC", "OFF")
    monkeypatch.setenv("M3_LIVE_PROVIDERS", "OFF")
    monkeypatch.setenv("M3_REAL_MONEY", "OFF")
    monkeypatch.setenv("M3_MAKER_PASSWORD", "dummy")
    monkeypatch.setenv("M3_MAKER_MFA", "123456")
    monkeypatch.setenv(unsafe_variable, "ON")
    with pytest.raises(RuntimeError, match="M3_UAT_ISOLATION_FLAGS_INVALID"):
        gate.assert_target()

def test_transient_maker_session_revoked_without_exposing_token(monkeypatch):
    token = "private-in-memory-test-token"
    invoked = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, args=None):
            if args: invoked.append(args[0])
        def fetchone(self): return ("REVOKED", "2026-11-04T11:00:00Z")
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return Cursor()
        def rollback(self): pass
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(gate.psycopg, "connect", lambda *args, **kwargs: Conn())
    monkeypatch.setattr(gate, "http_json", lambda *args, **kwargs:
                        {"status": "LOGGED_OUT"} if kwargs.get("session_token") == token else {})
    before = {table: (0, "original") for table in gate.MD_TABLES}
    monkeypatch.setattr(gate, "capture", lambda: dict(before))
    gate.revoke_test_session(token, before)
    assert invoked == [token]


def test_logout_failure_is_fail_closed_without_exception_details(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive-session-value-that-must-not-leak")
    monkeypatch.setattr(gate, "http_json", fail)
    with pytest.raises(RuntimeError, match="^MAKER_TEST_SESSION_CLEANUP_NOT_VERIFIED$") as exc:
        gate.revoke_test_session("private-token", {})
    assert "sensitive" not in str(exc.value)


def test_logout_runs_in_finally_and_does_not_change_master_records():
    source = path.read_text()
    assert "if session_token is not None:" in source
    assert "revoke_test_session(session_token, startup)" in source
    assert "if any(after[t] != baseline[t] for t in MD_TABLES):" in source
    assert "proc.terminate()" in source
