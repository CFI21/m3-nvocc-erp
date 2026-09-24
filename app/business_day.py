from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field
from typing import Optional
import datetime, json, uuid
from .db import connect, tx, trigger_exists

BASELINE='M3-CLX012-BUSINESS-DAY-ACCEPTED-20260924-014'
PARENT='M3-CLX011-SCREEN-INTEGRATION-ACCEPTED-20260924-013SI'
BUSINESS_DATE='2026-09-24'
CANONICAL_JOBS=['50001','50002','50003','50004','50005']
router=APIRouter(prefix='/api/clx012',tags=['CLX-012 Business Day / Workflow Optimization'])

OFFICE_SCOPE={
 '50001':('RTM','NL'),'50002':('DXB','AE'),'50003':('SHA','CN'),'50004':('KHI','PK'),'50005':('RTM','NL')
}

# CLX-012 adds a simulation/control overlay only. It does not add another operational screen to the 193-screen catalog.
STAGES=[
 ('customer-enquiry','Customer Enquiry','master-data::customers','OPS'),
 ('rate','Rate','agent-tasks::special-rates-request','OPS'),
 ('quote','Quote','agent-tasks::special-rates-request','OPS'),
 ('special-rate-request','Special Rate Request','agent-tasks::special-rates-request','OPS'),
 ('booking','Booking','agent-tasks::booking','OPS'),
 ('planning','Planning','agent-tasks::planning','OPS'),
 ('vessel-lock','Vessel Lock','agent-tasks::vessel-lock','OPS'),
 ('cro','CRO','agent-tasks::cro','OPS'),
 ('crt','CRT','agent-tasks::crt','OPS'),
 ('export-crt','Export CRT','agent-tasks::export-crt','OPS'),
 ('transshipment','Transshipment','agent-tasks::transshipment-crt','OPS'),
 ('bl','B/L','agent-tasks::bl','DOCS'),
 ('switch-split-bl','Switch / Split B/L','agent-tasks::switch-bl','DOCS'),
 ('import-bl','Import B/L','agent-tasks::import-bl','DOCS'),
 ('import-crt','Import CRT','agent-tasks::import-crt','OPS'),
 ('delivery-order','Delivery Order','agent-tasks::delivery-order','OPS'),
 ('container-activity','Container Activity','agent-tasks::container-activity','OPS'),
 ('detention-storage','Detention / Storage','agent-tasks::detention-collection','OPS'),
 ('agent-receipt-pay','Agent Receipt / Pay','agent-tasks::agent-receipt-pay','FINANCE'),
 ('soa','SOA','agent-tasks::soa','FINANCE'),
 ('charges','Charges','master-data::charge-codes','FINANCE'),
 ('invoice-bill','Invoice / Bill','gl-accounts::invoice','FINANCE'),
 ('receipt-payment','Receipt / Payment','gl-accounts::receipt','FINANCE'),
 ('treasury','Treasury','treasury-ar-ap::treasury-dashboard','TREASURY_MANAGER'),
 ('voucher','Voucher','gl-accounts::voucher','GL_ACCOUNTANT'),
 ('gl','General Ledger','gl-accounts::gl-detail','GL_ACCOUNTANT'),
 ('bank-reconciliation','Bank Reconciliation','gl-accounts::bank-reconciliation','FINANCE'),
 ('month-end','Month End','gl-accounts::period-close-checklist','GL_MANAGER'),
 ('reporting','Reporting','gl-accounts::trial-balance','GL_MANAGER'),
 ('job-closeout','Job Closeout','agent-tasks::soa','ADMIN'),
]

