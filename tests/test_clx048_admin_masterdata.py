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
from app.masterdata import Change,Decision,Withdrawal,create_change,decide,withdraw_change,records,duplicates,audit_list
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
    token=admin_session()
    for s in screens:
        d=screen_data(s['screen_id'],x_role='ADMIN',x_m3_session=token)
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
    assert same.value.detail['code']=='CHECKER_PERMISSION_DENIED'
    approved=decide(cr['change_ref'],Decision(decision='APPROVE',comment='approved'),x_m3_session=checker_session())
    assert approved['version']==1
    dr=create_change(Change(domain='carrier',record_key='CLX-NEW-CARRIER',operation='DEACTIVATE',payload={},reason='retire'),x_m3_session=maker_session())
    done=decide(dr['change_ref'],Decision(decision='APPROVE'),x_m3_session=checker_session())
    assert done['version']==2
    row=next(x for x in records('carrier',x_m3_session=admin_session()) if x['record_key']=='CLX-NEW-CARRIER')
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


def test_agent_master_create_update_four_eyes_and_readback(isolated):
    code='UAT-AGENT-001'
    initial={'name':'UAT Agent','short_name':'UAT','currency':'USD','active':True,
             'export_email':'export@example.com','booking_restriction':'None'}
    created=create_change(Change(domain='agent',record_key=code,operation='CREATE',
                                 payload=initial,reason='isolated agent acceptance'),
                          x_m3_session=maker_session())
    with pytest.raises(HTTPException):
        decide(created['change_ref'],Decision(decision='APPROVE'),x_m3_session=maker_session())
    assert not any(x['record_key']==code for x in records('agent',x_m3_session=admin_session()))
    assert decide(created['change_ref'],Decision(decision='APPROVE'),
                  x_m3_session=checker_session())['version']==1
    first=next(x for x in records('agent',x_m3_session=admin_session()) if x['record_key']==code)
    assert all(first['payload'][k]==v for k,v in initial.items())
    updated=create_change(Change(domain='agent',record_key=code,operation='UPDATE',
                                 payload={'short_name':'UAT2','import_email':'import@example.com','active':False},
                                 reason='agent field readback'),
                          x_m3_session=maker_session())
    # Pending maker changes must never leak into the authoritative Agent read.
    pending=next(x for x in records('agent',x_m3_session=admin_session()) if x['record_key']==code)
    assert pending['version']==1
    assert pending['payload']['short_name']=='UAT'
    assert pending['payload'].get('import_email') is None
    assert pending['payload']['active'] is True
    assert decide(updated['change_ref'],Decision(decision='APPROVE'),
                  x_m3_session=checker_session())['version']==2
    saved=next(x for x in records('agent',x_m3_session=admin_session()) if x['record_key']==code)
    assert saved['payload']['short_name']=='UAT2'
    assert saved['payload']['import_email']=='import@example.com'
    assert saved['payload']['export_email']==initial['export_email']
    assert saved['payload']['active'] is False


def test_agent_master_pending_reject_and_resubmit_lifecycle(isolated):
    code='UAT-AGENT-REJECT-001'
    initial={'name':'UAT Agent Rejection Cycle','short_name':'UATR','active':True}
    request=create_change(Change(domain='agent',record_key=code,operation='CREATE',
                                 payload=initial,reason='isolated create review'),
                          x_m3_session=maker_session())
    assert not any(row['record_key']==code for row in records('agent',x_m3_session=admin_session()))
    with pytest.raises(HTTPException):
        decide(request['change_ref'],Decision(decision='APPROVE'),
               x_m3_session=maker_session())
    rejected=decide(request['change_ref'],Decision(decision='REJECT',comment='check required'),
                    x_m3_session=checker_session())
    assert rejected['status']=='REJECTED'
    assert not any(row['record_key']==code for row in records('agent',x_m3_session=admin_session()))
    resubmitted=create_change(Change(domain='agent',record_key=code,operation='CREATE',
                                     payload=initial,reason='resubmitted after rejection'),
                              x_m3_session=maker_session())
    approved=decide(resubmitted['change_ref'],Decision(decision='APPROVE',comment='approved'),
                    x_m3_session=checker_session())
    assert approved['version']==1
    row=next(row for row in records('agent',x_m3_session=admin_session())
             if row['record_key']==code)
    assert row['payload']['name']==initial['name']
    assert row['payload']['active'] is True


def test_pending_change_withdrawal_is_maker_only_and_never_applies_record(isolated):
    maker=maker_session()
    checker=checker_session()
    created=create_change(Change(domain='carrier',record_key='B01-WITHDRAW-1',operation='CREATE',
                                 payload={'name':'B01 Withdrawal Carrier','short_name':'ORIGINAL'},
                                 reason='B01 test creation'),x_m3_session=maker)
    decide(created['change_ref'],Decision(decision='APPROVE',comment='independent approval'),x_m3_session=checker)
    pending=create_change(Change(domain='carrier',record_key='B01-WITHDRAW-1',operation='UPDATE',
                                 payload={'short_name':'UNAPPROVED'},reason='B01 update test'),
                          x_m3_session=checker)
    with pytest.raises(HTTPException) as unrelated:
        withdraw_change(pending['change_ref'],Withdrawal(reason='Not my request'),x_m3_session=maker)
    assert unrelated.value.status_code==403
    assert unrelated.value.detail['code']=='WITHDRAW_MAKER_ONLY'
    out=withdraw_change(pending['change_ref'],Withdrawal(reason='Mistaken UAT checker-originated update'),
                        x_m3_session=checker)
    assert out['status']=='WITHDRAWN'
    row=next(x for x in records('carrier',x_m3_session=maker) if x['record_key']=='B01-WITHDRAW-1')
    assert row['version']==1
    assert row['payload']['short_name']=='ORIGINAL'
    with pytest.raises(HTTPException) as already:
        withdraw_change(pending['change_ref'],Withdrawal(reason='Repeated withdrawal'),x_m3_session=checker)
    assert already.value.detail['code']=='CHANGE_ALREADY_DECIDED'
    with pytest.raises(HTTPException) as cannot_approve:
        decide(pending['change_ref'],Decision(decision='APPROVE',comment='cannot approve withdrawn request'),x_m3_session=maker)
    assert cannot_approve.value.status_code in (403,409)
    c=db.connect()
    try:
        change=c.execute('SELECT status,checker FROM md_change_requests WHERE change_ref=?',(pending['change_ref'],)).fetchone()
        assert change['status']=='WITHDRAWN'
        assert change['checker'] is None
        assert c.execute("SELECT 1 FROM md_audit WHERE change_ref=? AND action='CHANGE_WITHDRAW'",(pending['change_ref'],)).fetchone()
    finally:
        c.close()


