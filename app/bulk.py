from fastapi import APIRouter, HTTPException, Header, Query, Request
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Optional
import json, datetime, uuid
from .db import connect, tx, backend_name, list_public_tables, list_public_triggers

HERE=Path(__file__).resolve().parent
AGENT=json.loads((HERE/'module_meta.json').read_text())['modules']
GL_META=json.loads((HERE/'gl_meta.json').read_text())
GL=[dict(x,group=g) for g in ('setup','transactions','controls','reports') for x in GL_META.get(g,[])]
TREASURY=json.loads((HERE/'treasury_meta.json').read_text())['modules']
INTEGRATION=json.loads((HERE/'integration_meta.json').read_text())['modules']
router=APIRouter(prefix='/api/v1/bulk',tags=['CLX-009 Bulk Build / Unified Control'])
BASELINE='M3-CLX009-ACCEPTED-20260924-011'
CSRF='sandbox-csrf'

ROLE_CAPS={
 'ADMIN': {'view','email','export','print','create','edit','copy','approve','release','hold','cancel','amend','reissue','reverse'},
 'OPS': {'view','email','export','print','create','edit','copy','approve','release','hold','amend','reissue'},
 'DOCS': {'view','email','export','print','create','edit','copy','approve','hold','amend','reissue'},
 'FINANCE': {'view','email','export','print','create','edit','copy','approve','reverse'},
 'GL_MANAGER': {'view','email','export','print','create','edit','copy','approve','release','reverse'},
 'GL_ACCOUNTANT': {'view','email','export','print','create','edit','copy','approve'},
 'TREASURY_MANAGER': {'view','email','export','print','create','edit','copy','approve','release','reverse'},
 'SECURITY_ADMIN': {'view','email','export','print'},
 'AUDITOR': {'view','export','print'},
 'AGENT': {'view','email','export','print','create','edit','copy','amend'},
 'VIEWER': {'view','export','print'},
}

GAPS=[
 {'id':'GAP-001','area':'Unified UI','finding':'CLX-008 combined shell exposed all modules but did not surface accepted mutation/action workflows consistently.','fix':'CLX-009 standardized action toolbar with module-aware Create/Edit/Copy/Approve/Release/Hold/Cancel/Amend/Reissue/Reverse and controlled feedback.','status':'FIXED'},
 {'id':'GAP-002','area':'Navigation','finding':'Agent Tasks metadata retained pre-approved build order rather than the approved NVOCC lifecycle order.','fix':'Reordered all 19 Agent Tasks in the CLX-009 metadata only; no route or business-logic change.','status':'FIXED'},
 {'id':'GAP-003','area':'List UX','finding':'CLX-008 unified grids lacked one consistent filter/sort/pagination/saved-view implementation across all 123 screens.','fix':'Added common search, status filter, sort, page size, paging, saved views, reset, export and print behavior.','status':'FIXED'},
 {'id':'GAP-004','area':'Cross-module traceability','finding':'No single endpoint summarized the complete Job→Operations→GL→Treasury→Integration chain.','fix':'Added job E2E trace and dependency/impact map endpoints for jobs 50001–50005.','status':'FIXED'},
 {'id':'GAP-005','area':'Global navigation','finding':'No cross-domain global search over operational, GL, Treasury and integration references.','fix':'Added scoped global search with direct module/type/job references.','status':'FIXED'},
 {'id':'GAP-006','area':'Quick view','finding':'Current unified shell did not consistently show linked job context and domain record counts in the right-side panel.','fix':'Added shared job quick view and linked-domain summary to every record that carries a five-digit job reference.','status':'FIXED'},
 {'id':'GAP-007','area':'Email action','finding':'Email was an approved UI action but no safe non-delivery implementation existed in the combined sandbox.','fix':'Added explicit SIMULATED_EMAIL audit action; it records intent only and never sends external mail.','status':'FIXED'},
 {'id':'GAP-008','area':'Bulk acceptance evidence','finding':'No machine-readable system inventory/dependency map/gap-fix endpoint existed for whole-ERP acceptance.','fix':'Added CLX-009 inventory, gap/fix, dependency map, immutable bulk-event and E2E verification endpoints.','status':'FIXED'},
]

DEPENDENCY_CHAIN=[
 'Customer','Rate','Quote','Booking','Job','Routing','Vessel/Voyage','Container','Documentation','HBL/MBL','Manifest/VGM',
 'Import/Export','Delivery/Release','Charges','Invoice/Bill','Receipt/Payment','Agent SOA','Treasury','Voucher','GL','Bank Reconciliation','Month-End','Reporting'
]

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def role(role):
    r=role.upper()
    if r not in ROLE_CAPS: raise HTTPException(403,'Unknown role')
    return r

def log_event(c, actor_role, actor_id, action, module_type=None, module=None, record_ref=None, job_ref=None, outcome='SIMULATED', detail=None):
    c.execute('INSERT INTO bulk_events(event_id,ts,actor_role,actor_id,action,module_type,module,record_ref,job_ref,outcome,detail_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
              (str(uuid.uuid4()),now(),actor_role,actor_id,action,module_type,module,record_ref,job_ref,outcome,json.dumps(detail or {},sort_keys=True)))