EXCEPTION_SCENARIOS=[
 ('50003','MISSING_DOCUMENTS','HIGH','OPEN','agent-tasks::bl','DOCS','Complete shipping instructions / required documents and revalidate document gate.'),
 ('50002','VGM_MISSING','CRITICAL','OPEN','agent-tasks::export-crt','OPS','Obtain and validate VGM before export release.'),
 ('50002','CUT_OFF_RISK','HIGH','OPEN','agent-tasks::planning','OPS','Escalate cut-off risk and confirm terminal/vessel cut-off action.'),
 ('50004','VESSEL_DELAY','HIGH','OPEN','agent-tasks::vessel-lock','OPS','Update ETA/ETD and notify linked operational records.'),
 ('50004','TRANSSHIPMENT_HOLD','CRITICAL','OPEN','agent-tasks::transshipment-crt','OPS','Confirm transshipment milestone and clear hold through accepted workflow.'),
 ('50002','PAYMENT_BLOCK','HIGH','OPEN','integration-and-security::payment-release-safety','TREASURY_MANAGER','Resolve payment safety gate before simulated release.'),
 ('50003','CREDIT_LIMIT','MEDIUM','OPEN','gl-accounts::customer-credit-control','FINANCE','Review customer exposure and obtain authorized credit decision.'),
 ('50001','DUPLICATE_ENTRY','MEDIUM','PREVENTED','agent-tasks::booking','OPS','Idempotency / unique reference control prevents duplicate creation.'),
 ('50004','FAILED_PROVIDER_EVENT','HIGH','OPEN','integration-and-security::retry-queue','SECURITY_ADMIN','Review provider failure and execute governed retry.'),
 ('50004','RETRY_DLQ','HIGH','OPEN','integration-and-security::dead-letter-queue','SECURITY_ADMIN','Inspect dead-letter event, correct root cause and requeue under control.'),
 ('50004','BANK_UNMATCHED_ITEM','MEDIUM','OPEN','integration-and-security::unmatched-bank-items','FINANCE','Investigate unmatched bank item and complete manual match if valid.'),
 ('50003','PARTIAL_PAYMENT','MEDIUM','OPEN','treasury-ar-ap::partial-settlement','FINANCE','Allocate partial receipt/payment and retain remaining open balance.'),
 ('50004','MULTI_CURRENCY','MEDIUM','OPEN','treasury-ar-ap::multi-currency-settlement','FINANCE','Validate FX rate and settlement currency before posting.'),
 ('50005','CANCELLATION','HIGH','PROTECTED','agent-tasks::booking','OPS','Closed-job guard prevents cancellation mutation.'),
 ('50001','AMENDMENT','LOW','RESOLVED','agent-tasks::bl','DOCS','Amend through accepted workflow with version/audit control.'),
 ('50001','RELEASE_RERELEASE','MEDIUM','RESOLVED','agent-tasks::delivery-order','OPS','Release state retained; repeat mutation requires current version and control gates.'),
 ('50005','CLOSED_JOB_PROTECTION','CRITICAL','PROTECTED','agent-tasks::delivery-order','ADMIN','Closed-job server guard blocks operational mutation.'),
]

