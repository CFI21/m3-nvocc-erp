"""Offline checks of B-Atomic startup target controls. No database or network."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

path = Path(__file__).resolve().parents[1] / "scripts/m3_batomic_startup_gate.py"
spec = importlib.util.spec_from_file_location("startup_gate", path)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

HOST = gate.EXPECTED_HOST

@pytest.mark.parametrize("url,expected", [
    (f"postgresql://test:fake@{HOST}/neondb?sslmode=require", True),
    (f"postgresql://test:fake@{HOST}/neondb?sslmode=disable", False),
    (f"postgresql://test:fake@{HOST}/other?sslmode=require", False),
    (f"postgresql://test:fake@{HOST.replace('.c-', '-pooler.c-')}/neondb?sslmode=require", False),
    (f"postgresql://test:fake@{HOST}/neondb?sslmode=require&pgbouncer=true", False),
    ("postgresql://test:fake@fake.neon.tech/neondb?sslmode=require", False),
])
def test_direct_target_fail_closed(monkeypatch, url, expected):
    monkeypatch.setenv("DATABASE_URL",url)
    monkeypatch.setenv("M3_UAT_DB_HOST",HOST)
    if expected:
        gate.validate_target()
    else:
        with pytest.raises(RuntimeError):
            gate.validate_target()

def test_host_guard_mismatch(monkeypatch):
    monkeypatch.setenv("DATABASE_URL",f"postgresql://test:fake@{HOST}/neondb?sslmode=require")
    monkeypatch.setenv("M3_UAT_DB_HOST","wrong.neon.tech")
    with pytest.raises(RuntimeError):
        gate.validate_target()

def test_revision_guard(monkeypatch,tmp_path):
    def fake_run(*args,**kwargs):
        return SimpleNamespace(stdout=gate.EXPECTED_SHA+"\\n")
    monkeypatch.setattr(gate.subprocess,"run",fake_run)
    gate.validate_checkout(tmp_path)
    monkeypatch.setattr(gate.subprocess,"run",lambda *a,**kw: SimpleNamespace(stdout="0"*40))
    with pytest.raises(RuntimeError):
        gate.validate_checkout(tmp_path)

def test_snapshot_has_only_expected_tables():
    assert len(gate.TABLES) == 6
    assert len(set(gate.TABLES)) == len(gate.TABLES)
