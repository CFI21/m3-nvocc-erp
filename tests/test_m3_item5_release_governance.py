import datetime
import json

import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.nvocc_principal_extensions import WorkspaceWrite, upsert_workspace
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
        (con["job_id"],con["id"],(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=1)).isoformat())
    )
    c.close()
    post=upsert_release_container(release_ref="REL-TEST-001",container_no=isolated["container"],actor_user_id="USR-C",action="HOLD")
    assert post["status"]=="RELEASED_WITH_POST_DELIVERY_HOLD"


def test_future_container_event_is_not_current_release_state(isolated):
    c=db.connect()
    con=c.execute("SELECT id,job_id FROM containers WHERE container_no=?",(isolated["container"],)).fetchone()
    c.execute(
        """INSERT INTO container_events(
           event_id,job_id,container_id,event_type,event_time,location,status,source_module,detail_json
           ) VALUES('EVT-ITEM5-FUTURE',?,?,'DELIVERED',?,'TSTPOD','DELIVERED','item5-test','{}')""",
        (con["job_id"],con["id"],(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1)).isoformat())
    )
    c.close()
    out=upsert_release_container(
        release_ref="REL-TEST-001",container_no=isolated["container"],
        actor_user_id="USR-FUTURE-TEST",action="HOLD",
    )
    assert out["status"]=="REVOKED"


def test_synthetic_container_event_is_not_current_release_state(isolated):
    c=db.connect()
    con=c.execute("SELECT id,job_id FROM containers WHERE container_no=?",(isolated["container"],)).fetchone()
    c.execute(
        """INSERT INTO container_events(
           event_id,job_id,container_id,event_type,event_time,location,status,source_module,detail_json
           ) VALUES('EVT-ITEM5-SYNTHETIC',?,?,'DELIVERED',?,'TSTPOD','DELIVERED','item5-test',?)""",
        (
            con["job_id"],con["id"],
            (datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=1)).isoformat(),
            json.dumps({"synthetic":True}),
        )
    )
    c.close()
    out=upsert_release_container(
        release_ref="REL-TEST-001",container_no=isolated["container"],
        actor_user_id="USR-SYNTHETIC-TEST",action="HOLD",
    )
    assert out["status"]=="REVOKED"


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


def test_any_active_workflow_hold_blocks_release(isolated):
    c=db.connect()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()["id"]
    c.execute("INSERT INTO workflow_holds(job_id,code,active,created_at) VALUES(?,?,1,?)",
              (jid,"OPERATIONAL_HOLD",datetime.datetime.now(datetime.timezone.utc).isoformat()))
    c.close()
    p=evaluate_prerequisites(job_ref="50001",hbl_no=isolated["hbl"],container_no=isolated["container"])
    assert p["ready"] is False
    assert "no_legal_compliance_document_hold" in p["blocking_reasons"]
    assert "OPERATIONAL_HOLD" in p["active_holds"]


def test_generic_release_workspace_cannot_bypass_governed_release(isolated):
    with pytest.raises(HTTPException) as e:
        upsert_workspace(
            "release-control",
            WorkspaceWrite(data={
                "release_ref":"REL-BYPASS-001",
                "job_ref":"50001",
                "hbl_no":isolated["hbl"],
                "status":"RELEASED",
            }),
            x_role="ADMIN",
            x_branch_scope=None,
        )
    assert e.value.detail["code"]=="GOVERNED_RELEASE_ACTION_REQUIRED"


def test_revoked_or_expired_release_requires_governed_reissue(isolated):
    upsert_release_container(
        release_ref="REL-TEST-001",container_no=isolated["container"],
        actor_user_id="USR-A",action="RELEASE",
    )
    upsert_release_container(
        release_ref="REL-TEST-001",container_no=isolated["container"],
        actor_user_id="USR-B",action="REVOKE",
    )
    with pytest.raises(HTTPException) as e:
        upsert_release_container(
            release_ref="REL-TEST-001",container_no=isolated["container"],
            actor_user_id="USR-C",action="RELEASE",
        )
    assert e.value.detail["code"]=="RELEASE_REISSUE_REQUIRED"

    reopened=upsert_release_container(
        release_ref="REL-TEST-001",container_no=isolated["container"],
        actor_user_id="USR-C",action="REISSUE",
        reissue_ref="REISSUE-001",condition_reason="Corrected release authority",
        actor_role="OPS",office_scope="RTM",branch_scope="RTM",
        country_scope="NL",organization_scope="M3-EU",crt_ref="CRT-TEST-001",
    )
    assert reopened["status"] in {"PENDING","BLOCKED"}


