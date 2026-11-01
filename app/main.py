from fastapi import FastAPI,HTTPException,Header,Query,Request
from fastapi.responses import HTMLResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field
from pathlib import Path
from typing import Any,Optional
import json,hashlib,uuid,datetime,sqlite3,time,re,os,hmac
from .db import connect,tx,DB_PATH,IntegrityError,backend_name,approved_database_target,APPROVED_PROJECT_REF,database_health,_uat_target_approved
from .seed import run as seed_run
from .gl import router as gl_router
from .hardening import router as hardening_router
from .treasury import router as treasury_router
from .integration import router as integration_router
from .bulk import router as bulk_router
from .admin import router as admin_router, session as iam_session
from .bulk_permission_governance import (
    enabled as bulk_permission_enabled,
    authorize_job as bulk_authorize_job,
    assert_job_access as bulk_assert_job_access,
)
from .admin_seed import run as admin_seed_run
from .masterdata import router as masterdata_router
from .uat_approval_page import router as uat_approval_router
from .masterdata_seed import run as masterdata_seed_run
from .screen_integration import router as screen_integration_router
from .business_day import router as business_day_router
from .preprod import router as preprod_router, log_request, get_runtime_mode_value
from .prodprep import router as prodprep_router
from .liveprep import router as liveprep_router
from .live_provider_runtime import router as live_provider_router
from .provider_onboarding import router as provider_onboarding_router
from .operations_workbench import router as operations_workbench_router
from .control_tower import router as control_tower_router
from .management_kpi import router as management_kpi_router
from .clx030_finance_entitlements import router as clx030_finance_entitlements_router
from .clx032_finance_controls import router as clx032_finance_controls_router
from .clx033_finance_transaction_governance import router as clx033_finance_transaction_governance_router
from .clx034_smart_approval_fast_track import router as clx034_smart_approval_fast_track_router
from .clx035_ho_completion import router as clx035_ho_completion_router
from .clx036_ho_business_day import router as clx036_ho_business_day_router
from .clx042_agent_workspace import router as clx042_agent_workspace_router
from .clx044_gl_exact_flow import router as clx044_gl_exact_flow_router
from .clx045_gl_reporting import router as clx045_gl_reporting_router
from .clx046_treasury_hardening import router as clx046_treasury_hardening_router
from .clx047_integration_hardening import router as clx047_integration_hardening_router
from .clx048_admin_masterdata_hardening import router as clx048_admin_masterdata_hardening_router
from .clx049_booking_bl_workspace import router as clx049_booking_bl_workspace_router
from .clx070_equipment_runtime import router as clx070_equipment_runtime_router
from .clx070_container_master_control import router as clx070_container_master_control_router
from .clx071_container_journey import router as clx071_container_journey_router
from .clx072_equipment_network import router as clx072_equipment_network_router
from .clx073_equipment_optimization import router as clx073_equipment_optimization_router
from .clx074_inspection import router as clx074_inspection_router
from .clx074_repair import router as clx074_repair_router
from .clx074_control import router as clx074_control_router
from .clx075_lease import router as clx075_lease_router
from .clx075_readiness import router as clx075_readiness_router
from .clx077_dummy_bank import router as clx077_dummy_bank_router
from .nvocc_principal_extensions import router as nvocc_principal_extensions_router
from .crt_governance import router as crt_governance_router
from .mrg import router as mrg_router
from .agent_setup import router as agent_setup_router, assert_booking_rules
from .ho_operational_setup import router as ho_operational_setup_router
from .final_ho_setup import router as final_ho_setup_router

HERE=Path(__file__).resolve().parent
META=json.loads((HERE/'module_meta.json').read_text())
MODULES={m['key']:m for m in META['modules']}
PRIMARY=META['primary_keys']
CLX086_FINAL_BUILD_BASELINE='cc4963e73dcad8d584218b0a5aab445318a61885'
app=FastAPI(title='M3 NVOCC ERP CLX-018 Production Web/API Integration',version='0.86.0',description='Dedicated M3 production web/API integration. Production traffic, live providers and real money remain blocked.')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['https://m3-dev-sprint2-isolated-ui.onrender.com'],
    allow_credentials=False,
    allow_methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'],
    allow_headers=['*'],
    expose_headers=['X-Request-Id','X-Correlation-Id'],
)
app.mount('/static',StaticFiles(directory=HERE/'static'),name='static')
app.include_router(clx030_finance_entitlements_router)
app.include_router(clx032_finance_controls_router)
app.include_router(clx033_finance_transaction_governance_router)
app.include_router(clx034_smart_approval_fast_track_router)
app.include_router(clx035_ho_completion_router)
app.include_router(clx036_ho_business_day_router)
app.include_router(clx042_agent_workspace_router)
app.include_router(clx044_gl_exact_flow_router)
app.include_router(clx045_gl_reporting_router)
app.include_router(clx046_treasury_hardening_router)
app.include_router(clx047_integration_hardening_router)
app.include_router(clx048_admin_masterdata_hardening_router)
app.include_router(clx049_booking_bl_workspace_router)
app.include_router(clx070_equipment_runtime_router)
app.include_router(clx070_container_master_control_router)
app.include_router(clx071_container_journey_router)
app.include_router(clx072_equipment_network_router)
app.include_router(clx073_equipment_optimization_router)
app.include_router(clx074_inspection_router)
app.include_router(clx074_repair_router)
app.include_router(clx074_control_router)
app.include_router(clx075_lease_router)
app.include_router(clx075_readiness_router)
app.include_router(clx077_dummy_bank_router)
app.include_router(nvocc_principal_extensions_router)
app.include_router(crt_governance_router)
app.include_router(mrg_router)
app.include_router(agent_setup_router)
app.include_router(ho_operational_setup_router)
app.include_router(final_ho_setup_router)
app.include_router(management_kpi_router)
app.include_router(control_tower_router)
app.include_router(operations_workbench_router)
app.include_router(provider_onboarding_router)
app.include_router(live_provider_router)
app.include_router(liveprep_router)
app.include_router(prodprep_router)
app.include_router(preprod_router)
app.include_router(business_day_router)
app.include_router(screen_integration_router)
app.include_router(admin_router)
app.include_router(masterdata_router)
app.include_router(uat_approval_router)
app.include_router(bulk_router)
app.include_router(integration_router)
app.include_router(treasury_router)
app.include_router(hardening_router)
app.include_router(gl_router)

ROLE_PERMS={
 'ADMIN':set('create edit approve release hold cancel amend reissue reverse advance view'.split()),
 'SUPER_ADMIN':set('create edit approve release hold cancel amend reissue reverse advance view'.split()),
 'OPS':set('create edit approve release hold amend reissue advance view'.split()),
 'DOCS':set('create edit approve hold amend reissue view'.split()),
 'FINANCE':set('create edit approve hold reverse view'.split()),
 'GL_ACCOUNTANT':set('view'.split()),
 'GL_MANAGER':set('view'.split()),
 'TREASURY':set('view'.split()),
 'AUDITOR':set('view'.split()),
 'AGENT':set('create edit amend view'.split()),
 'VIEWER':set('view'.split())
}
RELEASE_MODULES={'trt','export-trt','import-trt','transshipment-trt','delivery-order','import-bl','cro'}
GOVERNED_POST_APPROVAL_MODULES={'booking','trt','export-trt','import-trt','transshipment-trt'}
POST_APPROVAL_LOCK_STATUSES={'approved','released','issued','completed','closed'}

