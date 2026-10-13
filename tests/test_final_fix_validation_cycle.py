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
import app.bulk_permission_governance as permission_gov

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/"web/index.html").read_text(encoding="utf-8")
SRC=(ROOT/"app/screen_integration.py").read_text(encoding="utf-8")
READ_ACTIONS={"quick-view","print","export","related-records","audit-history","email"}
MUTATIONS={"create","edit","copy","approve","release","hold","cancel","amend","reissue","reverse","retry","activate","deactivate","change-request","reject","advance"}

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"final_frontend_audit.db")
    seed_run(True); admin_seed_run(); masterdata_seed_run()
    c=db.connect()
    try:
        for jr in ('50001','50002','50003','50004','50005'):
            permission_gov.resolve_job_scope(c,jr,persist=True)
        c.commit()
    finally:
        c.close()
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
    scope_none={"x_agent_scope":None,"x_customer_scope":None,"x_branch_scope":None,"x_depot_scope":None,"x_office_scope":None}
    assert related("50001",x_role="SUPER_ADMIN",x_m3_session=ops,**scope_none)["identity"]["job_ref"]=="50001"
    with pytest.raises(HTTPException) as exc:
        related("50002",x_role="SUPER_ADMIN",x_m3_session=ops,**scope_none)
    assert exc.value.status_code==404


def test_booking_equipment_lines_validation_and_legacy_fallback():
    from app.main import validate_fields
    from app.agent_setup import _qty, _teu
    job={"job_ref":"50001","booking_ref":"BK-TEST","pol":"NLRTM","pod":"SGSIN"}
    valid={"Job Ref":"50001","Equipment Lines":[
        {"size":"20","type":"GP","quantity":2,"teu":2},
        {"size":"40","type":"HC","quantity":3,"teu":6},
        {"size":"45","type":"HC","quantity":1,"teu":2},
    ]}
    assert validate_fields("booking",job,valid)==[]
    assert _qty(valid)==6
    assert _teu(valid)==10
    assert _qty({"Equipment":"40GP","Quantity":"2"})==2
    assert _teu({"Equipment":"40GP","Quantity":"2"})==4
    for quantity in (1.5,"1.5",True,0,-1,10000):
        payload={"Equipment Lines":[{"size":"20","quantity":quantity,"teu":2}]}
        assert "INVALID_EQUIPMENT_LINE" in validate_fields("booking",job,payload)
    for invalid in ({"size":"53","quantity":1,"teu":2},
                    {"size":"20","quantity":2,"teu":4},
                    {"size":"40","quantity":1,"teu":"NaN"}):
        assert "INVALID_EQUIPMENT_LINE" in validate_fields("booking",job,{"Equipment Lines":[invalid]})
    assert "INVALID_EQUIPMENT_LINES" in validate_fields("booking",job,{"Equipment Lines":"not-a-list"})


def test_booking_ui_multi_equipment_payload_wiring_present():
    assert "clx81BookingDraft.values['Equipment Lines']=lines" in HTML
    assert "clx49Workspace?.booking?.fields?.['Equipment Lines']" in HTML
    assert "clx81EquipmentRowBookingKey!==bookingKey" in HTML
    assert "details.hidden=!lcl" in HTML


def test_booking_rejects_duplicate_equipment_size_and_type():
    from app.main import validate_fields
    from app.agent_setup import _qty, _teu
    job={"job_ref":"50001","booking_ref":"BK-TEST","pol":"NLRTM","pod":"SGSIN"}
    lines=[{"size":"40","type":"FR","quantity":2,"teu":4},
           {"size":"40","type":"fr","quantity":1,"teu":2}]
    assert "DUPLICATE_EQUIPMENT_TYPE" in validate_fields("booking",job,{"Equipment Lines":lines})
    unique=[{"size":"40","type":"FR","quantity":3,"teu":6},
            {"size":"40","type":"HC","quantity":2,"teu":4},
            {"size":"20","type":"FR","quantity":1,"teu":1}]
    fields={"Equipment Lines":unique}
    assert validate_fields("booking",job,fields)==[]
    assert _qty(fields)==6
    assert _teu(fields)==11


