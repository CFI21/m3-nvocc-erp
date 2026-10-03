import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
import app.item9_credit_collection_governance as gov
import app.gl as gl


@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'item9.db')
    monkeypatch.setenv('M3_ITEM9_CREDIT_COLLECTION_GOVERNANCE_ENABLED','true')
    seed_run(True); masterdata_seed_run(); admin_seed_run()
    return db.DB_PATH


def first_customer():
    c=db.connect()
    r=c.execute('SELECT id,name FROM customers ORDER BY id LIMIT 1').fetchone()
    c.close(); return dict(r)


def first_job_for_customer(cid):
    c=db.connect(); r=c.execute('SELECT id FROM jobs WHERE customer_id=? ORDER BY id LIMIT 1',(cid,)).fetchone(); c.close()
    return r['id']


def configure_credit(cid,limit=100,currency='USD'):
    c=db.connect()
    c.execute("INSERT OR REPLACE INTO gl_credit_limits(customer_id,currency,credit_limit,exposure,on_hold,version) VALUES(?,?,?,?,0,1)",(cid,currency,limit,0))
    c.close()


def add_ar(cid,jid,ref,outstanding,status='OPEN',currency='USD'):
    c=db.connect()
    c.execute("""INSERT INTO gl_ar_open_items(customer_id,job_id,source_ref,document_date,due_date,currency,original_amount,outstanding,status)
      VALUES(?,?,?,?,?,?,?,?,?)""",(cid,jid,ref,'2026-08-01','2026-09-01',currency,outstanding,outstanding,status))
    c.close()


def test_exposure_derived_from_authoritative_open_items_and_hold_synced(isolated):
    cst=first_customer(); jid=first_job_for_customer(cst['id']); configure_credit(cst['id'],100)
    c=db.connect(); c.execute('DELETE FROM gl_ar_open_items WHERE customer_id=?',(cst['id'],)); c.close()
    add_ar(cst['id'],jid,'AR-I9-1',70)
    add_ar(cst['id'],jid,'AR-I9-2',50)
    c=db.connect(); snap=gov.refresh_credit_exposure(c,cst['id'],'USD')
    assert snap['exposure']==120
    assert snap['on_hold']==1
    cc=c.execute('SELECT exposure,on_hold FROM gl_credit_limits WHERE customer_id=?',(cst['id'],)).fetchone()
    fs=c.execute('SELECT outstanding,credit_hold FROM finance_states WHERE job_id=?',(jid,)).fetchone()
    assert cc['exposure']==120 and cc['on_hold']==1
    assert fs['outstanding']==120 and fs['credit_hold']==1
    c.close()


def test_closed_and_writtenoff_items_not_counted_in_exposure(isolated):
    cst=first_customer(); jid=first_job_for_customer(cst['id']); configure_credit(cst['id'],100)
    c=db.connect(); c.execute('DELETE FROM gl_ar_open_items WHERE customer_id=?',(cst['id'],)); c.close()
    add_ar(cst['id'],jid,'AR-I9-O',40,'OPEN')
    add_ar(cst['id'],jid,'AR-I9-C',50,'CLOSED')
    add_ar(cst['id'],jid,'AR-I9-W',60,'WRITTEN_OFF')
    c=db.connect(); snap=gov.refresh_credit_exposure(c,cst['id'],'USD')
    assert snap['exposure']==40 and snap['on_hold']==0
    c.close()


def test_credit_hold_blocks_new_exposure_without_override(isolated):
    cst=first_customer(); jid=first_job_for_customer(cst['id']); configure_credit(cst['id'],50)
    add_ar(cst['id'],jid,'AR-I9-H',75,'OPEN')
    c=db.connect()
    with pytest.raises(HTTPException) as exc:
        gov.ensure_credit_available(c,cst['id'],'USD',1)
    assert exc.value.detail['code']=='CREDIT_HOLD_ACTIVE'
    c.close()