def counts(c):
    return {
      'agent_task_screens':len(AGENT),'gl_accounts_screens':len(GL),'treasury_arap_screens':len(TREASURY),'integration_security_screens':len(INTEGRATION),
      'combined_screens':len(AGENT)+len(GL)+len(TREASURY)+len(INTEGRATION),
      'jobs':c.execute('select count(*) n from jobs').fetchone()['n'],
      'operational_records':c.execute('select count(*) n from transaction_records').fetchone()['n'],
      'gl_records':c.execute('select count(*) n from gl_records').fetchone()['n'],
      'treasury_records':c.execute('select count(*) n from treasury_records').fetchone()['n'],
      'integration_records':c.execute('select count(*) n from integration_records').fetchone()['n'],
    }

@router.get('/health')
def health():
    c=connect(); d=counts(c); c.close()
    return {'project':'M3 NVOCC ERP','baseline':BASELINE,'module':'Bulk Build + Unified Operations Control','database':backend_name(),'sandbox_only':True,
            'live_credentials':False,'real_money_movement':False,'production_promoted':False,**d}

@router.get('/inventory')
def inventory(x_role:str=Header('VIEWER')):
    role(x_role); c=connect(); d=counts(c)
    tables=list_public_tables(c)
    triggers=list_public_triggers(c)
    c.close()
    return {'baseline':BASELINE,'counts':d,'agent_order':[m['key'] for m in AGENT], 'tables':tables,'triggers':triggers,
            'modules':{'agent':AGENT,'gl':GL,'treasury':TREASURY,'integration':INTEGRATION}}

@router.get('/gaps')
def gaps(x_role:str=Header('VIEWER')):
    role(x_role); return {'count':len(GAPS),'open':0,'fixed':len(GAPS),'items':GAPS}

@router.get('/dependency-map')
def dependency_map(x_role:str=Header('VIEWER')):
    role(x_role)
    edges=[{'from':DEPENDENCY_CHAIN[i],'to':DEPENDENCY_CHAIN[i+1]} for i in range(len(DEPENDENCY_CHAIN)-1)]
    return {'chain':DEPENDENCY_CHAIN,'edges':edges,'domain_links':{
      'Agent Tasks':['Booking','Job','Container','HBL/MBL','Agent SOA'],
      'GL / Accounts':['Charges','Invoice/Bill','Receipt/Payment','Voucher','GL','Month-End','Reporting'],
      'Treasury / AR-AP':['Receipt/Payment','Agent SOA','Treasury','Bank Reconciliation'],
      'Finance Integration':['Bank Reconciliation','Payment Release Sandbox','FX/Tax Mock Feeds','Security/Audit']}}

@router.get('/jobs/{job_ref}/e2e')
def e2e(job_ref:str,x_role:str=Header('VIEWER')):
    role(x_role); c=connect()
    j=c.execute('''SELECT j.*,b.booking_ref,c.code customer_code,c.name customer_name,a.code agent_code,v.voyage_no,vs.name vessel_name
      FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
      JOIN voyages v ON v.id=j.voyage_id JOIN vessels vs ON vs.id=v.vessel_id WHERE j.job_ref=?''',(job_ref,)).fetchone()
    if not j: c.close(); raise HTTPException(404,'Unknown job')
    jid=j['id']
    def n(sql,args=(jid,)): return c.execute(sql,args).fetchone()['n']
    bills=[dict(r) for r in c.execute('select bill_no,kind,status from bills where job_id=? order by kind',(jid,))]
    wf=dict(c.execute('select * from workflow_states where job_id=?',(jid,)).fetchone())
    finance=dict(c.execute('select * from finance_states where job_id=?',(jid,)).fetchone())
    summary={
      'operational_transactions':n('select count(*) n from transaction_records where job_id=?'),
      'containers':n('select count(*) n from containers where job_id=?'),
      'container_events':n('select count(*) n from container_events where job_id=?'),
      'gl_records':n('select count(*) n from gl_records where job_id=?'),
      'gl_links':n('select count(*) n from gl_source_links where job_id=?'),
      'treasury_records':n('select count(*) n from treasury_records where job_id=?'),
      'integration_records':n('select count(*) n from integration_records where job_id=?'),
      'audit_events':n('select count(*) n from audit_events where job_id=?'),
      'exception_events':n('select count(*) n from exception_events where job_id=?'),
    }
    coverage={
      'Customer':bool(j['customer_code']),'Rate':n("select count(*) n from transaction_records where job_id=? and module='special-rates-request'")>0,
      'Quote':n("select count(*) n from transaction_records where job_id=? and module='special-rates-request'")>0,'Booking':bool(j['booking_ref']),
      'Job':True,'Routing':bool(j['pol'] and j['pod']),'Vessel/Voyage':bool(j['voyage_no']),'Container':summary['containers']>0,
      'Documentation':n("select count(*) n from transaction_records where job_id=? and module in ('bl','import-bl','switch-bl','split-bl')")>0,
      'HBL/MBL':len(bills)>=2,'Manifest/VGM':wf['vgm_status'] is not None,'Import/Export':n("select count(*) n from transaction_records where job_id=? and module in ('export-crt','import-crt','transshipment-crt')")>0,
      'Delivery/Release':n("select count(*) n from transaction_records where job_id=? and module='delivery-order'")>0,
      'Charges':n("select count(*) n from transaction_records where job_id=? and module in ('detention-collection','storage-cost')")>0,
      'Invoice/Bill':n("select count(*) n from gl_records where job_id=? and module in ('invoice','bills')")>0,
      'Receipt/Payment':n("select count(*) n from gl_records where job_id=? and module in ('receipt','payment')")>0,
      'Agent SOA':n("select count(*) n from transaction_records where job_id=? and module='soa'")>0,
      'Treasury':summary['treasury_records']>0,'Voucher':n('select count(*) n from gl_vouchers where job_id=?')>0,
      'GL':summary['gl_records']>0,'Bank Reconciliation':n("select count(*) n from gl_records where job_id=? and module='bank-reconciliation'")>0,
      'Month-End':n("select count(*) n from gl_records where job_id=? and module='month-end-journals'")>0,
      'Reporting':True,
    }
    out={'job_ref':job_ref,'identity':dict(j),'bills':bills,'workflow':wf,'finance':finance,'summary':summary,'coverage':coverage,
         'complete':all(coverage.values()),'missing':[k for k,v in coverage.items() if not v]}
    c.close(); return out

