import json

import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.main import UpdateBody, update_record
from app.crt_governance import (
    ApplyBody,
    CreateTicket,
    Decision,
    apply,
    create_ticket,
    decision,
    get_ticket,
    required_approval_roles,
    start_review,
    submit,
)
from app.screen_catalog import build_catalog


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "crt-trt.db")
    seed_run(True)
    return db.DB_PATH


def target_id(module="trt"):
    c=db.connect()
    r=c.execute("SELECT id FROM transaction_records WHERE module=? ORDER BY id LIMIT 1",(module,)).fetchone()
    c.close()
    return r["id"]


def move_to_review(ref, maker="USR-MAKER-001", reviewer="USR-REVIEW-001"):
    submit(ref,x_user_id=maker,x_actor_type="HUMAN")
    start_review(ref,x_user_id=reviewer,x_actor_type="HUMAN")


def approve_role(ref, role, user):
    return decision(
        ref,
        Decision(role=role,decision="APPROVED",comment="Synthetic M3 TEST approval"),
        x_user_id=user,
        x_actor_type="HUMAN",
        x_mfa_verified="true",
    )


def test_operational_crt_is_renamed_to_trt_everywhere_current(isolated):
    catalog=build_catalog()
    ids={x["screen_id"] for x in catalog["screens"]}
    assert "agent-tasks::trt" in ids
    assert "agent-tasks::export-trt" in ids
    assert "agent-tasks::import-trt" in ids
    assert "agent-tasks::transshipment-trt" in ids
    assert "agent-tasks::crt" not in ids
    c=db.connect()
    modules={x["module"] for x in c.execute("SELECT DISTINCT module FROM transaction_records")}
    c.close()
    assert {"trt","export-trt","import-trt","transshipment-trt"} <= modules
    assert not ({"crt","export-crt","import-crt","transshipment-crt"} & modules)


def test_ai_or_service_identity_cannot_create_or_approve_crt(isolated):
    tid=target_id()
    with pytest.raises(HTTPException) as e:
        create_ticket(
            CreateTicket(target_module="trt",target_record_id=tid,change_payload={"Remarks":"x"},reason="Synthetic test"),
            x_user_id="AI-001",x_actor_type="AI",
        )
    assert e.value.detail["code"]=="HUMAN_ONLY"


def test_maker_checker_by_user_id_and_mfa(isolated):
    tid=target_id()
    t=create_ticket(
        CreateTicket(target_module="trt",target_record_id=tid,change_payload={"Remarks":"changed"},reason="Synthetic test"),
        x_user_id="USR-MAKER-001",x_actor_type="HUMAN",
    )
    submit(t["ticket_ref"],x_user_id="USR-MAKER-001",x_actor_type="HUMAN")
    with pytest.raises(HTTPException) as e:
        start_review(t["ticket_ref"],x_user_id="USR-MAKER-001",x_actor_type="HUMAN")
    assert e.value.detail["code"]=="MAKER_CHECKER_SAME_USER"
    start_review(t["ticket_ref"],x_user_id="USR-CHECKER-001",x_actor_type="HUMAN")
    with pytest.raises(HTTPException) as e2:
        decision(
            t["ticket_ref"],Decision(role="AUTHORIZED_APPROVER",decision="APPROVED"),
            x_user_id="USR-APPROVER-001",x_actor_type="HUMAN",x_mfa_verified="false",
        )
    assert e2.value.detail["code"]=="MFA_REQUIRED"


def test_normal_crt_lifecycle_applies_change_and_closes(isolated):
    tid=target_id()
    t=create_ticket(
        CreateTicket(target_module="trt",target_record_id=tid,change_payload={"Remarks":"Synthetic corrected value"},reason="Synthetic correction"),
        x_user_id="USR-MAKER-001",x_actor_type="HUMAN",
    )
    move_to_review(t["ticket_ref"])
    out=approve_role(t["ticket_ref"],"AUTHORIZED_APPROVER","USR-APPROVER-001")
    assert out["ticket"]["state"]=="APPROVED"
    applied=apply(t["ticket_ref"],ApplyBody(),x_user_id="USR-OPS-002",x_actor_type="HUMAN")
    assert applied["state"]=="APPLIED"
    c=db.connect()
    row=c.execute("SELECT payload_json FROM transaction_records WHERE id=?",(tid,)).fetchone()
    c.close()
    assert json.loads(row["payload_json"])["Remarks"]=="Synthetic corrected value"


