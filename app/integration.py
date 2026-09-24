from fastapi import APIRouter,HTTPException,Header,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from typing import Any,Optional,List
from pathlib import Path
import json,datetime,hashlib,hmac,uuid,re
from .db import connect,backend_name

HERE=Path(__file__).resolve().parent
META=json.loads((HERE/'integration_meta.json').read_text())['modules']
MODULES={x['key']:x for x in META}
router=APIRouter(prefix='/api/v1/integration',tags=['Finance Integration Sandbox + Security'])
MOCK_WEBHOOK_SECRET=b'm3-sandbox-webhook-secret'
CSRF_TOKEN='sandbox-csrf'
REAUTH_TOKEN='sandbox-reauth-ok'
ALLOWED_ORIGINS={'http://m3.test','http://localhost','http://127.0.0.1'}
ROLE_PERMS={
 'ADMIN':set('view simulate import retry reconcile release security'.split()),
 'FINANCE':set('view simulate import reconcile'.split()),
 'TREASURY_MANAGER':set('view simulate import retry reconcile release'.split()),
 'GL_MANAGER':set('view import reconcile'.split()),
 'SECURITY_ADMIN':set('view simulate retry security'.split()),
 'AUDITOR':set('view'.split()), 'VIEWER':set('view'.split()),
 'OPS':set('view'.split()), 'DOCS':set(), 'AGENT':set()
}

class ProviderSimulation(BaseModel):
    event_type:str=Field(min_length=2,max_length=64)
    mode:str=Field(pattern=r'^(success|failure|timeout)$')
    payload:dict[str,Any]=Field(default_factory=dict)
class RetryBody(BaseModel):
    force_success:bool=False
class BankLine(BaseModel):
    line_ref:str=Field(min_length=1,max_length=64)
    txn_date:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    amount:float
    currency:str=Field(pattern=r'^[A-Z]{3}$')
    description:str=Field(min_length=1,max_length=240)
class BankImportBody(BaseModel):
    import_ref:str=Field(min_length=3,max_length=80)
    bank_account_ref:str=Field(min_length=3,max_length=80)
    source_name:str=Field(min_length=1,max_length=120)
    lines:List[BankLine]=Field(min_length=1,max_length=500)
class ManualMatchBody(BaseModel):
    source_ref:str=Field(min_length=2,max_length=100)
class PaymentAction(BaseModel):
    version:int=Field(ge=1)
    reason:Optional[str]=Field(default=None,max_length=240)


def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha_bytes(b:bytes): return hashlib.sha256(b).hexdigest()
def sha_text(s:str): return hashlib.sha256(s.encode()).hexdigest()
def role_can(role,action): return action in ROLE_PERMS.get(role.upper(),set())
def require_role(role,action='view'):
    r=role.upper()
    if r not in ROLE_PERMS or not role_can(r,action): raise HTTPException(403,f'Role cannot {action} integration sandbox')
    return r

def mask_string(s):
    s=str(s)
    if len(s)<=4:return '****'
    return '****'+s[-4:]
def redact(obj,key=''):
    if isinstance(obj,dict): return {k:redact(v,k) for k,v in obj.items()}
    if isinstance(obj,list): return [redact(v,key) for v in obj]
    lk=key.lower()
    if any(x in lk for x in ('secret','password','token','credential','signature')): return '[REDACTED]'
    if any(x in lk for x in ('account','iban','beneficiary')): return mask_string(obj)
    if any(x in lk for x in ('amount','balance','limit')): return '[MASKED]'
    if 'email' in lk:
        s=str(obj); return (s[:1]+'***@***') if '@' in s else '[MASKED]'
    return obj