# Cycle 24: formatting policy only. Issuance must remain within a governed
# RFA action after session, agent/port scope and unique-number checks.
BOOKING_AGENT_REFERENCE_PREFIXES={
    'POL_EXPORT':'SE',
    'POD_IMPORT':'SI',
    'TRANSSHIPMENT':'TS',
    'PRINCIPAL':'PR',
}

def booking_agent_reference_prefix(operational_responsibility: str) -> str:
    key=str(operational_responsibility or '').strip().upper()
    if key not in BOOKING_AGENT_REFERENCE_PREFIXES:
        raise HTTPException(422, {'code':'UNRECOGNIZED_AGENT_RESPONSIBILITY'})
    return BOOKING_AGENT_REFERENCE_PREFIXES[key]


def booking_port_allowed(port_code, allowed_ports, *, all_ports_granted=False):
    """Fail closed unless a verified permission grant covers this exact port.

    Caller must obtain allowed_ports/all_ports_granted from authoritative IAM,
    not from submitted Booking payloads or caller-supplied headers.
    """
    if not isinstance(port_code,str):
        return False
    port=port_code.strip().upper()
    if not port or not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}', port):
        return False
    if all_ports_granted is True:
        return True
    if not isinstance(allowed_ports,(set,list,tuple,frozenset)):
        return False
    return port in {p.strip().upper() for p in allowed_ports if isinstance(p,str) and re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}',p.strip().upper())}


def booking_iam_agent_granted(conn, user_id, agent_code):
    """Verify an active agent-party assignment; no implicit global grant."""
    if not isinstance(user_id,int) or isinstance(user_id,bool) or user_id<=0:
        return False
    if not isinstance(agent_code,str):
        return False
    code=agent_code.strip()
    if not code or any(ch.isspace() for ch in code):
        return False
    row=conn.execute("""SELECT 1 FROM iam_party_access
        WHERE user_id=? AND party_type='AGENT' AND party_key=?
        AND status='ACTIVE' LIMIT 1""",(user_id,code)).fetchone()
    return bool(row)


def booking_validate_authorized_ports(fields, grants):
    """Validate Booking ports against verified, per-field IAM grants.

    A missing grant denies a populated field; the caller may not supply grants
    from request payload. Principal/HO all-port access requires an explicit
    trusted Boolean True for that field.
    """
    mapping={'POL':'POL','POD':'POD','POT (1)':'TS','POT (2)':'TS'}
    if not isinstance(fields,dict):
        raise HTTPException(422,{'code':'BOOKING_FIELDS_INVALID'})
    if not isinstance(grants,dict):
        raise HTTPException(403,{'code':'BOOKING_PORT_PERMISSION_MISSING'})
    for field,scope in mapping.items():
        value=fields.get(field)
        if value is None or value=='':
            continue
        if not isinstance(value,str) or not value.strip() or not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}',value.strip().upper()):
            raise HTTPException(422,{'code':'BOOKING_PORT_INVALID','field':field})
        rule=grants.get(scope)
        if not isinstance(rule,dict) or not booking_port_allowed(
            value.strip().upper(),rule.get('ports'),all_ports_granted=rule.get('all_ports') is True
        ):
            raise HTTPException(403,{'code':'BOOKING_PORT_NOT_AUTHORIZED','field':field})
    return True


def booking_agent_port_grants(conn,user_id,agent_code):
    """Derive explicit scoped port grants from existing IAM and Agent Network masters.

    No user-supplied fields or headers can grant access. Port coverage must
    identify the agent, a verified operating office, a scope and an exact port.
    """
    from .admin import _assigned_agent_offices
    grants={scope:{'ports':set(),'all_ports':False} for scope in ('POL','POD','TS')}
    if not isinstance(user_id,int) or isinstance(user_id,bool) or user_id<=0 or not isinstance(agent_code,str) or not agent_code.strip():
        return grants
    assigned=_assigned_agent_offices(conn,user_id,agent_code)
    # Deny offices with conflicting country grants, regardless of row order.
    offices={}
    ambiguous_offices=set()
    for item in (assigned or ()):
        if not hasattr(item,'keys') or 'office_code' not in item.keys() or 'country_code' not in item.keys():
            continue
        office=item['office_code']
        country=item['country_code']
        if not isinstance(office,str) or not isinstance(country,str):
            continue
        office=office.strip().upper()
        country=country.strip().upper()
        if not office or not re.fullmatch(r'[A-Z]{2}',country):
            continue
        if office in ambiguous_offices:
            continue
        if office in offices and offices[office]!=country:
            ambiguous_offices.add(office)
            offices.pop(office,None)
        else:
            offices[office]=country
    for office in ambiguous_offices:
        offices.pop(office,None)
    if not offices:
        return grants
    rows=conn.execute("""SELECT payload_json FROM md_records
        WHERE domain='configuration' AND status='ACTIVE'""").fetchall()
    for row in rows:
        try:
            config=json.loads(row['payload_json'] or '{}')
        except (ValueError,TypeError):
            continue
        if not isinstance(config,dict):
            continue
        if (str(config.get('config_type') or '').strip().upper()!='AGENT_NETWORK'
                or str(config.get('network_type') or '').strip().upper()!='PORT_COVERAGE'
                or str(config.get('agent_code') or '').strip().upper()!=str(agent_code or '').strip().upper()
                or str(config.get('office_code') or '').strip().upper() not in offices):
            continue
        active_day=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        start=str(config.get('effective_from') or '0001-01-01')
        end=str(config.get('effective_to') or '9999-12-31')
        # Only ISO dates are eligible; malformed effective periods cannot grant access.
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',start) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',end):
            continue
        try:
            datetime.date.fromisoformat(start)
            datetime.date.fromisoformat(end)
        except ValueError:
            continue
        if start>end or not start<=active_day<=end:
            continue
        scope=str(config.get('scope') or '').strip().upper()
        port=str(config.get('port_code') or '').strip().upper()
        office_code=str(config.get('office_code') or '').strip().upper()
        # Network configuration never grants rights without a matching active
        # IAM country/office and a separately approved, active Port Master.
        if scope not in grants or not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}',port):
            continue
        if offices.get(office_code)!=port[:2]:
            continue
        master=conn.execute("""SELECT payload_json FROM md_records
            WHERE domain='port' AND record_key=? AND status='ACTIVE'""",(port,)).fetchone()
        if not master:
            continue
        try:
            code=str(json.loads(master['payload_json'] or '{}').get('unlocode') or '').strip().upper()
        except (ValueError,TypeError,AttributeError):
            continue
        if code!=port:
            continue
        grants[scope]['ports'].add(port)
    return grants


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

def governed_job_actor(conn,session_token,job_ref,action):
    if not bulk_permission_enabled(): return None
    s=iam_session(conn,session_token)
    out=bulk_assert_job_access(conn,s['user_id'],job_ref,'agent-tasks',action)
    roles=[m['role'] for m in out.get('matches',[]) if m.get('effect')=='ALLOW']
    if not roles:
        raise HTTPException(403,{'code':'GOVERNED_JOB_ROLE_NOT_GRANTED'})
    role=roles[0]
    return {'session':s,'role':role,'access':out}

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

def module_db_names(module):
    return ('transshipment-trt','transshipment-crt') if module=='transshipment-trt' else (module,)

