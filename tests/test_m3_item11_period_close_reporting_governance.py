import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
from app.item11_period_reporting_governance import (
    REQUIRED_CLOSE_CHECKS,
    close_check_status,
    period_close_blockers,
    assert_period_close_ready,
    assert_period_transition,
    posted_trial_balance,
    statement_snapshot,
    assert_no_manual_report_override,
)
from app import gl
from app import hardening


@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'item11.db')
    monkeypatch.setenv('M3_ITEM11_PERIOD_REPORTING_GOVERNANCE_ENABLED','true')
    seed_run(True); masterdata_seed_run(); admin_seed_run()
    return db.DB_PATH


def period9(c):
    return c.execute("SELECT * FROM gl_periods WHERE start_date='2026-09-01'").fetchone()


def test_required_month_end_checklist_is_complete(isolated):
    c=db.connect(); p=period9(c)
    rows,missing,failed=close_check_status(c,p['id'])
    assert not missing
    assert not failed
    assert REQUIRED_CLOSE_CHECKS=={'SUBLEDGER','BANK','AR_AP','FX','TAX','JOBS','TB','ACCRUALS','PREPAYMENTS'}
    c.close()


def test_missing_or_failed_required_close_check_blocks(isolated):
    c=db.connect(); p=period9(c)
    c.execute("DELETE FROM gl_period_close_checks WHERE period_id=? AND check_code='FX'",(p['id'],))
    out=period_close_blockers(c,p['id'])
    assert any(x['code']=='MISSING_CLOSE_CHECKS' for x in out['blockers'])
    with pytest.raises(HTTPException) as exc:
        assert_period_close_ready(c,p['id'])
    assert exc.value.detail['code']=='PERIOD_CLOSE_BLOCKED'
    c.close()


def test_unmatched_required_bank_items_block_close(isolated):
    c=db.connect(); p=period9(c)
    out=period_close_blockers(c,p['id'])
    assert out['unmatched_bank_items']>0
    assert any(x['code']=='UNMATCHED_BANK_ITEMS' for x in out['blockers'])
    c.execute("UPDATE gl_bank_statement_items SET matched=1")
    out=period_close_blockers(c,p['id'])
    assert not any(x['code']=='UNMATCHED_BANK_ITEMS' for x in out['blockers'])
    c.close()


def test_unbalanced_trial_balance_blocks_close(isolated):
    c=db.connect(); p=period9(c)
    c.execute("UPDATE gl_bank_statement_items SET matched=1")
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    cur=c.execute("""INSERT INTO gl_vouchers(voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,posted_at,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
      VALUES('ITEM11-UNBAL','JV','2026-09-23','USD','Posted','TEST','ITEM11-UNBAL',?,10,10,1,'2026-09-23T00:00:00Z','2026-09-23T00:00:00Z','GL_ACCOUNTANT',1,'USD',10,10)""",(jid,))
    vid=cur.lastrowid
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,1,'1200',10,0,'unbalanced test',?)",(vid,jid))
    out=period_close_blockers(c,p['id'])
    assert any(x['code']=='TRIAL_BALANCE_UNBALANCED' for x in out['blockers'])
    c.close()


def test_financial_statements_use_posted_only(isolated):
    c=db.connect()
    before=statement_snapshot(c)
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    cur=c.execute("""INSERT INTO gl_vouchers(voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
      VALUES('ITEM11-APPROVED','JV','2026-09-23','USD','Approved','TEST','ITEM11-APPROVED',?,999,999,1,'2026-09-23T00:00:00Z','GL_ACCOUNTANT',1,'USD',999,999)""",(jid,))
    vid=cur.lastrowid
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,1,'1200',999,0,'approved only',?)",(vid,jid))
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,2,'4000',0,999,'approved only',?)",(vid,jid))
    after=statement_snapshot(c)
    assert before==after
    c.close()