def security_audit(c,actor_id,role,action,resource,outcome,detail=None,correlation_id=None):
    corr=correlation_id or str(uuid.uuid4())
    c.execute('INSERT INTO security_audit_events(event_id,ts,actor_id,actor_role,action,resource,outcome,correlation_id,detail_redacted) VALUES(?,?,?,?,?,?,?,?,?)',
      (str(uuid.uuid4()),now(),actor_id,role,action,resource,outcome,corr,json.dumps(redact(detail or {}),sort_keys=True)))
    return corr

def check_csrf(request:Request,csrf:Optional[str]):
    origin=request.headers.get('origin')
    if origin and origin not in ALLOWED_ORIGINS: raise HTTPException(403,{'code':'CORS_ORIGIN_DENIED'})
    if csrf!=CSRF_TOKEN: raise HTTPException(403,{'code':'CSRF_TOKEN_REQUIRED'})

def rate_limit(c,key,limit=20):
    minute=datetime.datetime.now(datetime.timezone.utc).replace(second=0,microsecond=0).isoformat()
    r=c.execute('SELECT * FROM rate_limit_windows WHERE actor_key=?',(key,)).fetchone()
    if not r or r['window_start']!=minute:
        c.execute('INSERT INTO rate_limit_windows(actor_key,window_start,request_count) VALUES(?,?,1) ON CONFLICT(actor_key) DO UPDATE SET window_start=excluded.window_start,request_count=1',(key,minute)); return 1
    count=r['request_count']+1
    c.execute('UPDATE rate_limit_windows SET request_count=? WHERE actor_key=?',(count,key))
    if count>limit: raise HTTPException(429,{'code':'RATE_LIMIT_EXCEEDED','limit':limit})
    return count

def session_auth(c,role,actor_id,session_token,require_reauth=False,reauth=None):
    if not session_token: raise HTTPException(401,{'code':'SESSION_REQUIRED'})
    r=c.execute('SELECT * FROM security_sessions WHERE token_hash=? AND active=1',(sha_text(session_token),)).fetchone()
    if not r or r['actor_id']!=actor_id or r['role']!=role: raise HTTPException(401,{'code':'INVALID_SESSION'})
    if r['expires_at']<now(): raise HTTPException(401,{'code':'SESSION_EXPIRED'})
    if require_reauth and reauth!=REAUTH_TOKEN: raise HTTPException(401,{'code':'REAUTH_REQUIRED'})
    return r

def provider(c,key):
    r=c.execute('SELECT * FROM finance_providers WHERE provider_key=?',(key,)).fetchone()
    if not r: raise HTTPException(404,'Unknown sandbox provider')
    return r

def payment_row(c,pid):
    r=c.execute('''SELECT p.*,j.job_ref,pb.batch_no,pb.status batch_status,v.voucher_no
      FROM sandbox_payment_requests p JOIN jobs j ON j.id=p.job_id JOIN treasury_payment_batches pb ON pb.id=p.batch_id
      LEFT JOIN gl_vouchers v ON v.id=p.voucher_id WHERE p.id=?''',(pid,)).fetchone()
    if not r: raise HTTPException(404,'Sandbox payment request not found')
    return r

