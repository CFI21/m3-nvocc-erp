import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.business_day import propagation
from app.screen_catalog import build_catalog
from app.screen_integration import screen_data, related, workflow
from app.clx049_booking_bl_workspace import verify as verify_clx049
from app.clx046_treasury_hardening import verify as verify_clx046
from app.clx045_gl_reporting import control_summary as gl_control_summary

FLOW_SCREENS = [
    ("agent-tasks::booking","OPS"),
    ("agent-tasks::planning","OPS"),
    ("agent-tasks::bl","DOCS"),
    ("agent-tasks::cro","OPS"),
    ("agent-tasks::container-activity","OPS"),
    ("agent-tasks::delivery-order","OPS"),
    ("gl-accounts::invoice","GL_ACCOUNTANT"),
    ("gl-accounts::voucher","GL_ACCOUNTANT"),
]

@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "final_ops_acceptance.db")
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH

def test_preserve_196_authoritative_screens():
    catalog=build_catalog()
    assert catalog["screen_count"]==196
    assert len({s["screen_id"] for s in catalog["screens"]})==196

def test_core_operational_flow_access_by_real_roles(isolated):
    for screen_id,role in FLOW_SCREENS:
        out=screen_data(screen_id,x_role=role)
        assert isinstance(out["rows"],list), (screen_id,role)

def test_agent_scope_preserved_on_end_to_end_context(isolated):
    ok=related("50001",x_role="AGENT",x_agent_scope="CLX-AGT-SIN")
    assert ok["identity"]["job_ref"]=="50001"
    with pytest.raises(HTTPException) as exc:
        related("50001",x_role="AGENT",x_agent_scope="WRONG-AGENT")
    assert exc.value.status_code==404

def test_booking_job_bl_cro_container_release_do_chain_present(isolated):
    wf=workflow("50001",x_role="OPS")
    by_module={x["module"]:x for x in wf["steps"]}
    for module in ("booking","planning","bl","cro","container-activity","delivery-order"):
        assert by_module[module]["present"] is True, module
        assert by_module[module]["record_ref"], module

def test_job_identity_propagates_through_ops_finance_gl_treasury(isolated):
    p=propagation(db.connect(),"50001")
    assert p["pass"] is True
    assert p["counts"]["booking"]==1
    assert p["counts"]["agent_tasks"]==19
    assert p["counts"]["gl"]>=30
    assert p["counts"]["treasury"]==37
    assert p["duplicate_reentry_required"] is False

def test_booking_bl_release_workspace_contract(isolated):
    out=verify_clx049("AUDITOR")
    assert out["all_jobs_pass"] is True
    assert out["booking_tab_order"]==["Booking Info","Equipment","Other Info"]
    assert out["bl_tab_order"]==["Booking Info","Release Instruction","Delivery Order","Lock Info","Authorization"]
    assert out["live_providers"] is False
    assert out["real_money"] is False

def test_treasury_and_gl_reachability_and_balance(isolated):
    tr=verify_clx046("AUDITOR")
    gl=gl_control_summary(x_role="AUDITOR",x_m3_session=None)
    assert tr["all_jobs_present"] is True
    assert tr["all_have_treasury"] is True
    assert tr["all_reach_gl_reporting"] is True
    assert tr["live_providers"] is False
    assert tr["real_money"] is False
    assert gl["trial_balance_balanced_vc"] is True
    assert gl["trial_balance_balanced_lc"] is True

def test_all_five_canonical_jobs_complete_cross_domain_propagation(isolated):
    for jr in ("50001","50002","50003","50004","50005"):
        c=db.connect()
        try:
            out=propagation(c,jr)
        finally:
            c.close()
        assert out["pass"] is True, jr
        assert out["duplicate_reentry_required"] is False, jr
