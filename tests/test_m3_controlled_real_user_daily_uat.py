from pathlib import Path

import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin import Login, login
from app.screen_catalog import build_catalog
from app.screen_integration import screen_data, related, workflow
from app.operations_workbench import _derive
from app.control_tower import _job_rows, control_status as tower_control_status
from app.management_kpi import control_status as kpi_control_status, _snapshot_payload
from app.clx049_booking_bl_workspace import verify as verify_clx049
from app.clx046_treasury_hardening import verify as verify_clx046
from app.clx045_gl_reporting import control_summary as gl_control_summary

RUNBOOK=Path("M3_LIVE_OPERATIONS_RUNBOOK.md").read_text()

@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "real_user_daily_uat.db")
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH

def test_frozen_surface_and_safety_contract():
    assert build_catalog()["screen_count"]==196
    assert "M3_LIVE_PROVIDERS=OFF" in RUNBOOK
    assert "REAL_MONEY=OFF" in RUNBOOK
    assert "Dummy Bank=ON" in RUNBOOK
    assert "Booking/Job party mismatches = 0" in RUNBOOK
    assert "P0/P1 defects = 0" in RUNBOOK
    assert "API/Web 5xx = 0" in RUNBOOK

def test_named_real_user_personas_authenticate(isolated):
    users=[
      ("admin","Admin123!","SUPER_ADMIN"),
      ("ops.rtm","Ops123!","OPS"),
      ("finance.dxb","Fin123!","FINANCE"),
      ("auditor","Audit123!","AUDITOR"),
    ]
    for username,password,role in users:
        out=login(Login(username=username,password=password,mfa_code="123456"))
        assert out["username"]==username
        assert role in out["roles"]
        assert out["mfa_verified"] is True
        assert out["session_token"]

def test_complete_daily_role_surface(isolated):
    daily=[
      ("OPS",["agent-tasks::booking","agent-tasks::planning","agent-tasks::cro","agent-tasks::container-activity","agent-tasks::delivery-order"]),
      ("DOCS",["agent-tasks::bl","agent-tasks::switch-bl","agent-tasks::split-bl","agent-tasks::import-bl"]),
      ("FINANCE",["agent-tasks::special-rates-request","agent-tasks::agent-receipt-pay","agent-tasks::soa"]),
      ("GL_ACCOUNTANT",["gl-accounts::invoice","gl-accounts::bills","gl-accounts::voucher"]),
      ("TREASURY",["treasury::treasury-dashboard","treasury::bank-reconciliation"]),
    ]
    for role,screens in daily:
        for sid in screens:
            out=screen_data(sid,x_role=role)
            assert isinstance(out["rows"],list),(role,sid)

def test_agent_daily_access_remains_party_scoped(isolated):
    ok=related("50001",x_role="AGENT",x_agent_scope="CLX-AGT-SIN")
    assert ok["identity"]["job_ref"]=="50001"
    with pytest.raises(HTTPException) as exc:
        related("50001",x_role="AGENT",x_agent_scope="WRONG-AGENT")
    assert exc.value.status_code==404
    c=db.connect()
    try:
        own=_derive(c,"AGENT","CLX-AGT-SIN",None)
        wrong=_derive(c,"AGENT","WRONG-AGENT",None)
    finally:c.close()
    assert all(x.get("job_ref")!="50001" for x in wrong)
    assert isinstance(own,list)

def test_operational_chain_and_finance_controls(isolated):
    wf=workflow("50001",x_role="OPS")
    present={x["module"]:x["present"] for x in wf["steps"]}
    for key in ("booking","planning","cro","bl","container-activity","delivery-order","agent-receipt-pay","soa"):
        assert present[key] is True,key
    op=verify_clx049("AUDITOR")
    tr=verify_clx046("AUDITOR")
    gl=gl_control_summary(x_role="AUDITOR",x_m3_session=None)
    assert op["all_jobs_pass"] is True
    assert tr["all_jobs_present"] and tr["all_have_treasury"] and tr["all_reach_gl_reporting"]
    assert gl["trial_balance_balanced_vc"] and gl["trial_balance_balanced_lc"]
    assert op["live_providers"] is False and op["real_money"] is False
    assert tr["live_providers"] is False and tr["real_money"] is False

def test_uat_records_are_excluded_from_management_jobs(isolated):
    c=db.connect()
    try:
        # The authoritative management query must exclude the reserved controlled-UAT identities.
        jobs=_job_rows(c,"AUDITOR",None,None)
        refs={str(x["job_ref"]) for x in jobs}
        assert "92200" not in refs
        assert all(not str(x.get("job_ref","")).startswith("UAT-") for x in jobs)
        snap=_snapshot_payload(c,"AUDITOR")
        assert snap["summary"]["jobs_total"]==len(jobs)
    finally:c.close()

def test_data_quality_relational_integrity(isolated):
    c=db.connect()
    try:
        assert c.execute("SELECT COUNT(*) n FROM jobs j LEFT JOIN bookings b ON b.id=j.booking_id WHERE b.id IS NULL").fetchone()["n"]==0
        assert c.execute("SELECT COUNT(*) n FROM jobs j LEFT JOIN customers x ON x.id=j.customer_id WHERE x.id IS NULL").fetchone()["n"]==0
        assert c.execute("SELECT COUNT(*) n FROM jobs j LEFT JOIN agents a ON a.id=j.agent_id WHERE a.id IS NULL").fetchone()["n"]==0
        assert c.execute("SELECT COUNT(*) n FROM transaction_records t LEFT JOIN jobs j ON j.id=t.job_id WHERE t.job_id IS NOT NULL AND j.id IS NULL").fetchone()["n"]==0
        assert c.execute("SELECT COUNT(*) n FROM containers x LEFT JOIN jobs j ON j.id=x.job_id WHERE x.job_id IS NOT NULL AND j.id IS NULL").fetchone()["n"]==0
        assert c.execute("SELECT COUNT(*) n FROM iam_user_roles ur LEFT JOIN iam_users u ON u.id=ur.user_id LEFT JOIN iam_roles r ON r.id=ur.role_id WHERE u.id IS NULL OR r.id IS NULL").fetchone()["n"]==0
    finally:c.close()

def test_management_and_treasury_controls_stay_read_only_simulation(isolated):
    tc=tower_control_status("AUDITOR")
    kc=kpi_control_status("AUDITOR")
    assert tc["screen_catalog_preserved"]==196
    assert tc["live_provider_activation"] is False
    assert tc["real_money"] is False
    assert kc["screen_catalog_preserved"]==196
    assert kc["live_provider_activation"] is False
    assert kc["real_money"] is False