def test_booking_ui_prevents_duplicate_type_and_type_change_on_edit():
    assert "This container size/type is already selected." in HTML
    assert "Container size/type cannot be changed after selection." in HTML



def test_booking_approval_controls_are_safe_until_governed_actions_exist():
    assert "Save Draft</button>" in HTML
    assert "Submit RFA</button>" in HTML
    assert "Approve</button>" in HTML
    assert "RFA issuance requires governed backend numbering" in HTML
    assert "Approval requires governed authorization and reference issuance" in HTML
    assert "Approved — Locked" in HTML
    assert "String(r.status||'').toLowerCase()==='approved'" in HTML


def test_booking_status_cannot_be_issued_by_ordinary_save():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[1]/"app"/"main.py").read_text(encoding="utf-8")
    assert "BOOKING_STATUS_ACTION_REQUIRED" in src
    assert "str(body.fields.get('Status','Draft')).strip().lower() != 'draft'" in src
    assert "'Status' in body.fields and str(body.fields['Status']).strip().lower()!=str(r['status']).strip().lower()" in src


def test_booking_approval_is_blocked_pending_governed_numbering():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'app'/'main.py').read_text(encoding='utf-8')
    assert 'BOOKING_RFA_REQUIRED_BEFORE_APPROVAL' in source
    assert 'BOOKING_APPROVAL_REFERENCE_ISSUANCE_NOT_READY' in source


def test_booking_agent_reference_prefix_policy_does_not_issue_numbers():
    from fastapi import HTTPException
    from app.main import booking_agent_reference_prefix
    assert booking_agent_reference_prefix('POL_EXPORT')=='SE'
    assert booking_agent_reference_prefix('POD_IMPORT')=='SI'
    assert booking_agent_reference_prefix('TRANSSHIPMENT')=='TS'
    assert booking_agent_reference_prefix('PRINCIPAL')=='PR'
    try:
        booking_agent_reference_prefix('OTHER')
    except HTTPException as exc:
        assert exc.status_code==422
    else:
        raise AssertionError('Unknown operational responsibility must be rejected')


def test_booking_approval_reference_requires_governed_action():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'app'/'main.py').read_text(encoding='utf-8')
    assert source.count('BOOKING_APPROVAL_REFERENCE_ACTION_REQUIRED') == 2
    assert "previous_fields=json.loads(r['payload_json'])" in source
    assert "'Approval #','Approval Ref','Approval Reference'" in source


def test_booking_port_permission_predicate_fails_closed():
    from app.main import booking_port_allowed
    assert booking_port_allowed('NLRTM',{'NLRTM','SGSIN'})
    assert not booking_port_allowed('USNYC',{'NLRTM','SGSIN'})
    assert not booking_port_allowed('NLRTM',None)
    assert not booking_port_allowed('NLRTM',[])
    assert booking_port_allowed('USNYC',None,all_ports_granted=True)
    assert not booking_port_allowed('USNYC',None,all_ports_granted='true')
    assert not booking_port_allowed('',{'NLRTM'},all_ports_granted=True)


def test_booking_pol_pod_ts_scopes_require_verified_grants():
    from fastapi import HTTPException
    from app.main import booking_validate_authorized_ports
    fields={'POL':'NLRTM','POD':'SGSIN','POT (1)':'AEJEA'}
    grants={
        'POL':{'ports':['NLRTM']},
        'POD':{'ports':['SGSIN']},
        'TS':{'ports':['AEJEA']},
    }
    assert booking_validate_authorized_ports(fields,grants)
    for invalid in (None,{}, {'POL':{'ports':['NLRTM']}}):
        try:
            booking_validate_authorized_ports(fields,invalid)
        except HTTPException as exc:
            assert exc.status_code==403
        else:
            raise AssertionError('Missing field permission must deny access')
    assert booking_validate_authorized_ports(fields,{
        'POL':{'all_ports':True},'POD':{'all_ports':True},'TS':{'all_ports':True}})
    try:
        booking_validate_authorized_ports(fields,{
            'POL':{'all_ports':'true'},'POD':{'all_ports':True},'TS':{'all_ports':True}})
    except HTTPException as exc:
        assert exc.status_code==403
    else:
        raise AssertionError('Untrusted string cannot grant all-port access')


