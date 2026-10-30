import sqlite3

from app import masterdata


def test_bulk_preview_rejects_case_variant_existing_and_pending_keys(tmp_path, monkeypatch):
    db_path = str(tmp_path / "bulk_casefold.db")

    def connect():
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        return db

    db = connect()
    masterdata.init_schema(db)
    db.execute(
        "INSERT INTO md_records(domain,record_key,display_name,payload_json,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("agent", "AGENT-001", "Existing Agent", '{"name":"Existing Agent"}',
         "ACTIVE", "2026-10-28", "2026-10-28"),
    )
    db.execute(
        "INSERT INTO md_change_requests(change_ref,domain,record_key,operation,payload_json,reason,status,maker,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("MDC-1234567890", "agent", "PENDING-002", "CREATE",
         '{"name":"Pending Agent"}', "UAT import", "PENDING", "maker", "2026-10-28"),
    )
    db.commit()
    db.close()
    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda c, token: ({"username": "md.maker"}, {"MASTER_DATA"}),
    )
    request = masterdata.BulkRequest(
        domain="agent",
        rows=[
            masterdata.BulkRow(record_key="agent-001", operation="CREATE",
                               payload={"name": "Different Agent"}, reason="UAT collision"),
            masterdata.BulkRow(record_key="pending-002", operation="CREATE",
                               payload={"name": "Another Agent"}, reason="UAT collision"),
        ],
    )
    result = masterdata.bulk_preview(request, x_m3_session="synthetic-unit-test-session")
    assert result["valid"] == 0
    assert result["rejected"] == 2
    assert "RECORD_ALREADY_EXISTS" in result["rows"][0]["errors"]
    assert "PENDING_CHANGE_EXISTS" in result["rows"][1]["errors"]


def test_direct_change_rejects_case_variant_existing_and_pending_keys(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException

    db_path = str(tmp_path / "direct_casefold.db")

    def connect():
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        return db

    db = connect()
    masterdata.init_schema(db)
    db.execute(
        "INSERT INTO md_records(domain,record_key,display_name,payload_json,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("agent", "AGENT-001", "Original Agent", '{"name":"Original Agent"}',
         "ACTIVE", "2026-10-28", "2026-10-28"),
    )
    db.execute(
        "INSERT INTO md_change_requests(change_ref,domain,record_key,operation,payload_json,reason,status,maker,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("MDC-1234567890", "agent", "PENDING-002", "CREATE",
         '{"name":"Pending Agent"}', "UAT import", "PENDING", "maker", "2026-10-28"),
    )
    db.commit()
    db.close()

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda c, token: ({"username": "md.maker", "user_ref": "md.maker"}, {"MASTER_DATA"}),
    )

    for key, code in (("agent-001", "DUPLICATE_MASTER_KEY"),
                      ("pending-002", "PENDING_CHANGE_EXISTS")):
        with pytest.raises(HTTPException) as exc:
            masterdata.create_change(
                masterdata.Change(domain="agent", record_key=key, operation="CREATE",
                                  payload={"name": "Another Agent"}, reason="UAT collision"),
                x_m3_session="synthetic-unit-test-session",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == code

    db = connect()
    assert db.execute("SELECT COUNT(*) FROM md_change_requests").fetchone()[0] == 1
    db.close()


def test_checker_rejects_case_variant_race_and_self_approval(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException

    db_path = str(tmp_path / "checker_casefold.db")

    def connect():
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        return db

    db = connect()
    masterdata.init_schema(db)
    db.execute(
        "INSERT INTO md_records(domain,record_key,display_name,payload_json,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("agent", "AGENT-001", "Original Agent", '{"name":"Original Agent"}',
         "ACTIVE", "2026-10-28", "2026-10-28"),
    )
    db.execute(
        "INSERT INTO md_change_requests(change_ref,domain,record_key,operation,payload_json,reason,status,maker,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("MDC-CASE-RACE", "agent", "agent-001", "CREATE",
         '{"name":"Another Agent"}', "Pre-existing pending race",
         "PENDING", "maker.user", "2026-10-28"),
    )
    db.commit()
    db.close()
    monkeypatch.setattr(masterdata, "connect", connect)

    def actor(c, token):
        identity = "maker.user" if token == "maker" else "checker.user"
        return {"username": identity, "user_ref": identity}, {"MASTER_DATA_MANAGER"}

    monkeypatch.setattr(masterdata, "authenticated_actor", actor)

    with pytest.raises(HTTPException) as same_actor:
        masterdata.decide("MDC-CASE-RACE",
                          masterdata.Decision(decision="APPROVE"),
                          x_m3_session="maker")
    assert same_actor.value.status_code == 409
    assert same_actor.value.detail["code"] == "FOUR_EYES_VIOLATION"

    with pytest.raises(HTTPException) as duplicate:
        masterdata.decide("MDC-CASE-RACE",
                          masterdata.Decision(decision="APPROVE"),
                          x_m3_session="checker")
    assert duplicate.value.status_code == 409
    assert duplicate.value.detail["code"] == "DUPLICATE_MASTER_KEY"

    db = connect()
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref='MDC-CASE-RACE'"
    ).fetchone()[0] == "PENDING"
    assert db.execute("SELECT COUNT(*) FROM md_versions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM md_records").fetchone()[0] == 1
    db.close()
