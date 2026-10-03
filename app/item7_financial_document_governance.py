import os, json
from fastapi import HTTPException


DOCUMENT_MODULES={'invoice','bills'}
CORRECTION_MODULES={'credit-note','debit-note'}
ALLOCATION_MODULES={'customer-receipt-allocation','supplier-carrier-payment-allocation'}
IMMUTABLE_TREASURY_STATES={'APPROVED','RELEASED','REVERSED','SETTLED','ALLOCATED','WRITTEN OFF'}


def enabled():
    return os.getenv('M3_ITEM7_DOCUMENT_SETTLEMENT_GOVERNANCE_ENABLED','false').lower()=='true'


def _num(v):
    try:return float(v)
    except:return 0.0


def _record_fields(row):
    return json.loads(row['payload_json']) if row and row['payload_json'] else {}


def _doc_amount(module, fields):
    if module in {'invoice','bills'}:
        return _num(fields.get('Invoice Amount') or fields.get('Amount') or fields.get('Net Amount'))
    return _num(fields.get('Amount'))


def _party(module, fields):
    if module=='invoice': return fields.get('Customer') or fields.get('Party')
    if module=='bills': return fields.get('Supplier') or fields.get('Supplier / Carrier') or fields.get('Party')
    return fields.get('Party') or fields.get('Customer') or fields.get('Supplier / Carrier') or fields.get('Supplier')


def find_source_document(conn, ref, preferred=None):
    if not ref:
        raise HTTPException(422,{'code':'SOURCE_DOCUMENT_REQUIRED'})
    mods=[preferred] if preferred in DOCUMENT_MODULES else ['invoice','bills']
    q="SELECT * FROM gl_records WHERE external_ref=? AND module=? ORDER BY id DESC LIMIT 1"
    for m in mods:
        r=conn.execute(q,(ref,m)).fetchone()
        if r:return r
    raise HTTPException(422,{'code':'SOURCE_DOCUMENT_NOT_FOUND','source_ref':ref})


def validate_correction(conn, module, fields, job_id=None, source_type=None, source_ref=None):
    if not enabled() or module not in CORRECTION_MODULES:return {}
    ref=source_ref or fields.get('Invoice Ref') or fields.get('Bill Ref') or fields.get('Source Ref')
    preferred='invoice' if fields.get('Invoice Ref') else ('bills' if fields.get('Bill Ref') else source_type)
    src=find_source_document(conn,ref,preferred)
    src_fields=_record_fields(src)
    if str(src['status']).upper() in {'CANCELLED','REVERSED'}:
        raise HTTPException(422,{'code':'SOURCE_DOCUMENT_NOT_CORRECTABLE','status':src['status']})
    if job_id is not None and src['job_id']!=job_id:
        raise HTTPException(422,{'code':'CORRECTION_JOB_MISMATCH'})
    currency=(fields.get('Currency') or 'USD').upper()
    src_currency=(src_fields.get('Currency') or 'USD').upper()
    if currency!=src_currency: raise HTTPException(422,{'code':'CORRECTION_CURRENCY_MISMATCH'})
    party=_party(src['module'],src_fields)
    corr_party=_party(module,fields)
    if corr_party and party and corr_party!=party:
        raise HTTPException(422,{'code':'CORRECTION_PARTY_MISMATCH'})
    amount=_num(fields.get('Amount'))
    if amount<=0: raise HTTPException(422,{'code':'CORRECTION_AMOUNT_REQUIRED'})
    original=_doc_amount(src['module'],src_fields)
    existing=0.0
    for r in conn.execute("""SELECT payload_json,status FROM gl_records
      WHERE module=? AND source_ref=? AND status NOT IN ('Cancelled','Reversed')""",(module,ref)):
        existing+=_num(_record_fields(r).get('Amount'))
    if existing+amount>original+0.005:
        raise HTTPException(422,{'code':'CORRECTION_EXCEEDS_ORIGINAL','original_amount':original,'existing_corrections':existing,'requested':amount})
    dup=conn.execute("""SELECT id,external_ref FROM gl_records WHERE module=? AND source_ref=?
      AND status NOT IN ('Cancelled','Reversed') AND json_extract(payload_json,'$.Amount')=?
      AND COALESCE(json_extract(payload_json,'$.Reason'),'')=COALESCE(?, '') LIMIT 1""",
      (module,ref,str(fields.get('Amount')),fields.get('Reason'))).fetchone()
    if dup: raise HTTPException(409,{'code':'DUPLICATE_FINANCIAL_CORRECTION','existing_ref':dup['external_ref']})
    return {'source_document_id':src['id'],'source_module':src['module'],'source_ref':ref,'original_amount':original,'party':party}


def validate_allocation(conn, module, fields, job_id=None):
    if not enabled() or module not in ALLOCATION_MODULES:return {}
    ref=fields.get('Invoice Ref') if module=='customer-receipt-allocation' else fields.get('Bill Ref')
    preferred='invoice' if module=='customer-receipt-allocation' else 'bills'
    src=find_source_document(conn,ref,preferred)
    src_fields=_record_fields(src)
    if str(src['status']).upper() in {'CANCELLED','REVERSED'}:
        raise HTTPException(422,{'code':'SOURCE_DOCUMENT_NOT_SETTLEABLE','status':src['status']})
    if job_id is not None and src['job_id']!=job_id:
        raise HTTPException(422,{'code':'SETTLEMENT_JOB_MISMATCH'})
    party=_party(src['module'],src_fields)
    alloc_party=_party(module,fields)
    if alloc_party and party and alloc_party!=party:
        raise HTTPException(422,{'code':'SETTLEMENT_PARTY_MISMATCH'})
    currency=(fields.get('Currency') or 'USD').upper()
    src_currency=(src_fields.get('Currency') or 'USD').upper()
    if currency!=src_currency: raise HTTPException(422,{'code':'SETTLEMENT_CURRENCY_MISMATCH'})
    amount=_num(fields.get('Allocated Amount'))
    if amount<=0:raise HTTPException(422,{'code':'ALLOCATION_MUST_BE_POSITIVE'})
    original=_doc_amount(src['module'],src_fields)
    used=conn.execute("""SELECT COALESCE(SUM(a.allocated_amount),0) x FROM treasury_allocations a
      JOIN treasury_records t ON t.id=a.treasury_record_id
      WHERE a.source_ref=? AND t.status NOT IN ('Reversed','Cancelled')""",(ref,)).fetchone()['x']
    remaining=round(original-float(used or 0),2)
    if amount>remaining+0.005:
        raise HTTPException(422,{'code':'OVER_ALLOCATION_BLOCKED','source_ref':ref,'original_amount':original,'already_allocated':float(used or 0),'remaining':remaining,'requested':amount})
    return {'source_document_id':src['id'],'source_module':src['module'],'source_ref':ref,'party':party,'remaining_before':remaining}


def enforce_treasury_update(status, session_token):
    if not enabled():return
    if not session_token: raise HTTPException(401,{'code':'SESSION_REQUIRED_FOR_SETTLEMENT_MUTATION'})
    if str(status or '').upper() in IMMUTABLE_TREASURY_STATES:
        raise HTTPException(409,{'code':'TREASURY_RECORD_IMMUTABLE','status':status})


def enforce_single_reversal(conn, treasury_record_id):
    if not enabled():return
    prior=conn.execute('SELECT id FROM treasury_reversals WHERE original_record_id=? LIMIT 1',(treasury_record_id,)).fetchone()
    if prior: raise HTTPException(409,{'code':'TREASURY_REVERSAL_ALREADY_EXISTS'})