def module_where(module):
    names=module_db_names(module)
    if len(names)==1:return 't.module=?',list(names)
    return 't.module IN (?,?)',list(names)

def get_tx(conn,module,tid,role,agent_scope,customer_scope):
    clause,args=scope_clause(role,agent_scope,customer_scope)
    mw,margs=module_where(module)
    r=conn.execute(tx_query(mw+' AND t.id=?')+clause,margs+[tid]+args).fetchone()
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
    if module=='booking' and 'Equipment Lines' in fields:
        lines=fields['Equipment Lines']
        if not isinstance(lines,list) or len(lines)>500:
            errs.append('INVALID_EQUIPMENT_LINES')
        else:
            selected_types=set()
            for line in lines:
                if not isinstance(line,dict):
                    errs.append('INVALID_EQUIPMENT_LINE'); break
                size=str(line.get('size','')).replace("'","")
                selected_key=(size,str(line.get('type','')).strip().upper())
                if selected_key in selected_types:
                    errs.append('DUPLICATE_EQUIPMENT_TYPE'); break
                selected_types.add(selected_key)
                try:
                    qty_raw=line.get('quantity')
                    if isinstance(qty_raw,bool) or not str(qty_raw).isdigit(): raise ValueError('Invalid quantity')
                    qty=int(qty_raw)
                    teu=float(line.get('teu'))
                    expected=qty*(1 if size=='20' else 2)
                    valid=qty>=1 and qty<=9999 and size in ('20','40','45') and abs(teu-expected)<0.001
                except (ValueError,TypeError,OverflowError):
                    valid=False
                if not valid:
                    errs.append('INVALID_EQUIPMENT_LINE'); break
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
        pass
    return errs

def job_context(conn,jr):
    r=conn.execute('''SELECT j.*,c.code customer_code,c.name customer_name,a.code agent_code,b.booking_ref,v.voyage_no,vs.name vessel_name,
       f.payment_status,f.currency,f.outstanding,f.credit_hold,co.container_no,bl.bill_no hbl_no,w.documentation_status,w.vgm_status,w.customs_status,w.transshipment_status,w.release_status,w.closed,w.version workflow_version
       FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id JOIN bookings b ON b.id=j.booking_id JOIN voyages v ON v.id=j.voyage_id JOIN vessels vs ON vs.id=v.vessel_id
       JOIN finance_states f ON f.job_id=j.id LEFT JOIN containers co ON co.id=(SELECT MIN(c2.id) FROM containers c2 WHERE c2.job_id=j.id) LEFT JOIN bills bl ON bl.id=(SELECT MIN(b2.id) FROM bills b2 WHERE b2.job_id=j.id AND b2.kind='HBL') JOIN workflow_states w ON w.job_id=j.id WHERE j.job_ref=?''',(jr,)).fetchone()
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
    sql="UPDATE transaction_records SET status='Released',version=version+1,updated_at=? WHERE job_id=? AND module IN ('delivery-order','import-trt','import-bl')"
    args=[now(),jid]
    if exclude_id is not None: sql+=' AND id<>?'; args.append(exclude_id)
    conn.execute(sql,args)
    q="SELECT id,payload_json,module FROM transaction_records WHERE job_id=? AND module IN ('delivery-order','import-trt','import-bl')"
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
    # One-time Admin setup is independently protected by a private 32+ character token,
    # host-pinned Neon UAT target, and an INACTIVE-only administrator check.
    setup_path=request.url.path=='/api/admin/auth/uat-admin-setup'
    setup_exempt=setup_path and request.method.upper() in {'GET','POST'}
    recovery_page_exempt=(request.url.path=='/api/admin/auth/uat-recovery-page' and request.method.upper()=='GET' and os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper()=='OFF')
    login_page_exempt=(request.url.path=='/api/admin/auth/uat-login-verify-page' and request.method.upper()=='GET' and os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper()=='OFF')
    maker_checker_page_exempt=(request.url.path=='/api/admin/auth/uat-maker-checker-verify-page' and request.method.upper()=='GET' and os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper()=='OFF')
    approval_page_exempt=(request.url.path=='/api/admin/auth/uat-masterdata-approval-page' and request.method.upper()=='GET' and os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper()=='OFF')
    production_traffic=os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper()
    uat_token=os.getenv('M3_UAT_TOKEN','')
    supplied_uat=request.headers.get('X-M3-UAT-Token','')
    uat_allowed=bool(uat_token and supplied_uat and hmac.compare_digest(uat_token,supplied_uat))
    try:
        if production_traffic!='ON' and not health_exempt and not setup_exempt and not recovery_page_exempt and not login_page_exempt and not maker_checker_page_exempt and not approval_page_exempt and not uat_allowed:
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
    response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' https://m3-nvocc-web-latest.onrender.com https://m3-nvocc-api-latest-1.onrender.com; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'no-cache'
    return response

@app.get('/',response_class=HTMLResponse)
def root(): return (HERE/'static/M3_NVOCC_ERP_CLX-015_LIVE_ENVIRONMENT_PREP_20260924.html').read_text()
@app.get('/api/v1/health')
def health(): return {'project':'M3 NVOCC ERP','baseline':'M3-CLX009-ACCEPTED-20260924-011','database':backend_name(),'production_promoted':False,'sandbox_only':True,'live_credentials':False,'live_bank_api':False,'live_tax_api':False,'live_carrier_api':False,'real_payment_execution':False}
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
        result['ready']=bool(
          health['status']=='ok' and jobs==5 and result['database_target_approved']
          and (result['configured_project_ref']==APPROVED_PROJECT_REF or _uat_target_approved())
          and result['production_traffic']=='OFF'
          and result['live_providers']=='OFF'
          and result['real_money']=='OFF'
        )
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
def list_records(module:str,job_ref:Optional[str]=None,status:Optional[str]=None,q:Optional[str]=None,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module); conn=connect()
    try:
        governed=bulk_permission_enabled()
        if governed:
            if not x_m3_session:
                raise HTTPException(401,{'code':'SESSION_REQUIRED_FOR_JOB_SCOPE'})
            ss=iam_session(conn,x_m3_session)
            clause='';args=[]
        else:
            role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope)
            clause,args=scope_clause(role,ascope,cscope)
        where,vals=module_where(module)
        if job_ref: where+=' AND j.job_ref=?'; vals.append(job_ref)
        if status: where+=' AND t.status=?'; vals.append(status)
        rows=conn.execute(tx_query(where)+clause+' ORDER BY t.id',vals+args).fetchall()
        if governed:
            filtered=[]
            for record in rows:
                authorization=bulk_authorize_job(conn,ss['user_id'],record['job_ref'],'agent-tasks','view')
                if authorization['allowed']:
                    filtered.append(record)
            rows=filtered
        out=[serialize_tx(record) for record in rows]
        if q:
            qq=q.lower()
            out=[record for record in out if qq in json.dumps(record).lower()]
        return {'module':module,'count':len(out),'records':out}
    finally:
        conn.close()

@app.get('/api/v1/{module}/{tid}')
def get_record(module:str,tid:int,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module); conn=connect()
    try:
        if bulk_permission_enabled():
            mw,margs=module_where(module)
            r=conn.execute(tx_query('t.id=? AND '+mw),[tid]+margs).fetchone()
            if not r:
                raise HTTPException(404,'Transaction not found')
            governed_job_actor(conn,x_m3_session,r['job_ref'],'view')
        else:
            role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope)
            r=get_tx(conn,module,tid,role,ascope,cscope)
        return serialize_tx(r)
    finally:
        conn.close()

