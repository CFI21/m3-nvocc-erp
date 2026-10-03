import datetime
import json

import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.release_governance import (
    assert_delivery_order_eligible,
    delivery_order_eligible,
    evaluate_prerequisites,
    upsert_release_container,
)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"item5.db")
    monkeypatch.setenv("M3_RELEASE_GOVERNANCE_ENABLED","true")
    seed_run(True)
    c=db.connect()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()["id"]
    hbl=c.execute("SELECT bill_no FROM bills WHERE job_id=? AND kind='HBL' ORDER BY id LIMIT 1",(jid,)).fetchone()["bill_no"]
    con=c.execute("SELECT container_no FROM containers WHERE job_id=? ORDER BY id LIMIT 1",(jid,)).fetchone()["container_no"]
    # Synthetic release header using existing authoritative table.
    c.execute(
      """INSERT INTO nvocc_release_controls(
         release_ref,job_ref,hbl_no,status,version,updated_at
         ) VALUES('REL-TEST-001','50001',?,'PENDING',1,?)""",
      (hbl,datetime.datetime.now(datetime.timezone.utc).isoformat())
    )
    # Synthetic DO authority: surrender requirement satisfied.
    c.execute(
      """INSERT INTO transaction_records(
         module,external_ref,job_id,booking_id,customer_id,agent_id,container_id,voyage_id,bill_id,
         status,version,payload_json,created_at,updated_at
         )
         SELECT 'delivery-order','DO-ITEM5-TEST',j.id,j.booking_id,j.customer_id,j.agent_id,ct.id,j.voyage_id,b.id,
                'Draft',1,?, ?, ?
         FROM jobs j JOIN containers ct ON ct.job_id=j.id JOIN bills b ON b.job_id=j.id AND b.kind='HBL'
         WHERE j.job_ref='50001' LIMIT 1""",
      (
        json.dumps({"Original BL Status":"SURRENDERED","Telex Release":"No"}),
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
      )
    )
    c.close()
    return {"hbl":hbl,"container":con}


def test_hbl_container_prerequisites_are_authoritative(isolated):
    p=evaluate_prerequisites(job_ref="50001",hbl_no=isolated["hbl"],container_no=isolated["container"])
    assert p["ready"] is True
    assert p["negative_margin_blocks_release"] is False


def test_release_one_container_and_do_eligibility(isolated):
    out=upsert_release_container(
        release_ref="REL-TEST-001",container_no=isolated["container"],
        actor_user_id="USR-RELEASE-001",action="RELEASE",
    )
    assert out["status"]=="RELEASED"
    assert out["summary_status"]=="RELEASED"
    assert delivery_order_eligible(job_ref="50001",hbl_no=isolated["hbl"],container_no=isolated["container"])["eligible"] is True


def test_partial_release_summary(isolated):
    c=db.connect()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()["id"]
    c.execute("INSERT INTO containers(container_no,job_id,size_type) VALUES('TSTU5000102',?,'40HC')",(jid,))
    c.close()
    upsert_release_container(release_ref="REL-TEST-001",container_no=isolated["container"],actor_user_id="USR-A",action="RELEASE")
    upsert_release_container(release_ref="REL-TEST-001",container_no="TSTU5000102",actor_user_id="USR-B",action="EVALUATE")
    c=db.connect()
    state=c.execute("SELECT status FROM nvocc_release_controls WHERE release_ref='REL-TEST-001'").fetchone()["status"]
    c.close()
    assert state=="PARTIALLY_RELEASED"


def test_credit_override_is_narrow_and_does_not_bypass_unpaid_balance(isolated):
    c=db.connect()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()["id"]
    c.execute("UPDATE finance_states SET credit_hold=1,payment_status='OPEN',outstanding=100 WHERE job_id=?",(jid,))
    c.close()
    valid=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1)).isoformat()
    out=upsert_release_container(
        release_ref="REL-TEST-001",container_no=isolated["container"],
        actor_user_id="USR-MAKER-001",action="RELEASE",
        condition_type="CREDIT_OVERRIDE",condition_reason="Synthetic credit exception",
        condition_valid_until=valid,approver_user_id="USR-APPROVER-001",
    )
    assert out["status"]=="BLOCKED"
    assert "payment_cleared" in out["prerequisites"]["blocking_reasons"]
    assert "credit_clear" not in out["prerequisites"]["blocking_reasons"]


def test_conditional_release_requires_distinct_human_checker(isolated):
    valid=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1)).isoformat()
    with pytest.raises(HTTPException) as e:
        upsert_release_container(
            release_ref="REL-TEST-001",container_no=isolated["container"],
            actor_user_id="USR-MAKER-001",action="RELEASE",
            condition_type="LOI",condition_reason="Synthetic LOI",condition_valid_until=valid,
            approver_user_id="USR-MAKER-001",
        )
    assert e.value.detail["code"]=="MAKER_CHECKER_SAME_USER"


def test_pre_delivery_revoke_and_post_delivery_hold(isolated):
    upsert_release_container(release_ref="REL-TEST-001",container_no=isolated["container"],actor_user_id="USR-A",action="RELEASE")
    pre=upsert_release_container(release_ref="REL-TEST-001",container_no=isolated["container"],actor_user_id="USR-B",action="REVOKE")
    assert pre["status"]=="REVOKED"

    c=db.connect()
    con=c.execute("SELECT id,job_id FROM containers WHERE container_no=?",(isolated["container"],)).fetchone()
    c.execute(
        """INSERT INTO container_events(
           event_id,job_id,container_id,event_type,event_time,location,status,source_module,detail_json
           ) VALUES('EVT-ITEM5-DELIVERED',?,?, 'DELIVERED',?,'TSTPOD','DELIVERED','item5-test','{}')""",
        (con["job_id"],con["id"],datetime.datetime.now(datetime.timezone.utc).isoformat())
    )
    c.close()
    post=upsert_release_container(release_ref="REL-TEST-001",container_no=isolated["container"],actor_user_id="USR-C",action="HOLD")
    assert post["status"]=="RELEASED_WITH_POST_DELIVERY_HOLD"


def test_delivery_order_blocks_unreleased_container(isolated):
    with pytest.raises(HTTPException) as e:
        assert_delivery_order_eligible(job_ref="50001",hbl_no=isolated["hbl"],container_no=isolated["container"])
    assert e.value.detail["code"]=="DELIVERY_ORDER_CONTAINER_NOT_RELEASED"


def test_release_events_are_immutable(isolated):
    upsert_release_container(release_ref="REL-TEST-001",container_no=isolated["container"],actor_user_id="USR-A",action="RELEASE")
    c=db.connect()
    with pytest.raises(Exception):
        c.execute("UPDATE nvocc_release_events SET to_state='BLOCKED'")
    with pytest.raises(Exception):
        c.execute("DELETE FROM nvocc_release_events")
    c.close()


def test_feature_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("M3_RELEASE_GOVERNANCE_ENABLED",raising=False)
    assert delivery_order_eligible(job_ref="50001",hbl_no="X",container_no="Y")["governance_enabled"] is False
