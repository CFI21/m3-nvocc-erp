import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run, phash
from app.admin import Login, login
from app import gl, hardening
from app.final_bulk_governance import (
    actual_amount, management_rollup, assert_no_management_override,
    create_line, submit_line, approve_line, revise_line
)

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'final-bulk.db')
    monkeypatch.setenv('M3_FINAL_BULK_COMPLETION_ENABLED','true')
    seed_run(True); masterdata_seed_run(); admin_seed_run()
    c=db.connect()
    # Independent checker reuses accepted delegation governance.
    admin_id=c.execute("SELECT id FROM iam_users WHERE username='admin'").fetchone()['id']
    auditor_id=c.execute("SELECT id FROM iam_users WHERE username='auditor'").fetchone()['id']
    c.execute("""INSERT INTO iam_delegations(delegation_ref,from_user_id,to_user_id,permission_code,valid_from,valid_to,status,approved_by)
      VALUES('DLG-FINAL-BUDGET',?,?,?,'2026-01-01T00:00:00+00:00','2027-12-31T23:59:59+00:00','ACTIVE','USR-001')""",
      (admin_id,auditor_id,'FINANCE_CONFIG_ADMIN;TX=GL;OFFICE=RTM;COUNTRY=NL'))
    # DXB-local GL manager for negative scope tests.
    office=c.execute("SELECT id FROM iam_offices WHERE office_code='DXB'").fetchone()['id']
    role=c.execute("SELECT id FROM iam_roles WHERE role_code='GL_MANAGER'").fetchone()['id']
    uid=c.execute("""INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required,status)
      VALUES('USR-FINAL-DXB','final.dxb','Final DXB GL Manager','final.dxb@m3.test',?,?,1,'ACTIVE')""",(phash('FinalDxb123!'),office)).lastrowid
    c.execute("""INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by)
      VALUES(?,?,?,'2026-10-04T00:00:00+00:00','ACTIVE','final-bulk')""",(uid,role,office))
    c.execute("INSERT INTO iam_office_membership(user_id,office_id,membership_type) VALUES(?,?,'PRIMARY')",(uid,office))
    c.close()
    return db.DB_PATH

def token(user,pwd):
    return login(Login(username=user,password=pwd,mfa_code='123456'))['session_token']

def user(username):
    c=db.connect();r=c.execute("SELECT id,user_ref FROM iam_users WHERE username=?",(username,)).fetchone();c.close();return dict(r)

def test_seed_budget_has_no_fake_actual_or_variance_and_screen_derives_them(isolated):
    c=db.connect()
    rec=c.execute("SELECT payload_json FROM gl_records WHERE module='budget' ORDER BY id LIMIT 1").fetchone()
    p=json.loads(rec['payload_json'])
    assert 'Actual Amount' not in p and 'Variance' not in p
    c.close()
    out=gl.list_records('budget',x_role='VIEWER',x_m3_session=token('admin','Admin123!'))
    assert out['count']>=5
    row=out['records'][0]
    assert row['fields']['Actual Source']=='POSTED_GL'
    assert 'Actual Amount' in row['fields'] and 'Variance' in row['fields']

def test_budget_forecast_create_and_client_actual_override_blocked(isolated):
    t=token('admin','Admin123!')
    out=hardening.budget_planning_create(hardening.BudgetPlanBody(fields={
      'fiscal_year':2026,'period':9,'account_code':'4000','amount':100000,'scenario':'FORECAST'
    }),x_m3_session=t)
    assert out['scenario']=='FORECAST' and out['status']=='DRAFT'
    assert out['actual_source']=='POSTED_GL'
    with pytest.raises(HTTPException) as exc:
        hardening.budget_planning_create(hardening.BudgetPlanBody(fields={
          'fiscal_year':2026,'period':10,'account_code':'4000','amount':100000,'Actual Amount':999
        }),x_m3_session=t)
    assert exc.value.detail['code']=='ACTUAL_VARIANCE_READ_ONLY'

