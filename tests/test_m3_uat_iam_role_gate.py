"""Fail-closed role separation for the existing isolated Neon UAT identities."""
import pytest
from app import uat_bootstrap


@pytest.mark.parametrize("roles,should_pass", [
    ([("md.maker", "ACTIVE", "MASTER_DATA"), ("md.checker", "ACTIVE", "MASTER_DATA_MANAGER")], True),
    ([("md.maker", "INACTIVE", "MASTER_DATA"), ("md.checker", "ACTIVE", "MASTER_DATA_MANAGER")], False),
    ([("md.maker", "ACTIVE", "MASTER_DATA_MANAGER"), ("md.checker", "ACTIVE", "MASTER_DATA")], False),
    ([("md.maker", "ACTIVE", "MASTER_DATA")], False),
    ([("md.maker", "ACTIVE", "MASTER_DATA"), ("md.checker", "ACTIVE", "MASTER_DATA_MANAGER"), ("md.maker", "ACTIVE", "MASTER_DATA_MANAGER")], False),
])
def test_startup_requires_active_separate_maker_checker(monkeypatch, roles, should_pass):
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA", "1")
    monkeypatch.setattr(uat_bootstrap.db, "DATABASE_URL", "postgresql://user@isolated.neon.tech/uat")
    monkeypatch.setattr(uat_bootstrap.db, "_uat_target_approved", lambda: True)

    class Cursor:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0]
        def fetchall(self): return self.rows

    class FakeDB:
        closed = False
        def execute(self, query):
            if "FROM iam_users WHERE username IN" in query:
                return Cursor([{"n": 2}])
            if "JOIN iam_user_roles" in query:
                return Cursor([{"username": name, "status": status, "role_code": role} for name, status, role in roles])
            return Cursor([{"n": 0}])
        def close(self): self.closed = True

    fake = FakeDB()
    monkeypatch.setattr(uat_bootstrap.db, "connect", lambda: fake)
    monkeypatch.setattr(uat_bootstrap.db, "table_exists", lambda conn, name: True)
    if should_pass:
        assert uat_bootstrap.ensure_uat_ready()["schema"] == "ready"
    else:
        with pytest.raises(RuntimeError, match="UAT_IAM_ROLE_SEPARATION_REQUIRED"):
            uat_bootstrap.ensure_uat_ready()
    assert fake.closed
