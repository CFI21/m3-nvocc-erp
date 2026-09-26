from fastapi import APIRouter,HTTPException,Header,Query
from pydantic import BaseModel,Field
from typing import Any,Optional
from pathlib import Path
import json,datetime,hashlib,uuid
from .db import connect,tx,backend_name
from .json_recovery import load_json_or_recover_arrays
from .gl import assert_period_postable,next_voucher,fx_rate
from .admin import session as iam_session
from .clx034_smart_approval_fast_track import enforce_treasury_action

HERE=Path(__file__).resolve().parent
_TREASURY_META, META_RECOVERED = load_json_or_recover_arrays(
    HERE/'treasury_meta.json',
    ['modules'],
)
META=_TREASURY_META['modules']
MODULES={x['key']:x for x in META}
READ_ONLY_GROUPS={'overview','work-queues','reports'}
READ_ONLY_MODULES={x['key'] for x in META if x.get('group') in READ_ONLY_GROUPS}
router=APIRouter(prefix='/api/v1/treasury',tags=['Treasury / AR-AP Settlement'])
ROLE_PERMS={
 'ADMIN':set('view create edit approve release reverse reconcile write_off'.split()),
 'FINANCE':set('view create edit approve release reverse reconcile write_off'.split()),
 'GL_MANAGER':set('view create edit approve release reverse reconcile'.split()),
 'TREASURY_MANAGER':set('view create edit approve release reverse reconcile'.split()),
 'AR_ACCOUNTANT':set('view create edit approve write_off'.split()),
 'AP_ACCOUNTANT':set('view create edit approve write_off'.split()),
 'GL_ACCOUNTANT':set('view create edit'.split()),
 'AUDITOR':set('view'.split()), 'VIEWER':set('view'.split()),
 'OPS':set('view'.split()), 'DOCS':set(), 'AGENT':set()
}
class CreateBody(BaseModel):
    job_ref:Optional[str]=Field(default=None,pattern=r'^\d{5}$')
    external_ref:Optional[str]=None
    fields:dict[str,Any]=Field(default_factory=dict)
class UpdateBody(BaseModel):
    version:int=Field(ge=1)
    fields:dict[str,Any]
class ActionBody(BaseModel):
    version:int=Field(ge=1)
    reason:Optional[str]=None

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def require(m):
    if m not in MODULES:raise HTTPException(404,'Unknown treasury module')
def require_mutable(m):
    require(m)
    if m in READ_ONLY_MODULES:raise HTTPException(405,{'code':'TREASURY_READ_ONLY_SCREEN','module':m})
def actor(r,a='view'):
    r=r.upper()
    if r not in ROLE_PERMS or a not in ROLE_PERMS[r]:raise HTTPException(403,f'Role cannot {a} treasury')
    return r
def ser(r):
    d=dict(r);d['fields']=json.loads(d.pop('payload_json'));return d