def test_booking_agent_iam_binding_is_fail_closed():
    import sqlite3
    from app.main import booking_iam_agent_granted
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE iam_party_access (user_id INTEGER,party_type TEXT,party_key TEXT,status TEXT)')
    db.execute('INSERT INTO iam_party_access VALUES (7, ?, ?, ?)',('AGENT','AG001','ACTIVE'))
    assert booking_iam_agent_granted(db,7,'AG001')
    assert not booking_iam_agent_granted(db,8,'AG001')
    assert not booking_iam_agent_granted(db,7,'AG002')
    db.close()


def test_booking_session_agent_write_guard_present():
    from pathlib import Path
    text=(Path(__file__).resolve().parents[1]/'app'/'main.py').read_text(encoding='utf-8')
    assert text.count('BOOKING_AGENT_LOGIN_NOT_AUTHORIZED') == 3


def test_existing_agent_office_cannot_silently_cross_country_scope():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'app'/'admin.py').read_text(encoding='utf-8')
    assert 'AGENT_OFFICE_SCOPE_CONFLICT' in source
    assert "office['country_id']!=co['id']" in source
    assert "office['organization_id']!=org['id']" in source


def test_agent_offices_endpoint_uses_existing_iam_memberships():
    from pathlib import Path
    s=(Path(__file__).resolve().parents[1]/'app'/'admin.py').read_text(encoding='utf-8')
    assert "@router.get('/agent/{agent_code}/authorized-offices')" in s
    assert 'iam_office_membership' in s
    assert 'AGENT_PARTY_ACCESS_DENIED' in s


def test_agent_office_lookup_has_exact_agent_scope():
    from pathlib import Path
    s=(Path(__file__).resolve().parents[1]/'app'/'admin.py').read_text(encoding='utf-8')
    assert "'AGT-'+agent_code" in s
    assert "if x['office_code'] in allowed" in s


def test_agent_offices_permission_boundary_in_sqlite():
    import sqlite3
    from app.admin import _assigned_agent_offices
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    try:
        c.executescript("""
        CREATE TABLE iam_party_access(user_id INTEGER,party_type TEXT,party_key TEXT,status TEXT);
        CREATE TABLE iam_office_membership(user_id INTEGER,office_id INTEGER,status TEXT);
        CREATE TABLE iam_offices(id INTEGER,office_code TEXT,country_id INTEGER,organization_id INTEGER,status TEXT);
        CREATE TABLE iam_countries(id INTEGER,country_code TEXT,status TEXT);
        CREATE TABLE iam_organizations(id INTEGER,org_code TEXT,status TEXT);
        INSERT INTO iam_party_access VALUES(7,'AGENT','A01','ACTIVE');
        INSERT INTO iam_party_access VALUES(7,'AGENT','B02','ACTIVE');
        INSERT INTO iam_countries VALUES(1,'NL','ACTIVE'),(2,'DE','ACTIVE');
        INSERT INTO iam_organizations VALUES(1,'M3-GLOBAL','ACTIVE');
        INSERT INTO iam_offices VALUES(1,'AGT-A01',1,1,'ACTIVE'),(2,'AGT-B02',2,1,'ACTIVE');
        INSERT INTO iam_office_membership VALUES(7,1,'ACTIVE'),(7,2,'ACTIVE');
        """)
        a=_assigned_agent_offices(c,7,'A01')
        b=_assigned_agent_offices(c,7,'B02')
        assert [(x['office_code'],x['country_code']) for x in a]==[('AGT-A01','NL')]
        assert [(x['office_code'],x['country_code']) for x in b]==[('AGT-B02','DE')]
        with pytest.raises(HTTPException) as denied:
            _assigned_agent_offices(c,8,'A01')
        assert denied.value.status_code==403
    finally:
        c.close()