def payment_gates(c,r):
    gates=[]
    if r['batch_status'] not in ('Approved','Released'): gates.append('BATCH_NOT_APPROVED')
    # Accounting period must be open
    p=c.execute('''SELECT p.status FROM gl_periods p JOIN gl_fiscal_years y ON y.id=p.fiscal_year_id
      WHERE ? BETWEEN p.start_date AND p.end_date ORDER BY y.fiscal_year DESC LIMIT 1''',(r['payment_date'],)).fetchone()
    if not p or p['status']!='OPEN': gates.append('ACCOUNTING_PERIOD_NOT_OPEN')
    if not r['beneficiary_validated']: gates.append('BENEFICIARY_NOT_VALIDATED')
    if float(r['amount'])>float(r['payment_limit']): gates.append('PAYMENT_LIMIT_EXCEEDED')
    if r['gl_control_status']!='BALANCED': gates.append('GL_NOT_BALANCED')
    if not r['voucher_id']: gates.append('GL_VOUCHER_MISSING')
    else:
        sums=c.execute('SELECT COALESCE(SUM(debit),0) d,COALESCE(SUM(credit),0) c FROM gl_voucher_lines WHERE voucher_id=?',(r['voucher_id'],)).fetchone()
        if abs(float(sums['d'])-float(sums['c']))>.005 or float(sums['d'])<=0: gates.append('GL_NOT_BALANCED')
        v=c.execute('SELECT status FROM gl_vouchers WHERE id=?',(r['voucher_id'],)).fetchone()
        if not v or v['status']!='Posted': gates.append('GL_NOT_POSTED')
    if c.execute('SELECT 1 FROM sandbox_payment_releases WHERE payment_request_id=?',(r['id'],)).fetchone(): gates.append('DUPLICATE_PAYMENT_RELEASE')
    if r['approval_status']!='APPROVED': gates.append('PAYMENT_NOT_APPROVED')
    return sorted(set(gates))

@router.get('/health')
def health():
    return {'project':'M3 NVOCC ERP','baseline':'M3-CLX008-ACCEPTED-20260924-010','module':'Finance Integration Sandbox + Security','database':backend_name(),'sandbox_only':True,'live_credentials':False,'live_bank_connection':False,'real_money_movement':False,'production_promoted':False}

@router.get('/modules')
def modules(): return META

@router.get('/providers/state')
def provider_state(x_role:str=Header('AUDITOR')):
    require_role(x_role); c=connect(); rows=[]
    for r in c.execute('SELECT * FROM finance_providers ORDER BY id'):
        d=dict(r); d['credential_ref']='secret://sandbox/[REDACTED]'; rows.append(d)
    c.close(); return rows

@router.get('/{module}')
def list_records(module:str,x_role:str=Header('AUDITOR'),x_office_scope:Optional[str]=Header(None),x_country_scope:Optional[str]=Header(None)):
    if module not in MODULES: raise HTTPException(404,'Unknown integration module')
    require_role(x_role); c=connect(); q='''SELECT ir.*,j.job_ref,p.provider_key,p.provider_type FROM integration_records ir JOIN jobs j ON j.id=ir.job_id JOIN finance_providers p ON p.id=ir.provider_id WHERE ir.module=?'''; args=[module]
    if x_office_scope: q+=' AND ir.office_scope=?'; args.append(x_office_scope)
    if x_country_scope: q+=' AND ir.country_scope=?'; args.append(x_country_scope)
    q+=' ORDER BY ir.id'; out=[]
    for r in c.execute(q,args):
        d=dict(r); d['fields']=json.loads(d.pop('payload_json')); out.append(d)
    c.close(); return {'module':module,'count':len(out),'records':out}

