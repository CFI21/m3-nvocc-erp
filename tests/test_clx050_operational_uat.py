from pathlib import Path

import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin import Login, login
from app.screen_catalog import build_catalog
from app.screen_integration import screen_data, quick_actions, action_route, related
from app.main import context
from app.clx049_booking_bl_workspace import verify as verify_clx049
from app.clx048_admin_masterdata_hardening import verify as verify_clx048
from app.clx047_integration_hardening import verify as verify_clx047
from app.clx046_treasury_hardening import verify as verify_clx046
from app.clx045_gl_reporting import control_summary as gl_control_summary

ROLE_PERSONAS = {
    "GLOBAL_ADMIN": "SUPER_ADMIN",
    "HO_OPERATIONS": "OPS",
    "AGENT": "AGENT",
    "FINANCE": "FINANCE",
    "TREASURY": "TREASURY",
    "ACCOUNTING": "GL_ACCOUNTANT",
    "AUDITOR": "AUDITOR",
    "READ_ONLY": "VIEWER",
}
MUTATIONS={"create","edit","copy","approve","release","hold","cancel","amend","reissue","reverse","retry","activate","deactivate","change-request","reject","advance"}

@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "clx050.db")
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH

def test_existing_authenticated_personas_login(isolated):
    credentials=[
        ("admin","Admin123!","SUPER_ADMIN"),
        ("ops.rtm","Ops123!","OPS"),
        ("finance.dxb","Fin123!","FINANCE"),
        ("auditor","Audit123!","AUDITOR"),
    ]
    for username,password,role in credentials:
        out=login(Login(username=username,password=password,mfa_code="123456"))
        assert out["username"]==username
        assert role in out["roles"]
        assert out["mfa_verified"] is True
        assert out["session_token"]

def test_all_eight_operational_personas_have_governed_screen_surface(isolated):
    catalog=build_catalog()
    assert catalog["screen_count"]==196
    visited=set()
    for persona,role in ROLE_PERSONAS.items():
        accessible=0
        for s in catalog["screens"]:
            try:
                data=screen_data(s["screen_id"],x_role=role)
                assert isinstance(data["rows"],list)
                accessible+=1
                visited.add(s["screen_id"])
                for action in quick_actions(s["screen_id"],role)["visible_actions"]:
                    route=action_route(s["screen_id"],action,1,1)
                    if action in {"quick-view","print","export","related-records","audit-history","email"}:
                        assert route["mode"] in {"CLIENT_OR_CLX011","CLX011_SIMULATED"}
                    else:
                        assert route["mode"] in {"EXISTING_API","EXISTING_GOVERNANCE","MASTER_DATA_GOVERNANCE"}
            except HTTPException as exc:
                assert exc.status_code==403
        assert accessible>0, persona
    assert len(visited)==196

def test_role_boundaries_and_daily_mutation_surfaces(isolated):
    catalog=build_catalog()
    by_domain={}
    for s in catalog["screens"]:
        by_domain.setdefault(s["domain"],[]).append(s)

    def mutations_for(role, domain):
        out=set()
        for s in by_domain.get(domain,[]):
            try:
                out.update(set(quick_actions(s["screen_id"],role)["visible_actions"]) & MUTATIONS)
            except HTTPException as exc:
                assert exc.status_code==403
        return out

    assert mutations_for("SUPER_ADMIN","Agent Tasks")
    assert mutations_for("OPS","Agent Tasks")
    assert {"create","edit"} <= mutations_for("AGENT","Agent Tasks")
    assert mutations_for("FINANCE","Treasury / AR-AP")
    assert mutations_for("TREASURY","Treasury / AR-AP")
    assert mutations_for("GL_ACCOUNTANT","Finance & Accounting Setup")
    for role in ("AUDITOR","VIEWER"):
        for domain in by_domain:
            assert not mutations_for(role,domain)

def test_agent_scope_and_read_only_cross_domain_context(isolated):
    ok=context("50001",x_role="AGENT",x_agent_scope="CLX-AGT-SIN",x_customer_scope=None)
    assert ok["job_ref"]=="50001"
    with pytest.raises(HTTPException) as exc:
        context("50001",x_role="AGENT",x_agent_scope="WRONG-AGENT",x_customer_scope=None)
    assert exc.value.status_code==404
    for role in ("TREASURY","GL_ACCOUNTANT","AUDITOR","VIEWER","SUPER_ADMIN"):
        assert context("50001",x_role=role,x_agent_scope=None,x_customer_scope=None)["job_ref"]=="50001"

def test_quick_view_related_deep_links_audit_and_same_page_contracts(isolated):
    catalog=build_catalog()
    assert catalog["screen_count"]==196
    for jr in ("50001","50002","50003","50004","50005"):
        rel=related(jr)
        assert rel["identity"]["job_ref"]==jr
        assert rel["linked_records"]
    v=verify_clx049("AUDITOR")
    assert v["all_jobs_pass"] is True
    assert v["booking_tab_order"]==["Booking Info","Other Info"]
    assert v["bl_tab_order"]==["Booking Info","Release Instruction","Delivery Order","Lock Info","Authorization"]

def test_jobs_50001_50005_complete_cross_domain_business_day(isolated):
    op=verify_clx049("AUDITOR")
    md=verify_clx048("AUDITOR")
    tr=verify_clx046("AUDITOR")
    integ=verify_clx047("AUDITOR")
    gl=gl_control_summary(x_role="AUDITOR",x_m3_session=None)
    assert op["all_jobs_pass"]
    assert md["all_jobs_valid"]
    assert tr["all_jobs_present"] and tr["all_have_treasury"] and tr["all_reach_gl_reporting"]
    assert integ["all_jobs_present"] and integ["all_have_integration"] and integ["all_reach_treasury"] and integ["all_reach_gl_reporting"]
    assert gl["trial_balance_balanced_vc"] and gl["trial_balance_balanced_lc"]
    assert op["live_providers"] is False and op["real_money"] is False
    assert tr["live_providers"] is False and tr["real_money"] is False
    assert integ["live_providers"] is False and integ["real_money"] is False

def test_web_shell_interaction_contract():
    html=Path("web/index.html").read_text()
    for marker in (
        "function calcRows", "openDrawer(", "loadRelated(", "loadAudit(",
        "renderQuickActions(", "showClx049Workspace(", "openClx49Link(",
        "pageSize", "sortKey", "state.search"
    ):
        assert marker in html, marker