def test_online_booking_request_is_pre_job_and_approval_gated():
    from app.online_booking_request_rules import online_request_transition
    assert online_request_transition("DRAFT","SUBMIT")=="SUBMITTED"
    assert online_request_transition("SUBMITTED","APPROVE")=="APPROVED"
    assert online_request_transition("SUBMITTED","REJECT")=="REJECTED"
    assert online_request_transition("APPROVED","CONVERT",approved=True)=="CONVERTED"
    assert online_request_transition("CONVERTED","CONVERT",linked_job_ref="50001")=="CONVERTED"
    with pytest.raises(HTTPException):
        online_request_transition("DRAFT","CONVERT",approved=True)
    with pytest.raises(HTTPException):
        online_request_transition("SUBMITTED","CONVERT",approved=True)
    with pytest.raises(HTTPException):
        online_request_transition("APPROVED","CONVERT",approved=False)
    with pytest.raises(HTTPException):
        online_request_transition("REJECTED","CONVERT",approved=True)


def test_online_request_types_and_internal_ids():
    from app.online_booking_request_rules import validate_online_request_input
    assert validate_online_request_input('EXPORT', {'POL':'NLRTM'})['request_type']=='EXPORT'
    assert validate_online_request_input('IMPORT', {})['request_type']=='IMPORT'
    assert validate_online_request_input('TRANSSHIPMENT', {})['request_type']=='TRANSSHIPMENT'
    with pytest.raises(HTTPException):
        validate_online_request_input('EXPORT', {'job_ref':'50001'})
    with pytest.raises(HTTPException):
        validate_online_request_input('EXPORT', {'approval_ref':'XYZ'})


def test_agent_profile_reuses_common_party_and_rejects_unassigned_agent():
    import sqlite3
    from app.admin import _authorized_agent_profile
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    try:
        c.executescript("""
        CREATE TABLE md_records(domain TEXT,record_key TEXT,display_name TEXT,payload_json TEXT,status TEXT);
        CREATE TABLE iam_party_access(user_id INTEGER,party_type TEXT,party_key TEXT,status TEXT);
        CREATE TABLE iam_office_membership(user_id INTEGER,office_id INTEGER,status TEXT);
        CREATE TABLE iam_offices(id INTEGER,office_code TEXT,country_id INTEGER,organization_id INTEGER,status TEXT);
        CREATE TABLE iam_countries(id INTEGER,country_code TEXT,status TEXT);
        CREATE TABLE iam_organizations(id INTEGER,org_code TEXT,status TEXT);
        INSERT INTO md_records VALUES('agent','AG01','Example Agent','{"common_party_key":"PA01"}','ACTIVE');
        INSERT INTO md_records VALUES('common-party','PA01','Example Party','{}','ACTIVE');
        INSERT INTO iam_party_access VALUES(4,'AGENT','AG01','ACTIVE');
        INSERT INTO iam_countries VALUES(1,'NL','ACTIVE');
        INSERT INTO iam_organizations VALUES(1,'M3-GLOBAL','ACTIVE');
        INSERT INTO iam_offices VALUES(1,'AGT-AG01',1,1,'ACTIVE');
        INSERT INTO iam_office_membership VALUES(4,1,'ACTIVE');
        """)
        profile=_authorized_agent_profile(c,4,'AG01')
        assert profile['common_party_key']=='PA01'
        assert profile['offices'][0]['country_code']=='NL'
        with pytest.raises(HTTPException) as denied:
            _authorized_agent_profile(c,8,'AG01')
        assert denied.value.status_code==403
    finally:
        c.close()


def test_agent_profile_country_mismatch_guard_present():
    from pathlib import Path
    code=(Path(__file__).resolve().parents[1]/'app'/'admin.py').read_text(encoding='utf-8')
    assert 'AGENT_OFFICE_COUNTRY_MISMATCH' in code
    assert "o['country_code']" in code


