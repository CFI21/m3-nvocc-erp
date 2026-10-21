"""IAM persistence guard tests; no real passwords, users, or databases accessed."""
import pytest
from app import admin_seed


class FakeConn:
    def __init__(self, exists):
        self.exists=exists
        self.closed=False
        self.writes=0
    def execute(self, sql, params=None):
        if sql == "SELECT 1 FROM iam_users LIMIT 1":
            return self
        self.writes += 1
        raise AssertionError("Unexpected IAM write")
    def fetchone(self):
        return {"id":1} if self.exists else None
    def close(self):
        self.closed=True
    def executescript(self, sql):
        self.writes+=1
        raise AssertionError("Bootstrap must remain opt-in")


def test_existing_iam_never_reseeded(monkeypatch):
    db=FakeConn(True)
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA","1")
    monkeypatch.setattr(admin_seed,"connect",lambda: db)
    admin_seed.run()
    assert db.closed and db.writes == 0


def test_empty_iam_refuses_implicit_seed(monkeypatch):
    db=FakeConn(False)
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA","1")
    monkeypatch.delenv("M3_UAT_ALLOW_INITIAL_IAM_SEED",raising=False)
    monkeypatch.setattr(admin_seed,"connect",lambda: db)
    with pytest.raises(RuntimeError,match="UAT_IAM_BOOTSTRAP_APPROVAL_REQUIRED"):
        admin_seed.run()
    assert db.closed and db.writes == 0
