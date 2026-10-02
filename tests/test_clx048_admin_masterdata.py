import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.screen_catalog import build_catalog
from app.screen_integration import screen_data,quick_actions
from app.admin import Login,Assign,TempAccess,UserStatus,login,assign,create_temp,set_user_status,permission,roles_for
from app.masterdata import Change,Decision,create_change,decide,records,duplicates,audit_list
from app.clx048_admin_masterdata_hardening import workspace,job_master_trace,integrity_summary,user_access,verify

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx048.db')
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH

def admin_session():
    return login(Login(username='admin',password='Admin123!',mfa_code='123456'))['session_token']
def maker_session():
    return login(Login(username='md.maker',password='Maker123!',mfa_code='123456'))['session_token']
def checker_session():
    return login(Login(username='md.checker',password='Checker123!',mfa_code='123456'))['session_token']

def test_frozen_196_top_level_and_all_admin_master_screens_load(isolated):
    c=build_catalog()
    assert c['screen_count']==196
    assert [x['domain'] for x in c['menu']]==[
      'HO Tasks','Agent Tasks','Treasury / AR-AP',
      'Integration & Security','General / Administration','Master Data'
    ]
    screens=[x for x in c['screens'] if x['domain'] in {'General / Administration','Master Data'}]
    assert screens
    for s in screens:
        d=screen_data(s['screen_id'],x_role='ADMIN')
        assert 'rows' in d
        a=set(quick_actions(s['screen_id'],'ADMIN')['visible_actions'])
        if s['screen_id'].startswith('administration::'):
            assert not ({'create','edit','release','reverse'} & a)

def test_admin_role_assignment_validates_active_status_effective_dates_and_self_sensitive(isolated):
    token=admin_session()
    with pytest.raises(HTTPException) as expired:
        assign(Assign(username='md.maker',role_code='VIEWER',office_code='RTM',valid_to='2020-01-01'),token)
    assert expired.value.detail['code']=='INVALID_EFFECTIVE_DATES'

    c=db.connect()
    try:
        c.execute("UPDATE iam_roles SET status='INACTIVE' WHERE role_code='VIEWER'")
        c.commit()
    finally:c.close()
    with pytest.raises(HTTPException) as inactive:
        assign(Assign(username='md.maker',role_code='VIEWER',office_code='RTM'),token)
    assert inactive.value.detail['code']=='ROLE_NOT_ACTIVE'

    c=db.connect()
    try:
        c.execute("UPDATE iam_roles SET status='ACTIVE' WHERE role_code='VIEWER'")
        c.commit()
    finally:c.close()
    with pytest.raises(HTTPException) as self_sensitive:
        assign(Assign(username='admin',role_code='GL_MANAGER',office_code='RTM'),token)
    assert self_sensitive.value.detail['code']=='FOUR_EYES_SELF_ROLE_ASSIGNMENT_BLOCKED'

def test_admin_permission_change_propagates_immediately(isolated):
    token=admin_session()
    c=db.connect()
    try:
        uid=c.execute("SELECT id FROM iam_users WHERE username='md.maker'").fetchone()['id']
        assert permission(c,uid,'agent-tasks','view','RTM') is False
    finally:c.close()
    out=assign(Assign(username='md.maker',role_code='VIEWER',office_code='RTM',valid_to='2027-12-31'),token)
    assert out['status']=='ASSIGNED'
    c=db.connect()
    try:
        uid=c.execute("SELECT id FROM iam_users WHERE username='md.maker'").fetchone()['id']
        assert permission(c,uid,'agent-tasks','view','RTM') is True
        assert 'VIEWER' in {r['role_code'] for r in roles_for(c,uid)}
    finally:c.close()

def test_admin_sensitive_self_temp_access_and_self_deactivation_blocked(isolated):
    token=admin_session()
    with pytest.raises(HTTPException) as temp:
        create_temp(TempAccess(username='admin',permission_code='identity:admin',valid_from='2026-09-26',valid_to='2027-09-26',reason='CLX048 test'),token)
    assert temp.value.detail['code']=='FOUR_EYES_SELF_TEMP_ACCESS_BLOCKED'
    with pytest.raises(HTTPException) as st:
        set_user_status('admin',UserStatus(status='SUSPENDED'),token)
    assert st.value.detail['code']=='SELF_DEACTIVATION_BLOCKED'

def test_master_pending_duplicate_name_prevention(isolated):
    a=create_change(Change(domain='carrier',record_key='NEW-CAR-1',operation='CREATE',payload={'name':'New Carrier'},reason='CLX048'),x_m3_session=maker_session())
    assert a['status']=='PENDING'
    with pytest.raises(HTTPException) as e:
        create_change(Change(domain='carrier',record_key='NEW-CAR-2',operation='CREATE',payload={'name':'New Carrier'},reason='CLX048 duplicate'),x_m3_session=maker_session())
    assert e.value.detail['code']=='PENDING_DUPLICATE_DISPLAY_NAME'