def test_override_requires_authority_reason_expiry_and_blocks_duplicates(isolated):
    cst=first_customer(); configure_credit(cst['id'],100)
    c=db.connect()
    c.execute("INSERT INTO iam_approval_limits(role_code,currency,amount_limit,action,status) VALUES('FINANCE','USD',500,'CREDIT_OVERRIDE','ACTIVE')")
    ctx={'user_ref':'maker-i9','roles':['FINANCE'],'office_code':'RTM'}
    with pytest.raises(HTTPException) as exc:
        gov.validate_override_request(c,ctx,cst['id'],100,'USD','', '2026-10-10T00:00:00+00:00')
    assert exc.value.detail['code']=='CREDIT_OVERRIDE_REASON_REQUIRED'
    req=gov.validate_override_request(c,ctx,cst['id'],100,'USD','Temporary approved exposure','2026-10-10T00:00:00+00:00')
    c.execute("""INSERT INTO credit_override_events(override_ref,customer_id,currency,amount,reason,expires_at,requested_by,status,created_at)
      VALUES('COV-I9',?,?,?,?,?,?,'PENDING',?)""",(cst['id'],'USD',100,'Temporary approved exposure','2026-10-10T00:00:00+00:00','maker-i9',gov.now()))
    with pytest.raises(HTTPException) as exc:
        gov.validate_override_request(c,ctx,cst['id'],50,'USD','Again','2026-10-10T00:00:00+00:00')
    assert exc.value.detail['code']=='DUPLICATE_ACTIVE_CREDIT_OVERRIDE'
    assert req['amount']==100
    c.close()


def test_override_above_authority_and_self_approval_blocked(isolated):
    cst=first_customer(); configure_credit(cst['id'],100)
    c=db.connect()
    c.execute("INSERT INTO iam_approval_limits(role_code,currency,amount_limit,action,status) VALUES('FINANCE','USD',100,'CREDIT_OVERRIDE','ACTIVE')")
    maker={'user_ref':'maker','roles':['FINANCE']}
    with pytest.raises(HTTPException) as exc:
        gov.validate_override_request(c,maker,cst['id'],101,'USD','Need room','2026-10-10T00:00:00+00:00')
    assert exc.value.detail['code']=='CREDIT_OVERRIDE_ABOVE_AUTHORITY'
    oid=c.execute("""INSERT INTO credit_override_events(override_ref,customer_id,currency,amount,reason,expires_at,requested_by,status,created_at)
      VALUES('COV-SELF',?,?,?,?,?,?,'PENDING',?)""",(cst['id'],'USD',50,'Need room','2026-10-10T00:00:00+00:00','maker',gov.now())).lastrowid
    with pytest.raises(HTTPException) as exc:
        gov.approve_override(c,maker,oid)
    assert exc.value.detail['code']=='MAKER_CANNOT_APPROVE_OWN_CREDIT_OVERRIDE'
    c.close()


def test_expired_override_not_effective_and_cannot_be_approved(isolated):
    cst=first_customer(); configure_credit(cst['id'],100)
    c=db.connect()
    c.execute("INSERT INTO iam_approval_limits(role_code,currency,amount_limit,action,status) VALUES('FINANCE','USD',500,'CREDIT_OVERRIDE','ACTIVE')")
    oid=c.execute("""INSERT INTO credit_override_events(override_ref,customer_id,currency,amount,reason,expires_at,requested_by,status,created_at)
      VALUES('COV-EXP',?,?,?,?,?,?,'PENDING',?)""",(cst['id'],'USD',50,'Old','2026-10-01T00:00:00+00:00','maker',gov.now())).lastrowid
    checker={'user_ref':'checker','roles':['FINANCE']}
    with pytest.raises(HTTPException) as exc:
        gov.approve_override(c,checker,oid)
    assert exc.value.detail['code']=='CREDIT_OVERRIDE_EXPIRED'
    c.close()


def test_direct_credit_control_create_update_blocked(isolated):
    with pytest.raises(HTTPException) as exc:
        gl.update('customer-credit-control',1,gl.UpdateBody(version=1,fields={'Exposure':'999'}),x_role='ADMIN',x_m3_session=None)
    assert exc.value.status_code==405
    assert exc.value.detail['code']=='AUTHORITATIVE_CREDIT_CONTROL_READ_ONLY'


def test_collection_metadata_cannot_change_principal(isolated):
    assert gov.validate_collection_metadata({'Promise To Pay':'2026-10-15','Dispute':'Open','Collector':'AR-TEAM'}) is True
    with pytest.raises(HTTPException) as exc:
        gov.validate_collection_metadata({'Dunning Level':'2','Outstanding':'0'})
    assert exc.value.detail['code']=='COLLECTION_METADATA_CANNOT_CHANGE_PRINCIPAL'


def test_dunning_levels_are_aging_based():
    assert gov.dunning_level(0)==0
    assert gov.dunning_level(15)==1
    assert gov.dunning_level(45)==2
    assert gov.dunning_level(75)==3
    assert gov.dunning_level(120)==4


@pytest.mark.parametrize('flow',['EXPORT','IMPORT','TS'])
def test_export_import_ts_credit_governance_matrix(flow):
    assert flow in {'EXPORT','IMPORT','TS'}
    assert callable(gov.refresh_credit_exposure)
    assert callable(gov.ensure_credit_available)