@app.get('/api/v1/cro/agent-defaults/{job_ref}')
def cro_agent_defaults_for_job(job_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    """Read-only, IAM-scoped preview of approved Agent defaults for a CRO draft."""
    from .cro_agent_defaults import CROAgentLinkError,map_agent_cro_defaults
    if not bulk_permission_enabled():
        raise HTTPException(403,{'code':'CRO_IAM_REQUIRED'})
    conn=connect()
    try:
        governed_job_actor(conn,x_m3_session,job_ref,'view')
        ctx=job_context(conn,job_ref)
        row=conn.execute(
            "SELECT record_key,status,version,payload_json FROM md_records "
            "WHERE domain='agent' AND record_key=? AND status='ACTIVE'",
            (ctx['agent_code'],)
        ).fetchone()
        if not row:
            raise HTTPException(409,{'code':'CRO_APPROVED_AGENT_MASTER_NOT_FOUND'})
        try:
            mapped=map_agent_cro_defaults(
                booking_agent_code=ctx['agent_code'],
                agent_record_key=row['record_key'],
                agent_status=row['status'],
                agent_version=int(row['version']),
                approved_agent_payload=json.loads(row['payload_json'] or '{}'),
            )
        except (CROAgentLinkError,ValueError,TypeError) as exc:
            raise HTTPException(409,{'code':'CRO_AGENT_DEFAULTS_INVALID'}) from exc
        return {'job_ref':job_ref,'booking_ref':ctx['booking_ref'],**mapped}
    finally:
        conn.close()

@app.post('/api/v1/{module}',status_code=201)
async def create_record(module:str,body:CreateBody,request:Request,x_role:str=Header('VIEWER'),idempotency_key:Optional[str]=Header(None,alias='Idempotency-Key'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module); conn=connect()
    try:
        if bulk_permission_enabled():
            ga=governed_job_actor(conn,x_m3_session,body.job_ref,'create'); role=ga['role'];ascope=cscope=None
        else:
            role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope)
            if not role_ok(role,'create'): raise HTTPException(403,'Role cannot create')
        raw=await request.body(); rh=hashlib.sha256(raw).hexdigest(); tx(conn)
    except BaseException:
        conn.close()
        raise
    try:
        # Revoked agent/port access must also block idempotent create replays.
        if module=='booking' and bulk_permission_enabled() and role=='AGENT':
            replay_ctx=job_context(conn,body.job_ref)
            if not booking_iam_agent_granted(conn,ga['session']['user_id'],replay_ctx['agent_code']):
                raise HTTPException(403,{'code':'BOOKING_AGENT_LOGIN_NOT_AUTHORIZED'})
            booking_validate_authorized_ports(
                body.fields,booking_agent_port_grants(conn,ga['session']['user_id'],replay_ctx['agent_code']))
        if idempotency_key:
            prior=conn.execute('SELECT * FROM idempotency_keys WHERE actor_role=? AND idem_key=?',(role,idempotency_key)).fetchone()
            if prior:
                if prior['request_hash']!=rh: raise HTTPException(409,'IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST')
                conn.execute('COMMIT'); return JSONResponse(json.loads(prior['response_json']),status_code=prior['status_code'])
        ctx=job_context(conn,body.job_ref)
        if module=='booking' and bulk_permission_enabled() and role=='AGENT' and not booking_iam_agent_granted(conn,ga['session']['user_id'],ctx['agent_code']):
            raise HTTPException(403,{'code':'BOOKING_AGENT_LOGIN_NOT_AUTHORIZED'})
        if module=='booking' and bulk_permission_enabled() and role=='AGENT':
            booking_validate_authorized_ports(body.fields,booking_agent_port_grants(conn,ga['session']['user_id'],ctx['agent_code']))
        if role=='AGENT' and ctx['agent_code']!=ascope: raise HTTPException(403,'Job outside agent scope')
        errs=validate_fields(module,ctx,body.fields)
        if errs: raise HTTPException(422,{'codes':errs})
        if module=='booking':
            if str(body.fields.get('Status','Draft')).strip().lower() != 'draft':
                raise HTTPException(409,{'code':'BOOKING_STATUS_ACTION_REQUIRED'})
            if any(k in body.fields for k in ('Approval #','Approval Ref','Approval Reference','Approved By','Approved At','RFA Submitted By')):
                raise HTTPException(409,{'code':'BOOKING_APPROVAL_REFERENCE_ACTION_REQUIRED'})
            assert_booking_rules(conn,ctx,body.fields,None)
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
def update_record(module:str,tid:int,body:UpdateBody,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module); conn=connect(); tx(conn)
    try:
        if bulk_permission_enabled():
            r=conn.execute(tx_query('t.id=? AND t.module=?'),(tid,module)).fetchone()
            if not r: raise HTTPException(404,'Transaction not found')
            ga=governed_job_actor(conn,x_m3_session,r['job_ref'],'edit'); role=ga['role'];ascope=cscope=None
        else:
            role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope)
            if not role_ok(role,'edit'): raise HTTPException(403,'Role cannot edit')
            r=get_tx(conn,module,tid,role,ascope,cscope)
        before=serialize_tx(r); ctx=job_context(conn,r['job_ref'])
        if module=='booking' and str(r['status']).strip().lower()!='draft':
            raise HTTPException(409,{'code':'BOOKING_DRAFT_ONLY_EDIT','current_status':r['status']})
        if module=='booking' and bulk_permission_enabled() and role=='AGENT' and not booking_iam_agent_granted(conn,ga['session']['user_id'],ctx['agent_code']):
            raise HTTPException(403,{'code':'BOOKING_AGENT_LOGIN_NOT_AUTHORIZED'})
        if module=='booking' and bulk_permission_enabled() and role=='AGENT':
            booking_validate_authorized_ports({**json.loads(r['payload_json']),**body.fields},booking_agent_port_grants(conn,ga['session']['user_id'],ctx['agent_code']))
        if module in GOVERNED_POST_APPROVAL_MODULES and str(r['status']).lower() in POST_APPROVAL_LOCK_STATUSES:
            raise HTTPException(409,{'code':'CRT_REQUIRED_AFTER_APPROVAL','module':module,'record_id':tid})
        if r['version']!=body.version: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        if module=='booking':
            previous_fields=json.loads(r['payload_json'])
            for reference_field in ('Approval #','Approval Ref','Approval Reference','Approved By','Approved At','RFA Submitted By'):
                if reference_field in body.fields and str(body.fields[reference_field] or '').strip()!=str(previous_fields.get(reference_field) or '').strip():
                    raise HTTPException(409,{'code':'BOOKING_APPROVAL_REFERENCE_ACTION_REQUIRED'})
        if module=='booking' and 'Status' in body.fields and str(body.fields['Status']).strip().lower()!=str(r['status']).strip().lower():
            raise HTTPException(409,{'code':'BOOKING_STATUS_ACTION_REQUIRED'})
        payload=json.loads(r['payload_json']); payload.update(body.fields); errs=validate_fields(module,ctx,payload)
        if errs: raise HTTPException(422,{'codes':errs})
        if module=='booking': assert_booking_rules(conn,ctx,payload,tid)
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
def delete_record(module:str,tid:int,version:int=Query(...,ge=1),x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module); conn=connect(); tx(conn)
    try:
        if bulk_permission_enabled():
            r=conn.execute(tx_query('t.id=? AND t.module=?'),(tid,module)).fetchone()
            if not r: raise HTTPException(404,'Transaction not found')
            ga=governed_job_actor(conn,x_m3_session,r['job_ref'],'admin'); role=ga['role'];ascope=cscope=None
        else:
            role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope)
            if role not in {'ADMIN','SUPER_ADMIN'}: raise HTTPException(403,'ADMIN only delete')
            r=get_tx(conn,module,tid,role,ascope,cscope)
        if module=='booking' and str(r['status']).strip().lower()!='draft':
            raise HTTPException(409,{'code':'BOOKING_DELETE_REQUIRES_DRAFT','current_status':r['status']})
        if module in GOVERNED_POST_APPROVAL_MODULES and str(r['status']).lower() in POST_APPROVAL_LOCK_STATUSES:
            raise HTTPException(409,{'code':'CRT_REQUIRED_AFTER_APPROVAL','module':module,'record_id':tid})
        if r['version']!=version: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        before=serialize_tx(r); conn.execute('DELETE FROM transaction_records WHERE id=?',(tid,)); audit(conn,role,None,'DELETE',module,tid,r['job_id'],before,None,{})
        conn.execute('COMMIT'); return {'deleted':True,'id':tid}
    except HTTPException: conn.execute('ROLLBACK'); raise
    finally: conn.close()