def test_agent_setup_bulk_existing_master_permissions_and_booking_rules():
    import sqlite3
    from app.admin import _authorized_agent_profile
    from app.agent_setup import booking_rule_check
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    try:
        c.executescript("""
        CREATE TABLE md_records(domain TEXT,record_key TEXT,display_name TEXT,payload_json TEXT,status TEXT);
        CREATE TABLE iam_party_access(user_id INTEGER,party_type TEXT,party_key TEXT,status TEXT);
        CREATE TABLE iam_office_membership(user_id INTEGER,office_id INTEGER,status TEXT);
        CREATE TABLE iam_offices(id INTEGER,office_code TEXT,country_id INTEGER,organization_id INTEGER,status TEXT);
        CREATE TABLE iam_countries(id INTEGER,country_code TEXT,status TEXT);
        CREATE TABLE iam_organizations(id INTEGER,org_code TEXT,status TEXT);
        INSERT INTO md_records VALUES('agent','AG01','Agent','{"common_party_key":"PA01","country":"NL"}','ACTIVE');
        INSERT INTO md_records VALUES('common-party','PA01','Party','{}','ACTIVE');
        INSERT INTO iam_party_access VALUES(5,'AGENT','AG01','ACTIVE');
        INSERT INTO iam_countries VALUES(1,'NL','ACTIVE'),(2,'DE','ACTIVE');
        INSERT INTO iam_organizations VALUES(1,'M3-GLOBAL','ACTIVE');
        INSERT INTO iam_offices VALUES(1,'AGT-AG01',1,1,'ACTIVE'),(2,'AGT-OTHER',2,1,'ACTIVE');
        INSERT INTO iam_office_membership VALUES(5,1,'ACTIVE'),(5,2,'ACTIVE');
        """)
        profile=_authorized_agent_profile(c,5,'AG01')
        assert profile['common_party_key']=='PA01'
        assert [o['office_code'] for o in profile['offices']]==['AGT-AG01']
        assert profile['offices'][0]['country_code']=='NL'
        with pytest.raises(HTTPException):
            _authorized_agent_profile(c,6,'AG01')
        # Existing configured network restrictions still apply to Booking;
        # operational network configuration must never substitute for IAM grants.
        result=booking_rule_check(c,{'agent_code':'AG01'}, {})
        assert not result['errors']
    finally:
        c.close()


def test_multi_country_agent_offices_require_explicit_mapping_and_membership():
    import sqlite3
    from app.admin import _assigned_agent_offices, _authorized_agent_profile
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    try:
        c.executescript("""
        CREATE TABLE md_records(domain TEXT,record_key TEXT,display_name TEXT,payload_json TEXT,status TEXT);
        CREATE TABLE iam_party_access(user_id INTEGER,party_type TEXT,party_key TEXT,status TEXT);
        CREATE TABLE iam_office_membership(user_id INTEGER,office_id INTEGER,status TEXT);
        CREATE TABLE iam_offices(id INTEGER,office_code TEXT,country_id INTEGER,organization_id INTEGER,status TEXT);
        CREATE TABLE iam_countries(id INTEGER,country_code TEXT,status TEXT);
        CREATE TABLE iam_organizations(id INTEGER,org_code TEXT,status TEXT);
        INSERT INTO md_records VALUES('agent','AG01','Example Agent','{"common_party_key":"PA01","country":"NL"}','ACTIVE');
        INSERT INTO md_records VALUES('common-party','PA01','Example Party','{}','ACTIVE');
        INSERT INTO iam_party_access VALUES(5,'AGENT','AG01','ACTIVE');
        INSERT INTO iam_countries VALUES(1,'NL','ACTIVE'),(2,'DE','ACTIVE');
        INSERT INTO iam_organizations VALUES(1,'M3-GLOBAL','ACTIVE');
        INSERT INTO iam_offices VALUES(1,'AGT-AG01',1,1,'ACTIVE'),(2,'HAM',2,1,'ACTIVE'),(3,'UNRELATED',2,1,'ACTIVE');
        INSERT INTO iam_office_membership VALUES(5,1,'ACTIVE'),(5,2,'ACTIVE'),(5,3,'ACTIVE');
        """)
        assert [x['office_code'] for x in _assigned_agent_offices(c,5,'AG01')]==['AGT-AG01']
        c.executescript("""
        INSERT INTO md_records VALUES('configuration','AG01-O1','Agent NL','{"config_type":"AGENT_OFFICE_ASSIGNMENT","agent_code":"AG01","office_code":"AGT-AG01"}','ACTIVE');
        INSERT INTO md_records VALUES('configuration','AG01-O2','Agent DE','{"config_type":"AGENT_OFFICE_ASSIGNMENT","agent_code":"AG01","office_code":"HAM"}','ACTIVE');
        """)
        offices=_authorized_agent_profile(c,5,'AG01')['offices']
        assert {x['office_code'] for x in offices}=={'AGT-AG01','HAM'}
        assert {x['country_code'] for x in offices}=={'NL','DE'}
        c.execute("UPDATE iam_office_membership SET status='INACTIVE' WHERE office_id=2")
        assert [x['office_code'] for x in _assigned_agent_offices(c,5,'AG01')]==['AGT-AG01']
    finally:
        c.close()


