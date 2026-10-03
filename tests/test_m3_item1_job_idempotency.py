import os

import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.job_creation_idempotency import business_key, execute_once


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "item1.db")
    monkeypatch.setenv("M3_JOB_IDEMPOTENCY_ENABLED", "true")
    seed_run(True)
    return db.DB_PATH


def synthetic_create(counter, job_ref="59991"):
    def _create(conn):
        counter["calls"] += 1
        booking = conn.execute(
            "SELECT * FROM bookings WHERE booking_ref=?",
            ("CLX-BKG-TEST-IDEMPOTENCY",),
        ).fetchone()
        if not booking:
            template = conn.execute("SELECT * FROM bookings ORDER BY id LIMIT 1").fetchone()
            conn.execute(
                """INSERT INTO bookings(booking_ref,customer_id,agent_id,voyage_id,pol,pod)
                   VALUES(?,?,?,?,?,?)""",
                (
                    "CLX-BKG-TEST-IDEMPOTENCY",
                    template["customer_id"],
                    template["agent_id"],
                    template["voyage_id"],
                    "TSTPOL",
                    "TSTPOD",
                ),
            )
            booking = conn.execute(
                "SELECT * FROM bookings WHERE booking_ref=?",
                ("CLX-BKG-TEST-IDEMPOTENCY",),
            ).fetchone()
        cur = conn.execute(
            """INSERT INTO jobs(
                 job_ref,booking_id,customer_id,agent_id,voyage_id,pol,pod,
                 operational_status
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                job_ref,
                booking["id"],
                booking["customer_id"],
                booking["agent_id"],
                booking["voyage_id"],
                booking["pol"],
                booking["pod"],
                "TEST_DRAFT",
            ),
        )
        job = conn.execute("SELECT id,job_ref FROM jobs WHERE job_ref=?", (job_ref,)).fetchone()
        return {"job_id": job["id"], "job_ref": job["job_ref"]}
    return _create


def test_business_identity_is_permanent_and_deterministic():
    a = business_key("BKG-001", "BOOKING_CONFIRMED", 1, None)
    b = business_key("bkg-001", "booking_confirmed", 1, "")
    assert a == b
    with pytest.raises(HTTPException) as e:
        business_key("BKG-001", "BOOKING_CONFIRMED", 0, None)
    assert e.value.status_code == 422


def test_same_business_intent_creates_exactly_one_job(isolated):
    counter = {"calls": 0}
    payload = {
        "booking_ref": "CLX-BKG-TEST-IDEMPOTENCY",
        "job_ref": "59991",
        "purpose": "BOOKING_CONFIRMED",
        "split_sequence": 1,
    }
    first = execute_once(
        booking_ref=payload["booking_ref"],
        purpose=payload["purpose"],
        split_sequence=1,
        consolidation_ref=None,
        request_payload=payload,
        actor_id="TEST-HUMAN-001",
        create_fn=synthetic_create(counter),
    )
    second = execute_once(
        booking_ref=payload["booking_ref"],
        purpose=payload["purpose"],
        split_sequence=1,
        consolidation_ref=None,
        request_payload=payload,
        actor_id="TEST-HUMAN-002",
        create_fn=synthetic_create(counter),
    )
    assert first["status"] == "CREATED"
    assert second["status"] == "JOB_ALREADY_CREATED"
    assert second["replayed"] is True
    assert first["job_ref"] == second["job_ref"] == "59991"
    assert counter["calls"] == 1


def test_same_business_key_with_different_payload_is_rejected(isolated):
    counter = {"calls": 0}
    execute_once(
        booking_ref="CLX-BKG-TEST-IDEMPOTENCY",
        purpose="BOOKING_CONFIRMED",
        split_sequence=1,
        consolidation_ref=None,
        request_payload={"job_ref": "59991"},
        actor_id="TEST-HUMAN-001",
        create_fn=synthetic_create(counter),
    )
    with pytest.raises(HTTPException) as e:
        execute_once(
            booking_ref="CLX-BKG-TEST-IDEMPOTENCY",
            purpose="BOOKING_CONFIRMED",
            split_sequence=1,
            consolidation_ref=None,
            request_payload={"job_ref": "59992"},
            actor_id="TEST-HUMAN-002",
            create_fn=synthetic_create(counter, "59992"),
        )
    assert e.value.status_code == 409
    assert e.value.detail["code"] == "IDEMPOTENCY_KEY_REUSE_CONFLICT"
    assert counter["calls"] == 1


def test_failed_attempt_can_retry_without_identity_expiry(isolated):
    attempts = {"calls": 0}

    def fail_once(conn):
        attempts["calls"] += 1
        if attempts["calls"] == 1:
            raise RuntimeError("synthetic failure")
        return synthetic_create({"calls": 0})(conn)

    payload = {"job_ref": "59991", "synthetic": True}
    with pytest.raises(HTTPException) as e:
        execute_once(
            booking_ref="CLX-BKG-TEST-IDEMPOTENCY",
            purpose="BOOKING_CONFIRMED",
            split_sequence=1,
            consolidation_ref=None,
            request_payload=payload,
            actor_id="TEST-HUMAN-001",
            create_fn=fail_once,
        )
    assert e.value.detail["code"] == "JOB_CREATION_EXCEPTION"

    out = execute_once(
        booking_ref="CLX-BKG-TEST-IDEMPOTENCY",
        purpose="BOOKING_CONFIRMED",
        split_sequence=1,
        consolidation_ref=None,
        request_payload=payload,
        actor_id="TEST-HUMAN-001",
        create_fn=fail_once,
    )
    assert out["status"] == "CREATED"
    assert attempts["calls"] == 2


def test_feature_flag_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("M3_JOB_IDEMPOTENCY_ENABLED", raising=False)
    with pytest.raises(HTTPException) as e:
        execute_once(
            booking_ref="BKG-TEST",
            purpose="BOOKING_CONFIRMED",
            split_sequence=1,
            consolidation_ref=None,
            request_payload={"synthetic": True},
            actor_id="TEST-HUMAN",
            create_fn=lambda conn: {"job_id": 1, "job_ref": "59991"},
        )
    assert e.value.status_code == 503
    assert e.value.detail["code"] == "JOB_IDEMPOTENCY_DISABLED"
