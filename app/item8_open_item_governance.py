import os, json
from fastapi import HTTPException

from .item7_financial_document_governance import find_source_document


def enabled():
    return os.getenv('M3_ITEM8_OPEN_ITEM_GOVERNANCE_ENABLED','false').lower()=='true'

def enforce_sensitive_action(action,session_token):
    if enabled() and str(action).lower() in {'write_off','reopen'} and not session_token:
        raise HTTPException(401,{'code':'SESSION_REQUIRED_FOR_GOVERNED_OPEN_ITEM_ACTION'})


def _num(v):
    try:return float(v)
    except:return 0.0


def _fields(row):
    return json.loads(row['payload_json']) if row and row['payload_json'] else {}


def _doc_amount(module,fields):
    if module in {'invoice','bills'}:
        return _num(fields.get('Invoice Amount') or fields.get('Net Amount') or fields.get('Amount'))
    return _num(fields.get('Amount'))


def _due(fields,date):
    return fields.get('Due Date') or fields.get('Due') or date


def _customer_id(conn,job_id,fields):
    name=fields.get('Customer') or fields.get('Party')
    if name:
        r=conn.execute('SELECT id FROM customers WHERE name=? ORDER BY id LIMIT 1',(name,)).fetchone()
        if r:return r['id']
    if job_id:
        r=conn.execute('SELECT customer_id FROM jobs WHERE id=?',(job_id,)).fetchone()
        if r and r['customer_id']:return r['customer_id']
    raise HTTPException(422,{'code':'AR_CUSTOMER_REQUIRED'})


def ensure_open_item(conn,module,record):
    if not enabled() or module not in {'invoice','bills'}:return None
    fields=_fields(record)
    amount=_doc_amount(module,fields)
    if amount<=0:raise HTTPException(422,{'code':'OPEN_ITEM_AMOUNT_REQUIRED'})
    ref=record['external_ref']
    currency=(fields.get('Currency') or 'USD').upper()
    date=fields.get('Date') or fields.get('Invoice Date') or '2026-09-23'
    due=_due(fields,date)
    if module=='invoice':
        old=conn.execute('SELECT * FROM gl_ar_open_items WHERE source_ref=?',(ref,)).fetchone()
        if old:return dict(old)
        cid=_customer_id(conn,record['job_id'],fields)
        conn.execute("""INSERT INTO gl_ar_open_items(customer_id,job_id,source_ref,document_date,due_date,currency,original_amount,outstanding,status)
          VALUES(?,?,?,?,?,?,?,?, 'OPEN')""",(cid,record['job_id'],ref,date,due,currency,amount,amount))
        return dict(conn.execute('SELECT * FROM gl_ar_open_items WHERE source_ref=?',(ref,)).fetchone())
    old=conn.execute('SELECT * FROM gl_ap_open_items WHERE source_ref=?',(ref,)).fetchone()
    if old:return dict(old)
    supplier=fields.get('Supplier') or fields.get('Supplier / Carrier') or fields.get('Party')
    if not supplier:raise HTTPException(422,{'code':'AP_SUPPLIER_REQUIRED'})
    conn.execute("""INSERT INTO gl_ap_open_items(supplier_name,job_id,source_ref,document_date,due_date,currency,original_amount,outstanding,status)
      VALUES(?,?,?,?,?,?,?,?, 'OPEN')""",(supplier,record['job_id'],ref,date,due,currency,amount,amount))
    return dict(conn.execute('SELECT * FROM gl_ap_open_items WHERE source_ref=?',(ref,)).fetchone())


def get_open_item(conn,source_ref,source_type=None):
    preferred=str(source_type or '').upper()
    order=['AR','AP'] if preferred not in {'BILL','BILLS','AP'} else ['AP','AR']
    for typ in order:
        table='gl_ar_open_items' if typ=='AR' else 'gl_ap_open_items'
        r=conn.execute(f'SELECT * FROM {table} WHERE source_ref=?',(source_ref,)).fetchone()
        if r:return typ,dict(r)
    return None,None


def require_open_item(conn,source_ref,source_type=None):
    typ,item=get_open_item(conn,source_ref,source_type)
    if item:return typ,item
    doc=find_source_document(conn,source_ref,'invoice' if str(source_type).upper() in {'INVOICE','AR'} else ('bills' if str(source_type).upper() in {'BILL','BILLS','AP'} else None))
    if str(doc['status']).upper() not in {'APPROVED','POSTED'}:
        raise HTTPException(422,{'code':'SOURCE_DOCUMENT_NOT_OPEN_ITEM_ELIGIBLE','status':doc['status']})
    ensure_open_item(conn,doc['module'],doc)
    return get_open_item(conn,source_ref,source_type)


def validate_application(conn,source_ref,source_type,amount,job_id,currency):
    if not enabled():return {}
    typ,item=require_open_item(conn,source_ref,source_type)
    if str(item['status']).upper()!='OPEN':
        raise HTTPException(422,{'code':'OPEN_ITEM_NOT_SETTLEABLE','status':item['status']})
    if job_id is not None and item['job_id']!=job_id:
        raise HTTPException(422,{'code':'OPEN_ITEM_JOB_MISMATCH'})
    if str(item['currency']).upper()!=str(currency or '').upper():
        raise HTTPException(422,{'code':'OPEN_ITEM_CURRENCY_MISMATCH'})
    a=_num(amount)
    if a<=0:raise HTTPException(422,{'code':'APPLICATION_AMOUNT_REQUIRED'})
    if a>float(item['outstanding'])+0.005:
        raise HTTPException(422,{'code':'OVER_APPLICATION_BLOCKED','outstanding':float(item['outstanding']),'requested':a})
    return {'type':typ,'open_item_id':item['id'],'source_ref':source_ref,'outstanding_before':float(item['outstanding'])}