def test_booking_agent_network_port_grants_connected_to_writes():
    from pathlib import Path
    s=(Path(__file__).resolve().parents[1]/'app'/'main.py').read_text(encoding='utf-8')
    assert s.count('booking_agent_port_grants(conn,ga[')==3
    assert "config.get('network_type')" in s
    assert "config.get('office_code')" in s


def test_booking_agent_port_grants_sqlite_allow_deny_and_revocation():
    import sqlite3
    from app.main import booking_agent_port_grants, booking_validate_authorized_ports
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    try:
        c.executescript("""
        CREATE TABLE md_records(domain TEXT,record_key TEXT,payload_json TEXT,status TEXT);
        CREATE TABLE iam_party_access(user_id INTEGER,party_type TEXT,party_key TEXT,status TEXT);
        CREATE TABLE iam_office_membership(user_id INTEGER,office_id INTEGER,status TEXT);
        CREATE TABLE iam_offices(id INTEGER,office_code TEXT,country_id INTEGER,organization_id INTEGER,status TEXT);
        CREATE TABLE iam_countries(id INTEGER,country_code TEXT,status TEXT);
        CREATE TABLE iam_organizations(id INTEGER,org_code TEXT,status TEXT);
        INSERT INTO iam_party_access VALUES(7,'AGENT','AG01','ACTIVE');
        INSERT INTO iam_countries VALUES(1,'NL','ACTIVE');
        INSERT INTO iam_organizations VALUES(1,'M3-GLOBAL','ACTIVE');
        INSERT INTO iam_offices VALUES(1,'AGT-AG01',1,1,'ACTIVE');
        INSERT INTO iam_office_membership VALUES(7,1,'ACTIVE');
        INSERT INTO md_records VALUES('configuration','N1','{"config_type":"AGENT_NETWORK","network_type":"PORT_COVERAGE","agent_code":"AG01","office_code":"AGT-AG01","scope":"POL","port_code":"NLRTM"}','ACTIVE');
        """)
        grants=booking_agent_port_grants(c,7,'AG01')
        assert booking_validate_authorized_ports({'POL':'NLRTM'},grants) is True
        with pytest.raises(HTTPException):
            booking_validate_authorized_ports({'POD':'NLRTM'},grants)
        with pytest.raises(HTTPException):
            booking_validate_authorized_ports({'POL':'DEHAM'},grants)
        c.execute("""UPDATE md_records SET payload_json=? WHERE record_key='N1'""", ('{"config_type":"AGENT_NETWORK","network_type":"PORT_COVERAGE","agent_code":"AG01","office_code":"AGT-AG01","scope":"POL","port_code":"NLRTM","effective_to":"2020-01-01"}',))
        assert booking_agent_port_grants(c,7,'AG01')['POL']['ports']==set()
        c.execute("UPDATE iam_office_membership SET status='INACTIVE' WHERE user_id=7")
        with pytest.raises(HTTPException):
            booking_validate_authorized_ports({'POL':'NLRTM',},booking_agent_port_grants(c,7,'AG01'))
    finally:
        c.close()


