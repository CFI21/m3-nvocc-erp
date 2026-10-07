from pathlib import Path
import pytest
from fastapi import HTTPException
from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin import Login, login
from app.screen_catalog import build_catalog
from app.screen_integration import action_route, events, field_contract, navigation_event, quick_actions, related, screen_data

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/"web/index.html").read_text(encoding="utf-8")
SRC=(ROOT/"app/screen_integration.py").read_text(encoding="utf-8")
READ_ACTIONS={"quick-view","print","export","related-records","audit-history","email"}
MUTATIONS={"create","edit","copy","approve","release","hold","cancel","amend","reissue","reverse","retry","activate","deactivate","change-request","reject","advance"}

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"final_frontend_audit.db")
    seed_run(True); admin_seed_run(); masterdata_seed_run()
    return db.DB_PATH

def test_verified_frontend_race_filter_viewer_and_status_fixes_are_present():
    assert "viewEpoch:0" in HTML
    assert "function viewCurrent(epoch,screenId,role)" in HTML
    assert "if(!viewCurrent(epoch,id,role))return;" in HTML
    assert "if(!viewCurrent(epoch,screenId,role))return;" in HTML
    assert "state.gridSearch=this.value;state.page=1;renderGrid()" in HTML
    assert "let q=String(state.gridSearch||'').toLowerCase();let st=state.status||''" in HTML
    assert "function gridColumnKeys(rows)" in HTML and "seen.has(canon)" in HTML
    assert "function viewerReadOnly(){return state.role==='VIEWER'}" in HTML
    assert "if(viewerReadOnly()&&UI_MUTATIONS.has(a))return" in HTML
    assert "if(setup&&role!=='VIEWER')" in HTML
    assert "if(role==='VIEWER')actions=actions.filter(x=>!UI_MUTATIONS.has(x))" in HTML
    assert "if(state.current){await openScreen(state.current.screen_id,state.navContext,'replace');return;}" in HTML

    assert "const epoch=++state.viewEpoch,screenId=state.current?.screen_id,role=state.role;" in HTML
    assert "async function loadRelated(epoch=state.viewEpoch,screenId=state.current?.screen_id,role=state.role)" in HTML
    assert "clx49Workspace!==w||clx49ActiveTab!==tab" in HTML
    assert "const p=record.fields||{},disabled=viewerReadOnly()?' disabled':'';" in HTML
    assert "const disabled=(opts.disabled||viewerReadOnly())?' disabled':'';" in HTML

def test_clx056_trt_presentation_is_consistent_without_breaking_legacy_read_or_governance_crt():
    assert "existing CRT/booking record" not in HTML
    assert "existing TRT / legacy booking record" in HTML
    assert "clx84Link('agent-tasks::transshipment-trt','TRT',jr)" in HTML
    assert "clx84Link('agent-tasks::transshipment-trt','CRT',jr)" not in HTML
    assert "'CRT No.'" in HTML
    assert "existing governed change / CRT / adjustment route" in HTML

def test_duplicate_review_is_database_portable_and_integrity_scan_is_not_changed_speculatively(isolated):
    assert "GROUP_CONCAT(record_key)" not in SRC
    c=db.connect()
    try:
        ts="2026-10-07T17:00:00+00:00"
        c.execute("INSERT INTO md_records(domain,record_key,display_name,payload_json,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",("carrier","AUD-DUP-1","Audit Duplicate","{}","ACTIVE",1,ts,ts))
        c.execute("INSERT INTO md_records(domain,record_key,display_name,payload_json,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",("carrier","AUD-DUP-2","Audit Duplicate","{}","ACTIVE",1,ts,ts))
        c.commit()
    finally:
        c.close()
    dup=screen_data("master-data::duplicate-review",x_role="ADMIN")
    row=next(x for x in dup["rows"] if x["domain"]=="carrier" and x["normalized_name"]=="audit duplicate")
    assert row["n"]==2
    assert set(row["record_keys"].split(","))=={"AUD-DUP-1","AUD-DUP-2"}
    integrity=screen_data("master-data::integrity-scan",x_role="ADMIN")
    assert integrity["rows"]==[{"status":"PASS","critical_issues":0}]

def test_authenticated_196_screen_field_api_action_state_and_audit_sweep(isolated):
    token=login(Login(username="admin",password="Admin123!",mfa_code="123456"))["session_token"]
    catalog=build_catalog()
    assert catalog["screen_count"]==196
    assert len({s["screen_id"] for s in catalog["screens"]})==196
    visited=set()
    for s in catalog["screens"]:
        sid=s["screen_id"]; fc=field_contract(sid)
        assert fc["screen_id"]==sid and fc["route"]==s["route"]
        assert fc["authoritative"] is True and fc["parallel_model"] is False
        assert fc["screen_data_api"].startswith("/api/clx011/screen-data?screen_id=")
        assert fc["field_contract_api"].startswith("/api/clx011/field-contract?screen_id=")
        assert fc["related_records_api"]=="/api/clx011/related/{job_ref}"
        data=screen_data(sid,x_role="VIEWER",x_m3_session=token)
        assert data["role"]=="SUPER_ADMIN" and isinstance(data["rows"],list)
        qa=quick_actions(sid,"SUPER_ADMIN")
        assert "visible_actions" in qa and "hidden_actions" in qa
        for action in qa["visible_actions"]:
            route=action_route(sid,action,1,1,role="SUPER_ADMIN")
            if action in READ_ACTIONS:
                assert route["mode"] in {"CLIENT_OR_CLX011","CLX011_SIMULATED"}
            else:
                assert route["mode"] in {"EXISTING_API","EXISTING_GOVERNANCE","MASTER_DATA_GOVERNANCE"}
        navigation_event(sid,x_role="VIEWER",x_actor_id="final-frontend-audit",x_m3_session=token)
        visited.add(sid)
    audit=events(500,x_role="VIEWER",x_m3_session=token,x_agent_scope=None,x_customer_scope=None,x_branch_scope=None,x_depot_scope=None,x_office_scope=None)
    nav={x["screen_id"] for x in audit if x["action"]=="NAVIGATE" and x["actor_id"]=="final-frontend-audit"}
    assert nav==visited and len(nav)==196

def test_authenticated_role_and_data_scope_are_server_authoritative(isolated):
    auditor=login(Login(username="auditor",password="Audit123!",mfa_code="123456"))["session_token"]
    out=screen_data("treasury::treasury-dashboard",x_role="SUPER_ADMIN",x_m3_session=auditor)
    assert out["role"]=="AUDITOR"
    assert not (set(quick_actions("agent-tasks::booking","VIEWER")["visible_actions"]) & MUTATIONS)
    ops=login(Login(username="ops.rtm",password="Ops123!",mfa_code="123456"))["session_token"]
    assert related("50001",x_role="SUPER_ADMIN",x_m3_session=ops)["identity"]["job_ref"]=="50001"
    with pytest.raises(HTTPException) as exc:
        related("50002",x_role="SUPER_ADMIN",x_m3_session=ops)
    assert exc.value.status_code==404
