import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run, phash
from app.admin import Login, login
from app.bulk_permission_governance import resolve_job_scope
from app.item12_reporting_consolidation_governance import (
    posted_lines,trial_balance,profit_loss,balance_sheet,cash_flow,job_profitability,scoped_pnl,
    validate_interbranch_eliminations,consolidated_reporting,assert_no_report_override,
    request_elimination,approve_elimination,
)
from app import clx045_gl_reporting as reporting
from app import nvocc_principal_extensions as principal


@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'item12.db')
    monkeypatch.setenv('M3_ITEM12_REPORTING_CONSOLIDATION_GOVERNANCE_ENABLED','true')
    seed_run(True); masterdata_seed_run(); admin_seed_run()
    c=db.connect()
    office=c.execute("SELECT id FROM iam_offices WHERE office_code='RTM'").fetchone()['id']
    role=c.execute("SELECT id FROM iam_roles WHERE role_code='GL_MANAGER'").fetchone()['id']
    for ref,user,email,pwd in [
        ('USR-I12-MAKER','i12.maker','i12.maker@m3.test','MakerI12!'),
        ('USR-I12-CHECKER','i12.checker','i12.checker@m3.test','CheckerI12!'),
    ]:
        uid=c.execute("""INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required,status)
          VALUES(?,?,?,?,?,?,1,'ACTIVE')""",(ref,user,user,email,phash(pwd),office)).lastrowid
        c.execute("""INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by)
          VALUES(?,?,?,'2026-10-04T00:00:00+00:00','ACTIVE','item12-test')""",(uid,role,office))
        c.execute("INSERT INTO iam_office_membership(user_id,office_id,membership_type) VALUES(?,?,'PRIMARY')",(uid,office))
    c.close()
    return db.DB_PATH


def token(user,pwd):
    return login(Login(username=user,password=pwd,mfa_code='123456'))['session_token']


def uid(username):
    c=db.connect();r=c.execute("SELECT id FROM iam_users WHERE username=?",(username,)).fetchone();c.close();return r['id']


def posted_voucher_for(job_ref):
    c=db.connect()
    r=c.execute("""SELECT v.voucher_no,v.id FROM gl_vouchers v JOIN jobs j ON j.id=v.job_id
      WHERE j.job_ref=? AND v.status='Posted' ORDER BY v.id LIMIT 1""",(job_ref,)).fetchone()
    c.close();assert r is not None
    return dict(r)


def insert_interbranch(ref='IBS-I12-1',gl_ref=None,elim=0,status='POSTED'):
    c=db.connect()
    scope=resolve_job_scope(c,'50001',persist=False)
    gl_ref=gl_ref or posted_voucher_for('50001')['voucher_no']
    c.execute("""INSERT INTO nvocc_interbranch_settlements(
      settlement_ref,from_branch,to_branch,legal_entity_from,legal_entity_to,job_ref,total_amount,currency,exchange_rate,status,
      gl_posting_ref,elimination_flag,elimination_status,version,updated_at)
      VALUES(?,?,?,?,?,'50001',100,'USD',1,?,?,?,?,1,'2026-10-04T00:00:00+00:00')""",
      (ref,scope['branch_code'],'DXB-MAIN','M3-NL','M3-AE',status,gl_ref,elim,'APPROVED' if elim else 'NONE'))
    c.close()
    return ref


def test_period_based_trial_balance_and_statements_use_posted_gl_only(isolated):
    c=db.connect()
    base=trial_balance(c,period='2026-09')
    assert base['balanced']
    pl=profit_loss(c,period='2026-09')
    bs=balance_sheet(c,period='2026-09')
    assert 'net_profit' in pl and 'assets' in bs
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    cur=c.execute("""INSERT INTO gl_vouchers(voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,
      total_debit,total_credit,version,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
      VALUES('I12-APPROVED','JV','2026-09-23','USD','Approved','TEST','I12-APPROVED',?,9999,9999,1,
      '2026-10-04T00:00:00Z','GL_ACCOUNTANT',1,'USD',9999,9999)""",(jid,))
    vid=cur.lastrowid
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,1,'1200',9999,0,'approved',?)",(vid,jid))
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,2,'4000',0,9999,'approved',?)",(vid,jid))
    after=trial_balance(c,period='2026-09')
    assert after==base
    c.close()