def test_agent_setup_configuration_contract_validation():
    from app.masterdata import validate_payload
    office={'config_type':'AGENT_OFFICE_ASSIGNMENT','agent_code':'AG01','office_code':'HAM'}
    port={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
          'agent_code':'AG01','office_code':'HAM','scope':'TS','port_code':'DEHAM'}
    assert not validate_payload('configuration',office)
    assert not validate_payload('configuration',port)
    assert 'AGENT_CONFIGURATION_REQUIRED_FIELDS' in validate_payload(
        'configuration',{'config_type':'AGENT_OFFICE_ASSIGNMENT','agent_code':'AG01'})
    assert 'AGENT_PORT_SCOPE_INVALID' in validate_payload(
        'configuration',{**port,'scope':'UNKNOWN'})
    assert 'AGENT_PORT_CODE_INVALID' in validate_payload(
        'configuration',{**port,'port_code':'invalid-port-code'})


@pytest.mark.parametrize("field,scope", [
    ("POL","POL"),("POD","POD"),("POT (1)","TS"),("POT (2)","TS")
])
def test_booking_agent_scoped_port_matrix(field,scope):
    from app.main import booking_validate_authorized_ports
    grants={
        "POL":{"ports":{"NLRTM"},"all_ports":False},
        "POD":{"ports":{"DEHAM"},"all_ports":False},
        "TS":{"ports":{"BEANR"},"all_ports":False},
    }
    valid={"POL":"NLRTM","POD":"DEHAM","TS":"BEANR"}[scope]
    assert booking_validate_authorized_ports({field:valid},grants)
    with pytest.raises(HTTPException) as denied:
        booking_validate_authorized_ports({field:"USNYC"},grants)
    assert denied.value.status_code==403
    assert denied.value.detail["field"]==field
    with pytest.raises(HTTPException):
        booking_validate_authorized_ports({field:valid},{"POL":{"ports":[]}})
    with pytest.raises(HTTPException):
        booking_validate_authorized_ports({field:valid},None)


def test_booking_agent_replay_rechecks_current_grants_before_idempotent_return():
    from pathlib import Path
    import inspect
    from app.main import create_record
    code=inspect.getsource(create_record)
    replay=code.index("if idempotency_key:")
    guard=code.index("replay_ctx=job_context(")
    port=code.index("booking_validate_authorized_ports(")
    assert guard<replay and port<replay
    assert "booking_iam_agent_granted(conn,ga['session']['user_id'],replay_ctx['agent_code'])" in code[:replay]


def test_agent_setup_ui_acceptance_missing_tabs_documented():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text(encoding='utf-8')
    assert "master-data::agents" in html
    assert "renderHoAgentRelation" in html
    assert "renderHoAgentNetwork" in html
    assert "renderHoPaParties" in html


