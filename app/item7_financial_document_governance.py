import os, json
from fastapi import HTTPException
from .db import table_exists


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


def _detention_segment_refs(fields):
    v=fields.get('Detention Segment Refs')
    if v in (None,''):v=fields.get('Detention Segment Ref')
    if isinstance(v,list):return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v,str):
        return [x.strip() for x in v.replace(';',',').split(',') if x.strip()]
    return []

def _detention_invoice_fx(conn,src,fields):
    currency=str(fields.get('Currency') or 'USD').upper()
    if currency=='USD':return 1.0
    r=conn.execute("""SELECT exchange_rate FROM gl_vouchers WHERE gl_record_id=?
      AND UPPER(COALESCE(status,'')) NOT IN ('REVERSED','CANCELLED','CANCELED','VOID')
      ORDER BY id DESC LIMIT 1""",(src['id'],)).fetchone()
    if r and _num(r['exchange_rate'])>0:return _num(r['exchange_rate'])
    rate=_num(fields.get('Exchange Rate') or fields.get('FX Rate'))
    if rate>0:return rate
    raise HTTPException(409,{'code':'FX_RATE_MISSING_ON_SEGMENT','invoice_ref':src['external_ref'],'currency':currency})

def _detention_document_fx(conn,fields,currency):
    cur=str(currency or 'USD').upper()
    supplied=_num(fields.get('Exchange Rate') or fields.get('FX Rate'))
    if supplied>0:return supplied
    if cur=='USD':return 1.0
    day=str(fields.get('Date') or fields.get('Invoice Date') or '')[:10]
    if not day:
        import datetime
        day=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    r=conn.execute("""SELECT rate FROM gl_fx_rates WHERE currency=? AND base_currency='USD'
      AND date(rate_date)<=date(?) AND status='ACTIVE' ORDER BY date(rate_date) DESC,id DESC LIMIT 1""",(cur,day)).fetchone()
    if not r:raise HTTPException(409,{'code':'FX_RATE_MISSING_ON_SEGMENT','currency':cur,'date':day})
    return float(r['rate'])

def validate_detention_document_link(conn,module,fields,job_id=None,source_type=None,source_ref=None):
    if module not in {'invoice','bills'} or str(source_type or '').upper()!='DETENTION_COLLECTION':
        return {}
    if not source_ref:raise HTTPException(422,{'code':'DETENTION_REF_REQUIRED'})
    if not table_exists(conn,'detention_segments'):
        raise HTTPException(503,{'code':'SEGMENT_TABLE_MISSING'})
    direction='AGENT_TO_CUSTOMER' if module=='invoice' else 'PRINCIPAL_TO_AGENT'
    rows=[dict(r) for r in conn.execute("""SELECT * FROM detention_segments
      WHERE detention_ref=? AND commercial_direction=? ORDER BY sequence,id""",(source_ref,direction)).fetchall()]
    if not rows:
        other=conn.execute("SELECT commercial_direction FROM detention_segments WHERE detention_ref=? LIMIT 1",(source_ref,)).fetchone()
        if other:
            raise HTTPException(409,{'code':'DETENTION_REF_DIRECTION_COLLISION','detention_ref':source_ref,
                                     'expected_direction':direction,'actual_direction':other['commercial_direction']})
        raise HTTPException(422,{'code':'DETENTION_REF_NOT_FOUND','detention_ref':source_ref})
    if job_id is not None and any(int(r['job_id'])!=int(job_id) for r in rows):
        raise HTTPException(422,{'code':'DETENTION_FINANCE_JOB_MISMATCH','detention_ref':source_ref})

    already=set()
    for doc in conn.execute("""SELECT payload_json,status FROM gl_records
      WHERE module=? AND UPPER(COALESCE(source_type,''))='DETENTION_COLLECTION' AND source_ref=?
        AND UPPER(COALESCE(status,'')) NOT IN ('CANCELLED','CANCELED','REVERSED','VOID')""",(module,source_ref)):
        p=_record_fields(doc)
        already.update(_detention_segment_refs(p))
    requested=_detention_segment_refs(fields)
    row_by_ref={r['segment_ref']:r for r in rows}
    if requested:
        bad=[x for x in requested if x not in row_by_ref]
        if bad:raise HTTPException(422,{'code':'DETENTION_SEGMENT_REF_INVALID','segment_refs':bad,'detention_ref':source_ref})
        dup=[x for x in requested if x in already]
        if dup:raise HTTPException(409,{'code':'DETENTION_SEGMENT_ALREADY_INVOICED','segment_refs':dup})
    else:
        requested=[r['segment_ref'] for r in rows if r['segment_ref'] not in already]
        if not requested:raise HTTPException(409,{'code':'NO_UNINVOICED_DETENTION_SEGMENTS','detention_ref':source_ref})
    selected=[row_by_ref[x] for x in requested]
    currency=str(fields.get('Currency') or selected[-1]['document_currency'] or 'USD').upper()
    fx=_detention_document_fx(conn,fields,currency)
    segment_base=round(sum(_num(x['base_amount']) for x in selected),2)
    expected_amount=round(segment_base/fx,2)
    supplied_amount=_num(fields.get('Amount') or fields.get('Invoice Amount'))
    if supplied_amount>0 and abs(supplied_amount-expected_amount)>0.01:
        raise HTTPException(422,{'code':'DETENTION_INVOICE_AMOUNT_MISMATCH','detention_ref':source_ref,
                                 'segment_refs':requested,'expected_amount':expected_amount,
                                 'requested_amount':supplied_amount,'currency':currency,'fx_rate':fx})
    fields['Currency']=currency
    fields['Exchange Rate']=str(fx)
    fields['Amount']=str(expected_amount)
    fields['Outstanding']=str(expected_amount)
    fields['Detention Ref']=source_ref
    fields['Detention Segment Refs']=requested
    fields['Commercial Direction']=direction
    fields['Detention Stage']=selected[-1]['stage'] if len({x['stage'] for x in selected})==1 else 'MIXED'
    fields['Tariff Versions']=[{'segment_ref':x['segment_ref'],'rule_ref':x['rule_ref'],'version':x['tariff_version']} for x in selected]
    return {'detention_ref':source_ref,'commercial_direction':direction,'segment_refs':requested,
            'segment_base_amount':segment_base,'document_currency':currency,'exchange_rate':fx,
            'document_amount':expected_amount}