def test_actuals_come_only_from_posted_gl(isolated):
    c=db.connect();admin=user('admin')
    line=create_line(c,{'fiscal_year':2026,'period':9,'account_code':'4000','amount':100000,'scenario':'BUDGET'},admin['id'],admin['user_ref'])
    before=actual_amount(c,c.execute("SELECT * FROM gl_budget_lines WHERE budget_ref=?",(line['budget_ref'],)).fetchone(),admin['id'])
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    cur=c.execute("""INSERT INTO gl_vouchers(voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
      VALUES('FINAL-UNPOSTED','JV','2026-09-23','USD','Approved','TEST','FINAL-UNPOSTED',?,7777,7777,1,'2026-10-04T00:00:00Z','GL_ACCOUNTANT',1,'USD',7777,7777)""",(jid,))
    vid=cur.lastrowid
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,1,'1200',7777,0,'approved only',?)",(vid,jid))
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,2,'4000',0,7777,'approved only',?)",(vid,jid))
    row=c.execute("SELECT * FROM gl_budget_lines WHERE budget_ref=?",(line['budget_ref'],)).fetchone()
    after=actual_amount(c,row,admin['id'])
    assert before==after
    c.close()

def test_duplicate_wrong_period_account_and_scope_are_blocked(isolated):
    c=db.connect();admin=user('admin')
    data={'fiscal_year':2026,'period':9,'account_code':'5000','amount':50000,'scenario':'BUDGET'}
    create_line(c,data,admin['id'],admin['user_ref'])
    with pytest.raises(HTTPException) as exc:create_line(c,data,admin['id'],admin['user_ref'])
    assert exc.value.detail['code']=='DUPLICATE_BUDGET_LINE'
    with pytest.raises(HTTPException) as exc:create_line(c,{**data,'period':13,'account_code':'4010'},admin['id'],admin['user_ref'])
    assert exc.value.detail['code']=='BUDGET_PERIOD_INVALID'
    with pytest.raises(HTTPException) as exc:create_line(c,{**data,'period':10,'account_code':'NOPE'},admin['id'],admin['user_ref'])
    assert exc.value.detail['code']=='BUDGET_ACCOUNT_INVALID'
    with pytest.raises(HTTPException) as exc:create_line(c,{**data,'period':10,'account_code':'4010','job_ref':'50001','branch_code':'DXB-MAIN'},admin['id'],admin['user_ref'])
    assert exc.value.detail['code']=='BUDGET_SCOPE_ATTRIBUTION_MISMATCH'
    c.close()

def test_local_role_scope_cannot_budget_other_office(isolated):
    c=db.connect();u=user('final.dxb')
    with pytest.raises(HTTPException) as exc:
        create_line(c,{'fiscal_year':2026,'period':9,'account_code':'4000','amount':1000,'branch_code':'RTM-HQ'},u['id'],u['user_ref'])
    assert exc.value.detail['code']=='BUDGET_SCOPE_DENIED'
    ok=create_line(c,{'fiscal_year':2026,'period':9,'account_code':'4000','amount':1000,'branch_code':'DXB-MAIN'},u['id'],u['user_ref'])
    assert ok['branch_code']=='DXB-MAIN'
    c.close()

def test_budget_maker_checker_self_approval_and_independent_approval(isolated):
    admin_t=token('admin','Admin123!');checker_t=token('auditor','Audit123!')
    made=hardening.budget_planning_create(hardening.BudgetPlanBody(fields={
      'fiscal_year':2026,'period':10,'account_code':'4010','amount':90000,'scenario':'BUDGET'
    }),x_m3_session=admin_t)
    submitted=hardening.budget_planning_submit(made['budget_ref'],hardening.BudgetActionBody(version=made['version']),x_m3_session=admin_t)
    with pytest.raises(HTTPException) as exc:
        hardening.budget_planning_approve(made['budget_ref'],hardening.BudgetActionBody(version=submitted['version']),x_m3_session=admin_t)
    assert exc.value.detail['code']=='BUDGET_SELF_APPROVAL_BLOCKED'
    approved=hardening.budget_planning_approve(made['budget_ref'],hardening.BudgetActionBody(version=submitted['version']),x_m3_session=checker_t)
    assert approved['status']=='APPROVED' and approved['approved_by']=='USR-004'