def test_booking_api_rejects_unauthenticated_governed_create(isolated,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('M3_BULK_PERMISSION_GOVERNANCE_ENABLED','true')
    # Existing UAT authorization permits API tests while production stays OFF.
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_UAT_TOKEN','isolated-booking-api-test')
    client=TestClient(app,raise_server_exceptions=False,headers={'X-M3-UAT-Token':'isolated-booking-api-test'})
    with db.connect() as conn:
        before=conn.execute("SELECT COUNT(*) FROM transaction_records WHERE module='booking'").fetchone()[0]
    response=client.post('/api/v1/booking',json={'job_ref':'50001','fields':{'POL':'NLRTM'}})
    assert response.status_code in (401,403), response.text
    with db.connect() as conn:
        after=conn.execute("SELECT COUNT(*) FROM transaction_records WHERE module='booking'").fetchone()[0]
    assert before==after


def test_booking_api_rejects_unauthenticated_governed_update(isolated,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('M3_BULK_PERMISSION_GOVERNANCE_ENABLED','true')
    # Existing UAT authorization permits API tests while production stays OFF.
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_UAT_TOKEN','isolated-booking-api-test')
    with db.connect() as conn:
        record=conn.execute("SELECT id,payload_json,version FROM transaction_records WHERE module='booking' ORDER BY id LIMIT 1").fetchone()
    if record is None:
        pytest.skip('Seed has no Booking transaction to exercise authenticated update gate')
    client=TestClient(app,raise_server_exceptions=False,headers={'X-M3-UAT-Token':'isolated-booking-api-test'})
    response=client.put(f"/api/v1/booking/{record['id']}",json={'version':record['version'],'fields':{'POL':'USNYC'}})
    assert response.status_code in (401,403), response.text
    with db.connect() as conn:
        after=conn.execute("SELECT payload_json,version FROM transaction_records WHERE id=?",(record['id'],)).fetchone()
    assert (after['payload_json'],after['version'])==(record['payload_json'],record['version'])


@pytest.mark.parametrize("session_token", ["nonexistent-session", ""])
def test_booking_api_rejects_invalid_agent_session_without_creating_record(isolated,monkeypatch,session_token):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('M3_BULK_PERMISSION_GOVERNANCE_ENABLED','true')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_UAT_TOKEN','isolated-booking-api-test')
    client=TestClient(app,raise_server_exceptions=False,
        headers={'X-M3-UAT-Token':'isolated-booking-api-test'})
    with db.connect() as conn:
        before=conn.execute("SELECT COUNT(*) FROM transaction_records WHERE module='booking'").fetchone()[0]
    headers={'X-M3-Session':session_token} if session_token else {}
    response=client.post('/api/v1/booking',
        headers=headers,json={'job_ref':'50001','fields':{'POL':'NLRTM'}})
    assert response.status_code==401, response.text
    with db.connect() as conn:
        after=conn.execute("SELECT COUNT(*) FROM transaction_records WHERE module='booking'").fetchone()[0]
    assert before==after


def test_booking_api_rejects_revoked_session_without_creating_record(isolated,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('M3_BULK_PERMISSION_GOVERNANCE_ENABLED','true')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_UAT_TOKEN','isolated-booking-api-test')
    token=login(Login(username='admin',password='Admin123!',mfa_code='123456'))['session_token']
    with db.connect() as conn:
        conn.execute("UPDATE iam_sessions SET status='REVOKED' WHERE session_token=?",(token,))
        conn.commit()
        before=conn.execute("SELECT COUNT(*) FROM transaction_records WHERE module='booking'").fetchone()[0]
    client=TestClient(app,raise_server_exceptions=False,
        headers={'X-M3-UAT-Token':'isolated-booking-api-test','X-M3-Session':token})
    response=client.post('/api/v1/booking',json={'job_ref':'50001','fields':{'POL':'NLRTM'}})
    assert response.status_code==401, response.text
    with db.connect() as conn:
        after=conn.execute("SELECT COUNT(*) FROM transaction_records WHERE module='booking'").fetchone()[0]
    assert before==after


def test_agent_multirow_email_governance_contract():
    from app.masterdata import validate_payload
    good={'config_type':'AGENT_EMAIL','agent_code':'CLX-AGT-RTM','port_code':'NLRTM','email':'ops@example.com'}
    assert validate_payload('configuration',good)==[]
    assert 'AGENT_CONFIGURATION_REQUIRED_FIELDS' in validate_payload(
        'configuration',{**good,'email':''})
    assert 'AGENT_EMAIL_INVALID' in validate_payload(
        'configuration',{**good,'email':'not-an-email'})


def test_agent_six_tab_related_master_workflow_wiring():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text(encoding='utf-8')
    assert "const m3AgentTabs=['Agent Detail','Emails','Countries','Offices','CRO Detail','B/L Template']" in html
    assert "async function m3AgentRelatedCreate()" in html
    assert "config_type:'AGENT_EMAIL'" in html
    assert "config_type:'AGENT_OFFICE_ASSIGNMENT'" in html
    assert "operation:'CREATE',payload,reason:reason.trim()" in html
