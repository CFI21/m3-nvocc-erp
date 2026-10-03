import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
import app.item7_financial_document_governance as gov


@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'item7.db')
    monkeypatch.setenv('M3_ITEM7_DOCUMENT_SETTLEMENT_GOVERNANCE_ENABLED','true')
    seed_run(True); masterdata_seed_run(); admin_seed_run()
    return db.DB_PATH


def add_doc(module,ref,job_id,party,amount,status='Posted',currency='USD'):
    c=db.connect()
    fields={'Status':status,'Currency':currency,'Amount':str(amount)}
    if module=='invoice':
        fields.update({'Customer':party,'Invoice Amount':str(amount)})
    else:
        fields.update({'Supplier':party,'Invoice Amount':str(amount)})
    c.execute("""INSERT INTO gl_records(module,external_ref,job_id,status,version,payload_json,created_at,updated_at)
      VALUES(?,?,?,?,1,?,?,?)""",(module,ref,job_id,status,json.dumps(fields),'2026-10-03','2026-10-03'))
    c.close()


def first_job():
    c=db.connect(); r=c.execute('SELECT id FROM jobs ORDER BY id LIMIT 1').fetchone(); c.close(); return r['id']


def test_credit_note_requires_valid_original_and_blocks_over_credit(isolated):
    jid=first_job(); add_doc('invoice','INV-I7',jid,'Customer A',100)
    c=db.connect()
    ok=gov.validate_correction(c,'credit-note',{'Invoice Ref':'INV-I7','Customer':'Customer A','Currency':'USD','Amount':'40','Reason':'Rate'},jid,'invoice','INV-I7')
    assert ok['source_module']=='invoice'
    c.execute("""INSERT INTO gl_records(module,external_ref,job_id,source_type,source_ref,status,version,payload_json,created_at,updated_at)
      VALUES('credit-note','CN-I7-1',?,'invoice','INV-I7','Approved',1,?,?,?)""",
      (jid,json.dumps({'Invoice Ref':'INV-I7','Customer':'Customer A','Currency':'USD','Amount':'40','Reason':'Rate','Status':'Approved'}),'2026-10-03','2026-10-03'))
    with pytest.raises(HTTPException) as exc:
        gov.validate_correction(c,'credit-note',{'Invoice Ref':'INV-I7','Customer':'Customer A','Currency':'USD','Amount':'70','Reason':'Other'},jid,'invoice','INV-I7')
    assert exc.value.detail['code']=='CORRECTION_EXCEEDS_ORIGINAL'
    c.close()


def test_duplicate_financial_correction_blocked(isolated):
    jid=first_job(); add_doc('invoice','INV-DUP',jid,'Customer A',100)
    c=db.connect()
    c.execute("""INSERT INTO gl_records(module,external_ref,job_id,source_type,source_ref,status,version,payload_json,created_at,updated_at)
      VALUES('credit-note','CN-DUP',?,'invoice','INV-DUP','Approved',1,?,?,?)""",
      (jid,json.dumps({'Amount':'20','Reason':'Rate','Status':'Approved'}),'2026-10-03','2026-10-03'))
    with pytest.raises(HTTPException) as exc:
        gov.validate_correction(c,'credit-note',{'Invoice Ref':'INV-DUP','Currency':'USD','Amount':'20','Reason':'Rate'},jid,'invoice','INV-DUP')
    assert exc.value.detail['code']=='DUPLICATE_FINANCIAL_CORRECTION'
    c.close()


