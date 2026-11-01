"""Master Data seed must not erase existing rows, even when only audit exists."""
from app import masterdata_seed


def test_seed_preserves_audit_only_partial_database(monkeypatch):
    calls = []
    class Conn:
        def execute(self, sql, params=None):
            calls.append(sql)
            if sql.startswith("SELECT 1 FROM md_audit"):
                return self
            if sql.startswith("SELECT 1 FROM"):
                return self
            raise AssertionError("seed attempted to write into existing UAT database: " + sql)
        def fetchone(self):
            return {"exists": 1} if calls[-1].startswith("SELECT 1 FROM md_audit") else None
        def close(self):
            calls.append("CLOSED")
    monkeypatch.setattr(masterdata_seed, "connect", lambda: Conn())
    monkeypatch.setattr(masterdata_seed, "init_schema", lambda conn: None)
    masterdata_seed.run()
    assert calls[-1] == "CLOSED"
    assert not any("DELETE" in sql for sql in calls)