def apply_application(conn,source_ref,source_type,amount,job_id,currency):
    meta=validate_application(conn,source_ref,source_type,amount,job_id,currency)
    if not meta:return {}
    table='gl_ar_open_items' if meta['type']=='AR' else 'gl_ap_open_items'
    new=round(meta['outstanding_before']-_num(amount),2)
    status='CLOSED' if new<=0.005 else 'OPEN'
    conn.execute(f'UPDATE {table} SET outstanding=?,status=? WHERE id=?',(max(new,0),status,meta['open_item_id']))
    meta.update({'outstanding_after':max(new,0),'status_after':status})
    return meta


def reverse_application(conn,source_ref,source_type,amount):
    if not enabled():return {}
    typ,item=get_open_item(conn,source_ref,source_type)
    if not item:raise HTTPException(422,{'code':'OPEN_ITEM_NOT_FOUND_FOR_REVERSAL'})
    table='gl_ar_open_items' if typ=='AR' else 'gl_ap_open_items'
    new=round(min(float(item['original_amount']),float(item['outstanding'])+_num(amount)),2)
    conn.execute(f"UPDATE {table} SET outstanding=?,status='OPEN' WHERE id=?",(new,item['id']))
    return {'type':typ,'open_item_id':item['id'],'outstanding_before':float(item['outstanding']),'outstanding_after':new,'status_after':'OPEN'}


def apply_write_off(conn,source_ref,source_type,reason):
    if not enabled():return {}
    if not reason:raise HTTPException(422,{'code':'WRITE_OFF_REASON_REQUIRED'})
    typ,item=require_open_item(conn,source_ref,source_type)
    if str(item['status']).upper()!='OPEN':raise HTTPException(422,{'code':'OPEN_ITEM_NOT_WRITEOFF_ELIGIBLE','status':item['status']})
    table='gl_ar_open_items' if typ=='AR' else 'gl_ap_open_items'
    conn.execute(f"UPDATE {table} SET outstanding=0,status='WRITTEN_OFF' WHERE id=?",(item['id'],))
    return {'type':typ,'open_item_id':item['id'],'written_off_amount':float(item['outstanding']),'status_after':'WRITTEN_OFF'}


def reopen_open_item(conn,source_ref,source_type,amount,reason):
    if not enabled():return {}
    if not reason:raise HTTPException(422,{'code':'REOPEN_REASON_REQUIRED'})
    typ,item=get_open_item(conn,source_ref,source_type)
    if not item:raise HTTPException(422,{'code':'OPEN_ITEM_NOT_FOUND'})
    if str(item['status']).upper() not in {'CLOSED','WRITTEN_OFF'}:
        raise HTTPException(422,{'code':'OPEN_ITEM_NOT_REOPENABLE','status':item['status']})
    target=_num(amount)
    if target<=0 or target>float(item['original_amount'])+0.005:
        raise HTTPException(422,{'code':'INVALID_REOPEN_OUTSTANDING'})
    table='gl_ar_open_items' if typ=='AR' else 'gl_ap_open_items'
    conn.execute(f"UPDATE {table} SET outstanding=?,status='OPEN' WHERE id=?",(target,item['id']))
    return {'type':typ,'open_item_id':item['id'],'outstanding_after':target,'status_after':'OPEN'}


def apply_document_correction(conn,module,record):
    if not enabled() or module not in {'credit-note','debit-note'}:return {}
    fields=_fields(record)
    source_ref=record['source_ref'] or fields.get('Invoice Ref') or fields.get('Bill Ref') or fields.get('Source Ref')
    if not source_ref:return {}
    amount=_num(fields.get('Amount'))
    if amount<=0:raise HTTPException(422,{'code':'CORRECTION_AMOUNT_REQUIRED'})
    source_type='INVOICE' if (fields.get('Invoice Ref') or str(record['source_type'] or '').lower()=='invoice') else 'BILL'
    if not ((module=='credit-note' and source_type=='INVOICE') or (module=='debit-note' and source_type=='BILL')):
        return {'applied':False,'reason':'NO_AUTOMATIC_OPEN_ITEM_REDUCTION_FOR_DIRECTION'}
    meta=validate_application(conn,source_ref,source_type,amount,record['job_id'],fields.get('Currency') or 'USD')
    table='gl_ar_open_items' if meta['type']=='AR' else 'gl_ap_open_items'
    new=round(meta['outstanding_before']-amount,2)
    status='CLOSED' if new<=0.005 else 'OPEN'
    conn.execute(f'UPDATE {table} SET outstanding=?,status=? WHERE id=?',(max(new,0),status,meta['open_item_id']))
    meta.update({'applied':True,'correction_module':module,'outstanding_after':max(new,0),'status_after':status})
    return meta