def audit(c,role,actor_id,action,module,rid=None,jid=None,before=None,after=None,meta=None):
    c.execute('INSERT INTO audit_events(event_id,ts,actor_role,actor_scope,action,module,transaction_id,job_id,before_json,after_json,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),now(),role,actor_id,action,'treasury:'+module,rid,jid,json.dumps(before,sort_keys=True) if before is not None else None,json.dumps(after,sort_keys=True) if after is not None else None,json.dumps(meta or {},sort_keys=True)))
def exception(c,code,module,rid,jid,detail):
    c.execute('INSERT INTO exception_events(event_id,ts,code,severity,module,transaction_id,job_id,detail,resolved) VALUES(?,?,?,?,?,?,?,?,0)',(str(uuid.uuid4()),now(),code,'BLOCKING','treasury:'+module,rid,jid,detail))
def jid(c,jr):
    if not jr:return None
    r=c.execute('SELECT id FROM jobs WHERE job_ref=?',(jr,)).fetchone()
    if not r:raise HTTPException(422,{'code':'UNKNOWN_JOB'})
    return r['id']
def getrec(c,m,rid):
    r=c.execute('SELECT tr.*,j.job_ref FROM treasury_records tr LEFT JOIN jobs j ON j.id=tr.job_id WHERE tr.module=? AND tr.id=?',(m,rid)).fetchone()
    if not r:raise HTTPException(404,'Treasury record not found')
    return r
def number(f,k):
    try:return float(f.get(k) or 0)
    except:return None
def validate(module,f):
    e=[]
    if module in {'customer-receipt-allocation','supplier-carrier-payment-allocation'}:
        a=number(f,'Allocated Amount')
        if a is None or a<=0:e.append('ALLOCATION_MUST_BE_POSITIVE')
        if not (f.get('Invoice Ref') or f.get('Bill Ref')):e.append('SOURCE_DOCUMENT_REQUIRED')
    if module=='partial-settlement':
        src=number(f,'Source Amount');sett=number(f,'Settlement Amount');bal=number(f,'Balance')
        if None in (src,sett,bal):e.append('INVALID_SETTLEMENT_AMOUNT')
        elif sett<=0 or sett>src or abs((src-sett)-bal)>.01:e.append('PARTIAL_SETTLEMENT_BALANCE_INVALID')
    if module=='multi-invoice-settlement':
        total=number(f,'Total Source Amount');sett=number(f,'Settlement Amount')
        if None in (total,sett) or sett<=0 or sett>total:e.append('MULTI_SETTLEMENT_AMOUNT_INVALID')
        try:
            if int(f.get('Allocation Count') or 0)<2:e.append('MULTI_SETTLEMENT_REQUIRES_MULTIPLE_DOCUMENTS')
        except:e.append('INVALID_ALLOCATION_COUNT')
    if module=='multi-currency-settlement':
        src=number(f,'Source Amount');rate=number(f,'FX Rate');sett=number(f,'Settlement Amount')
        if None in (src,rate,sett) or rate<=0 or abs(src*rate-sett)>.02:e.append('FX_SETTLEMENT_MISMATCH')
    if module in {'customer-refunds','supplier-refunds','bank-charges','bank-interest','petty-cash'}:
        a=number(f,'Amount')
        if a is None or a<=0:e.append('AMOUNT_MUST_BE_POSITIVE')
    if module in {'bank-transfer','inter-bank-transfer'}:
        fr=f.get('From Account') or f.get('From Bank');to=f.get('To Account') or f.get('To Bank')
        if not fr or not to or fr==to:e.append('TRANSFER_ACCOUNTS_MUST_DIFFER')
        a=number(f,'Amount')
        if a is None or a<=0:e.append('AMOUNT_MUST_BE_POSITIVE')
    if module=='cash-denomination':
        counted=number(f,'Counted Total');book=number(f,'Book Balance');diff=number(f,'Difference')
        if None in (counted,book,diff) or abs((counted-book)-diff)>.01:e.append('CASH_COUNT_DIFFERENCE_MISMATCH')
    if module=='post-dated-cheques' and str(f.get('Cheque Date') or '')<='2026-09-23':e.append('PDC_DATE_MUST_BE_FUTURE')
    if module=='payment-batches':
        a=number(f,'Total Amount')
        if a is None or a<=0:e.append('BATCH_TOTAL_MUST_BE_POSITIVE')
    return e

def amount_from(fields):
    for k in ('Amount','Allocated Amount','Settlement Amount','Total Amount','Net Amount','Value','Outstanding','Balance'):
        if fields.get(k) not in (None,''):
            try:return float(fields[k])
            except:continue
    return 0.0

def treasury_account_move(c,module,amount,reversal=False):
    sign=0
    if module in {'bank-charges','supplier-carrier-payment-allocation','advance-payments','customer-refunds','payment-batches','petty-cash'}:sign=-1
    elif module in {'bank-interest','customer-receipt-allocation','advance-receipts','supplier-refunds'}:sign=1
    if reversal:sign=-sign
    if sign:
        ref='CASH-USD-RTM' if module=='petty-cash' else 'BANK-USD-01'
        c.execute('UPDATE treasury_accounts SET current_balance=current_balance+?,version=version+1 WHERE account_ref=?',(sign*amount,ref))

def gl_post(c,r,module,actor_id,reversal=False):
    p=json.loads(r['payload_json'])
    date=p.get('Date') or p.get('Release Date') or p.get('Refund Date') or '2026-09-23'
    assert_period_postable(c,date)
    amount=float(r['amount'] or amount_from(p))
    if amount<=0:raise HTTPException(422,{'code':'POST_AMOUNT_REQUIRED'})
    mapping={
      'customer-receipt-allocation':('1100','1200','RC'),'advance-receipts':('1100','1200','RC'),'customer-refunds':('1200','1100','PV'),
      'supplier-carrier-payment-allocation':('2000','1100','PV'),'advance-payments':('2000','1100','PV'),'supplier-refunds':('1100','2000','RC'),
      'petty-cash':('6000','1000','PV'),'bank-charges':('6000','1100','JV'),'bank-interest':('1100','4000','JV'),
      'bank-transfer':('1100','1000','JV'),'inter-bank-transfer':('1100','1000','JV'),'payment-batches':('2000','1100','PV')
    }
    dr,cr,vtype=mapping.get(module,('1100','1200','JV'))
    configured=c.execute("SELECT debit_account_code,credit_account_code FROM gl_account_mappings WHERE source_module=? AND event_type='POST' AND active=1 ORDER BY id DESC LIMIT 1",(module,)).fetchone()
    if configured:dr,cr=configured['debit_account_code'],configured['credit_account_code']
    if reversal:dr,cr=cr,dr
    vt='RV' if reversal else vtype
    vno=next_voucher(c,vt);amt=round(amount,2)
    rate=fx_rate(c,r['currency'],date)
    base_amt=round(amt*rate,2)
    cur=c.execute('INSERT INTO gl_vouchers(gl_record_id,voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,posted_at,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(None,vno,vt,date,r['currency'],'Posted','TREASURY',r['external_ref'],r['job_id'],amt,amt,1,now(),now(),actor_id,rate,'USD',base_amt,base_amt))
    vid=cur.lastrowid
    c.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(vid,1,dr,amt,0,('Reversal ' if reversal else '')+r['external_ref'],r['job_id']))
    c.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(vid,2,cr,0,amt,('Reversal ' if reversal else '')+r['external_ref'],r['job_id']))
    c.execute('INSERT INTO treasury_gl_links(treasury_record_id,voucher_id,link_type,source_ref) VALUES(?,?,?,?)',(r['id'],vid,'REVERSAL' if reversal else 'POSTING',vno))
    treasury_account_move(c,module,amt,reversal)
    return vid,vno