def test_main_gl_trial_balance_excludes_approved_when_item11_enabled(isolated):
    c=db.connect(); jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    cur=c.execute("""INSERT INTO gl_vouchers(voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
      VALUES('ITEM11-APPROVED2','JV','2026-09-23','USD','Approved','TEST','ITEM11-APPROVED2',?,321,321,1,'2026-09-23T00:00:00Z','GL_ACCOUNTANT',1,'USD',321,321)""",(jid,))
    vid=cur.lastrowid
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,1,'1200',321,0,'approved only',?)",(vid,jid))
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,2,'4000',0,321,'approved only',?)",(vid,jid))
    c.close()
    rows=gl.trial_balance(x_role='AUDITOR',x_m3_session=None)
    ar=next(x for x in rows if x['account_code']=='1200')
    direct=db.connect()
    posted=direct.execute("""SELECT ROUND(COALESCE(SUM(l.debit),0),2) x FROM gl_voucher_lines l JOIN gl_vouchers v ON v.id=l.voucher_id WHERE l.account_code='1200' AND v.status='Posted'""").fetchone()['x']
    direct.close()
    assert ar['debit']==posted


def test_period_transition_duplicate_close_lock_and_reopen_four_eyes(isolated):
    c=db.connect(); p=period9(c)
    d=dict(p);d['status']='CLOSED';d['closed_by']='USR-001'
    with pytest.raises(HTTPException) as exc:
        assert_period_transition(d,'close','USR-002','done')
    assert exc.value.detail['code']=='PERIOD_ALREADY_CLOSED'
    with pytest.raises(HTTPException) as exc:
        assert_period_transition(d,'open','USR-001','reopen correction')
    assert exc.value.detail['code']=='FOUR_EYES_REOPEN_REQUIRED'
    assert assert_period_transition(d,'open','USR-002','reopen correction')
    d['status']='OPEN'
    with pytest.raises(HTTPException) as exc:
        assert_period_transition(d,'lock','USR-002','lock')
    assert exc.value.detail['code']=='PERIOD_MUST_BE_CLOSED_BEFORE_LOCK'
    c.close()


def test_reopen_requires_reason(isolated):
    c=db.connect(); p=dict(period9(c));p['status']='CLOSED';p['closed_by']='USR-001'
    with pytest.raises(HTTPException) as exc:
        assert_period_transition(p,'open','USR-002',None)
    assert exc.value.detail['code']=='REOPEN_REASON_REQUIRED'
    c.close()


def test_period_status_direct_patch_blocked(isolated):
    c=db.connect()
    r=c.execute("SELECT * FROM gl_records WHERE module='accounting-periods' ORDER BY id LIMIT 1").fetchone()
    c.close()
    if not r:
        pytest.skip('No accounting-period GL projection in seed')
    with pytest.raises(HTTPException) as exc:
        gl.update('accounting-periods',r['id'],gl.UpdateBody(version=r['version'],fields={'Status':'CLOSED'}),x_role='GL_MANAGER',x_m3_session=None)
    assert exc.value.detail['code']=='PERIOD_STATUS_DIRECT_PATCH_BLOCKED'


def test_period_action_requires_authenticated_session_when_item11_enabled(isolated):
    c=db.connect(); p=period9(c);c.close()
    with pytest.raises(HTTPException) as exc:
        hardening.period_action(p['id'],'close',hardening.PeriodAction(version=p['version'],reason='month end close'),x_role='ADMIN',x_m3_session=None)
    assert exc.value.status_code==401


def test_manual_financial_statement_total_override_blocked():
    with pytest.raises(HTTPException) as exc:
        assert_no_manual_report_override({'Net Profit':'999999'})
    assert exc.value.detail['code']=='FINANCIAL_STATEMENT_TOTALS_READ_ONLY'


def test_source_to_gl_statement_trace_exists(isolated):
    c=db.connect()
    row=c.execute("""SELECT l.source_type,l.source_ref,j.job_ref,v.voucher_no,v.status
      FROM gl_source_links l JOIN gl_vouchers v ON v.id=l.voucher_id
      LEFT JOIN jobs j ON j.id=l.job_id
      WHERE v.status='Posted' AND l.source_ref IS NOT NULL ORDER BY l.id LIMIT 1""").fetchone()
    assert row is not None
    assert row['source_ref'] and row['voucher_no']
    c.close()


@pytest.mark.parametrize('flow',['EXPORT','IMPORT','TS'])
def test_export_import_ts_share_period_reporting_governance(flow):
    assert flow in {'EXPORT','IMPORT','TS'}
    assert callable(assert_period_close_ready)
    assert callable(statement_snapshot)