@app.post('/api/v1/{module}/{tid}/actions/{action}')
def action_record(module:str,tid:int,action:str,body:ActionBody,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module); action=action.lower()
    if action=='submit-rfa' and module!='booking':
        raise HTTPException(422,{'code':'BOOKING_RFA_ACTION_ONLY'})
    if module=='booking' and action in {'submit-rfa','approve'} and not bulk_permission_enabled():
        raise HTTPException(403,{'code':'BOOKING_APPROVAL_FLOW_REQUIRES_IAM'})
    if module=='booking' and action not in {'submit-rfa','approve','hold','cancel','amend'}:
        raise HTTPException(422,{'code':'BOOKING_ACTION_NOT_SUPPORTED'})
    if action=='release' and module not in RELEASE_MODULES: raise HTTPException(422,{'code':'MODULE_NOT_RELEASABLE'})
    conn=connect(); tx(conn)
    try:
        if bulk_permission_enabled():
            mw,margs=module_where(module)
            r=conn.execute(tx_query('t.id=? AND '+mw),[tid]+margs).fetchone()
            if not r: raise HTTPException(404,'Transaction not found')
            ga=governed_job_actor(conn,x_m3_session,r['job_ref'],'edit' if module=='booking' and action=='submit-rfa' else action); role=ga['role'];ascope=cscope=None
        else:
            role,ascope,cscope=actor(x_role,x_agent_scope,x_customer_scope)
            if not role_ok(role,'edit' if module=='booking' and action=='submit-rfa' else action): raise HTTPException(403,f'Role cannot {action}')
            r=get_tx(conn,module,tid,role,ascope,cscope)
        before=serialize_tx(r)
        if module in GOVERNED_POST_APPROVAL_MODULES and str(r['status']).lower() in POST_APPROVAL_LOCK_STATUSES and action in {'amend','reissue','cancel','reverse'}:
            raise HTTPException(409,{'code':'CRT_REQUIRED_AFTER_APPROVAL','module':module,'record_id':tid,'action':action})
        if r['version']!=body.version: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        ctx=job_context(conn,r['job_ref']); payload=json.loads(r['payload_json']); new_status=r['status']
        if ctx['closed'] and action in {'submit-rfa','approve','release','hold','cancel','amend','reissue','advance'}:
            audit(conn,role,ascope or cscope,'CLOSED_JOB_BLOCKED',module,tid,r['job_id'],before,before,{'attempted_action':action})
            conn.execute('COMMIT')
            return JSONResponse({'detail':{'code':'JOB_CLOSED','action':action}},status_code=422)
        if module=='booking' and action in {'hold','cancel'} and str(r['status']).strip().lower() not in {'draft','rfa'}:
            raise HTTPException(409,{'code':'BOOKING_TERMINAL_STATUS_LOCK','current_status':r['status']})
        if module=='booking' and action=='submit-rfa':
            if str(r['status']).strip().lower()!='draft':
                raise HTTPException(409,{'code':'BOOKING_DRAFT_REQUIRED_FOR_RFA'})
            if bulk_permission_enabled() and role=='AGENT':
                if not booking_iam_agent_granted(conn,ga['session']['user_id'],ctx['agent_code']):
                    raise HTTPException(403,{'code':'BOOKING_AGENT_LOGIN_NOT_AUTHORIZED'})
                booking_validate_authorized_ports(
                    payload,booking_agent_port_grants(conn,ga['session']['user_id'],ctx['agent_code']))
            errs=validate_fields(module,ctx,payload)
            if errs: raise HTTPException(422,{'codes':errs})
            assert_booking_rules(conn,ctx,payload,tid)
        if module=='booking' and action=='amend':
            if str(r['status']).strip().lower()!='rfa':
                raise HTTPException(409,{'code':'BOOKING_RFA_REQUIRED_FOR_AMEND'})
            if bulk_permission_enabled() and role=='AGENT':
                if not booking_iam_agent_granted(conn,ga['session']['user_id'],ctx['agent_code']):
                    raise HTTPException(403,{'code':'BOOKING_AGENT_LOGIN_NOT_AUTHORIZED'})
                booking_validate_authorized_ports(
                    payload,booking_agent_port_grants(conn,ga['session']['user_id'],ctx['agent_code']))
        if module=='booking' and action=='approve' and str(r['status']).strip().lower()!='rfa':
            raise HTTPException(409,{'code':'BOOKING_RFA_REQUIRED_BEFORE_APPROVAL'})
        if module=='booking' and action=='approve':
            # Approval references are allocated transactionally from the existing
            # governed sequence. Never accept a client-supplied approval number.
            if role not in {'ADMIN','SUPER_ADMIN'} or not bulk_permission_enabled():
                raise HTTPException(403,{'code':'BOOKING_INDEPENDENT_APPROVER_REQUIRED'})
            submitting_user=str(payload.get('RFA Submitted By') or '').strip()
            checker_user=str(ga['session'].get('user_ref') or '').strip()
            if not submitting_user:
                raise HTTPException(409,{'code':'BOOKING_RFA_MAKER_IDENTITY_REQUIRED'})
            if not checker_user or submitting_user==checker_user:
                raise HTTPException(403,{'code':'BOOKING_FOUR_EYES_VIOLATION'})
            approval_errors=validate_fields(module,ctx,payload)
            if approval_errors:
                raise HTTPException(422,{'codes':approval_errors})
            assert_booking_rules(conn,ctx,payload,tid)
            if any(str(payload.get(k) or '').strip() for k in ('Approval #','Approval Ref','Approval Reference')):
                raise HTTPException(409,{'code':'BOOKING_APPROVAL_REFERENCE_ALREADY_SET'})
            # Legacy standalone UAT databases may not have Master Data schema yet.
            # Missing governed sequence infrastructure is a conflict, never a server 500.
            sequence_table=conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='md_sequences'" if using_postgres()
                else "SELECT 1 FROM sqlite_master WHERE type='table' AND name='md_sequences'").fetchone()
            if not sequence_table:
                raise HTTPException(409,{'code':'BOOKING_APPROVAL_SEQUENCE_NOT_CONFIGURED'})
            seq=conn.execute("SELECT id,prefix,next_number,width FROM md_sequences WHERE sequence_code='BOOKING_APPROVAL' AND status='ACTIVE'").fetchone()
            if not seq:
                raise HTTPException(409,{'code':'BOOKING_APPROVAL_SEQUENCE_NOT_CONFIGURED'})
            prefix=str(seq['prefix'] or '').strip()
            next_number=seq['next_number']
            width=seq['width']
            if not prefix or not isinstance(next_number,int) or next_number<1 or not isinstance(width,int) or not (1<=width<=12) or len(str(next_number))>width:
                raise HTTPException(409,{'code':'BOOKING_APPROVAL_SEQUENCE_INVALID'})
            number=prefix+f"{next_number:0{width}d}"
            # Protect historical references if a governed sequence was reset or imported.
            approval_duplicate_sql=(
                "SELECT id FROM transaction_records WHERE module='booking' AND id<>? AND CASE WHEN pg_input_is_valid(payload_json, 'jsonb') THEN payload_json::jsonb ->> 'Approval #' ELSE NULL END = ? LIMIT 1"
                if using_postgres() else
                "SELECT id FROM transaction_records WHERE module='booking' AND id<>? AND CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.\"Approval #\"') ELSE NULL END = ? LIMIT 1"
            )
            duplicate=conn.execute(approval_duplicate_sql,(tid,number)).fetchone()
            if duplicate:
                raise HTTPException(409,{'code':'BOOKING_APPROVAL_REFERENCE_DUPLICATE'})
            updated=conn.execute("UPDATE md_sequences SET next_number=next_number+1,version=version+1 WHERE id=? AND next_number=?",(seq['id'],seq['next_number']))
            if updated.rowcount!=1:
                raise HTTPException(409,{'code':'BOOKING_APPROVAL_SEQUENCE_CONFLICT'})
            payload['Approval #']=number
            payload['Approved By']=checker_user
            payload['Approved At']=now()
        if module=='booking' and action=='submit-rfa':
            if not bulk_permission_enabled():
                raise HTTPException(403,{'code':'BOOKING_RFA_REQUIRES_IAM_SESSION'})
            maker_user=str(ga['session'].get('user_ref') or '').strip()
            if not maker_user:
                raise HTTPException(403,{'code':'BOOKING_RFA_MAKER_IDENTITY_MISSING'})
            payload['RFA Submitted By']=maker_user
            new_status='RFA'
        elif action=='advance':
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
            granular_do_release=False
            if module=='delivery-order':
                try:
                    from .release_governance import enabled as release_governance_enabled, assert_delivery_order_eligible
                    if release_governance_enabled():
                        assert_delivery_order_eligible(job_ref=r['job_ref'],hbl_no=r['hbl_no'],container_no=r['container_no'])
                        granular_do_release=True
                except HTTPException:
                    raise
            # Current workflow controls remain authoritative even after a governed
            # cargo/physical release was previously authorized. A later VGM,
            # documentation, customs, transshipment, payment or explicit hold must
            # still block the Delivery Order action.
            reasons=release_gate(ctx)
            if reasons:
                for code in reasons: exception(conn,code,module,tid,r['job_id'],'Release blocked by server-side workflow gate')
                audit(conn,role,ascope or cscope,'RELEASE_BLOCKED',module,tid,r['job_id'],before,before,{'reasons':reasons,'granular_do_release':granular_do_release})
                conn.execute('COMMIT')
                return JSONResponse({'detail':{'code':'RELEASE_BLOCKED','reasons':reasons}},status_code=422)
            new_status='Released'
            if not granular_do_release:
                conn.execute("UPDATE workflow_states SET release_status='RELEASED',version=version+1 WHERE job_id=?",(r['job_id'],))
                sync_release(conn,r['job_id'],tid)
        elif action=='cancel':
            new_status='Cancelled'
            if module=='transshipment-trt':
                reason=(body.reason or '').strip()
                marker=reason.upper().replace(' ','_')
                if 'NOT_APPLICABLE' in marker or 'STALE_BASELINE' in marker:
                    if not reason: raise HTTPException(422,{'code':'REASON_REQUIRED'})
                    conn.execute("UPDATE workflow_states SET transshipment_status='N/A',version=version+1 WHERE job_id=?",(r['job_id'],))
                    clear_hold(conn,r['job_id'],'TRANSSHIPMENT_CONFIRMATION')
                    payload['Connection Status']='Not Applicable'
                    payload['Hold / Release']='N/A'
                    payload['Resolution Reason']=reason
        elif action=='amend': new_status='Draft'
        elif action=='reissue': new_status='Issued'
        elif action=='reverse': new_status='Reversed'
        # special transshipment completion clears hold and state
        if module=='transshipment-trt' and action in ('approve','release'):
            conn.execute("UPDATE workflow_states SET transshipment_status='CONFIRMED',version=version+1 WHERE job_id=?",(r['job_id'],)); clear_hold(conn,r['job_id'],'TRANSSHIPMENT_CONFIRMATION')
        payload['Status']=new_status
        if module=='special-rates-request': sync_special_rate(conn,tid,payload)
        if module=='delivery-order' and action=='release': payload['Release Status']='Released'
        canonical_module='transshipment-trt' if module=='transshipment-trt' and r['module']=='transshipment-crt' else r['module']
        cur=conn.execute('UPDATE transaction_records SET module=?,status=?,payload_json=?,version=version+1,updated_at=? WHERE id=? AND version=?',(canonical_module,new_status,json.dumps(payload),now(),tid,body.version))
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

