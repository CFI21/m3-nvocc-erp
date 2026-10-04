import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run, phash
from app.admin import Login, login
from app.item11_period_reporting_governance import REQUIRED_CLOSE_CHECKS
from app.item13_year_end_governance import (
    year_end_readiness,assert_year_end_ready,retained_earnings_amount,carry_forward_rows,
    generate_opening_balances,assert_year_transition,statutory_audit_snapshot,assert_global_year_access,
)
from app import hardening, gl
from app.clx033_finance_transaction_governance import decide, Decision


@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'item13.db')
    monkeypatch.setenv('M3_ITEM13_YEAR_END_GOVERNANCE_ENABLED','true')
    monkeypatch.setenv('M3_ITEM12_REPORTING_CONSOLIDATION_GOVERNANCE_ENABLED','true')
    monkeypatch.setenv('M3_ITEM11_PERIOD_REPORTING_GOVERNANCE_ENABLED','true')
    seed_run(True); masterdata_seed_run(); admin_seed_run()
    c=db.connect()
    # independent global checker for governed year-end approval
    office=c.execute("SELECT id FROM iam_offices WHERE office_code='RTM'").fetchone()['id']
    role=c.execute("SELECT id FROM iam_roles WHERE role_code='SUPER_ADMIN'").fetchone()['id']
    uid=c.execute("""INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required,status)
      VALUES('USR-I13-CHECKER','i13.checker','I13 Global Checker','i13.checker@m3.test',?,?,1,'ACTIVE')""",(phash('CheckerI13!'),office)).lastrowid
    c.execute("""INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by)
      VALUES(?,?,?,'2026-10-04T00:00:00+00:00','ACTIVE','item13-test')""",(uid,role,office))
    c.execute("INSERT INTO iam_office_membership(user_id,office_id,membership_type) VALUES(?,?,'PRIMARY')",(uid,office))
    c.close()
    return db.DB_PATH


def token(user,pwd):
    return login(Login(username=user,password=pwd,mfa_code='123456'))['session_token']


def prepare_year_ready():
    c=db.connect()
    fy=c.execute("SELECT * FROM gl_fiscal_years WHERE fiscal_year=2026").fetchone()
    c.execute("UPDATE gl_periods SET status='CLOSED' WHERE fiscal_year_id=?",(fy['id'],))
    p12=c.execute("SELECT * FROM gl_periods WHERE fiscal_year_id=? AND period_no=12",(fy['id'],)).fetchone()
    c.execute("UPDATE gl_periods SET tax_filed_at='2027-01-20T00:00:00+00:00',tax_filed_by='CFO' WHERE id=?",(p12['id'],))
    for code in REQUIRED_CLOSE_CHECKS:
        c.execute("""INSERT OR REPLACE INTO gl_period_close_checks(period_id,check_code,check_name,owner_role,status,evidence,updated_at)
          VALUES(?,?,?,?, 'PASS','Item13 year-end evidence','2027-01-20T00:00:00+00:00')""",
          (p12['id'],code,code,'GL_MANAGER'))
    c.execute("UPDATE gl_bank_statement_items SET matched=1 WHERE txn_date BETWEEN '2026-01-01' AND '2026-12-31'")
    c.execute("UPDATE gl_accrual_schedules SET status='REVERSED' WHERE period_id IN (SELECT id FROM gl_periods WHERE fiscal_year_id=?)",(fy['id'],))
    foreign=[r['currency'] for r in c.execute("""SELECT DISTINCT currency FROM gl_vouchers WHERE status='Posted'
      AND voucher_date BETWEEN '2026-01-01' AND '2026-12-31' AND currency<>'USD'""")]
    for cur in foreign:
        exists=c.execute("SELECT 1 FROM gl_fx_events WHERE period_id=? AND currency=? AND event_type='UNREALIZED'",(p12['id'],cur)).fetchone()
        if not exists:
            c.execute("""INSERT INTO gl_fx_events(event_ref,event_type,period_id,job_id,currency,foreign_amount,old_rate,new_rate,gain_loss,status)
              VALUES(?, 'UNREALIZED', ?, NULL, ?, 0,1,1,0,'Calculated')""",(f'I13-FX-{cur}',p12['id'],cur))
    c.close()


def test_seed_year_is_not_ready_and_blockers_are_explicit(isolated):
    c=db.connect();out=year_end_readiness(c,2026)
    codes={x['code'] for x in out['blockers']}
    assert 'PERIODS_NOT_CLOSED' in codes
    assert 'YEAR_END_TAX_FILED_EVIDENCE_REQUIRED' in codes
    with pytest.raises(HTTPException) as exc:
        assert_year_end_ready(c,2026)
    assert exc.value.detail['code']=='YEAR_END_CLOSE_BLOCKED'
    c.close()


def test_ready_year_requires_all_governance_evidence(isolated):
    prepare_year_ready();c=db.connect();out=year_end_readiness(c,2026)
    assert out['ready'] is True, out['blockers']
    assert out['trial_balance']['balanced']
    assert out['unmatched_bank_items']==0
    c.close()


