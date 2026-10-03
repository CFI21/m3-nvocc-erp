import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
from app.admin import Login, login
import app.gl as gl


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'item6.db')
    monkeypatch.setenv('M3_ITEM6_FINANCIAL_GOVERNANCE_ENABLED', 'true')
    seed_run(True)
    masterdata_seed_run()
    admin_seed_run()
    return db.DB_PATH


def admin_token():
    return login(Login(username='admin', password='Admin123!', mfa_code='123456'))['session_token']


def make_gl_record(status='Draft', flow='EXPORT', source_ref='SRC-ITEM6'):
    c=db.connect()
    jid=c.execute("SELECT id FROM jobs ORDER BY id LIMIT 1").fetchone()['id']
    payload={'Voucher No.':'ITEM6-JV','Voucher Type':'JV','Date':'2026-09-23','Currency':'USD',
             'Debit Account':'1100','Credit Account':'4000','Amount':'100','Status':status,'Flow':flow}
    cur=c.execute("""INSERT INTO gl_records(module,external_ref,job_id,source_type,source_ref,status,version,payload_json,created_at,updated_at)
      VALUES('voucher',?,?,?,?,?,1,?,?,?)""",
      ('ITEM6-'+status+'-'+flow+'-'+source_ref,jid,'ITEM6',source_ref,status,json.dumps(payload),gl.now(),gl.now()))
    rid=cur.lastrowid
    v=c.execute("""INSERT INTO gl_vouchers(gl_record_id,voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,
      total_debit,total_credit,version,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)""",
      (rid,'JV-I6-'+str(rid),'JV','2026-09-23','USD',status,'ITEM6',source_ref,jid,100,100,gl.now(),'USR-MAKER',1,'USD',100,100)).lastrowid
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)",(v,1,'1100',100,0,'Item6',jid))
    c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)",(v,2,'4000',0,100,'Item6',jid))
    c.close()
    return rid,v


def test_posted_and_approved_records_are_immutable_and_crt_governed(isolated):
    token=admin_token()
    for status in ('Approved','Posted'):
        rid,_=make_gl_record(status=status,source_ref='IMM-'+status)
        with pytest.raises(HTTPException) as exc:
            gl.update('voucher',rid,gl.UpdateBody(version=1,fields={'Amount':'999'}),x_role='ADMIN',x_m3_session=token)
        assert exc.value.status_code==409
        assert exc.value.detail['code']=='CRT_REQUIRED_AFTER_APPROVAL'


def test_client_supplied_role_cannot_bypass_session_requirement(isolated):
    rid,_=make_gl_record(status='Draft',source_ref='NOSESSION')
    with pytest.raises(HTTPException) as exc:
        gl.update('voucher',rid,gl.UpdateBody(version=1,fields={'Amount':'101'}),x_role='ADMIN',x_m3_session=None)
    assert exc.value.status_code==401
    assert exc.value.detail['code']=='SESSION_REQUIRED_FOR_FINANCIAL_MUTATION'
    with pytest.raises(HTTPException) as exc:
        gl.action('voucher',rid,'post',gl.ActionBody(version=1),x_role='ADMIN',x_m3_session=None)
    assert exc.value.detail['code']=='SESSION_REQUIRED_FOR_GOVERNED_FINANCIAL_ACTION'


@pytest.mark.parametrize('flow,expected',[
    ('EXPORT',set()),
    ('IMPORT',set()),
    ('TS',{'TS_BRANCH_MANAGER','TS_FINANCE'}),
])
def test_export_import_ts_acceptance_matrix_open_period(flow,expected):
    assert gl._period_required_roles('OPEN',flow)==expected


def test_closed_period_requires_finance_manager_and_cfo(isolated):
    _,vid=make_gl_record(status='Approved',source_ref='CLOSED')
    c=db.connect()
    p=c.execute("SELECT * FROM gl_periods WHERE date('2026-09-23') BETWEEN date(start_date) AND date(end_date)").fetchone()
    c.execute("UPDATE gl_periods SET status='CLOSED' WHERE id=?",(p['id'],))
    p=c.execute("SELECT * FROM gl_periods WHERE id=?",(p['id'],)).fetchone()
    with pytest.raises(HTTPException) as exc:
        gl._ensure_period_governance(c,p,vid,'POST','EXPORT')
    assert exc.value.detail['code']=='CLOSED_PERIOD_APPROVAL_REQUIRED'
    assert set(exc.value.detail['missing_roles'])=={'FINANCE_MANAGER','CFO'}
    for role in ('FINANCE_MANAGER','CFO'):
        c.execute("""INSERT INTO gl_period_override_approvals
          (approval_ref,period_id,voucher_id,action,approval_role,approver_user_ref,reason,status,created_at)
          VALUES(?,?,?,?,?,?,?,'APPROVED',?)""",(role+'-REF',p['id'],vid,'POST',role,'USR-'+role,'Item6 approval',gl.now()))
    out=gl._ensure_period_governance(c,p,vid,'POST','EXPORT')
    c.close()
    assert out['period_mode']=='CLOSED_CONTROLLED'
    assert set(out['approvals'])=={'FINANCE_MANAGER','CFO'}


