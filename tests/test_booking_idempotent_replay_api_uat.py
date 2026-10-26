"""Isolated API retry regression: no real UAT data or production calls."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"duplicate_booking.db")
    monkeypatch.setenv("M3_BULK_PERMISSION_GOVERNANCE_ENABLED","false")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC","OFF")
    monkeypatch.setenv("M3_UAT_TOKEN","isolated-booking-idempotency-test")
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return TestClient(app,raise_server_exceptions=False,headers={
        "X-M3-UAT-Token":"isolated-booking-idempotency-test",
        "X-Role":"ADMIN",
    })


def _count():
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM transaction_records WHERE module='booking' AND external_ref='UAT-IDEMPOTENT-BOOKING-001'").fetchone()[0]


def test_identical_booking_create_replay_never_creates_second_record(client):
    request={"job_ref":"50001","external_ref":"UAT-IDEMPOTENT-BOOKING-001","fields":{"Status":"Draft"}}
    headers={"Idempotency-Key":"uat-retry-booking-001"}
    before=_count()
    first=client.post("/api/v1/booking",json=request,headers=headers)
    assert first.status_code==201, first.text
    second=client.post("/api/v1/booking",json=request,headers=headers)
    assert second.status_code==201,second.text
    assert second.json()["id"]==first.json()["id"]
    assert _count()==before+1
    collision=client.post("/api/v1/booking",json={**request,"external_ref":"UAT-IDEMPOTENT-BOOKING-002"},headers=headers)
    assert collision.status_code==409
    assert _count()==before+1
    different_key=client.post("/api/v1/booking",json=request,headers={"Idempotency-Key":"uat-retry-booking-002"})
    assert different_key.status_code==409
    assert _count()==before+1