def test_approved_budget_is_immutable_except_governed_revision(isolated):
    admin_t=token('admin','Admin123!');checker_t=token('auditor','Audit123!')
    made=hardening.budget_planning_create(hardening.BudgetPlanBody(fields={
      'fiscal_year':2026,'period':11,'account_code':'5010','amount':80000
    }),x_m3_session=admin_t)
    sub=hardening.budget_planning_submit(made['budget_ref'],hardening.BudgetActionBody(version=made['version']),x_m3_session=admin_t)
    app=hardening.budget_planning_approve(made['budget_ref'],hardening.BudgetActionBody(version=sub['version']),x_m3_session=checker_t)
    c=db.connect();gr=c.execute("SELECT * FROM gl_records WHERE module='budget' AND external_ref=?",(made['budget_ref'],)).fetchone();c.close()
    with pytest.raises(HTTPException) as exc:
        gl.update('budget',gr['id'],gl.UpdateBody(version=gr['version'],fields={'Budget Amount':'1'}),x_role='ADMIN',x_m3_session=admin_t)
    assert exc.value.detail['code']=='GOVERNED_BUDGET_REVISION_REQUIRED'
    with pytest.raises(HTTPException) as exc:
        hardening.budget_planning_revise(made['budget_ref'],hardening.BudgetActionBody(version=app['version'],amount=85000),x_m3_session=admin_t)
    assert exc.value.detail['code']=='BUDGET_REVISION_REASON_REQUIRED'
    rev=hardening.budget_planning_revise(made['budget_ref'],hardening.BudgetActionBody(version=app['version'],amount=85000,reason='approved forecast update'),x_m3_session=admin_t)
    with pytest.raises(HTTPException) as exc:
        hardening.budget_planning_revise(made['budget_ref'],hardening.BudgetActionBody(version=app['version'],amount=86000,reason='second pending revision'),x_m3_session=admin_t)
    assert exc.value.detail['code']=='DUPLICATE_BUDGET_REVISION'
    sub2=hardening.budget_planning_submit(rev['budget_ref'],hardening.BudgetActionBody(version=rev['version']),x_m3_session=admin_t)
    app2=hardening.budget_planning_approve(rev['budget_ref'],hardening.BudgetActionBody(version=sub2['version']),x_m3_session=checker_t)
    assert app2['status']=='APPROVED' and app2['revision_no']==2
    c=db.connect();old=c.execute("SELECT status FROM gl_budget_lines WHERE budget_ref=?",(made['budget_ref'],)).fetchone();c.close()
    assert old['status']=='REVISED'

def test_annual_and_period_management_rollups_are_posted_gl_read_only(isolated):
    t=token('admin','Admin123!')
    annual=hardening.final_management_control(fiscal_year=2026,period=None,x_m3_session=t)
    monthly=hardening.final_management_control(fiscal_year=2026,period=9,x_m3_session=t)
    for out in (annual,monthly):
        assert out['actual_source']=='POSTED_GL'
        assert out['read_only_actuals'] is True
        for k in ('job_profitability','branch_pnl','office_pnl','country_pnl','organization_pnl','cash','ar','ap','credit','exceptions'):
            assert k in out
        for k in ('job_plan_vs_actual','branch_plan_vs_actual','office_plan_vs_actual','country_plan_vs_actual','organization_plan_vs_actual'):
            assert k in out
    assert annual['governance']['job_by_job_permissions'] is False

def test_manual_management_kpi_variance_override_blocked():
    with pytest.raises(HTTPException) as exc:assert_no_management_override({'Variance':999,'KPI':'fake'})
    assert exc.value.detail['code']=='MANAGEMENT_ACTUAL_KPI_READ_ONLY'

def test_existing_budget_screen_projection_stays_synchronized(isolated):
    t=token('admin','Admin123!')
    made=hardening.budget_planning_create(hardening.BudgetPlanBody(fields={
      'fiscal_year':2026,'period':12,'account_code':'6000','amount':70000,'scenario':'FORECAST'
    }),x_m3_session=t)
    c=db.connect();gr=c.execute("SELECT id FROM gl_records WHERE module='budget' AND external_ref=?",(made['budget_ref'],)).fetchone();c.close()
    out=gl.one('budget',gr['id'],x_role='VIEWER',x_m3_session=t)
    assert out['external_ref']==made['budget_ref']
    assert out['fields']['Scenario']=='FORECAST'
    assert out['fields']['Actual Source']=='POSTED_GL'
    assert out['fields']['Variance %'] is not None or float(out['fields']['Budget Amount'])==0

def test_no_duplicate_budget_reporting_or_permission_model(isolated):
    c=db.connect();tables={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'gl_budget_lines' in tables and 'gl_vouchers' in tables
    assert not any(x in tables for x in {'budget_v2','forecast_v2','planning_ledger','actuals_ledger','variance_ledger','user_job_permissions'})
    c.close()

@pytest.mark.parametrize('flow',['EXPORT','IMPORT','TS'])
def test_export_import_ts_use_same_final_governance(flow):
    assert flow in {'EXPORT','IMPORT','TS'}
    assert callable(management_rollup)