@router.get('/health')
def health():return {'project':'M3 NVOCC ERP','baseline':'M3-CLX007-ACCEPTED-20260923-009','module':'Treasury + AR/AP Settlement','database':backend_name(),'production_promoted':False,'live_bank_api':False,'real_payment_execution':False}
@router.get('/modules')
def modules():return META
@router.get('/cash-position')
def cash_position(x_role:str=Header('AUDITOR')):
    actor(x_role);c=connect();rows=[dict(x) for x in c.execute('SELECT account_ref,account_type,currency,current_balance,reserved_balance,ROUND(current_balance-reserved_balance,2) net_available,status FROM treasury_accounts ORDER BY account_ref')];c.close();return rows
@router.get('/bank-feed/exceptions')
def bank_exceptions(x_role:str=Header('AUDITOR')):
    actor(x_role);c=connect();rows=[dict(x) for x in c.execute("SELECT * FROM treasury_bank_feed_items WHERE match_status IN ('UNMATCHED','EXCEPTION') ORDER BY txn_date,id")];c.close();return rows
@router.get('/job/{job_ref}/links')
def job_links(job_ref:str,x_role:str=Header('AUDITOR')):
    actor(x_role);c=connect();j=c.execute('SELECT id FROM jobs WHERE job_ref=?',(job_ref,)).fetchone()
    if not j:c.close();raise HTTPException(404,'Unknown job')
    tr=[dict(x) for x in c.execute('SELECT id,module,external_ref,status,amount,currency FROM treasury_records WHERE job_id=? ORDER BY module',(j['id'],))]
    gl=[dict(x) for x in c.execute('SELECT l.treasury_record_id,l.link_type,l.source_ref,v.voucher_no,v.status voucher_status FROM treasury_gl_links l LEFT JOIN gl_vouchers v ON v.id=l.voucher_id JOIN treasury_records t ON t.id=l.treasury_record_id WHERE t.job_id=? ORDER BY l.id',(j['id'],))]
    c.close();return {'job_ref':job_ref,'treasury_records':tr,'gl_links':gl}