def _system_event_scope_sql(role,agent_scope,customer_scope,office_scope,branch_scope,depot_scope,alias='j',conn=None):
    clauses=[];args=[]
    if role=='AGENT':
        if not agent_scope: raise HTTPException(403,'AGENT requires X-Agent-Scope')
        clauses.append(f"{alias}.agent_id=(SELECT id FROM agents WHERE code=?)");args.append(agent_scope)
    if customer_scope:
        clauses.append(f"{alias}.customer_id=(SELECT id FROM customers WHERE code=?)");args.append(customer_scope)
    if office_scope:
        clauses.append(f"{alias}.office_code=?");args.append(office_scope)
    if branch_scope:
        clauses.append(f"{alias}.branch_code=?");args.append(branch_scope)
    if depot_scope:
        has_depot=False
        try:
            if backend_name()=='postgres':
                has_depot=bool(conn and conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='containers' AND column_name='depot_code'").fetchone())
            else:
                has_depot=bool(conn and any(str(r['name'])=='depot_code' for r in conn.execute("PRAGMA table_info(containers)").fetchall()))
        except Exception:
            has_depot=False
        if has_depot:
            clauses.append(f"EXISTS (SELECT 1 FROM containers sc WHERE sc.job_id={alias}.id AND sc.depot_code=?)");args.append(depot_scope)
        else:
            clauses.append("1=0")
    return ''.join(' AND '+x for x in clauses),args

@app.get('/api/v1/system/events/audit')
def audit_list(job_ref:Optional[str]=None,limit:int=100,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None,alias='X-Agent-Scope'),x_customer_scope:Optional[str]=Header(None,alias='X-Customer-Scope'),x_office_scope:Optional[str]=Header(None,alias='X-Office-Scope'),x_branch_scope:Optional[str]=Header(None,alias='X-Branch-Scope'),x_depot_scope:Optional[str]=Header(None,alias='X-Depot-Scope')):
    x_customer_scope=x_customer_scope if isinstance(x_customer_scope,str) else None
    x_office_scope=x_office_scope if isinstance(x_office_scope,str) else None
    x_branch_scope=x_branch_scope if isinstance(x_branch_scope,str) else None
    x_depot_scope=x_depot_scope if isinstance(x_depot_scope,str) else None
    role,agent_scope,customer_scope=actor(x_role,x_agent_scope,x_customer_scope)
    conn=connect();q='SELECT a.*,j.job_ref FROM audit_events a LEFT JOIN jobs j ON j.id=a.job_id WHERE 1=1';args=[]
    if job_ref:q+=' AND j.job_ref=?';args.append(job_ref)
    sc,sa=_system_event_scope_sql(role,agent_scope,customer_scope,x_office_scope,x_branch_scope,x_depot_scope,'j',conn);q+=sc;args+=sa
    q+=' ORDER BY a.id DESC LIMIT ?';args.append(min(limit,500));rows=[dict(r) for r in conn.execute(q,args)];conn.close();return rows