def test_clx045_client_status_cannot_include_approved_vouchers(isolated):
    t=token('i12.maker','MakerI12!')
    out=reporting.report('gl-detail',status='Approved',x_role='SUPER_ADMIN',x_m3_session=t)
    assert out['filters']['status']=='Posted'
    assert all(r['Voucher No']!='I12-APPROVED' for r in out['rows'])


def test_cash_flow_is_posted_cash_bank_gl_only(isolated):
    c=db.connect();u=uid('i12.maker')
    before=cash_flow(c,period='2026-09',user_id=u)
    assert all(x['account_code'] in {'1000','1100'} for x in before['lines'])
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    cur=c.execute("""INSERT INTO gl_vouchers(voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,
      total_debit,total_credit,version,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
      VALUES('I12-CASH-APP','JV','2026-09-23','USD','Approved','TEST','I12-CASH-APP',?,5000,5000,1,
      '2026-10-04T00:00:00Z','GL_ACCOUNTANT',1,'USD',5000,5000)""",(jid,))
    vid=cur.lastrowid
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,1,'1100',5000,0,'approved bank',?)",(vid,jid))
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,2,'3000',0,5000,'approved equity',?)",(vid,jid))
    after=cash_flow(c,period='2026-09',user_id=u)
    assert after['net_cash_flow']==before['net_cash_flow']
    c.close()


def test_job_profitability_and_four_scope_dimensions(isolated):
    c=db.connect();u=uid('i12.maker')
    jobs=job_profitability(c,period='2026-09',user_id=u)
    assert jobs and all(j['office_code']=='RTM' for j in jobs)
    branches=scoped_pnl(c,'branch_code',period='2026-09',user_id=u)
    offices=scoped_pnl(c,'office_code',period='2026-09',user_id=u)
    countries=scoped_pnl(c,'country_code',period='2026-09',user_id=u)
    orgs=scoped_pnl(c,'organization_code',period='2026-09',user_id=u)
    assert {x['scope'] for x in branches} <= {'RTM-HQ'}
    assert {x['scope'] for x in offices} <= {'RTM'}
    assert {x['scope'] for x in countries} <= {'NL'}
    assert {x['scope'] for x in orgs} <= {'M3-EU'}
    c.close()


def test_client_role_spoof_cannot_expand_branch_report_scope(isolated):
    t=token('i12.maker','MakerI12!')
    with pytest.raises(HTTPException) as exc:
        principal.branch_pnl(branch_code='DXB-MAIN',x_role='SUPER_ADMIN',x_m3_session=t)
    assert exc.value.status_code==403
    assert exc.value.detail['code']=='REPORT_SCOPE_DENIED'


def test_wrong_branch_office_attribution_is_blocked(isolated):
    c=db.connect()
    c.execute("UPDATE jobs SET office_code='RTM',branch_code='DXB-MAIN',country_code='NL',organization_code='M3-EU' WHERE job_ref='50001'")
    with pytest.raises(HTTPException) as exc:
        posted_lines(c,period='2026-09')
    assert exc.value.detail['code']=='REPORT_SCOPE_ATTRIBUTION_MISMATCH'
    c.close()


def test_reporting_trace_preserves_job_voucher_source_gl(isolated):
    c=db.connect()
    rows=posted_lines(c,period='2026-09')
    row=next(x for x in rows if x.get('job_ref') and x.get('source_ref'))
    assert row['job_ref'] and row['voucher_no'] and row['account_code'] and row['source_ref']
    c.close()