@router.get('/{module}')
def list_records(module:str,q:str=Query(''),status:str=Query(''),job_ref:str=Query(''),x_role:str=Header('VIEWER')):
    require(module);actor(x_role);c=connect();where=['tr.module=?'];args=[module]
    if status:where.append('tr.status=?');args.append(status)
    if job_ref:where.append('j.job_ref=?');args.append(job_ref)
    if q:where.append("(tr.external_ref LIKE ? OR tr.payload_json LIKE ? OR COALESCE(tr.party_name,'') LIKE ?)");args += [f'%{q}%']*3
    rows=[ser(x) for x in c.execute('SELECT tr.*,j.job_ref FROM treasury_records tr LEFT JOIN jobs j ON j.id=tr.job_id WHERE '+' AND '.join(where)+' ORDER BY tr.id',args)];c.close();return {'module':MODULES[module],'records':rows,'count':len(rows)}
@router.get('/{module}/{rid}')
def one(module:str,rid:int,x_role:str=Header('VIEWER')):
    require(module);actor(x_role);c=connect();r=getrec(c,module,rid);d=ser(r);c.close();return d
@router.post('/{module}',status_code=201)
def create(module:str,b:CreateBody,idempotency_key:Optional[str]=Header(None,alias='Idempotency-Key'),x_role:str=Header('VIEWER'),x_actor_id:str=Header('maker-user',alias='X-Actor-Id'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_mutable(module);role=actor(x_role,'create')
    if x_m3_session:
        c0=connect();s0=iam_session(c0,x_m3_session);x_actor_id=s0['user_ref'];c0.close()
    errs=validate(module,b.fields)
    if errs:raise HTTPException(422,{'code':'VALIDATION_FAILED','errors':errs})
    c=connect();tx(c)
    try:
        if idempotency_key:
            old=c.execute('SELECT * FROM idempotency_keys WHERE actor_role=? AND idem_key=?',(role,idempotency_key,)).fetchone()
            if old:c.execute('ROLLBACK');return json.loads(old['response_json'])
        j=jid(c,b.job_ref);ext=b.external_ref or b.fields.get(MODULES[module]['fields'][0]) or f'M3-{module[:8].upper()}-{uuid.uuid4().hex[:8].upper()}'
        amt=amount_from(b.fields);curr=b.fields.get('Currency') or b.fields.get('Settlement Currency') or 'USD';stat=b.fields.get('Status') or 'Draft'
        sensitive={'supplier-carrier-payment-allocation','payment-batches','advance-payments','customer-refunds','bank-transfer','inter-bank-transfer'}
        source_ref=b.fields.get('Payment Ref') or b.fields.get('Bill Ref') or b.fields.get('Batch / Payment Ref') or b.fields.get('Payments') or b.fields.get('Reference')
        if module in sensitive and source_ref:
            duplicate=c.execute("SELECT id,external_ref FROM treasury_records WHERE module=? AND source_ref=? AND ABS(amount-?)<0.005 AND currency=? AND status NOT IN ('Reversed','Cancelled') ORDER BY id LIMIT 1",(module,source_ref,amt,curr)).fetchone()
            if duplicate:raise HTTPException(409,{'code':'DUPLICATE_TREASURY_SOURCE','existing_ref':duplicate['external_ref']})
        cur=c.execute('INSERT INTO treasury_records(module,external_ref,job_id,party_type,party_name,currency,amount,status,version,maker_id,source_type,source_ref,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)',(module,ext,j,None,b.fields.get('Party') or b.fields.get('Customer') or b.fields.get('Supplier / Carrier'),curr,amt,stat,x_actor_id,None,source_ref,json.dumps(b.fields),now(),now()))
        r=getrec(c,module,cur.lastrowid);out=ser(r);audit(c,role,x_actor_id,'CREATE',module,r['id'],j,None,out)
        if idempotency_key:c.execute('INSERT INTO idempotency_keys(actor_role,idem_key,request_hash,response_json,status_code,created_at) VALUES(?,?,?,?,201,?)',(role,idempotency_key,hashlib.sha256(json.dumps(b.model_dump(),sort_keys=True).encode()).hexdigest(),json.dumps(out),now()))
        c.execute('COMMIT');return out
    except HTTPException:c.execute('ROLLBACK');raise
    except Exception as e:c.execute('ROLLBACK');raise HTTPException(409,{'code':'DUPLICATE_OR_CONSTRAINT','detail':str(e)})
    finally:c.close()
@router.put('/{module}/{rid}')
def update(module:str,rid:int,b:UpdateBody,x_role:str=Header('VIEWER'),x_actor_id:str=Header('maker-user',alias='X-Actor-Id'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_mutable(module);role=actor(x_role,'edit')
    if x_m3_session:
        c0=connect();s0=iam_session(c0,x_m3_session);x_actor_id=s0['user_ref'];c0.close()
    errs=validate(module,b.fields)
    if errs:raise HTTPException(422,{'code':'VALIDATION_FAILED','errors':errs})
    c=connect();tx(c)
    try:
        r=getrec(c,module,rid)
        if r['version']!=b.version:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        before=ser(r);stat=b.fields.get('Status') or r['status'];amt=amount_from(b.fields) or r['amount']
        cur=c.execute('UPDATE treasury_records SET payload_json=?,status=?,amount=?,version=version+1,updated_at=? WHERE id=? AND version=?',(json.dumps(b.fields),stat,amt,now(),rid,b.version))
        if cur.rowcount!=1:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT'})
        aft=ser(getrec(c,module,rid));audit(c,role,x_actor_id,'UPDATE',module,rid,r['job_id'],before,aft);c.execute('COMMIT');return aft
    except HTTPException:c.execute('ROLLBACK');raise
    finally:c.close()
@router.post('/{module}/{rid}/actions/{action}')
def action(module:str,rid:int,action:str,b:ActionBody,x_role:str=Header('VIEWER'),x_actor_id:str=Header('actor-user',alias='X-Actor-Id'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_mutable(module);action=action.lower().replace('-','_');role=actor(x_role,action if action in {'approve','release','reverse','reconcile','write_off'} else 'edit')
    if action in {'approve','release','reverse','write_off'}: enforce_treasury_action(module,rid,action,x_m3_session,b.version)
    if x_m3_session:
        c0=connect();s0=iam_session(c0,x_m3_session);x_actor_id=s0['user_ref'];c0.close()
    c=connect();tx(c);r=None
    try:
        r=getrec(c,module,rid)
        if r['version']!=b.version:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        p=json.loads(r['payload_json']);before=ser(r);new=r['status']
        if action=='approve':
            if r['maker_id']==x_actor_id:raise HTTPException(422,{'code':'MAKER_CANNOT_APPROVE_OWN_TRANSACTION'})
            new='Approved';c.execute('UPDATE treasury_records SET checker_id=? WHERE id=?',(x_actor_id,rid))
            if module=='payment-batches':c.execute('UPDATE treasury_payment_batches SET checker_id=?,status=?,approved_at=?,version=version+1 WHERE record_id=?',(x_actor_id,'Approved',now(),rid))
        elif action=='release':
            if r['status']!='Approved':raise HTTPException(422,{'code':'APPROVAL_REQUIRED_BEFORE_RELEASE'})
            if not r['checker_id']:raise HTTPException(422,{'code':'CHECKER_REQUIRED'})
            gl_post(c,r,module,x_actor_id,False);new='Released'
            if module=='payment-batches':c.execute('UPDATE treasury_payment_batches SET released_by=?,status=?,released_at=?,version=version+1 WHERE record_id=?',(x_actor_id,'Released',now(),rid))
        elif action=='reverse':
            link=c.execute("SELECT * FROM treasury_gl_links WHERE treasury_record_id=? AND link_type='POSTING' ORDER BY id DESC LIMIT 1",(rid,)).fetchone()
            if not link:raise HTTPException(422,{'code':'POSTED_TREASURY_TRANSACTION_REQUIRED'})
            rev_vid,rev_vno=gl_post(c,r,module,x_actor_id,True);new='Reversed'
            rev_ext=r['external_ref']+'-REV';rev_payload=dict(p);rev_payload['Status']='Reversal';rev_payload['Reversal Of']=r['external_ref']
            cur=c.execute('INSERT INTO treasury_records(module,external_ref,job_id,party_type,party_name,currency,amount,status,version,maker_id,checker_id,source_type,source_ref,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)',(module,rev_ext,r['job_id'],r['party_type'],r['party_name'],r['currency'],-abs(r['amount']),'Reversal',x_actor_id,x_actor_id,'REVERSAL',r['external_ref'],json.dumps(rev_payload),now(),now()))
            c.execute('INSERT INTO treasury_reversals(original_record_id,reversal_record_id,original_voucher_id,reversal_voucher_id,reason,ts) VALUES(?,?,?,?,?,?)',(rid,cur.lastrowid,link['voucher_id'],rev_vid,b.reason or 'Treasury reversal',now()))
        elif action=='write_off':
            if not ('customer' in module or 'supplier' in module or 'carrier' in module):raise HTTPException(422,{'code':'WRITE_OFF_ONLY_AR_AP'})
            new='Written Off'
        elif action=='reconcile':
            if module=='cash-denomination' and abs(number(p,'Difference') or 0)>.01:raise HTTPException(422,{'code':'CASH_DIFFERENCE_MUST_BE_ZERO'})
            if module=='bank-reconciliation-exception-queue':raise HTTPException(422,{'code':'EXCEPTION_MUST_BE_RESOLVED_VIA_APPROVED_MATCH_WORKFLOW'})
            new='Reconciled'
        else:raise HTTPException(422,{'code':'UNSUPPORTED_ACTION'})
        p['Status']=new;cur=c.execute('UPDATE treasury_records SET status=?,payload_json=?,version=version+1,updated_at=? WHERE id=? AND version=?',(new,json.dumps(p),now(),rid,b.version))
        if cur.rowcount!=1:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT'})
        aft=ser(getrec(c,module,rid));audit(c,role,x_actor_id,action.upper(),module,rid,r['job_id'],before,aft,{'reason':b.reason});c.execute('COMMIT');return {'record':aft}
    except HTTPException as e:
        c.execute('ROLLBACK')
        if action=='release':
            c2=connect();tx(c2)
            try:
                code=e.detail.get('code') if isinstance(e.detail,dict) else 'RELEASE_BLOCKED';exception(c2,code,module,rid,r['job_id'] if r else None,str(e.detail));c2.execute('COMMIT')
            finally:c2.close()
        raise
    finally:c.close()