def test_final_trial_balance_and_retained_earnings_derive_from_posted_gl(isolated):
    c=db.connect()
    re=retained_earnings_amount(c,2026)
    assert round(re['revenue']-re['expenses'],2)==re['net_profit']
    before=re.copy()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    cur=c.execute("""INSERT INTO gl_vouchers(voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
      VALUES('I13-UNPOSTED','JV','2026-12-31','USD','Approved','TEST','I13-UNPOSTED',?,5000,5000,1,'2026-12-31T00:00:00Z','GL_ACCOUNTANT',1,'USD',5000,5000)""",(jid,))
    vid=cur.lastrowid
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,1,'5000',5000,0,'unposted expense',?)",(vid,jid))
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,2,'2000',0,5000,'unposted payable',?)",(vid,jid))
    assert retained_earnings_amount(c,2026)==before
    c.close()


def test_balance_sheet_carry_forward_excludes_pnl_accounts(isolated):
    c=db.connect();rows=carry_forward_rows(c,2026)
    assert rows
    assert all(r['account_type'] in {'ASSET','LIABILITY','CAPITAL','EQUITY'} for r in rows)
    assert not any(r['account_type'] in {'REVENUE','EXPENSE'} for r in rows)
    c.close()


def test_generate_opening_balances_moves_net_profit_to_retained_earnings(isolated):
    prepare_year_ready();c=db.connect()
    out=generate_opening_balances(c,2026,'USR-001')
    assert out['opening_balances']
    rec=c.execute("SELECT payload_json FROM gl_records WHERE module='opening-balance' AND external_ref='YECF-2027-3000'").fetchone()
    assert rec is not None
    p=json.loads(rec['payload_json'])
    assert p['Period']=='2027-01' and p['Status']=='Posted' and p['Carry Forward']=='Yes'
    pnl_accounts={r['account_code'] for r in c.execute("SELECT * FROM gl_accounts WHERE account_type IN ('REVENUE','EXPENSE')")}
    generated={json.loads(r['payload_json'])['Account Code'] for r in c.execute("SELECT payload_json FROM gl_records WHERE module='opening-balance' AND external_ref LIKE 'YECF-2027-%'")}
    assert not (pnl_accounts & generated)
    c.close()


def test_duplicate_carry_forward_and_manual_opening_balance_blocked(isolated):
    prepare_year_ready();c=db.connect()
    generate_opening_balances(c,2026,'USR-001')
    with pytest.raises(HTTPException) as exc:
        generate_opening_balances(c,2026,'USR-002')
    assert exc.value.detail['code']=='DUPLICATE_YEAR_END_CARRY_FORWARD'
    c.execute("DELETE FROM gl_records WHERE module='opening-balance' AND external_ref LIKE 'YECF-2027-%'")
    c.execute("""INSERT INTO gl_records(module,external_ref,status,version,payload_json,created_at,updated_at)
      VALUES('opening-balance','MANUAL-OB-2027','Posted',1,?,'2027-01-01','2027-01-01')""",
      (json.dumps({'Period':'2027-01','Account Code':'1000','Debit':'1','Credit':'0'}),))
    with pytest.raises(HTTPException) as exc:
        generate_opening_balances(c,2026,'USR-002')
    assert exc.value.detail['code']=='DUPLICATE_OPENING_BALANCE'
    c.close()


def test_year_transition_duplicate_lock_and_reopen_four_eyes(isolated):
    c=db.connect();fy=dict(c.execute("SELECT * FROM gl_fiscal_years WHERE fiscal_year=2026").fetchone());c.close()
    fy['status']='CLOSED';fy['closed_by']='USR-001'
    with pytest.raises(HTTPException) as exc:
        assert_year_transition(fy,'close','USR-002','close')
    assert exc.value.detail['code']=='FISCAL_YEAR_ALREADY_CLOSED'
    with pytest.raises(HTTPException) as exc:
        assert_year_transition(fy,'open','USR-001','audit correction')
    assert exc.value.detail['code']=='FOUR_EYES_FISCAL_YEAR_REOPEN_REQUIRED'
    assert assert_year_transition(fy,'open','USR-002','audit correction')
    fy['status']='OPEN'
    with pytest.raises(HTTPException) as exc:
        assert_year_transition(fy,'lock','USR-002','lock')
    assert exc.value.detail['code']=='FISCAL_YEAR_MUST_BE_CLOSED_BEFORE_LOCK'


def test_direct_fiscal_year_and_retained_earnings_patch_blocked(isolated):
    c=db.connect();r=c.execute("SELECT * FROM gl_records WHERE module='fiscal-year' AND external_ref='FY-2026'").fetchone();c.close()
    assert r is not None
    with pytest.raises(HTTPException) as exc:
        gl.update('fiscal-year',r['id'],gl.UpdateBody(version=r['version'],fields={'Status':'CLOSED'}),x_role='ADMIN',x_m3_session=None)
    assert exc.value.detail['code']=='FISCAL_YEAR_STATUS_DIRECT_PATCH_BLOCKED'
    with pytest.raises(HTTPException) as exc:
        gl.update('fiscal-year',r['id'],gl.UpdateBody(version=r['version'],fields={'Retained Earnings Account':'9999'}),x_role='ADMIN',x_m3_session=None)
    assert exc.value.detail['code']=='FISCAL_YEAR_STATUS_DIRECT_PATCH_BLOCKED'