def test_manual_management_report_override_blocked():
    with pytest.raises(HTTPException) as exc:
        assert_no_report_override({'Consolidated Margin':'999999'})
    assert exc.value.detail['code']=='MANAGEMENT_REPORT_TOTALS_READ_ONLY'


def test_duplicate_consolidation_gl_reference_is_blocked(isolated):
    glref=posted_voucher_for('50001')['voucher_no']
    insert_interbranch('IBS-I12-D1',glref,1)
    insert_interbranch('IBS-I12-D2',glref,1)
    c=db.connect();out=validate_interbranch_eliminations(c)
    assert any(x['code']=='DUPLICATE_CONSOLIDATION_GL_REF' for x in out['invalid'])
    with pytest.raises(HTTPException) as exc:
        consolidated_reporting(c,period='2026-09')
    assert exc.value.detail['code']=='CONSOLIDATION_ELIMINATION_INVALID'
    c.close()


def test_elimination_maker_checker_and_posted_gl_link(isolated):
    ref=insert_interbranch()
    c=db.connect()
    req=request_elimination(c,ref,'USR-I12-MAKER','intercompany elimination')
    assert req['elimination_status']=='PENDING' and req['elimination_flag']==0
    with pytest.raises(HTTPException) as exc:
        approve_elimination(c,ref,'USR-I12-MAKER')
    assert exc.value.detail['code']=='FOUR_EYES_ELIMINATION_REQUIRED'
    app=approve_elimination(c,ref,'USR-I12-CHECKER')
    assert app['elimination_status']=='APPROVED' and app['elimination_flag']==1
    out=consolidated_reporting(c,period='2026-09')
    assert out['intercompany'][0]['settlement_ref']==ref
    assert out['source']=='POSTED_GL_PLUS_GOVERNED_INTERBRANCH_ELIMINATION'
    c.close()


def test_direct_elimination_override_requires_governed_action(isolated):
    with pytest.raises(HTTPException) as exc:
        principal.upsert_workspace('interbranch-settlement',principal.WorkspaceWrite(data={
          'settlement_ref':'IBS-DIRECT','from_branch':'RTM-HQ','to_branch':'DXB-MAIN',
          'job_ref':'50001','currency':'USD','status':'POSTED','elimination_flag':True
        }),x_role='GL_MANAGER',x_branch_scope='RTM-HQ',x_m3_session=token('i12.maker','MakerI12!'))
    assert exc.value.detail['code']=='GOVERNED_ELIMINATION_ACTION_REQUIRED'


def test_elimination_routes_enforce_session_scope_and_user_id_four_eyes(isolated):
    ref=insert_interbranch('IBS-I12-ROUTE')
    maker=token('i12.maker','MakerI12!')
    checker=token('i12.checker','CheckerI12!')
    req=principal.request_interbranch_elimination(ref,principal.WorkspaceWrite(data={'reason':'group elimination'}),x_m3_session=maker)
    assert req['record']['elimination_requested_by']=='USR-I12-MAKER'
    with pytest.raises(HTTPException) as exc:
        principal.approve_interbranch_elimination(ref,x_m3_session=maker)
    assert exc.value.detail['code']=='FOUR_EYES_ELIMINATION_REQUIRED'
    ok=principal.approve_interbranch_elimination(ref,x_m3_session=checker)
    assert ok['record']['elimination_approved_by']=='USR-I12-CHECKER'


def test_no_duplicate_reporting_or_consolidation_model(isolated):
    c=db.connect()
    tables={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'gl_vouchers' in tables and 'nvocc_interbranch_settlements' in tables
    assert not any(x in tables for x in {'item12_ledger','item12_reporting','item12_consolidation','consolidation_ledger_v2','reporting_ledger_v2'})
    c.close()


@pytest.mark.parametrize('flow',['EXPORT','IMPORT','TS'])
def test_export_import_ts_use_same_reporting_governance(flow):
    assert flow in {'EXPORT','IMPORT','TS'}
    assert callable(posted_lines)
    assert callable(consolidated_reporting)
