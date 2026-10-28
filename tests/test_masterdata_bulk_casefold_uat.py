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
