"""Executable cross-domain linkage checks using a temporary seeded SQLite database.

No changes to Neon, live UAT data, IAM assignments, or production.
"""
import pytest
from fastapi import HTTPException
from app import db
from app.seed import run as seed_run
from app.main import job_context, validate_fields

@pytest.fixture
def seeded(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"master_job_link_uat.db")
    seed_run(True)
    conn=db.connect()
    yield conn
    conn.close()

def test_job_uses_existing_customer_agent_booking_and_finance_relations(seeded):
    ctx=job_context(seeded,"50001")
    assert ctx["agent_code"] and ctx["customer_code"] and ctx["customer_name"]
    assert ctx["booking_ref"] and ctx["job_ref"]=="50001"
    for table,fk in (("agents","agent_id"),("customers","customer_id"),("bookings","booking_id")):
        assert seeded.execute(f"SELECT 1 FROM {table} WHERE id=?",(ctx[fk],)).fetchone(),(table,fk)

def test_booking_rejects_cross_agent_and_cross_customer_payloads(seeded):
    ctx=job_context(seeded,"50001")
    assert "AGENT_JOB_MISMATCH" in validate_fields("booking",ctx,{"Agent":"UAT-OTHER-AGENT"})
    assert "CUSTOMER_JOB_MISMATCH" in validate_fields("booking",ctx,{"Customer":"UAT-OTHER-CUSTOMER"})
    assert "JOB_REF_MISMATCH" in validate_fields("booking",ctx,{"Job Ref":"99999"})

def test_booking_accepts_matching_authoritative_master_values(seeded):
    ctx=job_context(seeded,"50001")
    assert not validate_fields("booking",ctx,{"Job Ref":ctx["job_ref"],"Agent":ctx["agent_code"],"Customer":ctx["customer_name"]})
