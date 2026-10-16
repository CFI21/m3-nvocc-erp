"""UAT diagnostic must remain read-only and never expose credentials."""
import pytest
from app import db, uat_iam_diagnostics as diag


def test_diagnostics_fail_closed_without_explicit_isolated_uat(monkeypatch):
    monkeypatch.delenv("M3_UAT_PRESERVE_DATA", raising=False)
    with pytest.raises(RuntimeError, match="ISOLATED_UAT_DATABASE_REQUIRED"):
        diag.inspect_accounts()


def test_diagnostics_only_read_safe_metadata(monkeypatch):
    class Connection:
        def __init__(self):
            self.queries = []
            self.closed = False

        def execute(self, sql, params):
            self.queries.append((sql, params))
            assert sql.strip().upper().startswith("SELECT")
            assert len(params) == 1
            if params[0] == "admin":
                return self
            return Empty()

        def fetchone(self):
            return {
                "user_ref": "USR-001", "status": "ACTIVE",
                "mfa_required": 1, "failed_attempts": 2,
                "password_present": 1,
            }

        def close(self):
            self.closed = True

    class Empty:
        def fetchone(self):
            return None

    conn = Connection()
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA", "1")
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://placeholder", raising=False)
    monkeypatch.setattr(db, "_uat_target_approved", lambda: True)
    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(db, "table_exists", lambda c, name: name == "iam_users")
    report = diag.inspect_accounts()
    assert conn.closed
    assert [a["username"] for a in report["accounts"]] == list(diag.ACCOUNTS)
    assert report["accounts"][0]["password_provisioned"] is True
    assert report["accounts"][1]["exists"] is False
    assert all("hash" not in k and "token" not in k for a in report["accounts"] for k in a)
    assert len(conn.queries) == 3