def test_master_reference_integrity_validation(isolated):
    with pytest.raises(HTTPException) as bank:
        create_change(Change(domain='bank-account',record_key='BAD-BA',operation='CREATE',payload={'name':'Bad Account','bank':'NO-BANK','currency':'USD'},reason='CLX048'),x_m3_session=maker_session())
    assert 'BANK_REFERENCE_NOT_ACTIVE' in bank.value.detail['codes']

    with pytest.raises(HTTPException) as route:
        create_change(Change(domain='route',record_key='BAD-ROUTE',operation='CREATE',payload={'name':'Bad Route','pol':'XXXXX','pod':'NLRTM'},reason='CLX048'),x_m3_session=maker_session())
    assert 'POL_REFERENCE_NOT_ACTIVE' in route.value.detail['codes']

def test_master_approval_rechecks_duplicate_at_apply_time(isolated):
    cr=create_change(Change(domain='carrier',record_key='RACE-1',operation='CREATE',payload={'name':'Race Carrier'},reason='CLX048 race'),x_m3_session=maker_session())
    c=db.connect()
    try:
        ts='2026-09-26T12:00:00+00:00'
        c.execute("INSERT INTO md_records(domain,record_key,display_name,payload_json,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  ('carrier','RACE-OTHER','Race Carrier',json.dumps({'name':'Race Carrier'}),'ACTIVE',1,ts,ts))
        c.commit()
    finally:c.close()
    with pytest.raises(HTTPException) as e:
        decide(cr['change_ref'],Decision(decision='APPROVE',comment='checker'),x_m3_session=checker_session())
    assert e.value.detail['code']=='DUPLICATE_DISPLAY_NAME'

def test_used_master_cannot_be_deactivated(isolated):
    with pytest.raises(HTTPException) as e:
        create_change(Change(domain='customer',record_key='CLX-CUS-001',operation='DEACTIVATE',payload={},reason='still used'),x_m3_session=maker_session())
    assert e.value.detail['code']=='MASTER_IN_USE'

def test_unreferenced_master_four_eyes_version_and_audit(isolated):
    cr=create_change(Change(domain='carrier',record_key='CLX-NEW-CARRIER',operation='CREATE',payload={'name':'CLX New Carrier','scac':'CLXN'},reason='create'),x_m3_session=maker_session())
    with pytest.raises(HTTPException) as same:
        decide(cr['change_ref'],Decision(decision='APPROVE'),x_m3_session=maker_session())
    assert same.value.detail['code']=='FOUR_EYES_VIOLATION'
    approved=decide(cr['change_ref'],Decision(decision='APPROVE',comment='approved'),x_m3_session=checker_session())
    assert approved['version']==1
    dr=create_change(Change(domain='carrier',record_key='CLX-NEW-CARRIER',operation='DEACTIVATE',payload={},reason='retire'),x_m3_session=maker_session())
    done=decide(dr['change_ref'],Decision(decision='APPROVE'),x_m3_session=checker_session())
    assert done['version']==2
    row=next(x for x in records('carrier') if x['record_key']=='CLX-NEW-CARRIER')
    assert row['status']=='INACTIVE'
    assert any(x['record_key']=='CLX-NEW-CARRIER' for x in audit_list())

def test_master_and_iam_audits_are_immutable(isolated):
    token=admin_session()
    assign(Assign(username='md.maker',role_code='VIEWER',office_code='RTM',valid_to='2027-12-31'),token)
    cr=create_change(Change(domain='carrier',record_key='AUD-CAR',operation='CREATE',payload={'name':'Audit Carrier'},reason='audit'),x_m3_session=maker_session())
    decide(cr['change_ref'],Decision(decision='APPROVE'),x_m3_session=checker_session())
    c=db.connect()
    try:
        ia=c.execute('SELECT id FROM iam_audit_events ORDER BY id DESC LIMIT 1').fetchone()
        ma=c.execute('SELECT id FROM md_audit ORDER BY id DESC LIMIT 1').fetchone()
        with pytest.raises(Exception): c.execute("UPDATE iam_audit_events SET action='TAMPER' WHERE id=?",(ia['id'],))
        with pytest.raises(Exception): c.execute("UPDATE md_audit SET action='TAMPER' WHERE id=?",(ma['id'],))
    finally:c.close()

def test_jobs_50001_50005_master_and_scope_references_resolve(isolated):
    for jr in ('50001','50002','50003','50004','50005'):
        t=job_master_trace(jr,'AUDITOR')
        assert t['master_references_valid'] is True
        assert t['scope_references_valid'] is True
    s=integrity_summary('AUDITOR')
    assert s['status']=='PASS'
    assert s['duplicate_active_master_names']==0
    assert s['pending_duplicate_names']==0
    v=verify('AUDITOR')
    assert v['all_jobs_valid'] is True and v['integrity_status']=='PASS'

def test_workspace_and_live_access_trace(isolated):
    w=workspace()
    assert w['screen_count_change']==0
    assert w['live_providers'] is False and w['real_money'] is False
    a=user_access('md.maker','AUDITOR')
    assert a['evaluated_live'] is True
    assert any(r['role_code']=='MASTER_DATA' for r in a['roles'])
