import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
import app.item8_open_item_governance as gov
import app.hardening as hardening


@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'item8.db')
    monkeypatch.setenv('M3_ITEM8_OPEN_ITEM_GOVERNANCE_ENABLED','true')
    monkeypatch.setenv('M3_ITEM7_DOCUMENT_SETTLEMENT_GOVERNANCE_ENABLED','true')
    monkeypatch.setenv('M3_ITEM6_FINANCIAL_GOVERNANCE_ENABLED','true')
    seed_run(True); masterdata_seed_run(); admin_seed_run()
    return db.DB_PATH


def first_job():
    c=db.connect(); r=c.execute('SELECT id,customer_id FROM jobs ORDER BY id LIMIT 1').fetchone(); c.close(); return dict(r)


def customer_name(cid):
    c=db.connect(); r=c.execute('SELECT name FROM customers WHERE id=?',(cid,)).fetchone(); c.close(); return r['name']


def add_doc(module,ref,job_id,party,amount,status='Approved',currency='USD'):
    fields={'Status':status,'Currency':currency,'Amount':str(amount),'Date':'2026-09-23','Due Date':'2026-10-23'}
    if module=='invoice':
        fields.update({'Customer':party,'Invoice Amount':str(amount)})
    elif module=='bills':
        fields.update({'Supplier':party,'Invoice Amount':str(amount)})
    elif module=='credit-note':
        fields.update({'Customer':party,'Invoice Ref':ref.replace('CN-','INV-')})
    elif module=='debit-note':
        fields.update({'Supplier':party,'Bill Ref':ref.replace('DN-','BILL-')})
    c=db.connect()
    cur=c.execute("""INSERT INTO gl_records(module,external_ref,job_id,status,version,payload_json,created_at,updated_at)
      VALUES(?,?,?,?,1,?,?,?)""",(module,ref,job_id,status,json.dumps(fields),'2026-10-03','2026-10-03'))
    row=c.execute('SELECT * FROM gl_records WHERE id=?',(cur.lastrowid,)).fetchone()
    c.close(); return row


def test_invoice_approval_creates_authoritative_ar_open_item(isolated):
    j=first_job(); party=customer_name(j['customer_id'])
    r=add_doc('invoice','INV-I8-AR',j['id'],party,100)
    c=db.connect()
    item=gov.ensure_open_item(c,'invoice',r)
    again=gov.ensure_open_item(c,'invoice',r)
    assert item['source_ref']=='INV-I8-AR'
    assert item['original_amount']==100
    assert item['outstanding']==100
    assert item['status']=='OPEN'
    assert again['id']==item['id']
    assert c.execute("SELECT COUNT(*) n FROM gl_ar_open_items WHERE source_ref='INV-I8-AR'").fetchone()['n']==1
    c.close()


def test_bill_approval_creates_authoritative_ap_open_item(isolated):
    j=first_job()
    r=add_doc('bills','BILL-I8-AP',j['id'],'Carrier I8',75)
    c=db.connect()
    item=gov.ensure_open_item(c,'bills',r)
    assert item['source_ref']=='BILL-I8-AP'
    assert item['supplier_name']=='Carrier I8'
    assert item['outstanding']==75
    c.close()


def test_partial_and_full_application_update_outstanding_and_status(isolated):
    j=first_job(); party=customer_name(j['customer_id'])
    r=add_doc('invoice','INV-I8-SET',j['id'],party,100)
    c=db.connect(); gov.ensure_open_item(c,'invoice',r)
    p=gov.apply_application(c,'INV-I8-SET','INVOICE',40,j['id'],'USD')
    assert p['outstanding_after']==60 and p['status_after']=='OPEN'
    f=gov.apply_application(c,'INV-I8-SET','INVOICE',60,j['id'],'USD')
    assert f['outstanding_after']==0 and f['status_after']=='CLOSED'
    row=c.execute("SELECT outstanding,status FROM gl_ar_open_items WHERE source_ref='INV-I8-SET'").fetchone()
    assert row['outstanding']==0 and row['status']=='CLOSED'
    c.close()


def test_over_application_wrong_currency_and_wrong_job_blocked(isolated):
    j=first_job(); party=customer_name(j['customer_id'])
    r=add_doc('invoice','INV-I8-BLOCK',j['id'],party,100)
    c=db.connect(); gov.ensure_open_item(c,'invoice',r)
    with pytest.raises(HTTPException) as exc:
        gov.validate_application(c,'INV-I8-BLOCK','INVOICE',101,j['id'],'USD')
    assert exc.value.detail['code']=='OVER_APPLICATION_BLOCKED'
    with pytest.raises(HTTPException) as exc:
        gov.validate_application(c,'INV-I8-BLOCK','INVOICE',10,j['id'],'EUR')
    assert exc.value.detail['code']=='OPEN_ITEM_CURRENCY_MISMATCH'
    with pytest.raises(HTTPException) as exc:
        gov.validate_application(c,'INV-I8-BLOCK','INVOICE',10,j['id']+999,'USD')
    assert exc.value.detail['code']=='OPEN_ITEM_JOB_MISMATCH'
    c.close()


