"""Regression coverage for PostgreSQL-safe master data read operations."""
from app import masterdata

class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.statements = []
    def execute(self, statement, args=()):
        self.statements.append(statement)
        return iter(self.rows)
    def close(self):
        self.closed = True

def test_duplicate_review_works_without_sqlite_aggregate(monkeypatch):
    conn = FakeConnection([
        dict(domain="agent", display_name="Harbor", record_key="A01"),
        dict(domain="agent", display_name="HARBOR", record_key="A02"),
        dict(domain="agent", display_name="Other", record_key="A03"),
    ])
    monkeypatch.setattr(masterdata, "connect", lambda: conn)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: True)
    monkeypatch.setattr(masterdata, "init_schema", lambda c: (_ for _ in ()).throw(AssertionError("SQLite schema forbidden")))
    assert masterdata.duplicates() == [dict(domain="agent", norm="harbor", n=2, keys="A01,A02")]
    assert all("GROUP_CONCAT" not in q for q in conn.statements)
    assert conn.closed

def test_read_views_do_not_initialize_sqlite_on_postgres(monkeypatch):
    monkeypatch.setattr(masterdata, "using_postgres", lambda: True)
    monkeypatch.setattr(masterdata, "init_schema", lambda c: (_ for _ in ()).throw(AssertionError("SQLite schema forbidden")))

    def synthetic_authorized_actor(conn, token):
        # Only the test fixture bypasses IAM; production endpoint authorization stays intact.
        assert token == "synthetic-iam-test-session"
        return {"user_ref": "uat-read-compat"}, {"MASTER_DATA"}

    monkeypatch.setattr(masterdata, "authenticated_actor", synthetic_authorized_actor)
    for fn, args, kwargs in [
        (masterdata.versions, ("agent", "A01"), {"x_m3_session": "synthetic-iam-test-session"}),
        (masterdata.audit_list, (), {"x_m3_session": "synthetic-iam-test-session"}),
        (masterdata.quality, (), {}),
    ]:
        conn = FakeConnection([])
        monkeypatch.setattr(masterdata, "connect", lambda: conn)
        assert fn(*args, **kwargs) == []
        assert all("sqlite_master" not in statement for statement in conn.statements)
        assert conn.closed
