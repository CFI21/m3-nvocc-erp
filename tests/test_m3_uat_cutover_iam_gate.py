"""UAT read-only readiness must block a cutover without independent IAM users."""
import pytest
from app import uat_bootstrap

def test_uat_cutover_requires_independent_maker_checker(monkeypatch):
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA", "1")
    monkeypatch.setattr(uat_bootstrap.db, "DATABASE_URL", "postgresql://dummy@mock.neon.tech/uat")
    monkeypatch.setattr(uat_bootstrap.db, "_uat_target_approved", lambda: True)
    class Row:
        def __init__(self, n): self.n = n
        def fetchone(self): return {"n":self.n}
    class DB:
        closed=False
        def execute(self, sql):
            if "username IN" in sql: return Row(1)
            return Row(0)
        def close(self): self.closed=True
    db=DB()
    monkeypatch.setattr(uat_bootstrap.db, "connect", lambda: db)
    monkeypatch.setattr(uat_bootstrap.db, "table_exists", lambda conn, name: True)
    with pytest.raises(RuntimeError, match="UAT_IAM_INITIALIZATION_REQUIRED"):
        uat_bootstrap.ensure_uat_ready()
    assert db.closed
