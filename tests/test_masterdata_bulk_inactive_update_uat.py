"""Bulk UPDATE preview must reject inactive records before pending submission.

All records are synthetic fixtures in disposable SQLite; no external writes.
"""
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app import masterdata as md


@pytest.mark.parametrize("domain", ("agent", "customer", "equipment-type", "container-type"))
def test_bulk_update_inactive_record_is_rejected_before_submit(domain, tmp_path, monkeypatch):
    db = tmp_path / "bulk-status-preview.sqlite3"

    def connect():
        c = sqlite3.connect(str(db), isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    c = connect()
    md.init_schema(c)
    c.execute(
        """INSERT INTO md_records
           (domain,record_key,display_name,payload_json,status,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (domain, "SYNTH-INACTIVE", "Synthetic Inactive",
         json.dumps({"name": "Synthetic Inactive"}), "INACTIVE", 2,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    c.close()

    monkeypatch.setattr(md, "connect", connect)
    monkeypatch.setattr(
        md, "authenticated_actor",
        lambda c, token: ({"user_ref": "synthetic-maker", "username": "md.maker"}, {"MASTER_DATA"}),
    )

    update = md.BulkRequest(domain=domain, rows=[{
        "record_key": "SYNTH-INACTIVE", "operation": "UPDATE",
        "payload": {"name": "Synthetic Proposed Update"},
        "reason": "Disposable status validation",
    }])

    result = md.bulk_preview(update, x_m3_session="synthetic-session")
    assert result["valid"] == 0 and result["rejected"] == 1
    assert "MASTER_RECORD_NOT_ACTIVE" in result["rows"][0]["errors"]

    with pytest.raises(HTTPException) as error:
        md.bulk_submit(update, x_m3_session="synthetic-session")
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "BULK_PREVIEW_REJECTED"

    c = connect()
    assert c.execute("SELECT COUNT(*) FROM md_change_requests").fetchone()[0] == 0
    assert c.execute(
        "SELECT status,version FROM md_records WHERE domain=? AND record_key=?",
        (domain, "SYNTH-INACTIVE"),
    ).fetchone()[:] == ("INACTIVE", 2)
    c.close()


@pytest.mark.parametrize("domain", ("agent", "customer", "equipment-type", "container-type"))
def test_bulk_update_active_record_remains_valid(domain, tmp_path, monkeypatch):
    db = tmp_path / "bulk-active-preview.sqlite3"

    def connect():
        c = sqlite3.connect(str(db), isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    c = connect()
    md.init_schema(c)
    c.execute(
        """INSERT INTO md_records
           (domain,record_key,display_name,payload_json,status,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (domain, "SYNTH-ACTIVE", "Synthetic Active",
         json.dumps({"name": "Synthetic Active"}), "ACTIVE", 1,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    c.close()
    monkeypatch.setattr(md, "connect", connect)
    monkeypatch.setattr(
        md, "authenticated_actor",
        lambda c, token: ({"user_ref": "synthetic-maker", "username": "md.maker"}, {"MASTER_DATA"}),
    )

    request = md.BulkRequest(domain=domain, rows=[{
        "record_key": "SYNTH-ACTIVE", "operation": "UPDATE",
        "payload": {"name": "Synthetic Active"}, "reason": "Read-only acceptance",
    }])
    preview = md.bulk_preview(request, x_m3_session="synthetic-session")
    assert preview["valid"] == 1 and preview["rejected"] == 0
    c = connect()
    assert c.execute("SELECT COUNT(*) FROM md_change_requests").fetchone()[0] == 0
    c.close()
