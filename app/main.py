from fastapi import FastAPI,HTTPException,Header,Query,Request
from fastapi.responses import HTMLResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field
from pathlib import Path
from typing import Any,Optional
import json,hashlib,uuid,datetime,sqlite3,time,re,os,hmac
from .db import connect,tx,DB_PATH,IntegrityError,backend_name,approved_database_target,APPROVED_PROJECT_REF,database_health
from .seed import run as seed_run
from .gl import router as gl_router
from .hardening import router as hardening_router
from .treasury import router as treasury_router
from .integration import router as integration_router
from .bulk import router as bulk_router
from .admin import router as admin_router
from .admin_seed import run as admin_seed_run
from .masterdata import router as masterdata_router
from .masterdata_seed import run as masterdata_seed_run
from .screen_integration import router as screen_integration_router
from .business_day import router as business_day_router
from .preprod import router as preprod_router, log_request, get_runtime_mode_value
from .prodprep import router as prodprep_router
from .liveprep import router as liveprep_router

HERE=Path(__file__).resolve().parent
META=json.loads((HERE/'module_meta.json').read_text())
MODULES={m['key']:m for m in META['modules']}
PRIMARY=META['primary_keys']
app=FastAPI(title='M3 NVOCC ERP',version='0.24.0',description='M3 NVOCC ERP runtime. Production traffic state and external execution controls are reported dynamically.')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['https://m3-nvocc-web-latest.onrender.com'],
    allow_credentials=False,
    allow_methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'],
    allow_headers=['*'],
    expose_headers=['X-Request-Id','X-Correlation-Id'],
)
app.mount('/static',StaticFiles(directory=HERE/'static'),name='static')
app.include_router(liveprep_router)
app.include_router(prodprep_router)
app.include_router(preprod_router)
app.include_router(business_day_router)
app.include_router(screen_integration_router)
app.include_router(admin_router)
app.include_router(masterdata_router)
app.include_router(bulk_router)
app.include_router(integration_router)
app.include_router(treasury_router)
app.include_router(hardening_router)
app.include_router(gl_router)

ROLE_PERMS={
 'ADMIN':set('create edit approve release hold cancel amend reissue reverse advance view'.split()),
 'OPS':set('create edit approve release hold amend reissue advance view'.split()),
 'DOCS':set('create edit approve hold amend reissue view'.split()),
 'FINANCE':set('create edit approve hold reverse view'.split()),
 'AGENT':set('create edit amend view'.split()),
 'VIEWER':set('view'.split())
}
RELEASE_MODULES={'crt','export-crt','import-crt','transshipment-crt','delivery-order','import-bl','cro'}

class CreateBody(BaseModel):
    job_ref:str=Field(pattern=r'^\d{5}$')
    external_ref:Optional[str]=None
    fields:dict[str,Any]=Field(default_factory=dict)
class UpdateBody(BaseModel):
    version:int=Field(ge=1)
    fields:dict[str,Any]
