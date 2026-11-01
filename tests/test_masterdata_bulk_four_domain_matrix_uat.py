"""Four-domain bulk CREATE/UPDATE UAT with disposable SQLite only.

No external database, Render instance, or real master record is modified.
"""
import json
import sqlite3
import pytest
from fastapi import HTTPException
from app import masterdata as md

DOMAINS = ("agent", "customer", "equipment-type", "container-type")

@pytest.mark.parametrize("domain", DOMAINS)
def test_bulk_create_update_versions_and_four_eyes(domain, tmp_path, monkeypatch):
    path = tmp_path / "bulk-four-domain.sqlite"
    def connect():
        c = sqlite3.connect(path, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c
    c = connect()
    md.init_schema(c)
    c.close()
    monkeypatch.setattr(md, "connect", connect)
    monkeypatch.setattr(md, "sync_operational_projection", lambda *a: None)
    actors = {
        "maker": ({"user_ref": "synthetic-maker", "username": "md.maker"}, {"MASTER_DATA"}),
        "checker": ({"user_ref": "synthetic-checker", "username": "md.checker"}, {"MASTER_DATA_MANAGER"}),
    }
    def authenticate(c, token):
        if token not in actors:
            raise HTTPException(401, {"code": "INVALID_SESSION"})
        return actors[token]
    monkeypatch.setattr(md, "authenticated_actor", authenticate)

    key = "SYNTHETIC-001"
    def request(operation, name):
        return md.BulkRequest(domain=domain, rows=[{
            "record_key": key, "operation": operation,
            "payload": {"name": name}, "reason": "Disposable acceptance regression"
        }])
    create = request("CREATE", "Synthetic Original")
    assert md.bulk_preview(create, x_m3_session="maker")["valid"] == 1
    outcome = md.bulk_submit(create, x_m3_session="maker")
    assert outcome["pending"] == 1 and outcome["rejected"] == 0
    ref1 = outcome["rows"][0]["change_ref"]
    c = connect()
    assert c.execute("SELECT COUNT(*) FROM md_records").fetchone()[0] == 0
    assert c.execute("SELECT status FROM md_change_requests WHERE change_ref=?", (ref1,)).fetchone()[0] == "PENDING"
    c.close()
    assert "PENDING_CHANGE_EXISTS" in md.bulk_preview(create, x_m3_session="maker")["rows"][0]["errors"]
    with pytest.raises(HTTPException):
        md.decide(ref1, md.Decision(decision="APPROVE"), x_m3_session="maker")
    approved = md.decide(ref1, md.Decision(decision="APPROVE"), x_m3_session="checker")
    assert approved["status"] == "APPROVED" and approved["version"] == 1
    assert "RECORD_ALREADY_EXISTS" in md.bulk_preview(create, x_m3_session="maker")["rows"][0]["errors"]

    update = request("UPDATE", "Synthetic Updated")
    assert md.bulk_preview(update, x_m3_session="maker")["valid"] == 1
    outcome = md.bulk_submit(update, x_m3_session="maker")
    assert outcome["pending"] == 1 and outcome["rejected"] == 0
    ref2 = outcome["rows"][0]["change_ref"]
    c = connect()
    assert json.loads(c.execute("SELECT payload_json FROM md_records WHERE domain=? AND record_key=?", (domain,key)).fetchone()[0])["name"] == "Synthetic Original"
    c.close()
    with pytest.raises(HTTPException):
        md.decide(ref2, md.Decision(decision="APPROVE"), x_m3_session="maker")
    approved = md.decide(ref2, md.Decision(decision="APPROVE"), x_m3_session="checker")
    assert approved["status"] == "APPROVED" and approved["version"] == 2
    c = connect()
    record = c.execute("SELECT version,payload_json,approved_by FROM md_records WHERE domain=? AND record_key=?", (domain,key)).fetchone()
    assert record["version"] == 2 and record["approved_by"] == "synthetic-checker"
    assert json.loads(record["payload_json"])["name"] == "Synthetic Updated"
    versions = c.execute("SELECT version FROM md_versions WHERE domain=? AND record_key=? ORDER BY version", (domain,key)).fetchall()
    assert [v[0] for v in versions] == [1,2]
    actions = [x[0] for x in c.execute("SELECT action FROM md_audit WHERE domain=? AND record_key=?", (domain,key))]
    assert actions.count("CHANGE_REQUEST_CREATE") == 2
    assert actions.count("CHANGE_APPROVE_APPLY") == 2
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM md_audit WHERE domain=? AND record_key=?", (domain,key))
    c.close()

@pytest.mark.parametrize("domain", DOMAINS)
def test_bulk_update_requires_existing_record_and_rejects_unapproved_create(domain, tmp_path, monkeypatch):
    path = tmp_path / "bulk-missing.sqlite"
    def connect():
        c = sqlite3.connect(path, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c
    c = connect()
    md.init_schema(c)
    c.close()
    monkeypatch.setattr(md, "connect", connect)
    monkeypatch.setattr(md, "authenticated_actor", lambda c,t: ({"user_ref":"synthetic-maker","username":"md.maker"},{"MASTER_DATA"}))
    b = md.BulkRequest(domain=domain,rows=[{
        "record_key":"SYNTHETIC-MISSING","operation":"UPDATE",
        "payload":{"name":"Synthetic Missing"},"reason":"Negative regression"
    }])
    preview = md.bulk_preview(b,x_m3_session="maker")
    assert preview["rejected"] == 1
    assert "RECORD_NOT_FOUND" in preview["rows"][0]["errors"]
    with pytest.raises(HTTPException) as ex:
        md.bulk_submit(b,x_m3_session="maker")
    assert ex.value.status_code == 422
    c = connect()
    assert c.execute("SELECT COUNT(*) FROM md_change_requests").fetchone()[0] == 0
    c.close()