def test_release_event_captures_governance_context(isolated):
    upsert_release_container(
        release_ref="REL-TEST-001",container_no=isolated["container"],
        actor_user_id="USR-A",action="RELEASE",
        actor_role="OPS",office_scope="RTM",branch_scope="RTM",
        country_scope="NL",organization_scope="M3-EU",
    )
    c=db.connect()
    row=c.execute("SELECT * FROM nvocc_release_events ORDER BY id DESC LIMIT 1").fetchone()
    detail=json.loads(row["detail_json"])
    c.close()
    assert row["actor_user_id"]=="USR-A"
    assert detail["actor_role"]=="OPS"
    assert detail["office_scope"]=="RTM"
    assert detail["branch_scope"]=="RTM"
    assert detail["country_scope"]=="NL"
    assert detail["organization_scope"]=="M3-EU"
    assert detail["job_ref"]=="50001"
    assert detail["hbl_no"]==isolated["hbl"]


@pytest.mark.parametrize("flow,module",[
    ("EXPORT","export-trt"),
    ("IMPORT","import-trt"),
    ("TS","transshipment-trt"),
])
def test_final_release_acceptance_matrix_by_flow(isolated, flow, module):
    # Item 5 authority is HBL + container release governance and is shared by
    # Export / Import / TS. Each operational flow must still be explicitly
    # present in the releasable module set so it cannot bypass the common gate.
    from app.main import RELEASE_MODULES
    assert module in RELEASE_MODULES, flow

    out=upsert_release_container(
        release_ref="REL-TEST-001",
        container_no=isolated["container"],
        actor_user_id=f"USR-{flow}-MAKER",
        action="RELEASE",
        actor_role="OPS",
        office_scope=f"{flow}-OFFICE",
        branch_scope=f"{flow}-BRANCH",
        country_scope="NL",
        organization_scope="M3-EU",
    )
    assert out["status"]=="RELEASED", flow
    assert out["prerequisites"]["ready"] is True, flow

    c=db.connect()
    ev=c.execute("SELECT * FROM nvocc_release_events ORDER BY id DESC LIMIT 1").fetchone()
    detail=json.loads(ev["detail_json"])
    c.close()
    assert detail["actor_role"]=="OPS", flow
    assert detail["branch_scope"]==f"{flow}-BRANCH", flow
    assert detail["job_ref"]=="50001", flow
    assert detail["hbl_no"]==isolated["hbl"], flow


def test_express_bl_does_not_require_stale_original_surrender_field(isolated):
    c=db.connect()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()["id"]
    bl=c.execute("SELECT id,payload_json FROM transaction_records WHERE job_id=? AND module='bl' ORDER BY id DESC LIMIT 1",(jid,)).fetchone()
    p=json.loads(bl["payload_json"])
    p["Original / Express"]="Express"
    c.execute("UPDATE transaction_records SET payload_json=? WHERE id=?",(json.dumps(p),bl["id"]))
    do=c.execute("SELECT id,payload_json FROM transaction_records WHERE job_id=? AND module='delivery-order' ORDER BY id DESC LIMIT 1",(jid,)).fetchone()
    dp=json.loads(do["payload_json"])
    dp["Original BL Status"]="Open"
    dp["Telex Release"]="No"
    c.execute("UPDATE transaction_records SET payload_json=? WHERE id=?",(json.dumps(dp),do["id"]))
    c.close()
    out=evaluate_prerequisites(job_ref="50001",hbl_no=isolated["hbl"],container_no=isolated["container"])
    assert out["checks"]["surrender_or_telex"] is True
    assert out["bl_release_authority"]["bl_type"]=="EXPRESS"
    assert out["bl_release_authority"]["original_bl_required"] is False
    assert out["bl_release_authority"]["express_release_authorized"] is True
    assert out["bl_release_authority"]["stale_do_document_fields"] is True
    assert out["document_source_of_truth"].startswith("transaction_records:bl")


def test_original_bl_without_surrender_or_telex_stays_blocked(isolated):
    c=db.connect()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()["id"]
    bl=c.execute("SELECT id,payload_json FROM transaction_records WHERE job_id=? AND module='bl' ORDER BY id DESC LIMIT 1",(jid,)).fetchone()
    p=json.loads(bl["payload_json"])
    p["Original / Express"]="Original"
    c.execute("UPDATE transaction_records SET payload_json=? WHERE id=?",(json.dumps(p),bl["id"]))
    do=c.execute("SELECT id,payload_json FROM transaction_records WHERE job_id=? AND module='delivery-order' ORDER BY id DESC LIMIT 1",(jid,)).fetchone()
    dp=json.loads(do["payload_json"])
    dp["Original BL Status"]="Open"
    dp["Telex Release"]="No"
    c.execute("UPDATE transaction_records SET payload_json=? WHERE id=?",(json.dumps(dp),do["id"]))
    c.close()
    out=evaluate_prerequisites(job_ref="50001",hbl_no=isolated["hbl"],container_no=isolated["container"])
    assert out["checks"]["surrender_or_telex"] is False
    assert "surrender_or_telex" in out["blocking_reasons"]
    assert out["bl_release_authority"]["original_bl_required"] is True
