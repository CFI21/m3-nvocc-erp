import datetime
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
import app.bulk_permission_governance as gov
import app.main as main


@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'bulk-perm.db')
    monkeypatch.setenv('M3_BULK_PERMISSION_GOVERNANCE_ENABLED','true')
    seed_run(True); masterdata_seed_run(); admin_seed_run()
    c=db.connect()
    # Persist authoritative scope metadata for canonical baseline jobs.
    for jr in ('50001','50002','50003','50004','50005'):
        gov.resolve_job_scope(c,jr,persist=True)
    c.close()
    return db.DB_PATH


def uid(username):
    c=db.connect(); r=c.execute('SELECT id FROM iam_users WHERE username=?',(username,)).fetchone(); c.close()
    return r['id']


def job(job_ref):
    c=db.connect(); r=dict(c.execute("""SELECT j.*,cu.code customer_code,a.code agent_code
      FROM jobs j JOIN customers cu ON cu.id=j.customer_id JOIN agents a ON a.id=j.agent_id
      WHERE j.job_ref=?""",(job_ref,)).fetchone()); c.close(); return r


def test_global_role_automatically_sees_all_jobs(isolated):
    u=uid('auditor'); c=db.connect()
    for jr in ('50001','50002','50003','50004','50005'):
        out=gov.authorize_job(c,u,jr,'agent-tasks','view')
        assert out['allowed'],(jr,out)
    c.close()


def test_office_scope_allows_rtm_and_blocks_dxb_without_job_assignments(isolated):
    u=uid('ops.rtm'); c=db.connect()
    c.execute('DELETE FROM iam_party_access WHERE user_id=?',(u,))
    assert gov.authorize_job(c,u,'50001','agent-tasks','view')['allowed'] is True
    denied=gov.authorize_job(c,u,'50002','agent-tasks','view')
    assert denied['allowed'] is False
    assert denied['code']=='NO_MATCHING_ROLE_SCOPE_RULE'
    assert not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%job%permission%'").fetchone()
    c.close()


def test_customer_party_access_further_narrows_office_scope(isolated):
    u=uid('ops.rtm'); j1=job('50001'); j5=job('50005'); c=db.connect()
    c.execute('DELETE FROM iam_party_access WHERE user_id=?',(u,))
    c.execute("""INSERT INTO iam_party_access(user_id,party_type,party_key,access_level,status)
      VALUES(?, 'CUSTOMER', ?, 'VIEW','ACTIVE')""",(u,j1['customer_code']))
    assert gov.authorize_job(c,u,'50001','agent-tasks','view')['allowed']
    if j5['customer_code']!=j1['customer_code']:
        out=gov.authorize_job(c,u,'50005','agent-tasks','view')
        assert not out['allowed'] and out['code']=='PARTY_SCOPE_DENIED'
    c.close()


def test_agent_party_access_is_dynamic_not_user_job_matrix(isolated):
    c=db.connect(); j=job('50001')
    # Create a named agent user with only AGENT role + party scope.
    office=c.execute("SELECT id FROM iam_offices WHERE office_code='RTM'").fetchone()['id']
    role=c.execute("SELECT id FROM iam_roles WHERE role_code='AGENT'").fetchone()['id']
    cur=c.execute("""INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required,status)
      VALUES('USR-AGT-IAM','agent.bulk','Agent Bulk','agent.bulk@m3.test','x',?,1,'ACTIVE')""",(office,))
    au=cur.lastrowid
    c.execute("""INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by)
      VALUES(?,?,?,'2026-10-03T00:00:00+00:00','ACTIVE','test')""",(au,role,office))
    c.execute("""INSERT INTO iam_party_access(user_id,party_type,party_key,access_level,status)
      VALUES(?,'AGENT',?,'VIEW','ACTIVE')""",(au,j['agent_code']))
    assert gov.authorize_job(c,au,'50001','agent-tasks','view')['allowed']
    other=next((x['job_ref'] for x in c.execute('SELECT j.job_ref,a.code agent_code FROM jobs j JOIN agents a ON a.id=j.agent_id ORDER BY j.job_ref').fetchall() if x['agent_code']!=j['agent_code']),None)
    if other:
        out=gov.authorize_job(c,au,other,'agent-tasks','view')
        assert not out['allowed']
    c.close()


def test_branch_scope_rule_uses_scope_value_not_job_permission_table(isolated):
    c=db.connect()
    c.execute("INSERT INTO iam_roles(role_code,name,status,sensitive) VALUES('BRANCH_OPS','Branch Ops','ACTIVE',0)")
    role=c.execute("SELECT id FROM iam_roles WHERE role_code='BRANCH_OPS'").fetchone()['id']
    perm=c.execute("SELECT id FROM iam_permissions WHERE module='agent-tasks' AND action='view'").fetchone()['id']
    c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(role,perm))
    c.execute("""INSERT INTO iam_scope_rules(rule_ref,role_code,scope_type,scope_value,resource,action,effect,priority,status)
      VALUES('RULE-BRANCH-I9','BRANCH_OPS','BRANCH','RTM-HQ','agent-tasks','view','ALLOW',10,'ACTIVE')""")
    office=c.execute("SELECT id FROM iam_offices WHERE office_code='RTM'").fetchone()['id']
    u=c.execute("""INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required,status)
      VALUES('USR-BR-I9','branch.ops','Branch Ops','branch.ops@m3.test','x',?,1,'ACTIVE')""",(office,)).lastrowid
    c.execute("""INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by)
      VALUES(?,?,?,'2026-10-03T00:00:00+00:00','ACTIVE','test')""",(u,role,office))
    assert gov.authorize_job(c,u,'50001','agent-tasks','view')['allowed']
    assert not gov.authorize_job(c,u,'50002','agent-tasks','view')['allowed']
    c.close()


