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
    monkeypatch.setenv("M3_MAKER_PASSWORD","dummy")
    monkeypatch.setenv("M3_MAKER_MFA","dummy")
    if good: gate.assert_target()
    else:
        with pytest.raises(RuntimeError): gate.assert_target()

def test_missing_preservation_flag(monkeypatch):
    monkeypatch.setenv("DATABASE_URL",f"postgresql://tester:fake@{HOST}/neondb?sslmode=require")
    monkeypatch.setenv("M3_UAT_DB_HOST",HOST)
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA","0")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC","OFF")
    monkeypatch.setenv("M3_MAKER_PASSWORD","dummy")
    monkeypatch.setenv("M3_MAKER_MFA","dummy")
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