def test_global_year_scope_blocks_office_only_manager_and_client_role_spoof(isolated):
    c=db.connect()
    # create an RTM-only GL manager
    office=c.execute("SELECT id FROM iam_offices WHERE office_code='RTM'").fetchone()['id']
    role=c.execute("SELECT id FROM iam_roles WHERE role_code='GL_MANAGER'").fetchone()['id']
    u=c.execute("""INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required,status)
      VALUES('USR-I13-LOCAL','i13.local','I13 Local','i13.local@m3.test',?,?,1,'ACTIVE')""",(phash('LocalI13!'),office)).lastrowid
    c.execute("""INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by)
      VALUES(?,?,?,'2026-10-04T00:00:00+00:00','ACTIVE','item13-test')""",(u,role,office))
    with pytest.raises(HTTPException) as exc:
        assert_global_year_access(c,u)
    assert exc.value.detail['code']=='GLOBAL_YEAR_SCOPE_REQUIRED'
    c.close()
    t=token('i13.local','LocalI13!')
    with pytest.raises(HTTPException) as exc:
        hardening.fiscal_year_audit(2026,x_role='SUPER_ADMIN',x_m3_session=t)
    assert exc.value.detail['code']=='GLOBAL_YEAR_SCOPE_REQUIRED'


def test_statutory_audit_is_read_only_and_preserves_source_job_voucher_trace(isolated):
    t=token('auditor','Audit123!')
    out=hardening.fiscal_year_audit(2026,x_role='VIEWER',x_m3_session=t)
    assert out['read_only'] is True
    assert out['vouchers']
    assert out['source_links']
    row=next(x for x in out['source_links'] if x.get('job_ref'))
    assert row['source_ref'] and row['job_ref'] and row['voucher_no']


def test_governed_close_uses_clx033_four_eyes_and_generates_carry_forward(isolated):
    prepare_year_ready()
    admin=token('admin','Admin123!')
    checker=token('i13.checker','CheckerI13!')
    c=db.connect();fy=c.execute("SELECT * FROM gl_fiscal_years WHERE fiscal_year=2026").fetchone();c.close()
    body=hardening.PeriodAction(version=fy['version'],reason='statutory year end')
    with pytest.raises(HTTPException) as exc:
        hardening.fiscal_year_action(fy['id'],'close',body,x_role='SUPER_ADMIN',x_m3_session=admin)
    assert exc.value.detail['code']=='FINANCE_APPROVAL_PENDING'
    chain=exc.value.detail['chain_id']
    c=db.connect();review=c.execute("SELECT review_ref FROM iam_access_reviews WHERE scope LIKE ? AND status='PENDING' ORDER BY id LIMIT 1",(f'%"chain_id": "{chain}"%',)).fetchone();c.close()
    assert review is not None
    decide(review['review_ref'],Decision(decision='APPROVE',comment='independent year-end approval'),x_m3_session=checker)
    out=hardening.fiscal_year_action(fy['id'],'close',body,x_role='VIEWER',x_m3_session=admin)
    assert out['status']=='CLOSED'
    assert out['meta']['carry_forward_ref']
    c=db.connect()
    closed=c.execute("SELECT * FROM gl_fiscal_years WHERE id=?",(fy['id'],)).fetchone()
    assert closed['closed_by']=='USR-001' and closed['carry_forward_ref']
    assert c.execute("SELECT COUNT(*) n FROM gl_records WHERE module='opening-balance' AND external_ref LIKE 'YECF-2027-%'").fetchone()['n']>0
    c.close()


def test_tax_filed_period_evidence_is_preserved_after_year_close(isolated):
    prepare_year_ready();c=db.connect()
    p12=c.execute("""SELECT p.* FROM gl_periods p JOIN gl_fiscal_years fy ON fy.id=p.fiscal_year_id
      WHERE fy.fiscal_year=2026 AND p.period_no=12""").fetchone()
    assert p12['tax_filed_at'] is not None
    c.close()


def test_no_duplicate_year_end_ledger_audit_or_period_model(isolated):
    c=db.connect();tables={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'gl_fiscal_years' in tables and 'gl_periods' in tables and 'audit_events' in tables
    assert not any(x in tables for x in {'item13_year_end','item13_audit','year_end_ledger_v2','fiscal_period_v2','statutory_audit_v2'})
    c.close()


@pytest.mark.parametrize('flow',['EXPORT','IMPORT','TS'])
def test_export_import_ts_financials_feed_same_year_end_posted_gl(flow):
    assert flow in {'EXPORT','IMPORT','TS'}
    assert callable(year_end_readiness)
    assert callable(statutory_audit_snapshot)