@app.get('/api/v1/system/events/exceptions')
def exception_list(job_ref:Optional[str]=None,limit:int=100,x_role:str=Header('VIEWER'),x_agent_scope:Optional[str]=Header(None,alias='X-Agent-Scope'),x_customer_scope:Optional[str]=Header(None,alias='X-Customer-Scope'),x_office_scope:Optional[str]=Header(None,alias='X-Office-Scope'),x_branch_scope:Optional[str]=Header(None,alias='X-Branch-Scope'),x_depot_scope:Optional[str]=Header(None,alias='X-Depot-Scope')):
    x_customer_scope=x_customer_scope if isinstance(x_customer_scope,str) else None
    x_office_scope=x_office_scope if isinstance(x_office_scope,str) else None
    x_branch_scope=x_branch_scope if isinstance(x_branch_scope,str) else None
    x_depot_scope=x_depot_scope if isinstance(x_depot_scope,str) else None
    role,agent_scope,customer_scope=actor(x_role,x_agent_scope,x_customer_scope)
    conn=connect();q='SELECT e.*,j.job_ref FROM exception_events e LEFT JOIN jobs j ON j.id=e.job_id WHERE 1=1';args=[]
    if job_ref:q+=' AND j.job_ref=?';args.append(job_ref)
    sc,sa=_system_event_scope_sql(role,agent_scope,customer_scope,x_office_scope,x_branch_scope,x_depot_scope,'j');q+=sc;args+=sa
    q+=' ORDER BY e.id DESC LIMIT ?';args.append(min(limit,500));rows=[dict(r) for r in conn.execute(q,args)];conn.close();return rows