def test_ts_requires_branch_manager_and_ts_finance_plus_authorized_approver(isolated):
    tid=target_id("transshipment-trt")
    t=create_ticket(
        CreateTicket(target_module="transshipment-trt",target_record_id=tid,change_payload={"Exception Reason":"Synthetic"},reason="Synthetic TS change"),
        x_user_id="USR-MAKER-001",x_actor_type="HUMAN",
    )
    move_to_review(t["ticket_ref"])
    roles=required_approval_roles(get_ticket(t["ticket_ref"])["ticket"])
    assert roles==["AUTHORIZED_APPROVER","TS_BRANCH_MANAGER","TS_FINANCE"]
    approve_role(t["ticket_ref"],"AUTHORIZED_APPROVER","USR-APPROVER-001")
    approve_role(t["ticket_ref"],"TS_BRANCH_MANAGER","USR-TSMGR-001")
    out=approve_role(t["ticket_ref"],"TS_FINANCE","USR-TSFIN-001")
    assert out["ticket"]["state"]=="APPROVED"


def test_closed_period_and_tax_filed_escalation(isolated):
    tid=target_id("import-trt")
    t=create_ticket(
        CreateTicket(
            target_module="import-trt",target_record_id=tid,
            change_payload={"Remarks":"Synthetic tax-period correction"},reason="Synthetic filed-period change",
            closed_period=True,tax_filed=True,
        ),
        x_user_id="USR-MAKER-001",x_actor_type="HUMAN",
    )
    move_to_review(t["ticket_ref"])
    roles=set(required_approval_roles(get_ticket(t["ticket_ref"])["ticket"]))
    assert {"AUTHORIZED_APPROVER","FINANCE_MANAGER","CFO","EXTERNAL_ADVISOR"} <= roles
    approve_role(t["ticket_ref"],"AUTHORIZED_APPROVER","USR-APPROVER-001")
    approve_role(t["ticket_ref"],"FINANCE_MANAGER","USR-FINMGR-001")
    approve_role(t["ticket_ref"],"CFO","USR-CFO-001")
    with pytest.raises(HTTPException) as e:
        decision(
            t["ticket_ref"],
            Decision(role="EXTERNAL_ADVISOR",decision="APPROVED"),
            x_user_id="USR-TAX-001",x_actor_type="HUMAN",x_mfa_verified="true",
        )
    assert e.value.detail["code"]=="EXTERNAL_ADVISOR_EVIDENCE_REQUIRED"
    out=decision(
        t["ticket_ref"],
        Decision(
            role="EXTERNAL_ADVISOR",decision="APPROVED",
            external_advisor_ref="EXT-ADVISOR-TEST",evidence_ref="EVIDENCE-TEST-001",
        ),
        x_user_id="USR-TAX-001",x_actor_type="HUMAN",x_mfa_verified="true",
    )
    assert out["ticket"]["state"]=="APPROVED"


def test_financial_posted_change_requires_reversal_corrected_doc_repost(isolated):
    tid=target_id("export-trt")
    t=create_ticket(
        CreateTicket(
            target_module="export-trt",target_record_id=tid,
            change_payload={"Remarks":"Synthetic posted correction"},reason="Synthetic posted financial impact",
            financial_posted=True,
        ),
        x_user_id="USR-MAKER-001",x_actor_type="HUMAN",
    )
    move_to_review(t["ticket_ref"])
    approve_role(t["ticket_ref"],"AUTHORIZED_APPROVER","USR-APPROVER-001")
    with pytest.raises(HTTPException) as e:
        apply(t["ticket_ref"],ApplyBody(),x_user_id="USR-OPS-002",x_actor_type="HUMAN")
    assert e.value.detail["code"]=="FINANCIAL_REVERSAL_CORRECTION_REPOST_REQUIRED"
    out=apply(
        t["ticket_ref"],
        ApplyBody(
            application_mode="REVERSAL_CORRECTED_REPOST",
            reversal_ref="REV-TEST-001",corrected_document_ref="DOC-TEST-001",repost_ref="REPOST-TEST-001",
        ),
        x_user_id="USR-OPS-002",x_actor_type="HUMAN",
    )
    assert out["state"]=="APPLIED"


def test_direct_edit_after_approved_trt_is_blocked(isolated):
    tid=target_id("trt")
    c=db.connect()
    row=c.execute("SELECT version,payload_json FROM transaction_records WHERE id=?",(tid,)).fetchone()
    c.execute("UPDATE transaction_records SET status='Approved' WHERE id=?",(tid,))
    c.close()
    with pytest.raises(HTTPException) as e:
        update_record("trt",tid,UpdateBody(version=row["version"],fields={"Remarks":"Direct change"}),x_role="OPS",x_agent_scope=None,x_customer_scope=None)
    assert e.value.detail["code"]=="CRT_REQUIRED_AFTER_APPROVAL"