def validate_correction(conn, module, fields, job_id=None, source_type=None, source_ref=None):
    if module not in CORRECTION_MODULES:return {}
    ref=source_ref or fields.get('Invoice Ref') or fields.get('Bill Ref') or fields.get('Source Ref')
    preferred='invoice' if fields.get('Invoice Ref') else ('bills' if fields.get('Bill Ref') else source_type)
    src=find_source_document(conn,ref,preferred)
    src_fields=_record_fields(src)
    detention_segments=_detention_segment_refs(src_fields)
    detention_credit=(module=='credit-note' and bool(detention_segments))
    if not enabled() and not detention_credit:return {}
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

    segment_ref=None
    if detention_credit:
        refs=_detention_segment_refs(fields)
        if len(refs)!=1:
            raise HTTPException(422,{'code':'DETENTION_CREDIT_SEGMENT_REQUIRED','required':'exactly one Detention Segment Ref'})
        segment_ref=refs[0]
        if segment_ref not in detention_segments:
            raise HTTPException(422,{'code':'DETENTION_CREDIT_SEGMENT_NOT_ON_INVOICE','segment_ref':segment_ref,'invoice_ref':ref})
        reason_code=str(fields.get('Reason Code') or '').strip()
        if not reason_code:
            raise HTTPException(422,{'code':'DETENTION_CREDIT_REASON_CODE_REQUIRED','invoice_ref':ref,'segment_ref':segment_ref})
        if not table_exists(conn,'detention_segments'):
            raise HTTPException(503,{'code':'SEGMENT_TABLE_MISSING'})
        seg=conn.execute("SELECT * FROM detention_segments WHERE segment_ref=?",(segment_ref,)).fetchone()
        if not seg:raise HTTPException(422,{'code':'DETENTION_SEGMENT_REF_INVALID','segment_ref':segment_ref})
        src_fx=_detention_invoice_fx(conn,src,src_fields)
        segment_limit=round(_num(seg['base_amount'])/src_fx,2)
        existing_segment_credit=0.0
        for r in conn.execute("""SELECT payload_json,status FROM gl_records
          WHERE module='credit-note' AND source_ref=? AND UPPER(COALESCE(status,'')) NOT IN ('CANCELLED','CANCELED','REVERSED','VOID')""",(ref,)):
            p=_record_fields(r)
            if segment_ref in _detention_segment_refs(p):
                existing_segment_credit+=_num(p.get('Amount'))
        if existing_segment_credit+amount>segment_limit+0.005:
            raise HTTPException(422,{'code':'CREDIT_EXCEEDS_INVOICE','invoice_ref':ref,'segment_ref':segment_ref,
                                     'segment_invoice_bound':segment_limit,'existing_segment_credits':round(existing_segment_credit,2),
                                     'requested':amount})

    original=_doc_amount(src['module'],src_fields)
    existing=0.0
    for r in conn.execute("""SELECT payload_json,status FROM gl_records
      WHERE module=? AND source_ref=? AND status NOT IN ('Cancelled','Reversed')""",(module,ref)):
        existing+=_num(_record_fields(r).get('Amount'))
    if existing+amount>original+0.005:
        code='CREDIT_EXCEEDS_INVOICE' if detention_credit else 'CORRECTION_EXCEEDS_ORIGINAL'
        raise HTTPException(422,{'code':code,'original_amount':original,'existing_corrections':existing,'requested':amount,
                                 'invoice_ref':ref,'segment_ref':segment_ref})
    dup=None
    for candidate in conn.execute("""SELECT id,external_ref,payload_json,status FROM gl_records
      WHERE module=? AND source_ref=? AND UPPER(COALESCE(status,'')) NOT IN ('CANCELLED','CANCELED','REVERSED','VOID')""",(module,ref)):
        cp=_record_fields(candidate)
        if str(cp.get('Amount'))==str(fields.get('Amount')) and str(cp.get('Reason') or '')==str(fields.get('Reason') or ''):
            dup=candidate;break
    if dup: raise HTTPException(409,{'code':'DUPLICATE_FINANCIAL_CORRECTION','existing_ref':dup['external_ref']})
    return {'source_document_id':src['id'],'source_module':src['module'],'source_ref':ref,'original_amount':original,
            'party':party,'detention_segment_ref':segment_ref}

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