class ActionBody(BaseModel):
    version:int=Field(ge=1)
    reason:Optional[str]=None

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def jdump(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def rowdict(r): return dict(r) if r else None
def role_ok(role,action): return action in ROLE_PERMS.get(role,set())
def require_module(module):
    if module not in MODULES: raise HTTPException(404,'Unknown transaction module')
def actor(x_role:str,x_agent_scope:Optional[str],x_customer_scope:Optional[str]):
    role=x_role.upper()
    if role not in ROLE_PERMS: raise HTTPException(403,'Unknown role')
    return role,x_agent_scope,x_customer_scope

def scope_clause(role,agent_scope,customer_scope):
    if role=='AGENT':
        if not agent_scope: raise HTTPException(403,'AGENT requires X-Agent-Scope')
        return ' AND a.code=?',[agent_scope]
    if customer_scope:
        return ' AND c.code=?',[customer_scope]
    return '',[]

def audit(conn,role,scope,action,module=None,tid=None,jid=None,before=None,after=None,metadata=None):
    conn.execute('INSERT INTO audit_events(event_id,ts,actor_role,actor_scope,action,module,transaction_id,job_id,before_json,after_json,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (str(uuid.uuid4()),now(),role,scope,action,module,tid,jid,jdump(before) if before is not None else None,jdump(after) if after is not None else None,jdump(metadata or {})))
def exception(conn,code,module,tid,jid,detail,severity='BLOCKING'):
    conn.execute('INSERT INTO exception_events(event_id,ts,code,severity,module,transaction_id,job_id,detail,resolved) VALUES(?,?,?,?,?,?,?,?,0)',
                 (str(uuid.uuid4()),now(),code,severity,module,tid,jid,detail))

def tx_query(where='1=1'):
    return f'''SELECT t.*,j.job_ref,b.booking_ref,c.code customer_code,c.name customer_name,a.code agent_code,a.name agent_name,
      co.container_no,v.voyage_no,vs.name vessel_name,bl.bill_no hbl_no
      FROM transaction_records t JOIN jobs j ON j.id=t.job_id JOIN bookings b ON b.id=t.booking_id JOIN customers c ON c.id=t.customer_id
      JOIN agents a ON a.id=t.agent_id LEFT JOIN containers co ON co.id=t.container_id JOIN voyages v ON v.id=t.voyage_id JOIN vessels vs ON vs.id=v.vessel_id LEFT JOIN bills bl ON bl.id=t.bill_id WHERE {where}'''
def serialize_tx(r):
    d=dict(r); payload=json.loads(d.pop('payload_json')); d['fields']=payload; return d

def get_tx(conn,module,tid,role,agent_scope,customer_scope):
    clause,args=scope_clause(role,agent_scope,customer_scope)
    r=conn.execute(tx_query('t.module=? AND t.id=?')+clause,[module,tid]+args).fetchone()
    if not r: raise HTTPException(404,'Transaction not found in actor scope')
    return r

def validate_fields(module,job,fields):
    errs=[]
    jr=str(fields.get('Job Ref') or fields.get('Job References') or fields.get('Job Allocation') or job['job_ref'])
    if jr!=job['job_ref']: errs.append('JOB_REF_MISMATCH')
    if fields.get('Agent') and fields['Agent']!=job['agent_code']: errs.append('AGENT_JOB_MISMATCH')
    if fields.get('Customer') and fields['Customer']!=job['customer_name']: errs.append('CUSTOMER_JOB_MISMATCH')
    if module=='planning':
        try:
            if float(fields.get('Allocated',0))>float(fields.get('Booked',0)): errs.append('OVER_ALLOCATION')
        except Exception: errs.append('INVALID_ALLOCATION')
    if module=='booking':
        if fields.get('Booking No.') and fields.get('Booking No.')!=job['booking_ref']: errs.append('BOOKING_MASTER_MISMATCH')
        if fields.get('POL') and fields.get('POL')!=job['pol']: errs.append('POL_JOB_MISMATCH')
        if fields.get('POD') and fields.get('POD')!=job['pod']: errs.append('POD_JOB_MISMATCH')
    if module=='special-rates-request':
        allowed={'Requested','Pricing Review','Carrier Response','Approved','Linked'}
        if fields.get('Stage','Requested') not in allowed: errs.append('INVALID_SPECIAL_RATE_STAGE')
        if fields.get('Stage') in ('Approved','Linked') and not fields.get('Approved Rate'): errs.append('APPROVED_RATE_REQUIRED')
        if fields.get('Stage')=='Linked' and not (fields.get('Quote Ref') or fields.get('Booking Ref')): errs.append('QUOTE_OR_BOOKING_LINK_REQUIRED')
    if module=='switch-bl':
        if fields.get('Original B/L')!=job['hbl_no']: errs.append('ORIGINAL_BL_MISMATCH')
        if str(fields.get('Confidentiality','')).lower() not in ('yes','true','1'): errs.append('CONFIDENTIALITY_REQUIRED')
        if str(fields.get('Original B/L Preserved','')).lower() not in ('yes','true','1'): errs.append('ORIGINAL_BL_HISTORY_REQUIRED')
    if module=='split-bl':
        try:
            sp=float(fields.get('Source Packages',0)); sw=float(fields.get('Source Weight',0)); sm=float(fields.get('Source Measurement',0));
            ap=float(fields.get('Child 1 Packages',0))+float(fields.get('Child 2 Packages',0)); aw=float(fields.get('Child 1 Weight',0))+float(fields.get('Child 2 Weight',0)); am=float(fields.get('Child 1 Measurement',0))+float(fields.get('Child 2 Measurement',0));
            if abs(sp-ap)>0.0001: errs.append('SPLIT_PACKAGES_UNBALANCED')
            if abs(sw-aw)>0.0001: errs.append('SPLIT_WEIGHT_UNBALANCED')
            if abs(sm-am)>0.0001: errs.append('SPLIT_MEASUREMENT_UNBALANCED')
            if not fields.get('Child B/L 1') or not fields.get('Child B/L 2') or fields.get('Child B/L 1')==fields.get('Child B/L 2'): errs.append('INVALID_CHILD_BL_SET')
        except Exception: errs.append('INVALID_SPLIT_ALLOCATION')
    if module=='container-activity' and fields.get('Container') and fields.get('Container')!=job.get('container_no',fields.get('Container')):
        errs.append('CONTAINER_JOB_MISMATCH')
    return errs

def job_context(conn,jr):
    r=conn.execute('''SELECT j.*,c.code customer_code,c.name customer_name,a.code agent_code,b.booking_ref,v.voyage_no,vs.name vessel_name,
       f.payment_status,f.currency,f.outstanding,f.credit_hold,co.container_no,bl.bill_no hbl_no,w.documentation_status,w.vgm_status,w.customs_status,w.transshipment_status,w.release_status,w.closed,w.version workflow_version
       FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id JOIN bookings b ON b.id=j.booking_id JOIN voyages v ON v.id=j.voyage_id JOIN vessels vs ON vs.id=v.vessel_id
       JOIN finance_states f ON f.job_id=j.id JOIN containers co ON co.job_id=j.id LEFT JOIN bills bl ON bl.job_id=j.id AND bl.kind='HBL' JOIN workflow_states w ON w.job_id=j.id WHERE j.job_ref=?''',(jr,)).fetchone()
    if not r: raise HTTPException(404,'Unknown job')
    d=dict(r); d['holds']=[x['code'] for x in conn.execute('SELECT code FROM workflow_holds WHERE job_id=? AND active=1',(r['id'],))]; return d

def release_gate(ctx):
    reasons=list(ctx['holds'])
    if ctx['closed']: reasons.append('JOB_CLOSED')
    if ctx['payment_status']!='CLEARED': reasons.append('PAYMENT_NOT_CLEARED')
    if ctx['vgm_status'] in ('MISSING','PENDING'): reasons.append('VGM_MISSING')
    if ctx['documentation_status'] not in ('COMPLETE','APPROVED'): reasons.append('DOCUMENTATION_PENDING')
    if ctx['customs_status'] not in ('CLEARED','N/A'): reasons.append('CUSTOMS_PENDING')
    if ctx['transshipment_status']=='PENDING': reasons.append('TRANSSHIPMENT_CONFIRMATION')
    return sorted(set(reasons))

def clear_hold(conn,jid,code):
    conn.execute('UPDATE workflow_holds SET active=0,cleared_at=? WHERE job_id=? AND code=?',(now(),jid,code))
def add_hold(conn,jid,code):
    conn.execute('INSERT INTO workflow_holds(job_id,code,active,created_at) VALUES(?,?,1,?) ON CONFLICT(job_id,code) DO UPDATE SET active=1,cleared_at=NULL',(jid,code,now()))

def sync_release(conn,jid,exclude_id=None):
    sql="UPDATE transaction_records SET status='Released',version=version+1,updated_at=? WHERE job_id=? AND module IN ('delivery-order','import-crt','import-bl')"
    args=[now(),jid]
    if exclude_id is not None: sql+=' AND id<>?'; args.append(exclude_id)
    conn.execute(sql,args)
    q="SELECT id,payload_json,module FROM transaction_records WHERE job_id=? AND module IN ('delivery-order','import-crt','import-bl')"
    vals=[jid]
    if exclude_id is not None: q+=' AND id<>?'; vals.append(exclude_id)
    for r in conn.execute(q,vals):
        p=json.loads(r['payload_json']); p['Status']='Released';
        if r['module']=='delivery-order': p['Release Status']='Released'
        conn.execute('UPDATE transaction_records SET payload_json=? WHERE id=?',(json.dumps(p),r['id']))


def sync_special_rate(conn,tid,payload):
    stage=payload.get('Stage','Requested')
    conn.execute('INSERT INTO special_rate_workflow(transaction_id,stage,carrier_response,approved_rate,quote_ref,booking_ref,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(transaction_id) DO UPDATE SET stage=excluded.stage,carrier_response=excluded.carrier_response,approved_rate=excluded.approved_rate,quote_ref=excluded.quote_ref,booking_ref=excluded.booking_ref,updated_at=excluded.updated_at',(tid,stage,payload.get('Carrier Response'),float(payload.get('Approved Rate') or 0) or None,payload.get('Quote Ref'),payload.get('Booking Ref'),now()))
def sync_split(conn,tid,payload):
    conn.execute('DELETE FROM split_bl_allocations WHERE transaction_id=?',(tid,))
    for n in (1,2):
        conn.execute('INSERT INTO split_bl_allocations(transaction_id,child_bill_no,container_no,packages,weight,measurement) VALUES(?,?,?,?,?,?)',(tid,payload.get(f'Child B/L {n}',''),payload.get('Container',''),float(payload.get(f'Child {n} Packages') or 0),float(payload.get(f'Child {n} Weight') or 0),float(payload.get(f'Child {n} Measurement') or 0)))
def preserve_switch_history(conn,tid,jid,payload,role):
    raw=f"{tid}|{payload.get('Original B/L')}|{payload.get('Switch B/L')}|{jid}|{payload.get('New Shipper')}|{payload.get('New Consignee')}"; h=hashlib.sha256(raw.encode()).hexdigest()
    if conn.execute('SELECT 1 FROM switch_bl_history WHERE immutable_hash=?',(h,)).fetchone(): return
    conn.execute('INSERT INTO switch_bl_history(transaction_id,job_id,ts,original_bill_no,switch_bill_no,original_parties_json,new_parties_json,approved_by,confidentiality,immutable_hash) VALUES(?,?,?,?,?,?,?,?,1,?)',(tid,jid,now(),payload.get('Original B/L',''),payload.get('Switch B/L',''),json.dumps({'shipper':payload.get('Original Shipper'),'consignee':payload.get('Original Consignee')}),json.dumps({'shipper':payload.get('New Shipper'),'consignee':payload.get('New Consignee'),'notify':payload.get('New Notify')}),payload.get('Approved By') or role,h))

@app.middleware('http')
async def security_headers(request, call_next):
    started=time.perf_counter()
    request_id=request.headers.get('X-Request-Id') or str(uuid.uuid4())
    correlation_id=request.headers.get('X-Correlation-Id') or request_id
    error_class=None
    runtime_mode=get_runtime_mode_value()
    health_exempt=request.method.upper()=='OPTIONS' or request.url.path in {'/api/v1/health','/api/clx013/runtime-mode','/api/clx013/health','/api/clx013/readiness','/api/clx016/readiness'}
    production_traffic=os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper()
    uat_token=os.getenv('M3_UAT_TOKEN','')
    supplied_uat=request.headers.get('X-M3-UAT-Token','')
    uat_allowed=bool(uat_token and supplied_uat and hmac.compare_digest(uat_token,supplied_uat))
    try:
        if production_traffic!='ON' and not health_exempt and not uat_allowed:
            response=JSONResponse({'detail':{'code':'PRODUCTION_TRAFFIC_LOCKED','uat_header':'X-M3-UAT-Token'}},status_code=503)
        elif not health_exempt and runtime_mode=='MAINTENANCE':
            response=JSONResponse({'detail':{'code':'MAINTENANCE_MODE'}},status_code=503)
        elif not health_exempt and runtime_mode=='READ_ONLY' and request.method.upper() in {'POST','PUT','PATCH','DELETE'}:
            response=JSONResponse({'detail':{'code':'READ_ONLY_MODE'}},status_code=423)
        else:
            response=await call_next(request)
    except Exception as exc:
        error_class=exc.__class__.__name__
        raise
    finally:
        if 'response' in locals():
            job_match=re.search(r'(?<!\d)(5000[1-5])(?!\d)',request.url.path)
            log_request(request_id,correlation_id,request.method,request.url.path,response.status_code,round((time.perf_counter()-started)*1000,3),request.headers.get('X-Role'),request.headers.get('X-Office'),job_match.group(1) if job_match else None,error_class)
    response.headers['X-Request-Id']=request_id
    response.headers['X-Correlation-Id']=correlation_id
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://m3.test"
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'no-cache'
    return response

@app.get('/',response_class=HTMLResponse)
def root(): return (HERE/'static/M3_NVOCC_ERP_CLX-015_LIVE_ENVIRONMENT_PREP_20260924.html').read_text()
def runtime_flags():
    production_traffic=os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper()
    live_providers=os.getenv('M3_LIVE_PROVIDERS','OFF').upper()
    real_money=os.getenv('REAL_MONEY','OFF').upper()
    return {
      'production_traffic':production_traffic,
      'live_providers':live_providers,
      'real_money':real_money,
      'production_promoted':production_traffic=='ON',
      'sandbox_only':production_traffic!='ON',
      'live_credentials':live_providers=='ON',
      'live_bank_api':live_providers=='ON',
      'live_tax_api':live_providers=='ON',
      'live_carrier_api':live_providers=='ON',
      'real_payment_execution':real_money=='ON',
    }

@app.get('/api/v1/health')
def health():
    flags=runtime_flags()
    return {
      'project':'M3 NVOCC ERP',
      'baseline':os.getenv('M3_RUNTIME_BASELINE','M3-PRODUCTION-TRAFFIC-CUTOVER-ACCEPTED-20260925-024'),
      'database':backend_name(),
      **flags,
    }
@app.get('/api/clx016/readiness')
def clx016_readiness():
    result={
      'project':'M3 NVOCC ERP',
      'phase':'CLX-016',
      'approved_project_ref':APPROVED_PROJECT_REF,
      'configured_project_ref':os.getenv('M3_SUPABASE_PROJECT_REF',''),
      'database_target_approved':approved_database_target(),
      'database':backend_name(),
      'production_traffic':os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper(),
      'live_providers':os.getenv('M3_LIVE_PROVIDERS','OFF').upper(),
      'real_money':os.getenv('REAL_MONEY','OFF').upper(),
      'uat_token_configured':bool(os.getenv('M3_UAT_TOKEN','')),
      'ready':False,
    }
    try:
        c=connect()
        health=database_health(c)
        jobs=c.execute("SELECT COUNT(*) n FROM jobs WHERE job_ref IN ('50001','50002','50003','50004','50005')").fetchone()['n']
        c.close()
        result['database_health']=health['status']
        result['jobs_50001_50005']=jobs
        infrastructure_ready=bool(
          health['status']=='ok' and jobs==5 and result['database_target_approved']
          and result['configured_project_ref']==APPROVED_PROJECT_REF
        )
        external_execution_safe=bool(
          result['live_providers']=='OFF'
          and result['real_money']=='OFF'
        )
        result['infrastructure_ready']=infrastructure_ready
        result['external_execution_safe']=external_execution_safe
        result['traffic_mode']='LIVE' if result['production_traffic']=='ON' else 'LOCKED'
        result['ready']=bool(infrastructure_ready and external_execution_safe)
    except Exception as exc:
        result['database_health']='fail'
        result['error_class']=exc.__class__.__name__
    return result
@app.get('/api/clx010/health')
def clx010_health(): return {'project':'M3 NVOCC ERP','baseline':'M3-CLX010-ACCEPTED-20260924-012F','parent':'M3-CLX009-ACCEPTED-20260924-011','database':backend_name(),'production_promoted':False,'sandbox_only':True,'live_credentials':False,'real_money_movement':False,'admin_screens':28,'master_domains':29}
@app.post('/api/v1/admin/reset-test-data')
def reset(x_role:str=Header('VIEWER')):
    if x_role.upper()!='ADMIN': raise HTTPException(403,'ADMIN only')
    seed_run(True); admin_seed_run(); masterdata_seed_run(); return {'ok':True}
@app.get('/api/v1/modules')
def modules(): return list(MODULES.values())

@app.get('/api/v1/{module}')
def list_records(module:str,job_ref:Optional[str]=None,status:Optional[str]=None,q:Optional[str]=None,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    require_module(module); role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope); conn=connect()
    clause,args=scope_clause(role,ascope,cscope); where='t.module=?'; vals=[module]
    if job_ref: where+=' AND j.job_ref=?'; vals.append(job_ref)
    if status: where+=' AND t.status=?'; vals.append(status)
    rows=conn.execute(tx_query(where)+clause+' ORDER BY t.id',vals+args).fetchall(); conn.close()
    out=[serialize_tx(r) for r in rows]
    if q:
        qq=q.lower(); out=[r for r in out if qq in json.dumps(r).lower()]
    return {'module':module,'count':len(out),'records':out}

@app.get('/api/v1/{module}/{tid}')
def get_record(module:str,tid:int,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    require_module(module); role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope); conn=connect(); r=get_tx(conn,module,tid,role,ascope,cscope); out=serialize_tx(r); conn.close(); return out

@app.post('/api/v1/{module}',status_code=201)
async def create_record(module:str,body:CreateBody,request:Request,x_role:str=Header('VIEWER'),idempotency_key:Optional[str]=Header(None,alias='Idempotency-Key'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    require_module(module); role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope)
    if not role_ok(role,'create'): raise HTTPException(403,'Role cannot create')
    raw=await request.body(); rh=hashlib.sha256(raw).hexdigest(); conn=connect(); tx(conn)
    try:
        if idempotency_key:
            prior=conn.execute('SELECT * FROM idempotency_keys WHERE actor_role=? AND idem_key=?',(role,idempotency_key)).fetchone()
            if prior:
                if prior['request_hash']!=rh: raise HTTPException(409,'IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST')
                conn.execute('COMMIT'); return JSONResponse(json.loads(prior['response_json']),status_code=prior['status_code'])
        ctx=job_context(conn,body.job_ref)
        if role=='AGENT' and ctx['agent_code']!=ascope: raise HTTPException(403,'Job outside agent scope')
        errs=validate_fields(module,ctx,body.fields)
        if errs: raise HTTPException(422,{'codes':errs})
        ext=body.external_ref or f"CLX-{module.upper()}-{body.job_ref}-{uuid.uuid4().hex[:8].upper()}"
        if conn.execute('SELECT 1 FROM transaction_records WHERE module=? AND external_ref=?',(module,ext)).fetchone(): raise HTTPException(409,'DUPLICATE_TRANSACTION')
        payload=dict(body.fields); payload.setdefault('Job Ref',body.job_ref); payload.setdefault('Agent',ctx['agent_code']); payload.setdefault('Customer',ctx['customer_name']); payload.setdefault('Status','Draft')
        container=conn.execute('SELECT id FROM containers WHERE job_id=?',(ctx['id'],)).fetchone(); bill=conn.execute("SELECT id FROM bills WHERE job_id=? AND kind='HBL'",(ctx['id'],)).fetchone()
        cur=conn.execute('''INSERT INTO transaction_records(module,external_ref,job_id,booking_id,customer_id,agent_id,container_id,voyage_id,bill_id,status,version,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?)''',
          (module,ext,ctx['id'],ctx['booking_id'],ctx['customer_id'],ctx['agent_id'],container['id'] if container else None,ctx['voyage_id'],bill['id'] if bill else None,payload['Status'],json.dumps(payload),now(),now()))
        tid=cur.lastrowid
        if module=='special-rates-request': sync_special_rate(conn,tid,payload)
        if module=='split-bl': sync_split(conn,tid,payload)
        audit(conn,role,ascope or cscope,'CREATE',module,tid,ctx['id'],None,payload,{'external_ref':ext})
        out=serialize_tx(conn.execute(tx_query('t.id=?'),(tid,)).fetchone())
        if idempotency_key: conn.execute('INSERT INTO idempotency_keys(actor_role,idem_key,request_hash,response_json,status_code,created_at) VALUES(?,?,?,?,201,?)',(role,idempotency_key,rh,json.dumps(out),now()))
        conn.execute('COMMIT'); return JSONResponse(out,status_code=201)
    except HTTPException:
        conn.execute('ROLLBACK'); raise
    except IntegrityError as e:
        conn.execute('ROLLBACK'); raise HTTPException(409,'DUPLICATE_OR_INTEGRITY_CONFLICT')
    finally: conn.close()

@app.put('/api/v1/{module}/{tid}')
def update_record(module:str,tid:int,body:UpdateBody,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    require_module(module); role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope)
    if not role_ok(role,'edit'): raise HTTPException(403,'Role cannot edit')
    conn=connect(); tx(conn)
    try:
        r=get_tx(conn,module,tid,role,ascope,cscope); before=serialize_tx(r); ctx=job_context(conn,r['job_ref'])
        if r['version']!=body.version: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        payload=json.loads(r['payload_json']); payload.update(body.fields); errs=validate_fields(module,ctx,payload)
        if errs: raise HTTPException(422,{'codes':errs})
        status=payload.get('Status') or payload.get('Release Status') or r['status']
        cur=conn.execute('UPDATE transaction_records SET payload_json=?,status=?,version=version+1,updated_at=? WHERE id=? AND version=?',(json.dumps(payload),status,now(),tid,body.version))
        if cur.rowcount!=1: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT'})
        if module=='special-rates-request': sync_special_rate(conn,tid,payload)
        if module=='split-bl': sync_split(conn,tid,payload)
        after=serialize_tx(conn.execute(tx_query('t.id=?'),(tid,)).fetchone()); audit(conn,role,ascope or cscope,'UPDATE',module,tid,r['job_id'],before,after,{})
        conn.execute('COMMIT'); return after
    except HTTPException: conn.execute('ROLLBACK'); raise
    finally: conn.close()

@app.delete('/api/v1/{module}/{tid}')
def delete_record(module:str,tid:int,version:int=Query(...,ge=1),x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    require_module(module); role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope)
    if role!='ADMIN': raise HTTPException(403,'ADMIN only delete')
    conn=connect(); tx(conn)
    try:
        r=get_tx(conn,module,tid,role,ascope,cscope)
        if r['version']!=version: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        before=serialize_tx(r); conn.execute('DELETE FROM transaction_records WHERE id=?',(tid,)); audit(conn,role,None,'DELETE',module,tid,r['job_id'],before,None,{})
        conn.execute('COMMIT'); return {'deleted':True,'id':tid}
    except HTTPException: conn.execute('ROLLBACK'); raise
    finally: conn.close()

@app.post('/api/v1/{module}/{tid}/actions/{action}')
def action_record(module:str,tid:int,action:str,body:ActionBody,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    require_module(module); role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope); action=action.lower()
    if not role_ok(role,action): raise HTTPException(403,f'Role cannot {action}')
    if action=='release' and module not in RELEASE_MODULES: raise HTTPException(422,{'code':'MODULE_NOT_RELEASABLE'})
    conn=connect(); tx(conn)
    try:
        r=get_tx(conn,module,tid,role,ascope,cscope); before=serialize_tx(r)
        if r['version']!=body.version: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        ctx=job_context(conn,r['job_ref']); payload=json.loads(r['payload_json']); new_status=r['status']
        if ctx['closed'] and action in {'approve','release','hold','cancel','amend','reissue','advance'}:
            audit(conn,role,ascope or cscope,'CLOSED_JOB_BLOCKED',module,tid,r['job_id'],before,before,{'attempted_action':action})
            conn.execute('COMMIT')
            return JSONResponse({'detail':{'code':'JOB_CLOSED','action':action}},status_code=422)
        if action=='advance':
            if module!='special-rates-request': raise HTTPException(422,{'code':'ADVANCE_ONLY_SPECIAL_RATE'})
            stages=['Requested','Pricing Review','Carrier Response','Approved','Linked']; curstage=payload.get('Stage','Requested'); idx=stages.index(curstage) if curstage in stages else 0; nextstage=stages[min(idx+1,len(stages)-1)]
            if nextstage in ('Approved','Linked') and not payload.get('Approved Rate'): raise HTTPException(422,{'code':'APPROVED_RATE_REQUIRED'})
            if nextstage=='Linked' and not (payload.get('Quote Ref') or payload.get('Booking Ref')): raise HTTPException(422,{'code':'QUOTE_OR_BOOKING_LINK_REQUIRED'})
            payload['Stage']=nextstage; new_status='Approved' if nextstage in ('Approved','Linked') else 'Open'
        elif action=='approve':
            if module=='switch-bl':
                errs=validate_fields(module,ctx,payload)
                if errs: raise HTTPException(422,{'codes':errs})
                payload['Approval']='Approved'; payload['Approved By']=payload.get('Approved By') or role; preserve_switch_history(conn,tid,r['job_id'],payload,role)
            if module=='split-bl':
                errs=validate_fields(module,ctx,payload)
                if errs: raise HTTPException(422,{'codes':errs})
                payload['Approval']='Approved'; sync_split(conn,tid,payload)
            new_status='Approved'
        elif action=='hold':
            code=(body.reason or 'MANUAL_HOLD').strip().upper().replace(' ','_'); add_hold(conn,r['job_id'],code); new_status='Pending'; exception(conn,code,module,tid,r['job_id'],body.reason or 'Manual hold')
        elif action=='release':
            reasons=release_gate(ctx)
            if reasons:
                for code in reasons: exception(conn,code,module,tid,r['job_id'],'Release blocked by server-side workflow gate')
                audit(conn,role,ascope or cscope,'RELEASE_BLOCKED',module,tid,r['job_id'],before,before,{'reasons':reasons})
                conn.execute('COMMIT')
                return JSONResponse({'detail':{'code':'RELEASE_BLOCKED','reasons':reasons}},status_code=422)
            new_status='Released'; conn.execute("UPDATE workflow_states SET release_status='RELEASED',version=version+1 WHERE job_id=?",(r['job_id'],)); sync_release(conn,r['job_id'],tid)
        elif action=='cancel': new_status='Cancelled'
        elif action=='amend': new_status='Draft'
        elif action=='reissue': new_status='Issued'
        elif action=='reverse': new_status='Reversed'
        # special transshipment completion clears hold and state
        if module=='transshipment-crt' and action in ('approve','release'):
            conn.execute("UPDATE workflow_states SET transshipment_status='CONFIRMED',version=version+1 WHERE job_id=?",(r['job_id'],)); clear_hold(conn,r['job_id'],'TRANSSHIPMENT_CONFIRMATION')
        payload['Status']=new_status
        if module=='special-rates-request': sync_special_rate(conn,tid,payload)
        if module=='delivery-order' and action=='release': payload['Release Status']='Released'
        cur=conn.execute('UPDATE transaction_records SET status=?,payload_json=?,version=version+1,updated_at=? WHERE id=? AND version=?',(new_status,json.dumps(payload),now(),tid,body.version))
        if cur.rowcount!=1: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT'})
        after=serialize_tx(conn.execute(tx_query('t.id=?'),(tid,)).fetchone()); audit(conn,role,ascope or cscope,action.upper(),module,tid,r['job_id'],before,after,{'reason':body.reason})
        conn.execute('COMMIT'); return {'ok':True,'record':after,'job':job_context(connect(),r['job_ref'])}
    except HTTPException: conn.execute('ROLLBACK'); raise
    finally: conn.close()

@app.get('/api/v1/jobs/{job_ref}/context')
def context(job_ref:str,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope); conn=connect(); ctx=job_context(conn,job_ref)
    if role=='AGENT' and ctx['agent_code']!=ascope: conn.close(); raise HTTPException(404,'Job outside agent scope')
    ctx['release_gate_reasons']=release_gate(ctx); conn.close(); return ctx
@app.get('/api/v1/jobs/{job_ref}/linked-transactions')
def linked(job_ref:str,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope); conn=connect(); ctx=job_context(conn,job_ref)
    if role=='AGENT' and ctx['agent_code']!=ascope: conn.close(); raise HTTPException(404,'Job outside agent scope')
    rows=conn.execute(tx_query('j.job_ref=?'),'').fetchall() if False else conn.execute(tx_query('j.job_ref=?'),(job_ref,)).fetchall(); out=[serialize_tx(r) for r in rows]; conn.close(); return {'job_ref':job_ref,'count':len(out),'records':out}

@app.get('/api/v1/jobs/{job_ref}/container-events')
def container_events(job_ref:str,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope); conn=connect(); ctx=job_context(conn,job_ref)
    if role=='AGENT' and ctx['agent_code']!=ascope: conn.close(); raise HTTPException(404,'Job outside agent scope')
    rows=[dict(r) for r in conn.execute('SELECT ce.*,c.container_no FROM container_events ce JOIN containers c ON c.id=ce.container_id WHERE ce.job_id=? ORDER BY ce.event_time,ce.id',(ctx['id'],))]; conn.close(); return {'job_ref':job_ref,'count':len(rows),'events':rows}
@app.get('/api/v1/jobs/{job_ref}/bl-history')
def bl_history(job_ref:str,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope); conn=connect(); ctx=job_context(conn,job_ref)
    if role=='AGENT' and ctx['agent_code']!=ascope: conn.close(); raise HTTPException(404,'Job outside agent scope')
    rows=[dict(r) for r in conn.execute('SELECT * FROM switch_bl_history WHERE job_id=? ORDER BY id',(ctx['id'],))]; conn.close(); return {'job_ref':job_ref,'count':len(rows),'history':rows}
@app.get('/api/v1/split-bl/{tid}/allocations')
def split_allocations(tid:int,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope); conn=connect(); r=get_tx(conn,'split-bl',tid,role,ascope,cscope); rows=[dict(x) for x in conn.execute('SELECT * FROM split_bl_allocations WHERE transaction_id=? ORDER BY id',(tid,))]; conn.close(); return {'transaction_id':tid,'count':len(rows),'allocations':rows}
@app.get('/api/v1/special-rates-request/{tid}/workflow')
def special_rate_workflow(tid:int,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)):
    role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope); conn=connect(); r=get_tx(conn,'special-rates-request',tid,role,ascope,cscope); w=conn.execute('SELECT * FROM special_rate_workflow WHERE transaction_id=?',(tid,)).fetchone(); conn.close(); return dict(w) if w else {}

@app.get('/api/v1/system/events/audit')
def audit_list(job_ref:Optional[str]=None,limit:int=100):
    conn=connect(); q='SELECT a.*,j.job_ref FROM audit_events a LEFT JOIN jobs j ON j.id=a.job_id'; args=[]
    if job_ref: q+=' WHERE j.job_ref=?'; args.append(job_ref)
    q+=' ORDER BY a.id DESC LIMIT ?'; args.append(min(limit,500)); rows=[dict(r) for r in conn.execute(q,args)]; conn.close(); return rows
@app.get('/api/v1/system/events/exceptions')
def exception_list(job_ref:Optional[str]=None,limit:int=100):
    conn=connect(); q='SELECT e.*,j.job_ref FROM exception_events e LEFT JOIN jobs j ON j.id=e.job_id'; args=[]
    if job_ref: q+=' WHERE j.job_ref=?'; args.append(job_ref)
    q+=' ORDER BY e.id DESC LIMIT ?'; args.append(min(limit,500)); rows=[dict(r) for r in conn.execute(q,args)]; conn.close(); return rows