@router.post('/providers/{provider_key}/simulate')
async def simulate_provider(provider_key:str,body:ProviderSimulation,request:Request,x_role:str=Header('VIEWER'),x_actor_id:str=Header('unknown'),x_csrf_token:Optional[str]=Header(None),idempotency_key:Optional[str]=Header(None,alias='Idempotency-Key')):
    role=require_role(x_role,'simulate'); check_csrf(request,x_csrf_token); c=connect(); rate_limit(c,'simulate:'+x_actor_id,20); p=provider(c,provider_key)
    if p['circuit_state']=='OPEN': security_audit(c,x_actor_id,role,'PROVIDER_CALL_BLOCKED',provider_key,'DENY',{'reason':'CIRCUIT_OPEN'}); c.close(); raise HTTPException(503,{'code':'CIRCUIT_OPEN'})
    raw=await request.body(); req_hash=sha_bytes(raw); idem=idempotency_key or str(uuid.uuid4())
    old=c.execute('SELECT * FROM integration_events WHERE provider_id=? AND idempotency_key=?',(p['id'],idem)).fetchone()
    if old:
        if old['request_hash']!=req_hash: c.close(); raise HTTPException(409,{'code':'IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST'})
        d=dict(old); c.close(); return d
    ref='EVT-'+uuid.uuid4().hex[:12].upper(); corr=str(uuid.uuid4()); red=json.dumps(redact(body.model_dump()),sort_keys=True)
    mode=body.mode; status='DELIVERED' if mode=='success' else 'RETRY'; result='SUCCESS' if mode=='success' else ('TIMEOUT' if mode=='timeout' else 'FAILURE')
    cur=c.execute('INSERT INTO integration_events(event_ref,provider_id,event_type,idempotency_key,request_hash,request_redacted,response_redacted,status,attempt_count,max_attempts,next_retry_at,correlation_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?)',
      (ref,p['id'],body.event_type,idem,req_hash,red,json.dumps({'result':result,'mode':'SANDBOX'}),status,p['max_retries'],now() if status=='RETRY' else None,corr,now(),now()))
    eid=cur.lastrowid
    c.execute('INSERT INTO integration_attempts(event_id,attempt_no,result,latency_ms,detail_redacted,ts) VALUES(?,?,?,?,?,?)',(eid,1,result,min(p['timeout_ms'],250),json.dumps({'detail':'synthetic provider response'}),now()))
    if mode=='success':
        c.execute("UPDATE finance_providers SET failure_count=0,health_status='HEALTHY',circuit_state='CLOSED',last_health_at=?,version=version+1 WHERE id=?",(now(),p['id']))
    else:
        nf=p['failure_count']+1; state='OPEN' if nf>=3 else 'CLOSED'
        c.execute("UPDATE finance_providers SET failure_count=?,health_status='DEGRADED',circuit_state=?,last_health_at=?,version=version+1 WHERE id=?",(nf,state,now(),p['id']))
    security_audit(c,x_actor_id,role,'PROVIDER_SIMULATE',provider_key,'PASS' if mode=='success' else 'RETRY',{'event_ref':ref,'payload':body.payload},corr); row=dict(c.execute('SELECT * FROM integration_events WHERE id=?',(eid,)).fetchone()); c.close()
    return JSONResponse(row,status_code=200 if mode=='success' else 502)