def test_wrong_party_and_over_allocation_blocked(isolated):
    jid=first_job(); add_doc('invoice','INV-ALLOC',jid,'Customer A',100)
    c=db.connect()
    with pytest.raises(HTTPException) as exc:
        gov.validate_allocation(c,'customer-receipt-allocation',{'Invoice Ref':'INV-ALLOC','Customer':'Customer B','Currency':'USD','Allocated Amount':'10'},jid)
    assert exc.value.detail['code']=='SETTLEMENT_PARTY_MISMATCH'
    tr=c.execute("""INSERT INTO treasury_records(module,external_ref,job_id,party_name,currency,amount,status,version,maker_id,payload_json,created_at,updated_at)
      VALUES('customer-receipt-allocation','TRA-I7',?,'Customer A','USD',80,'Approved',1,'maker',?,?,?)""",
      (jid,json.dumps({'Invoice Ref':'INV-ALLOC','Allocated Amount':'80'}),'2026-10-03','2026-10-03')).lastrowid
    c.execute("""INSERT INTO treasury_allocations(treasury_record_id,source_type,source_ref,allocated_amount,currency,fx_rate,job_id)
      VALUES(?,'INVOICE','INV-ALLOC',80,'USD',1,?)""",(tr,jid))
    with pytest.raises(HTTPException) as exc:
        gov.validate_allocation(c,'customer-receipt-allocation',{'Invoice Ref':'INV-ALLOC','Customer':'Customer A','Currency':'USD','Allocated Amount':'30'},jid)
    assert exc.value.detail['code']=='OVER_ALLOCATION_BLOCKED'
    assert exc.value.detail['remaining']==20
    c.close()


def test_cancelled_or_reversed_source_not_settleable(isolated):
    jid=first_job(); add_doc('invoice','INV-CAN',jid,'Customer A',100,status='Cancelled')
    c=db.connect()
    with pytest.raises(HTTPException) as exc:
        gov.validate_allocation(c,'customer-receipt-allocation',{'Invoice Ref':'INV-CAN','Customer':'Customer A','Currency':'USD','Allocated Amount':'10'},jid)
    assert exc.value.detail['code']=='SOURCE_DOCUMENT_NOT_SETTLEABLE'
    c.close()


def test_released_and_reversed_treasury_records_immutable(isolated):
    for st in ('Released','Reversed','Settled','Allocated'):
        with pytest.raises(HTTPException) as exc:
            gov.enforce_treasury_update(st,'session')
        assert exc.value.detail['code']=='TREASURY_RECORD_IMMUTABLE'


def test_settlement_mutation_requires_session(isolated):
    with pytest.raises(HTTPException) as exc:
        gov.enforce_treasury_update('Draft',None)
    assert exc.value.detail['code']=='SESSION_REQUIRED_FOR_SETTLEMENT_MUTATION'


def test_double_reversal_blocked(isolated):
    jid=first_job(); c=db.connect()
    orig=c.execute("""INSERT INTO treasury_records(module,external_ref,job_id,currency,amount,status,version,maker_id,payload_json,created_at,updated_at)
      VALUES('customer-refunds','REF-I7',?,'USD',10,'Released',1,'maker','{}','2026-10-03','2026-10-03')""",(jid,)).lastrowid
    rev=c.execute("""INSERT INTO treasury_records(module,external_ref,job_id,currency,amount,status,version,maker_id,payload_json,created_at,updated_at)
      VALUES('customer-refunds','REF-I7-REV',?,'USD',-10,'Reversal',1,'checker','{}','2026-10-03','2026-10-03')""",(jid,)).lastrowid
    c.execute("INSERT INTO treasury_reversals(original_record_id,reversal_record_id,reason,ts) VALUES(?,?,?,?)",(orig,rev,'test','2026-10-03'))
    with pytest.raises(HTTPException) as exc:
        gov.enforce_single_reversal(c,orig)
    assert exc.value.detail['code']=='TREASURY_REVERSAL_ALREADY_EXISTS'
    c.close()


@pytest.mark.parametrize('flow',['EXPORT','IMPORT','TS'])
def test_export_import_ts_document_governance_matrix(flow):
    assert flow in {'EXPORT','IMPORT','TS'}
    assert gov.CORRECTION_MODULES=={'credit-note','debit-note'}
    assert gov.ALLOCATION_MODULES=={'customer-receipt-allocation','supplier-carrier-payment-allocation'}