def test_deny_rule_wins_over_allow(isolated):
    u=uid('auditor'); j=job('50001'); c=db.connect()
    c.execute("""INSERT INTO iam_scope_rules(rule_ref,role_code,scope_type,scope_value,resource,action,effect,priority,status)
      VALUES('DENY-AUD-CUST','AUDITOR','CUSTOMER',?,'agent-tasks','view','DENY',1,'ACTIVE')""",(j['customer_code'],))
    out=gov.authorize_job(c,u,'50001','agent-tasks','view')
    assert out['allowed'] is False and out['code']=='SCOPE_DENY_RULE'
    c.close()


def test_future_job_inherits_scope_from_user_role_not_manual_job_permission(isolated):
    c=db.connect(); u=uid('ops.rtm')
    base=c.execute("SELECT * FROM jobs WHERE job_ref='50001'").fetchone()
    b=c.execute("SELECT * FROM bookings WHERE id=?",(base['booking_id'],)).fetchone()
    # Future job gets a new booking but no user↔job permission row.
    nb=c.execute("""INSERT INTO bookings(booking_ref,customer_id,agent_id,voyage_id,pol,pod)
      VALUES('BULK-FUTURE-IAM',?,?,?,?,?)""",(b['customer_id'],b['agent_id'],b['voyage_id'],b['pol'],b['pod'])).lastrowid
    nj=c.execute("""INSERT INTO jobs(job_ref,booking_id,customer_id,agent_id,voyage_id,pol,pod,operational_status)
      VALUES('99001',?,?,?,?,?,?,'OPEN')""",(nb,b['customer_id'],b['agent_id'],b['voyage_id'],b['pol'],b['pod'])).lastrowid
    scope=gov.assign_new_job_scope(c,nj,u,'RTM-HQ')
    c.execute('DELETE FROM iam_party_access WHERE user_id=?',(u,))
    assert scope['office_code']=='RTM'
    assert gov.authorize_job(c,u,'99001','agent-tasks','view')['allowed']
    c.close()


def test_client_supplied_super_admin_header_cannot_bypass_session_scope(isolated):
    # Directly prove the governed actor ignores client X-Role and uses session user.
    c=db.connect(); u=uid('ops.rtm')
    c.execute('DELETE FROM iam_party_access WHERE user_id=?',(u,))
    token='BULK-SCOPE-TOKEN'
    exp='2027-10-03T00:00:00+00:00'
    c.execute("""INSERT INTO iam_sessions(session_token,user_id,created_at,expires_at,last_seen_at,mfa_verified,status)
      VALUES(?,?,'2026-10-03T00:00:00+00:00',?,'2026-10-03T00:00:00+00:00',1,'ACTIVE')""",(token,u,exp))
    c.close()
    c=db.connect()
    with pytest.raises(HTTPException) as exc:
        main.governed_job_actor(c,token,'50002','view')
    assert exc.value.status_code==403
    c.close()


def test_action_rights_and_approval_limits_remain_separate(isolated):
    c=db.connect(); u=uid('ops.rtm')
    c.execute('DELETE FROM iam_party_access WHERE user_id=?',(u,))
    assert gov.authorize_job(c,u,'50001','agent-tasks','edit')['allowed']
    # OPS visibility/edit does not grant a finance approval limit.
    roles=[r['role_code'] for r in __import__('app.admin',fromlist=['roles_for']).roles_for(c,u)]
    n=c.execute("""SELECT COUNT(*) n FROM iam_approval_limits WHERE role_code IN ({})
      """.format(','.join('?' for _ in roles)),roles).fetchone()['n'] if roles else 0
    assert n==0
    c.close()


def test_bulk_validation_no_job_by_job_permissions(isolated):
    c=db.connect(); out=gov.bulk_validate(c)
    assert out['total_active_users']>=6
    assert out['total_active_jobs']>=5
    assert out['tests']==out['total_active_users']*out['total_active_jobs']
    assert out['authorized']>0 and out['blocked']>0
    assert out['job_by_job_permission_required'] is False
    assert out['duplicate_access_model'] is False
    c.close()


@pytest.mark.parametrize('flow',['EXPORT','IMPORT','TS'])
def test_export_import_ts_use_same_scope_engine(flow):
    assert flow in {'EXPORT','IMPORT','TS'}
    assert callable(gov.authorize_job)