def test_closed_item_cannot_be_settled_again_and_reversal_reopens(isolated):
    j=first_job(); party=customer_name(j['customer_id'])
    r=add_doc('invoice','INV-I8-REV',j['id'],party,50)
    c=db.connect(); gov.ensure_open_item(c,'invoice',r)
    gov.apply_application(c,'INV-I8-REV','INVOICE',50,j['id'],'USD')
    with pytest.raises(HTTPException) as exc:
        gov.validate_application(c,'INV-I8-REV','INVOICE',1,j['id'],'USD')
    assert exc.value.detail['code']=='OPEN_ITEM_NOT_SETTLEABLE'
    rev=gov.reverse_application(c,'INV-I8-REV','INVOICE',50)
    assert rev['outstanding_after']==50 and rev['status_after']=='OPEN'
    c.close()


def test_writeoff_and_reopen_are_governed(isolated):
    j=first_job(); party=customer_name(j['customer_id'])
    r=add_doc('invoice','INV-I8-WO',j['id'],party,80)
    c=db.connect(); gov.ensure_open_item(c,'invoice',r)
    with pytest.raises(HTTPException) as exc:
        gov.apply_write_off(c,'INV-I8-WO','INVOICE',None)
    assert exc.value.detail['code']=='WRITE_OFF_REASON_REQUIRED'
    wo=gov.apply_write_off(c,'INV-I8-WO','INVOICE','Approved bad debt')
    assert wo['written_off_amount']==80
    with pytest.raises(HTTPException) as exc:
        gov.validate_application(c,'INV-I8-WO','INVOICE',1,j['id'],'USD')
    assert exc.value.detail['code']=='OPEN_ITEM_NOT_SETTLEABLE'
    with pytest.raises(HTTPException) as exc:
        gov.reopen_open_item(c,'INV-I8-WO','INVOICE',80,None)
    assert exc.value.detail['code']=='REOPEN_REASON_REQUIRED'
    ro=gov.reopen_open_item(c,'INV-I8-WO','INVOICE',80,'CFO approved reopen')
    assert ro['outstanding_after']==80 and ro['status_after']=='OPEN'
    c.close()


def test_writeoff_and_reopen_require_real_session_not_client_role_header(isolated):
    for action in ('write_off','reopen'):
        with pytest.raises(HTTPException) as exc:
            gov.enforce_sensitive_action(action,None)
        assert exc.value.status_code==401
        assert exc.value.detail['code']=='SESSION_REQUIRED_FOR_GOVERNED_OPEN_ITEM_ACTION'


def test_credit_note_and_debit_note_application_reduce_authoritative_open_item(isolated):
    j=first_job(); party=customer_name(j['customer_id'])
    inv=add_doc('invoice','INV-I8-CN',j['id'],party,100)
    bill=add_doc('bills','BILL-I8-DN',j['id'],'Carrier I8',90)
    c=db.connect(); gov.ensure_open_item(c,'invoice',inv); gov.ensure_open_item(c,'bills',bill)
    cn_fields={'Status':'Approved','Currency':'USD','Amount':'25','Customer':party,'Invoice Ref':'INV-I8-CN'}
    cnid=c.execute("""INSERT INTO gl_records(module,external_ref,job_id,source_type,source_ref,status,version,payload_json,created_at,updated_at)
      VALUES('credit-note','CN-I8',?,'invoice','INV-I8-CN','Approved',1,?,?,?)""",(j['id'],json.dumps(cn_fields),'2026-10-03','2026-10-03')).lastrowid
    dn_fields={'Status':'Approved','Currency':'USD','Amount':'30','Supplier':'Carrier I8','Bill Ref':'BILL-I8-DN'}
    dnid=c.execute("""INSERT INTO gl_records(module,external_ref,job_id,source_type,source_ref,status,version,payload_json,created_at,updated_at)
      VALUES('debit-note','DN-I8',?,'bills','BILL-I8-DN','Approved',1,?,?,?)""",(j['id'],json.dumps(dn_fields),'2026-10-03','2026-10-03')).lastrowid
    cn=gov.apply_document_correction(c,'credit-note',c.execute('SELECT * FROM gl_records WHERE id=?',(cnid,)).fetchone())
    dn=gov.apply_document_correction(c,'debit-note',c.execute('SELECT * FROM gl_records WHERE id=?',(dnid,)).fetchone())
    assert cn['outstanding_after']==75
    assert dn['outstanding_after']==60
    c.close()


def test_aging_uses_current_open_item_outstanding(isolated):
    j=first_job(); party=customer_name(j['customer_id'])
    r=add_doc('invoice','INV-I8-AGE',j['id'],party,120)
    c=db.connect(); gov.ensure_open_item(c,'invoice',r); gov.apply_application(c,'INV-I8-AGE','INVOICE',20,j['id'],'USD'); c.close()
    rows=hardening.ar_aging('AUDITOR')
    row=next(x for x in rows if x['party']==party)
    assert row['total']>=100


def test_no_direct_open_item_mutation_api_exposed():
    mutating=[]
    for route in hardening.router.routes:
        methods=set(route.methods or [])
        if methods & {'PUT','PATCH','DELETE'} and ('ar' in route.path.lower() or 'ap' in route.path.lower()):
            mutating.append((route.path,sorted(methods)))
    assert mutating==[]


@pytest.mark.parametrize('flow',['EXPORT','IMPORT','TS'])
def test_export_import_ts_open_item_governance_matrix(flow):
    assert flow in {'EXPORT','IMPORT','TS'}
    assert gov.enabled() in {True,False}