@router.post('/events/{event_id}/retry')
def retry_event(event_id:int,body:RetryBody,request:Request,x_role:str=Header('VIEWER'),x_actor_id:str=Header('unknown'),x_csrf_token:Optional[str]=Header(None)):
    role=require_role(x_role,'retry'); check_csrf(request,x_csrf_token); c=connect(); rate_limit(c,'retry:'+x_actor_id,30); r=c.execute('SELECT e.*,p.provider_key,p.circuit_state FROM integration_events e JOIN finance_providers p ON p.id=e.provider_id WHERE e.id=?',(event_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Integration event not found')
    if r['status']=='DELIVERED': c.close(); return {'ok':True,'status':'DELIVERED','already_delivered':True}
    n=r['attempt_count']+1; success=body.force_success
    if success:
        status='DELIVERED'; result='SUCCESS'; next_retry=None
        c.execute("UPDATE finance_providers SET failure_count=0,circuit_state='CLOSED',health_status='HEALTHY',version=version+1 WHERE id=?",(r['provider_id'],))
    elif n>=r['max_attempts']:
        status='DEAD_LETTER'; result='FAILURE'; next_retry=None
    else:
        status='RETRY'; result='FAILURE'; next_retry=now()
    c.execute('UPDATE integration_events SET status=?,attempt_count=?,next_retry_at=?,updated_at=? WHERE id=?',(status,n,next_retry,now(),event_id))
    c.execute('INSERT INTO integration_attempts(event_id,attempt_no,result,latency_ms,detail_redacted,ts) VALUES(?,?,?,?,?,?)',(event_id,n,result,120,json.dumps({'detail':'sandbox retry'}),now()))
    security_audit(c,x_actor_id,role,'EVENT_RETRY',r['event_ref'],status,{'force_success':success}); out=dict(c.execute('SELECT * FROM integration_events WHERE id=?',(event_id,)).fetchone()); c.close(); return out

@router.get('/queues/retry')
def retry_queue(x_role:str=Header('AUDITOR')):
    require_role(x_role); c=connect(); rows=[dict(r) for r in c.execute("SELECT e.*,p.provider_key FROM integration_events e JOIN finance_providers p ON p.id=e.provider_id WHERE e.status='RETRY' ORDER BY e.id")]; c.close(); return rows
@router.get('/queues/dead-letter')
def dead_queue(x_role:str=Header('AUDITOR')):
    require_role(x_role); c=connect(); rows=[dict(r) for r in c.execute("SELECT e.*,p.provider_key FROM integration_events e JOIN finance_providers p ON p.id=e.provider_id WHERE e.status='DEAD_LETTER' ORDER BY e.id")]; c.close(); return rows

@router.post('/webhooks/{provider_key}')
async def webhook(provider_key:str,request:Request,x_webhook_signature:Optional[str]=Header(None),x_event_id:Optional[str]=Header(None)):
    raw=await request.body(); c=connect(); rate_limit(c,'webhook:'+provider_key,25); p=provider(c,provider_key); external=x_event_id or sha_bytes(raw)[:16]; expected=hmac.new(MOCK_WEBHOOK_SECRET,raw,hashlib.sha256).hexdigest(); valid=bool(x_webhook_signature and hmac.compare_digest(expected,x_webhook_signature))
    prior=c.execute('SELECT * FROM webhook_receipts WHERE provider_id=? AND external_event_id=?',(p['id'],external)).fetchone()
    if prior:
        security_audit(c,'webhook', 'SYSTEM','WEBHOOK_DUPLICATE',provider_key,'IGNORED',{'external_event_id':external}); c.close(); return {'duplicate':True,'status':prior['status']}
    status='ACCEPTED' if valid else 'REJECTED'
    c.execute('INSERT INTO webhook_receipts(provider_id,external_event_id,payload_hash,signature_valid,status,received_at) VALUES(?,?,?,?,?,?)',(p['id'],external,sha_bytes(raw),1 if valid else 0,status,now()))
    security_audit(c,'webhook','SYSTEM','WEBHOOK_SIGNATURE',provider_key,'PASS' if valid else 'DENY',{'external_event_id':external})
    c.close()
    if not valid: raise HTTPException(401,{'code':'INVALID_WEBHOOK_SIGNATURE'})
    return {'accepted':True,'external_event_id':external,'sandbox_only':True}

@router.post('/bank-imports',status_code=201)
def bank_import(body:BankImportBody,request:Request,x_role:str=Header('VIEWER'),x_actor_id:str=Header('unknown'),x_csrf_token:Optional[str]=Header(None)):
    role=require_role(x_role,'import'); check_csrf(request,x_csrf_token); c=connect(); rate_limit(c,'bank-import:'+x_actor_id,10)
    if c.execute('SELECT 1 FROM bank_import_batches WHERE import_ref=?',(body.import_ref,)).fetchone(): c.close(); raise HTTPException(409,{'code':'DUPLICATE_BANK_IMPORT'})
    cur=c.execute('INSERT INTO bank_import_batches(import_ref,bank_account_ref,source_name,status,line_count,matched_count,unmatched_count,failed_count,created_at) VALUES(?,?,?,?,?,0,0,0,?)',(body.import_ref,body.bank_account_ref,body.source_name,'PROCESSING',len(body.lines),now())); bid=cur.lastrowid
    matched=unmatched=failed=0
    for line in body.lines:
        if abs(line.amount)<0.000001:
            st='FAILED'; src=None; reason='ZERO_AMOUNT'; failed+=1
        else:
            hit=c.execute('SELECT matched_source_ref FROM treasury_bank_feed_items WHERE bank_account_ref=? AND txn_date=? AND ABS(amount-?)<0.005 AND matched_source_ref IS NOT NULL LIMIT 1',(body.bank_account_ref,line.txn_date,line.amount)).fetchone()
            if hit: st='MATCHED'; src=hit['matched_source_ref']; reason=None; matched+=1
            else: st='UNMATCHED'; src=None; reason='NO_EXACT_MATCH'; unmatched+=1
        c.execute('INSERT INTO bank_import_lines(batch_id,line_ref,txn_date,amount,currency,description,status,matched_source_ref,failure_reason) VALUES(?,?,?,?,?,?,?,?,?)',(bid,line.line_ref,line.txn_date,line.amount,line.currency,line.description,st,src,reason))
    status='COMPLETE' if unmatched==0 and failed==0 else ('FAILED' if failed==len(body.lines) else 'PARTIAL')
    c.execute('UPDATE bank_import_batches SET status=?,matched_count=?,unmatched_count=?,failed_count=? WHERE id=?',(status,matched,unmatched,failed,bid))
    security_audit(c,x_actor_id,role,'BANK_IMPORT',body.import_ref,status,{'lines':len(body.lines),'bank_account':body.bank_account_ref}); out=dict(c.execute('SELECT * FROM bank_import_batches WHERE id=?',(bid,)).fetchone()); c.close(); return out

@router.get('/bank-imports/unmatched')
def unmatched_bank(x_role:str=Header('AUDITOR')):
    require_role(x_role); c=connect(); rows=[dict(r) for r in c.execute("SELECT l.*,b.import_ref,b.bank_account_ref FROM bank_import_lines l JOIN bank_import_batches b ON b.id=l.batch_id WHERE l.status IN ('UNMATCHED','FAILED') ORDER BY l.id")]; c.close(); return rows

@router.post('/bank-imports/lines/{line_id}/manual-match')
def manual_match(line_id:int,body:ManualMatchBody,request:Request,x_role:str=Header('VIEWER'),x_actor_id:str=Header('unknown'),x_csrf_token:Optional[str]=Header(None)):
    role=require_role(x_role,'reconcile'); check_csrf(request,x_csrf_token); c=connect(); r=c.execute('SELECT * FROM bank_import_lines WHERE id=?',(line_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Bank import line not found')
    if r['status']=='FAILED': c.close(); raise HTTPException(422,{'code':'FAILED_LINE_CANNOT_MATCH'})
    c.execute("UPDATE bank_import_lines SET status='MATCHED',matched_source_ref=?,failure_reason=NULL WHERE id=?",(body.source_ref,line_id))
    b=c.execute('SELECT batch_id FROM bank_import_lines WHERE id=?',(line_id,)).fetchone()['batch_id']; counts=c.execute("SELECT SUM(status='MATCHED') m,SUM(status='UNMATCHED') u,SUM(status='FAILED') f FROM bank_import_lines WHERE batch_id=?",(b,)).fetchone(); c.execute('UPDATE bank_import_batches SET matched_count=?,unmatched_count=?,failed_count=?,status=? WHERE id=?',(counts['m'],counts['u'],counts['f'],'COMPLETE' if counts['u']==0 and counts['f']==0 else 'PARTIAL',b)); security_audit(c,x_actor_id,role,'BANK_MANUAL_MATCH',str(line_id),'PASS',{'source_ref':body.source_ref}); c.close(); return {'ok':True,'line_id':line_id,'source_ref':body.source_ref}

@router.get('/payment-sandbox/requests')
def payments(x_role:str=Header('AUDITOR')):
    require_role(x_role); c=connect(); out=[]
    for r in c.execute('''SELECT p.*,j.job_ref,pb.batch_no,pb.status batch_status,v.voucher_no FROM sandbox_payment_requests p JOIN jobs j ON j.id=p.job_id JOIN treasury_payment_batches pb ON pb.id=p.batch_id LEFT JOIN gl_vouchers v ON v.id=p.voucher_id ORDER BY p.id'''):
        d=dict(r); d['gates']=payment_gates(c,r); out.append(d)
    c.close(); return out

@router.post('/payment-sandbox/{payment_id}/release')
def payment_release(payment_id:int,body:PaymentAction,request:Request,x_role:str=Header('VIEWER'),x_actor_id:str=Header('unknown'),x_session_token:Optional[str]=Header(None),x_reauth_token:Optional[str]=Header(None),x_csrf_token:Optional[str]=Header(None)):
    role=require_role(x_role,'release'); check_csrf(request,x_csrf_token); c=connect(); rate_limit(c,'payment-release:'+x_actor_id,10); session_auth(c,role,x_actor_id,x_session_token,True,x_reauth_token); r=payment_row(c,payment_id)
    if r['version']!=body.version: c.close(); raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
    gates=payment_gates(c,r)
    if gates:
        security_audit(c,x_actor_id,role,'PAYMENT_RELEASE',r['payment_ref'],'DENY',{'gates':gates}); c.close(); raise HTTPException(422,{'code':'PAYMENT_RELEASE_BLOCKED','gates':gates})
    pref='SIM-'+uuid.uuid4().hex[:12].upper(); cur=c.execute("UPDATE sandbox_payment_requests SET status='SIMULATED_RELEASED',version=version+1,simulated_provider_ref=?,released_by=?,released_at=? WHERE id=? AND version=?",(pref,x_actor_id,now(),payment_id,body.version))
    if cur.rowcount!=1: c.close(); raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT'})
    c.execute('INSERT INTO sandbox_payment_releases(payment_request_id,provider_ref,release_mode,actor_id,ts) VALUES(?,?,?,?,?)',(payment_id,pref,'SIMULATED',x_actor_id,now()))
    security_audit(c,x_actor_id,role,'PAYMENT_RELEASE',r['payment_ref'],'SIMULATED',{'provider_ref':pref,'real_money_movement':False}); out=dict(c.execute('SELECT * FROM sandbox_payment_requests WHERE id=?',(payment_id,)).fetchone()); c.close(); return {'ok':True,'record':out,'execution_mode':'SIMULATED','real_money_movement':False}

@router.get('/security/policies')
def security_policies(x_role:str=Header('AUDITOR')):
    require_role(x_role); return {
      'authentication':'session token required for sensitive release','reauthentication':'required for payment release',
      'csrf':'required on sandbox mutation routes','cors_allowlist':sorted(ALLOWED_ORIGINS),'rate_limit':'enabled',
      'security_headers':['Content-Security-Policy','X-Content-Type-Options','X-Frame-Options','Referrer-Policy'],
      'sql':'parameterized sqlite statements','secrets':'credential references only; no live secrets','pii_financial_masking':'enabled in audit logs',
      'four_eyes':'maker/checker retained from Treasury/GL','sandbox_only':True
    }

@router.get('/security/audit')
def security_events(limit:int=100,x_role:str=Header('AUDITOR')):
    require_role(x_role); c=connect(); rows=[dict(r) for r in c.execute('SELECT * FROM security_audit_events ORDER BY id DESC LIMIT ?',(min(limit,500),))]; c.close(); return rows

@router.post('/security/rate-limit-probe')
def rate_limit_probe(request:Request,x_role:str=Header('VIEWER'),x_actor_id:str=Header('unknown'),x_csrf_token:Optional[str]=Header(None)):
    role=require_role(x_role,'security'); check_csrf(request,x_csrf_token); c=connect(); n=rate_limit(c,'probe:'+x_actor_id,5); security_audit(c,x_actor_id,role,'RATE_LIMIT_PROBE','security','PASS',{'count':n}); c.close(); return {'count':n,'limit':5}