def test_b01_strict_agent_role_split_does_not_affect_other_master_domains(isolated,monkeypatch):
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_B01_STRICT_MAKER_CHECKER','true')
    maker=maker_session()
    checker=checker_session()
    from app.masterdata import Withdrawal,withdraw_change
    with pytest.raises(HTTPException) as blocked:
        create_change(Change(domain='agent',record_key='B01-STRICT-AGENT',
                             operation='CREATE',payload={'name':'Prohibited Checker Agent'},
                             reason='strict separation test'),x_m3_session=checker)
    assert blocked.value.status_code==403
    assert blocked.value.detail['code']=='B01_UAT_CHECKER_CANNOT_SUBMIT_AGENT_CHANGE'
    created=create_change(Change(domain='agent',record_key='B01-STRICT-AGENT',
                                 operation='CREATE',payload={'name':'Permitted Maker Agent'},
                                 reason='strict separation test'),x_m3_session=maker)
    with pytest.raises(HTTPException):
        decide(created['change_ref'],Decision(decision='APPROVE',comment='self'),x_m3_session=maker)
    result=decide(created['change_ref'],Decision(decision='APPROVE',comment='separate checker'),x_m3_session=checker)
    assert result['status']=='APPROVED'
    other=create_change(Change(domain='carrier',record_key='B01-STRICT-CARRIER',
                               operation='CREATE',payload={'name':'Independent Carrier'},
                               reason='non-Agent role unaffected'),x_m3_session=checker)
    assert other['status']=='PENDING'


def test_b01_checker_cannot_propose_agent_related_configuration(isolated,monkeypatch):
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_B01_STRICT_MAKER_CHECKER','true')
    checker=checker_session()
    from app.masterdata import Withdrawal,withdraw_change
    for config_type in ('AGENT_EMAIL','AGENT_OFFICE_ASSIGNMENT','AGENT_NETWORK'):
        with pytest.raises(HTTPException) as exc:
            create_change(Change(domain='configuration',record_key='B01-'+config_type,
                    operation='CREATE',payload={'config_type':config_type,'agent_code':'B01-NO-AGENT'},
                    reason='check segregation before any reference validation'),x_m3_session=checker)
        assert exc.value.status_code==403
        assert exc.value.detail['code']=='B01_UAT_CHECKER_CANNOT_SUBMIT_AGENT_CHANGE'
    # Unrelated configuration is unaffected by the Agent-only B01 policy.
    other=create_change(Change(domain='configuration',record_key='B01-UNRELATED-CONFIG',
                    operation='CREATE',payload={'name':'Unrelated UAT configuration','config_type':'GENERAL'},
                    reason='preserve unrelated workflows'),x_m3_session=checker)
    assert other['status']=='PENDING'
    assert withdraw_change(other['change_ref'],Withdrawal(reason='clean up test'),x_m3_session=checker)['status']=='WITHDRAWN'



def test_b01_rejects_semantic_duplicate_agent_office_pending_and_active(isolated):
    maker=maker_session()
    first=Change(domain='configuration',record_key='AGCFG-OFFICE-A',operation='CREATE',
                 payload={'config_type':'AGENT_OFFICE_ASSIGNMENT','agent_code':'CLX-AGENT-001',
                          'office_code':'RTM','address':'UAT address'},reason='Office test')
    # The target Agent must first exist and be approved for reference integrity.
    a=create_change(Change(domain='agent',record_key='CLX-AGENT-001',operation='CREATE',
                  payload={'name':'Cycle 2 UAT agent','short_name':'C2'},reason='prepare test'),
                  x_m3_session=maker)
    decide(a['change_ref'],Decision(decision='APPROVE',comment='checker'),x_m3_session=checker_session())
    created=create_change(first,x_m3_session=maker)
    same=Change(domain='configuration',record_key='AGCFG-OFFICE-B',operation='CREATE',
                 payload=dict(first.payload),reason='Should be blocked')
    with pytest.raises(HTTPException) as pending:
        create_change(same,x_m3_session=maker)
    assert pending.value.status_code==409
    assert pending.value.detail['code']=='DUPLICATE_AGENT_RELATIONSHIP'
    decide(created['change_ref'],Decision(decision='APPROVE',comment='independent approval'),
           x_m3_session=checker_session())
    with pytest.raises(HTTPException) as active:
        create_change(same,x_m3_session=maker)
    assert active.value.detail['code']=='DUPLICATE_AGENT_RELATIONSHIP'