def test_tax_filed_locked_period_requires_cfo_and_external_advisor(isolated):
    _,vid=make_gl_record(status='Approved',source_ref='TAXFILED')
    c=db.connect()
    p=c.execute("SELECT * FROM gl_periods WHERE date('2026-09-23') BETWEEN date(start_date) AND date(end_date)").fetchone()
    c.execute("UPDATE gl_periods SET status='LOCKED' WHERE id=?",(p['id'],))
    p=c.execute("SELECT * FROM gl_periods WHERE id=?",(p['id'],)).fetchone()
    with pytest.raises(HTTPException) as exc:
        gl._ensure_period_governance(c,p,vid,'POST','IMPORT')
    assert exc.value.detail['code']=='TAX_FILED_PERIOD_APPROVAL_REQUIRED'
    assert set(exc.value.detail['missing_roles'])=={'CFO','EXTERNAL_ADVISOR'}
    c.close()


def test_ts_closed_period_combines_period_and_ts_authority(isolated):
    _,vid=make_gl_record(status='Approved',flow='TS',source_ref='TS-CLOSED')
    c=db.connect()
    p=c.execute("SELECT * FROM gl_periods WHERE date('2026-09-23') BETWEEN date(start_date) AND date(end_date)").fetchone()
    c.execute("UPDATE gl_periods SET status='CLOSED' WHERE id=?",(p['id'],))
    p=c.execute("SELECT * FROM gl_periods WHERE id=?",(p['id'],)).fetchone()
    with pytest.raises(HTTPException) as exc:
        gl._ensure_period_governance(c,p,vid,'POST','TS')
    assert set(exc.value.detail['missing_roles'])=={'FINANCE_MANAGER','CFO','TS_BRANCH_MANAGER','TS_FINANCE'}
    c.close()


def test_reversal_is_single_and_preserves_original_link(isolated,monkeypatch):
    token=admin_token()
    rid,vid=make_gl_record(status='Posted',source_ref='REV')
    monkeypatch.setattr(gl,'enforce_gl_action',lambda *a,**k:{'approved':True})
    out=gl.action('voucher',rid,'reverse',gl.ActionBody(version=1,reason='Correct coding'),x_role='ADMIN',x_m3_session=token)
    assert out['record']['status']=='Reversed'
    c=db.connect()
    revs=list(c.execute("SELECT * FROM gl_vouchers WHERE reversal_of=?",(vid,)))
    assert len(revs)==1
    assert revs[0]['source_type']=='REVERSAL'
    assert revs[0]['source_ref']=='JV-I6-'+str(rid)
    c.close()


def test_duplicate_source_posting_is_blocked(isolated,monkeypatch):
    token=admin_token()
    _,first_vid=make_gl_record(status='Posted',source_ref='DUP-SOURCE')
    rid2,vid2=make_gl_record(status='Approved',source_ref='DUP-SOURCE')
    c=db.connect()
    c.execute("INSERT INTO gl_approval_events(voucher_id,level,actor_role,decision,ts,comment) VALUES(?,1,'USR-CHECKER','APPROVED',?,?)",(vid2,gl.now(),'Item6'))
    c.close()
    monkeypatch.setattr(gl,'enforce_gl_action',lambda *a,**k:{'approved':True})
    with pytest.raises(HTTPException) as exc:
        gl.action('voucher',rid2,'post',gl.ActionBody(version=1,reason='Post once'),x_role='ADMIN',x_m3_session=token)
    assert exc.value.status_code==409
    assert exc.value.detail['code']=='DUPLICATE_SOURCE_POSTING'


def test_audit_context_includes_user_scope(isolated):
    ctx=None
    c=db.connect()
    try:
        ctx=gl._session_context(c,admin_token())
    finally:
        c.close()
    assert ctx['user_ref']
    assert ctx['office_code']
    assert isinstance(ctx['roles'],list)
    assert 'SUPER_ADMIN' in ctx['roles']
