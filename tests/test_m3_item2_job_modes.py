import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.job_modes import (
    create_standard_or_split_job,
    create_consolidation_job,
    linked_bookings,
)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"item2.db")
    monkeypatch.setenv("M3_JOB_IDEMPOTENCY_ENABLED","true")
    monkeypatch.setenv("M3_JOB_MODES_ENABLED","true")
    seed_run(True)
    c=db.connect()
    # Add synthetic bookings only. No real data.
    template=c.execute("SELECT * FROM bookings ORDER BY id LIMIT 1").fetchone()
    for ref in ("TST-BKG-A","TST-BKG-B","TST-BKG-C"):
        c.execute(
            """INSERT INTO bookings(booking_ref,customer_id,agent_id,voyage_id,pol,pod)
               VALUES(?,?,?,?,?,?)""",
            (ref,template["customer_id"],template["agent_id"],template["voyage_id"],"TSTPOL","TSTPOD"),
        )
    c.close()
    return db.DB_PATH


def test_standard_default_one_booking_one_job(isolated):
    out=create_standard_or_split_job(booking_ref="TST-BKG-A",job_ref="59981",split_sequence=1)
    assert out["status"]=="CREATED"
    link=linked_bookings("59981")
    assert link["job_type"]=="STANDARD"
    assert [x["booking_ref"] for x in link["bookings"]]==["TST-BKG-A"]


def test_split_booking_can_create_multiple_jobs_with_distinct_sequence(isolated):
    a=create_standard_or_split_job(booking_ref="TST-BKG-A",job_ref="59981",split_sequence=1)
    b=create_standard_or_split_job(booking_ref="TST-BKG-A",job_ref="59982",split_sequence=2)
    assert a["job_ref"]=="59981"
    assert b["job_ref"]=="59982"
    c=db.connect()
    n=c.execute(
        """SELECT COUNT(*) n FROM job_booking_links l
           JOIN bookings b ON b.id=l.booking_id WHERE b.booking_ref='TST-BKG-A'"""
    ).fetchone()["n"]
    c.close()
    assert n==2


def test_consolidation_links_multiple_bookings_without_commercial_winner(isolated):
    out=create_consolidation_job(
        booking_refs=["TST-BKG-A","TST-BKG-B","TST-BKG-C"],
        consolidation_ref="TST-CONS-001",
        job_ref="59983",
    )
    assert out["status"]=="CREATED"
    link=linked_bookings("59983")
    assert link["job_type"]=="CONSOLIDATION"
    assert link["commercial_authority"]=="JOB_BOOKING_LINKS"
    assert [x["booking_ref"] for x in link["bookings"]]==["TST-BKG-A","TST-BKG-B","TST-BKG-C"]


def test_same_consolidation_ref_replays_same_job(isolated):
    first=create_consolidation_job(
        booking_refs=["TST-BKG-A","TST-BKG-B"],
        consolidation_ref="TST-CONS-001",
        job_ref="59983",
    )
    second=create_consolidation_job(
        booking_refs=["TST-BKG-B","TST-BKG-A"],
        consolidation_ref="TST-CONS-001",
        job_ref="59999",
    )
    assert first["job_ref"]==second["job_ref"]=="59983"
    assert second["status"]=="JOB_ALREADY_CREATED"


def test_consolidation_ref_cannot_be_reused_for_different_booking_set(isolated):
    create_consolidation_job(
        booking_refs=["TST-BKG-A","TST-BKG-B"],
        consolidation_ref="TST-CONS-001",
        job_ref="59983",
    )
    with pytest.raises(HTTPException) as e:
        create_consolidation_job(
            booking_refs=["TST-BKG-A","TST-BKG-C"],
            consolidation_ref="TST-CONS-001",
            job_ref="59984",
        )
    assert e.value.detail["code"]=="CONSOLIDATION_REF_REUSE_CONFLICT"


def test_consolidation_requires_two_bookings(isolated):
    with pytest.raises(HTTPException) as e:
        create_consolidation_job(
            booking_refs=["TST-BKG-A"],
            consolidation_ref="TST-CONS-001",
            job_ref="59983",
        )
    assert e.value.status_code==422


def test_feature_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("M3_JOB_MODES_ENABLED",raising=False)
    with pytest.raises(HTTPException) as e:
        create_standard_or_split_job(booking_ref="TST-BKG-A",job_ref="59981")
    assert e.value.detail["code"]=="JOB_MODES_DISABLED"