@app.get('/api/admin/auth/uat-recovery-page', response_class=HTMLResponse, include_in_schema=False)
def isolated_uat_recovery_page():
    from . import db
    from urllib.parse import urlparse
    enabled = (os.getenv('M3_ALLOW_ADMIN_RECOVERY','').lower() == 'true'
               and os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper() == 'OFF'
               and os.getenv('M3_UAT_PRESERVE_DATA') == '1'
               and db.using_postgres() and db._uat_target_approved()
               and urlparse(db.DATABASE_URL).hostname == 'ep-wild-sea-b49pzkl3-pooler.c-6.us-east-2.aws.neon.tech')
    if not enabled:
        raise HTTPException(404, 'NOT_FOUND')
    html = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>M3 Isolated UAT Admin Recovery</title><style>body{font:16px system-ui,sans-serif;background:#f4f6f9;color:#111827;max-width:520px;margin:40px auto;padding:20px}main{background:white;padding:28px;border-radius:12px;box-shadow:0 3px 16px #0001}label{display:block;margin-top:16px;font-weight:600}input{display:block;box-sizing:border-box;width:100%;padding:12px;margin-top:6px;font:inherit;border:1px solid #9ca3af;border-radius:6px}button{margin-top:22px;padding:13px 18px;background:#173f67;color:white;border:0;border-radius:6px;font:inherit;cursor:pointer}button:disabled{opacity:.5}#out{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#4b5563}h1{font-size:24px}</style></head><body><main><h1>M3 — Isolated UAT Admin Recovery</h1><p>Enter existing Render secrets privately. They are submitted only to this same-origin isolated API; they are not stored by this page. Run recovery only once.</p><form id="f" autocomplete="off"><label>UAT access token<input id="uat" type="password" required autocomplete="off"></label><label>UAT diagnostic token<input id="diag" type="password" required autocomplete="off"></label><label>Recovery secret<input id="rec" type="password" required autocomplete="off"></label><label>New Admin password<input id="pw" type="password" required minlength="12" autocomplete="new-password"></label><button id="go" type="submit">Recover Admin — once</button></form><p id="out" role="status" aria-live="polite"></p><small>Production and other services are not targeted. Never share secrets or session tokens in chat.</small></main><script>const f=document.getElementById('f'),o=document.getElementById('out'),go=document.getElementById('go');f.addEventListener('submit',async e=>{e.preventDefault();go.disabled=true;o.textContent='Submitting one recovery request…';const u=document.getElementById('uat').value.trim(),d=document.getElementById('diag').value.trim(),r=document.getElementById('rec').value.trim(),p=document.getElementById('pw').value;try{const x=await fetch('/api/admin/auth/uat-recover-password',{method:'POST',credentials:'omit',cache:'no-store',headers:{'Content-Type':'application/json','X-M3-UAT-Token':u,'X-M3-UAT-Diagnostic-Token':d,'X-M3-Recovery-Secret':r},body:JSON.stringify({new_password:p})});let b=await x.json().catch(()=>({}));o.textContent=x.ok?'RECOVERED — contact ChatGPT to disable recovery and verify login.':'HTTP '+x.status+' — '+(b.detail?.code||b.detail||'Request failed');}catch(err){o.textContent='Network failure — do not retry without checking server logs.'}finally{for(const id of ['uat','diag','rec','pw'])document.getElementById(id).value='';}});</script></body></html>'''
    return HTMLResponse(html, headers={'Cache-Control':'no-store, max-age=0','Pragma':'no-cache','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'"})

@app.get('/api/admin/auth/uat-login-verify-page',response_class=HTMLResponse,include_in_schema=False)
def isolated_uat_login_verify_page():
    from . import db
    from urllib.parse import urlparse
    if (os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper() != 'OFF'
            or os.getenv('M3_UAT_PRESERVE_DATA') != '1'
            or not db.using_postgres() or not db._uat_target_approved()
            or urlparse(db.DATABASE_URL).hostname != db.M3_UAT_DB_HOST):
        raise HTTPException(404,'NOT_FOUND')
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>M3 Isolated Admin Login Verification</title><style>body{font:16px system-ui,sans-serif;background:#f4f6f9;color:#111827;max-width:520px;margin:40px auto;padding:20px}main{background:#fff;padding:28px;border-radius:12px}label{display:block;margin-top:16px;font-weight:600}input{display:block;box-sizing:border-box;width:100%;padding:12px;margin-top:6px;font:inherit;border:1px solid #9ca3af;border-radius:6px}button{margin-top:22px;padding:13px 18px;background:#173f67;color:white;border:0;border-radius:6px;font:inherit;cursor:pointer}button:disabled{opacity:.5}#out{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body><main><h2>M3 — Isolated Admin Login Verification</h2><p>Runs one login with the new Admin password, sandbox MFA and session verification. Credentials and session tokens will not appear in the result.</p><form id="f" autocomplete="off"><label>UAT access token<input id="uat" type="password" required autocomplete="off"></label><label>New Admin password<input id="pw" type="password" required autocomplete="new-password"></label><label>Sandbox MFA code<input id="mfa" value="123456" required inputmode="numeric" autocomplete="off"></label><button id="go" type="submit">Verify Admin Login</button></form><p id="out" role="status" aria-live="polite"></p></main><script>const f=document.getElementById('f'),o=document.getElementById('out'),go=document.getElementById('go');f.addEventListener('submit',async e=>{e.preventDefault();go.disabled=true;const u=document.getElementById('uat').value.trim(),p=document.getElementById('pw').value,m=document.getElementById('mfa').value.trim();o.textContent='Checking login and permissions…';try{let x=await fetch('/api/admin/auth/login',{method:'POST',credentials:'omit',cache:'no-store',headers:{'Content-Type':'application/json','X-M3-UAT-Token':u},body:JSON.stringify({username:'admin',password:p,mfa_code:m})});let b=await x.json().catch(()=>({}));if(!x.ok){o.textContent='LOGIN FAILED: HTTP '+x.status+' '+(b.detail?.code||b.detail||'');return}if(!b.session_token){o.textContent='Login response missing session token';return}const h={'X-M3-UAT-Token':u,'X-M3-Session':b.session_token};const who=await fetch('/api/admin/auth/whoami',{headers:h,credentials:'omit',cache:'no-store'});const w=await who.json().catch(()=>({}));o.textContent='Login: SUCCESS\\nMFA verified: '+(b.mfa_verified===true?'YES':'NO')+'\\nSession validation: '+(who.ok?'PASS':'FAIL')+'\\nUsername: '+(w.username||'unknown')+'\\nSUPER_ADMIN role: '+(Array.isArray(w.roles)&&w.roles.includes('SUPER_ADMIN')?'PASS':'FAIL');const accessResponse=await fetch('/api/admin/bulk-job-access?resource=agent-tasks&action=approve',{headers:h,credentials:'omit',cache:'no-store'});const j=await accessResponse.json().catch(()=>({}));const expected={'50001':true,'50002':false,'50003':false,'50004':false,'50005':true};const rows=(Array.isArray(j.results)?j.results:[]).filter(r=>r.username==='admin'&&Object.prototype.hasOwnProperty.call(expected,String(r.job_ref)));const ok=accessResponse.ok&&rows.length===5&&rows.every(r=>r.allowed===expected[String(r.job_ref)]);o.textContent+='\\nAdmin RTM/cross-office approval: '+(ok?'PASS':'FAIL')+'; HTTP '+accessResponse.status+'; checks '+rows.length+'/5';}catch(err){o.textContent='Network error: login not verified'}finally{document.getElementById('uat').value='';document.getElementById('pw').value='';go.disabled=false}});</script></body></html>"""
    return HTMLResponse(html,headers={'Cache-Control':'no-store, max-age=0','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'"})

@app.get('/api/admin/auth/uat-maker-checker-verify-page',response_class=HTMLResponse,include_in_schema=False)
def isolated_uat_maker_checker_page():
    from . import db
    from urllib.parse import urlparse
    if (os.getenv('M3_PRODUCTION_TRAFFIC','OFF').upper() != 'OFF'
        or os.getenv('M3_UAT_PRESERVE_DATA') != '1' or not db.using_postgres()
        or not db._uat_target_approved()
        or urlparse(db.DATABASE_URL).hostname != db.M3_UAT_DB_HOST):
        raise HTTPException(404,'NOT_FOUND')
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>M3 UAT Maker Checker Verification</title><style>body{font:16px system-ui,sans-serif;background:#f5f6fa;color:#111827;max-width:550px;margin:36px auto;padding:18px}main{background:white;border-radius:12px;padding:25px}label{display:block;margin-top:15px;font-weight:600}input,select{display:block;box-sizing:border-box;width:100%;padding:11px;margin-top:6px;font:inherit;border:1px solid #94a3b8;border-radius:7px}button{margin-top:20px;background:#223963;color:white;border:0;border-radius:6px;padding:12px 16px;font:inherit}button:disabled{opacity:.5}#result{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body><main><h2>M3 — Maker / Checker UAT</h2><p>Check existing credentials, MFA, session validity and role isolation. This page does not reset any password or grant permissions.</p><form id="f" autocomplete="off"><label>Account<select id="account"><option value="md.maker">md.maker — Maker</option><option value="md.checker">md.checker — Checker</option></select></label><label>UAT access token<input id="uat" type="password" required autocomplete="off"></label><label>Existing account password<input id="pw" type="password" required autocomplete="off"></label><label>Sandbox MFA code<input id="mfa" value="123456" required autocomplete="off" inputmode="numeric"></label><button id="go" type="submit">Verify selected account</button></form><p role="status" aria-live="polite" id="result"></p><small>No secrets or session tokens are shown. Do not share them in chat.</small></main><script>const f=document.getElementById('f'),out=document.getElementById('result'),btn=document.getElementById('go');f.addEventListener('submit',async e=>{e.preventDefault();btn.disabled=true;const name=document.getElementById('account').value,uat=document.getElementById('uat').value.trim(),pw=document.getElementById('pw').value,mfa=document.getElementById('mfa').value.trim(),head={'X-M3-UAT-Token':uat};out.textContent='Checking account…';try{let res=await fetch('/api/admin/auth/login',{method:'POST',credentials:'omit',cache:'no-store',headers:{...head,'Content-Type':'application/json'},body:JSON.stringify({username:name,password:pw,mfa_code:mfa})});let login=await res.json().catch(()=>({}));if(!res.ok){out.textContent='Login: FAIL (HTTP '+res.status+') '+(login.detail?.code||login.detail||'');return;}if(!login.session_token){out.textContent='Login succeeded but session token missing';return;}let auth={...head,'X-M3-Session':login.session_token};let whoRes=await fetch('/api/admin/auth/whoami',{headers:auth,credentials:'omit',cache:'no-store'}),who=await whoRes.json().catch(()=>({}));let roles=Array.isArray(who.roles)?who.roles:[];let ok=whoRes.ok&&who.username===name, noAdmin=!roles.includes('SUPER_ADMIN');out.textContent='Login: PASS\\nMFA: '+(login.mfa_verified===true?'PASS':'FAIL')+'\\nSession / username: '+(ok?'PASS':'FAIL')+'\\nRoles: '+roles.join(', ')+'\\nSUPER_ADMIN isolation: '+(noAdmin?'PASS':'FAIL');await fetch('/api/admin/auth/logout',{method:'POST',headers:auth,credentials:'omit',cache:'no-store'}).catch(()=>{});}catch(err){out.textContent='Network error; verification incomplete';}finally{document.getElementById('uat').value='';document.getElementById('pw').value='';btn.disabled=false;}});</script></body></html>"""
    return HTMLResponse(html,headers={'Cache-Control':'no-store, max-age=0','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})