@router.get('/global-search')
def global_search(q:str=Query(...,min_length=2,max_length=80),x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None)):
    r=role(x_role); c=connect(); like='%'+q.lower()+'%'; out=[]
    agent_clause=''
    args=[]
    if r=='AGENT':
        if not x_agent_scope: c.close(); raise HTTPException(403,'AGENT requires X-Agent-Scope')
        agent_clause=' and a.code=?'; args=[x_agent_scope]
    for row in c.execute('''SELECT t.id,t.module,t.external_ref,j.job_ref,a.code agent_code,t.status FROM transaction_records t JOIN jobs j on j.id=t.job_id JOIN agents a on a.id=t.agent_id
      WHERE (lower(t.external_ref) like ? or lower(j.job_ref) like ? or lower(t.payload_json) like ?)'''+agent_clause+' ORDER BY t.id LIMIT 50',[like,like,like]+args):
        d=dict(row);d['type']='agent';out.append(d)
    if r!='AGENT':
        for table,typ in [('gl_records','gl'),('treasury_records','treasury'),('integration_records','integration')]:
            job_join=' LEFT JOIN jobs j ON j.id=x.job_id '
            for row in c.execute(f'''SELECT x.id,x.module,x.external_ref,j.job_ref,x.status FROM {table} x {job_join}
              WHERE lower(x.external_ref) like ? or lower(COALESCE(j.job_ref,'')) like ? or lower(x.payload_json) like ? ORDER BY x.id LIMIT 50''',(like,like,like)):
                d=dict(row);d['type']=typ;out.append(d)
    c.close(); return {'query':q,'count':len(out),'results':out[:100]}

class EmailBody(BaseModel):
    module_type:str=Field(pattern=r'^(agent|gl|treasury|integration)$')
    module:str=Field(min_length=1,max_length=80)
    record_ref:str=Field(min_length=1,max_length=120)
    job_ref:Optional[str]=Field(default=None,pattern=r'^\d{5}$')
    subject:str=Field(default='M3 ERP transaction update',min_length=1,max_length=160)

@router.post('/simulate-email')
def simulate_email(body:EmailBody,request:Request,x_role:str=Header('VIEWER'),x_actor_id:str=Header('ui-user',alias='X-Actor-Id'),x_csrf_token:Optional[str]=Header(None,alias='X-CSRF-Token')):
    r=role(x_role)
    if 'email' not in ROLE_CAPS[r]: raise HTTPException(403,'Role cannot email')
    if x_csrf_token!=CSRF: raise HTTPException(403,{'code':'CSRF_TOKEN_REQUIRED'})
    c=connect();tx(c)
    try:
        log_event(c,r,x_actor_id,'SIMULATED_EMAIL',body.module_type,body.module,body.record_ref,body.job_ref,'SIMULATED_ONLY',{'subject':body.subject,'external_delivery':False})
        c.execute('COMMIT')
        return {'ok':True,'delivery':'SIMULATED_ONLY','external_delivery':False,'record_ref':body.record_ref}
    finally:c.close()

@router.get('/events')
def events(limit:int=Query(100,ge=1,le=500),x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); rows=[dict(r) for r in c.execute('select * from bulk_events order by id desc limit ?',(limit,))];c.close();return rows

@router.get('/capabilities')
def capabilities(x_role:str=Header('VIEWER')):
    r=role(x_role);return {'role':r,'actions':sorted(ROLE_CAPS[r])}