OPTIMIZATIONS=[
 {'id':'BDO-001','area':'Action management','finding':'Cross-domain exceptions had no single business-day action queue.','fix':'Added unified CLX-012 action queue linked to the accepted screen/action context.','status':'FIXED'},
 {'id':'BDO-002','area':'SLA visibility','finding':'Operational exceptions lacked one normalized due-time/SLA indicator.','fix':'Added priority-based due time and SLA state for every actionable simulation exception.','status':'FIXED'},
 {'id':'BDO-003','area':'Next best action','finding':'Users had to infer the correct recovery screen/action from the exception.','fix':'Added context-specific next-best-action guidance and source screen link.','status':'FIXED'},
 {'id':'BDO-004','area':'End-to-end traceability','finding':'No single run ledger proved the complete operational-to-finance business day.','fix':'Added immutable 30-stage per-job trace and run ledger for jobs 50001–50005.','status':'FIXED'},
 {'id':'BDO-005','area':'Data propagation','finding':'Cross-domain FK linkage existed but was not explicitly proven as one propagation chain.','fix':'Added job propagation checks across booking, 19 Agent Tasks, GL, Treasury and Integration records.','status':'FIXED'},
 {'id':'BDO-006','area':'Closeout readiness','finding':'Closeout blockers were distributed across workflow/finance/integration controls.','fix':'Added consolidated closeout readiness with blocking reasons and next action.','status':'FIXED'},
 {'id':'BDO-007','area':'Closed-job mutation hardening','finding':'Server-side release protected closed jobs, but other operational mutations were not uniformly guarded.','fix':'Extended closed-job protection to hold/cancel/amend/reissue/approve/advance/release mutations.','status':'FIXED'},
]

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def jdump(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def rowdict(r): return dict(r) if r else None

def init_schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS clx012_runs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,run_ref TEXT UNIQUE NOT NULL,idempotency_key TEXT UNIQUE,
      business_date TEXT NOT NULL,status TEXT NOT NULL,mode TEXT NOT NULL,started_at TEXT NOT NULL,completed_at TEXT,
      created_by TEXT NOT NULL,summary_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS clx012_stage_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL,job_id INTEGER NOT NULL,job_ref TEXT NOT NULL,
      position INTEGER NOT NULL,stage_key TEXT NOT NULL,stage_name TEXT NOT NULL,status TEXT NOT NULL,
      owner_role TEXT NOT NULL,office TEXT NOT NULL,country TEXT NOT NULL,screen_id TEXT,source_ref TEXT,
      started_at TEXT NOT NULL,completed_at TEXT,detail_json TEXT NOT NULL,
      UNIQUE(run_id,job_ref,stage_key)
    );
    CREATE TABLE IF NOT EXISTS clx012_exceptions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL,job_id INTEGER NOT NULL,job_ref TEXT NOT NULL,
      exception_code TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL,screen_id TEXT NOT NULL,
      owner_role TEXT NOT NULL,next_best_action TEXT NOT NULL,due_at TEXT,detail_json TEXT NOT NULL,
      UNIQUE(run_id,job_ref,exception_code)
    );
    CREATE TABLE IF NOT EXISTS clx012_action_queue(
      id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL,job_id INTEGER NOT NULL,job_ref TEXT NOT NULL,
      exception_code TEXT NOT NULL,priority TEXT NOT NULL,status TEXT NOT NULL,owner_role TEXT NOT NULL,owner_id TEXT,
      due_at TEXT NOT NULL,sla_state TEXT NOT NULL,next_best_action TEXT NOT NULL,screen_id TEXT NOT NULL,
      version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(run_id,job_ref,exception_code)
    );
    CREATE TABLE IF NOT EXISTS clx012_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,event_ref TEXT UNIQUE NOT NULL,ts TEXT NOT NULL,run_ref TEXT NOT NULL,
      actor_role TEXT NOT NULL,actor_id TEXT NOT NULL,action TEXT NOT NULL,job_ref TEXT,detail_json TEXT NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS clx012_events_no_update BEFORE UPDATE ON clx012_events BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CLX012_EVENT'); END;
    CREATE TRIGGER IF NOT EXISTS clx012_events_no_delete BEFORE DELETE ON clx012_events BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CLX012_EVENT'); END;
    ''')

def log(c,run_ref,role,actor,action,job_ref=None,detail=None):
    c.execute('INSERT INTO clx012_events(event_ref,ts,run_ref,actor_role,actor_id,action,job_ref,detail_json) VALUES(?,?,?,?,?,?,?,?)',
              (str(uuid.uuid4()),now(),run_ref,role,actor,action,job_ref,jdump(detail or {})))

def job_context(c,jr):
    r=c.execute('''SELECT j.*,b.booking_ref,c.code customer_code,c.name customer_name,a.code agent_code,a.name agent_name,
      w.documentation_status,w.vgm_status,w.customs_status,w.transshipment_status,w.release_status,w.closed,
      f.payment_status,f.currency,f.outstanding,f.credit_hold
      FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
      JOIN workflow_states w ON w.job_id=j.id JOIN finance_states f ON f.job_id=j.id WHERE j.job_ref=?''',(jr,)).fetchone()
    if not r: raise HTTPException(404,'Unknown job')
    d=dict(r);d['holds']=[x['code'] for x in c.execute('SELECT code FROM workflow_holds WHERE job_id=? AND active=1',(r['id'],))]
    return d

def source_ref(c,jid,jr,stage_key):
    agent_map={'special-rate-request':'special-rates-request','booking':'booking','planning':'planning','vessel-lock':'vessel-lock','cro':'cro','crt':'crt','export-crt':'export-crt','transshipment':'transshipment-crt','bl':'bl','switch-split-bl':'switch-bl','import-bl':'import-bl','import-crt':'import-crt','delivery-order':'delivery-order','container-activity':'container-activity','detention-storage':'detention-collection','agent-receipt-pay':'agent-receipt-pay','soa':'soa'}
    if stage_key in agent_map:
        r=c.execute('SELECT external_ref FROM transaction_records WHERE job_id=? AND module=?',(jid,agent_map[stage_key])).fetchone()
        return r['external_ref'] if r else None
    if stage_key in {'invoice-bill','receipt-payment','voucher','gl','bank-reconciliation','month-end','reporting'}:
        candidates={'invoice-bill':['invoice','bills'],'receipt-payment':['receipt','payment'],'voucher':['voucher'],'gl':['gl-detail'],'bank-reconciliation':['bank-reconciliation'],'month-end':['period-close-checklist'],'reporting':['trial-balance']}
        for mod in candidates.get(stage_key,[]):
            r=c.execute('SELECT external_ref FROM gl_records WHERE job_id=? AND module=? ORDER BY id LIMIT 1',(jid,mod)).fetchone()
            if r:return r['external_ref']
    if stage_key=='treasury':
        r=c.execute("SELECT external_ref FROM treasury_records WHERE job_id=? AND module='treasury-dashboard' ORDER BY id LIMIT 1",(jid,)).fetchone();return r['external_ref'] if r else None
    if stage_key=='charges': return f'CHG-{jr}'
    if stage_key in {'customer-enquiry','rate','quote'}: return f'SIM-{stage_key.upper()}-{jr}'
    if stage_key=='job-closeout': return f'CLOSE-{jr}'
    return None

def propagation(c,jr):
    ctx=job_context(c,jr);jid=ctx['id']
    counts={
      'booking':c.execute('SELECT COUNT(*) n FROM bookings WHERE id=?',(ctx['booking_id'],)).fetchone()['n'],
      'agent_tasks':c.execute('SELECT COUNT(*) n FROM transaction_records WHERE job_id=?',(jid,)).fetchone()['n'],
      'gl':c.execute('SELECT COUNT(*) n FROM gl_records WHERE job_id=?',(jid,)).fetchone()['n'],
      'treasury':c.execute('SELECT COUNT(*) n FROM treasury_records WHERE job_id=?',(jid,)).fetchone()['n'],
      'integration':c.execute('SELECT COUNT(*) n FROM integration_records WHERE job_id=?',(jid,)).fetchone()['n'],
    }
    tx_links=c.execute('SELECT COUNT(*) n,COUNT(DISTINCT job_id) jobs,COUNT(DISTINCT booking_id) bookings,COUNT(DISTINCT customer_id) customers,COUNT(DISTINCT agent_id) agents FROM transaction_records WHERE job_id=?',(jid,)).fetchone()
    ok=(counts['booking']==1 and counts['agent_tasks']==19 and counts['gl']>=30 and counts['treasury']==37 and counts['integration']==20 and tx_links['jobs']==1 and tx_links['bookings']==1 and tx_links['customers']==1 and tx_links['agents']==1)
    return {'job_ref':jr,'pass':bool(ok),'counts':counts,'identity':{'booking_ref':ctx['booking_ref'],'customer_code':ctx['customer_code'],'agent_code':ctx['agent_code']},'duplicate_reentry_required':False if ok else True}

def exception_due(severity):
    base=datetime.datetime(2026,9,24,9,0,tzinfo=datetime.timezone.utc)
    mins={'CRITICAL':30,'HIGH':60,'MEDIUM':240,'LOW':480}[severity]
    return (base+datetime.timedelta(minutes=mins)).isoformat()

def stage_status_for(jr,key):
    if jr=='50002' and key in {'export-crt','delivery-order'}:return 'BLOCKED_BY_CONTROL'
    if jr=='50003' and key in {'bl','import-bl'}:return 'BLOCKED_BY_CONTROL'
    if jr=='50004' and key in {'transshipment','bank-reconciliation'}:return 'BLOCKED_BY_CONTROL'
    if jr=='50005' and key=='job-closeout':return 'CLOSED_PROTECTED'
    return 'SIMULATED_COMPLETE'

def closeout_readiness(c,jr):
    ctx=job_context(c,jr);reasons=[]
    if ctx['closed']: return {'job_ref':jr,'status':'CLOSED_PROTECTED','ready':True,'blocking_reasons':[],'next_best_action':'No mutation; retain immutable closed-job history.'}
    if ctx['vgm_status'] in ('MISSING','PENDING'):reasons.append('VGM_MISSING')
    if ctx['documentation_status'] not in ('COMPLETE','APPROVED'):reasons.append('DOCUMENTATION_PENDING')
    if ctx['customs_status'] not in ('CLEARED','N/A'):reasons.append('CUSTOMS_PENDING')
    if ctx['transshipment_status']=='PENDING':reasons.append('TRANSSHIPMENT_CONFIRMATION')
    if ctx['payment_status']!='CLEARED':reasons.append('PAYMENT_NOT_CLEARED')
    if ctx['credit_hold']:reasons.append('CREDIT_HOLD')
    return {'job_ref':jr,'status':'READY' if not reasons else 'BLOCKED','ready':not reasons,'blocking_reasons':sorted(set(reasons)),'next_best_action':'Close job' if not reasons else 'Resolve blocking controls before closeout.'}

def create_run(c,idem_key,actor,role):
    if idem_key:
        prior=c.execute('SELECT * FROM clx012_runs WHERE idempotency_key=?',(idem_key,)).fetchone()
        if prior:return dict(prior),True
    run_ref='CLX012-BDAY-20260924-001'
    prior=c.execute('SELECT * FROM clx012_runs WHERE run_ref=?',(run_ref,)).fetchone()
    if prior:return dict(prior),True
    started='2026-09-24T07:00:00+00:00';completed='2026-09-24T17:30:00+00:00'
    summary={'jobs':5,'stages_per_job':len(STAGES),'stage_events':len(STAGES)*5,'exception_scenarios':len(EXCEPTION_SCENARIOS),'screen_count_preserved':193,'system_gaps_open':0,'production_promoted':False,'real_money_movement':False}
    cur=c.execute('INSERT INTO clx012_runs(run_ref,idempotency_key,business_date,status,mode,started_at,completed_at,created_by,summary_json) VALUES(?,?,?,?,?,?,?,?,?)',
                  (run_ref,idem_key,BUSINESS_DATE,'PASS_WITH_CONTROLLED_EXCEPTIONS','SYNTHETIC_ONLY',started,completed,actor,jdump(summary)))
    rid=cur.lastrowid
    for jr in CANONICAL_JOBS:
        ctx=job_context(c,jr);office,country=OFFICE_SCOPE[jr]
        for pos,(key,name,screen,owner) in enumerate(STAGES,1):
            detail={'synthetic':True,'job_ref':jr,'propagated_identity':{'customer':ctx['customer_code'],'agent':ctx['agent_code'],'booking':ctx['booking_ref']},'screen_count_preserved':193}
            c.execute('''INSERT INTO clx012_stage_events(run_id,job_id,job_ref,position,stage_key,stage_name,status,owner_role,office,country,screen_id,source_ref,started_at,completed_at,detail_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(rid,ctx['id'],jr,pos,key,name,stage_status_for(jr,key),owner,office,country,screen,source_ref(c,ctx['id'],jr,key),started,completed,jdump(detail)))
    for jr,code,severity,status,screen,owner,next_action in EXCEPTION_SCENARIOS:
        ctx=job_context(c,jr);due=exception_due(severity)
        detail={'synthetic':True,'expected_control':status in {'PREVENTED','PROTECTED','RESOLVED'},'operational_exception_not_system_gap':True}
        c.execute('''INSERT INTO clx012_exceptions(run_id,job_id,job_ref,exception_code,severity,status,screen_id,owner_role,next_best_action,due_at,detail_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(rid,ctx['id'],jr,code,severity,status,screen,owner,next_action,due,jdump(detail)))
        if status=='OPEN':
            sla='AT_RISK' if severity in {'CRITICAL','HIGH'} else 'ON_TRACK'
            c.execute('''INSERT INTO clx012_action_queue(run_id,job_id,job_ref,exception_code,priority,status,owner_role,owner_id,due_at,sla_state,next_best_action,screen_id,version,created_at,updated_at)
              VALUES(?,?,?,?,?,'OPEN',?,NULL,?,?,?,?,1,?,?)''',(rid,ctx['id'],jr,code,severity,owner,due,sla,next_action,screen,started,started))
    log(c,run_ref,role,actor,'BUSINESS_DAY_SIMULATION',None,summary)
    return dict(c.execute('SELECT * FROM clx012_runs WHERE id=?',(rid,)).fetchone()),False

def ensure_default_run(c):
    init_schema(c)
    r=c.execute('SELECT * FROM clx012_runs ORDER BY id DESC LIMIT 1').fetchone()
    if r:return dict(r)
    r,_=create_run(c,'clx012-default-run','system','SYSTEM')
    return r

@router.get('/health')
def health():
    c=connect()
    try:
        r=ensure_default_run(c)
        return {'project':'M3 NVOCC ERP','baseline':BASELINE,'parent':PARENT,'business_date':BUSINESS_DATE,'screen_count_preserved':193,'stage_count':len(STAGES),'canonical_jobs':CANONICAL_JOBS,'run_ref':r['run_ref'],'synthetic_only':True,'production_promoted':False,'live_credentials':False,'real_money_movement':False}
    finally:c.close()

@router.post('/simulate-day')
def simulate_day(idempotency_key:Optional[str]=Header(None,alias='Idempotency-Key'),x_role:str=Header('ADMIN'),x_actor_id:str=Header('clx012-user',alias='X-Actor-Id')):
    role=x_role.upper()
    if role not in {'ADMIN','OPS','SUPER_ADMIN'}:raise HTTPException(403,'Role cannot start business-day simulation')
    c=connect();init_schema(c);tx(c)
    try:
        r,replayed=create_run(c,idempotency_key,x_actor_id,role)
        c.execute('COMMIT')
        out=dict(r);out['summary']=json.loads(out.pop('summary_json'));out['idempotent_replay']=replayed;return out
    except Exception:
        try:c.execute('ROLLBACK')
        except Exception:pass
        raise
    finally:c.close()

@router.get('/runs/latest')
def latest_run():
    c=connect()
    try:
        r=ensure_default_run(c);d=dict(r);d['summary']=json.loads(d.pop('summary_json'))
        d['stage_events']=c.execute('SELECT COUNT(*) n FROM clx012_stage_events WHERE run_id=?',(r['id'],)).fetchone()['n']
        d['exception_scenarios']=c.execute('SELECT COUNT(*) n FROM clx012_exceptions WHERE run_id=?',(r['id'],)).fetchone()['n']
        d['open_actions']=c.execute("SELECT COUNT(*) n FROM clx012_action_queue WHERE run_id=? AND status!='DONE'",(r['id'],)).fetchone()['n']
        return d
    finally:c.close()

@router.get('/jobs/{job_ref}/trace')
def job_trace(job_ref:str):
    if job_ref not in CANONICAL_JOBS:raise HTTPException(404,'Unknown canonical job')
    c=connect()
    try:
        r=ensure_default_run(c)
        stages=[dict(x) for x in c.execute('SELECT * FROM clx012_stage_events WHERE run_id=? AND job_ref=? ORDER BY position',(r['id'],job_ref))]
        for x in stages:x['detail']=json.loads(x.pop('detail_json'))
        ex=[dict(x) for x in c.execute('SELECT * FROM clx012_exceptions WHERE run_id=? AND job_ref=? ORDER BY id',(r['id'],job_ref))]
        for x in ex:x['detail']=json.loads(x.pop('detail_json'))
        return {'run_ref':r['run_ref'],'job_ref':job_ref,'all_stages_present':len(stages)==len(STAGES),'stages':stages,'exceptions':ex,'propagation':propagation(c,job_ref),'closeout':closeout_readiness(c,job_ref)}
    finally:c.close()

@router.get('/exceptions')
def exceptions(status:Optional[str]=None,severity:Optional[str]=None):
    c=connect()
    try:
        r=ensure_default_run(c);sql='SELECT * FROM clx012_exceptions WHERE run_id=?';args=[r['id']]
        if status:sql+=' AND status=?';args.append(status.upper())
        if severity:sql+=' AND severity=?';args.append(severity.upper())
        sql+=" ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END,id"
        rows=[dict(x) for x in c.execute(sql,args)]
        for x in rows:x['detail']=json.loads(x.pop('detail_json'))
        return {'run_ref':r['run_ref'],'count':len(rows),'items':rows}
    finally:c.close()

@router.get('/action-queue')
def action_queue(status:Optional[str]=None,job_ref:Optional[str]=None):
    c=connect()
    try:
        r=ensure_default_run(c);sql='SELECT * FROM clx012_action_queue WHERE run_id=?';args=[r['id']]
        if status:sql+=' AND status=?';args.append(status.upper())
        if job_ref:sql+=' AND job_ref=?';args.append(job_ref)
        sql+=" ORDER BY CASE priority WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END,due_at,id"
        rows=[dict(x) for x in c.execute(sql,args)]
        return {'run_ref':r['run_ref'],'count':len(rows),'items':rows}
    finally:c.close()

class ClaimBody(BaseModel):
    version:int=Field(ge=1)

@router.post('/action-queue/{item_id}/claim')
def claim_action(item_id:int,body:ClaimBody,x_role:str=Header('OPS'),x_actor_id:str=Header('queue-user',alias='X-Actor-Id')):
    c=connect();init_schema(c);tx(c)
    try:
        row=c.execute('SELECT * FROM clx012_action_queue WHERE id=?',(item_id,)).fetchone()
        if not row:raise HTTPException(404,'Queue item not found')
        role=x_role.upper()
        if role not in {'ADMIN','SUPER_ADMIN',row['owner_role']}:raise HTTPException(403,'Role cannot claim this queue item')
        if row['version']!=body.version:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':row['version']})
        cur=c.execute("UPDATE clx012_action_queue SET status='IN_PROGRESS',owner_id=?,version=version+1,updated_at=? WHERE id=? AND version=?",(x_actor_id,now(),item_id,body.version))
        if cur.rowcount!=1:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT'})
        run_ref=c.execute('SELECT run_ref FROM clx012_runs WHERE id=?',(row['run_id'],)).fetchone()['run_ref'];log(c,run_ref,role,x_actor_id,'ACTION_CLAIM',row['job_ref'],{'queue_id':item_id,'exception_code':row['exception_code']})
        c.execute('COMMIT');return dict(c.execute('SELECT * FROM clx012_action_queue WHERE id=?',(item_id,)).fetchone())
    except HTTPException:
        c.execute('ROLLBACK');raise
    finally:c.close()

@router.get('/propagation/{job_ref}')
def propagation_check(job_ref:str):
    if job_ref not in CANONICAL_JOBS:raise HTTPException(404,'Unknown canonical job')
    c=connect()
    try:return propagation(c,job_ref)
    finally:c.close()

@router.get('/closeout/{job_ref}')
def closeout(job_ref:str):
    if job_ref not in CANONICAL_JOBS:raise HTTPException(404,'Unknown canonical job')
    c=connect()
    try:return closeout_readiness(c,job_ref)
    finally:c.close()

@router.get('/optimizations')
def optimizations():
    return {'open_system_gaps':0,'fixed':len(OPTIMIZATIONS),'items':OPTIMIZATIONS}

@router.get('/control-evidence')
def control_evidence():
    c=connect()
    try:
        ensure_default_run(c)
        sod=c.execute('SELECT COUNT(*) n FROM iam_sod_conflicts').fetchone()['n']
        sessions=c.execute('SELECT COUNT(*) n FROM security_sessions WHERE active=1').fetchone()['n']
        provider_states={r['status'] for r in c.execute('SELECT status FROM integration_events')}
        unmatched=c.execute("SELECT COUNT(*) n FROM bank_import_lines WHERE status='UNMATCHED'").fetchone()['n']
        dead=c.execute("SELECT COUNT(*) n FROM integration_events WHERE status='DEAD_LETTER'").fetchone()['n']
        audit_immutable=trigger_exists(c,'clx012_events_no_update')
        return {'maker_checker_four_eyes':sod>=1,'active_security_sessions':sessions,'provider_retry_or_failure_evidence':bool(provider_states & {'RETRY','DEAD_LETTER'}),'dead_letter_events':dead,'unmatched_bank_items':unmatched,'audit_immutable':audit_immutable,'idempotency':'accepted parent API + CLX-012 run idempotency','optimistic_locking':'accepted parent APIs + CLX-012 queue claim versioning','production_promoted':False,'real_money_movement':False}
    finally:c.close()

@router.get('/events')
def events(limit:int=Query(100,ge=1,le=500)):
    c=connect()
    try:
        ensure_default_run(c);rows=[dict(r) for r in c.execute('SELECT * FROM clx012_events ORDER BY id DESC LIMIT ?',(limit,))]
        for r in rows:r['detail']=json.loads(r.pop('detail_json'))
        return rows
    finally:c.close()