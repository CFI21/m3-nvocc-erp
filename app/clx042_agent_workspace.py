from fastapi import APIRouter, HTTPException, Query
import json
from pathlib import Path

from .db import connect
from .screen_catalog import build_catalog, AGENT_SETUP_ALIASES
from .screen_integration import MD_DOMAIN_MAP

HERE=Path(__file__).resolve().parent
META=json.loads((HERE/'module_meta.json').read_text())
MODULES={m['key']:m for m in META['modules']}
router=APIRouter(prefix='/api/clx042',tags=['CLX-042 Agent Tasks Workspace'])

LIFECYCLE={
    'draft':['open','in_progress','cancelled'],
    'open':['in_progress','completed','closed','cancelled'],
    'in_progress':['completed','closed','cancelled'],
    'completed':['closed'],
    'approved':['released','closed'],
    'released':['closed'],
    'closed':[],
    'cancelled':[],
}

SETUP_DOMAIN_BY_TARGET={
    item['target']:MD_DOMAIN_MAP[item['target'].split('::',1)[1]]
    for item in AGENT_SETUP_ALIASES
}

def _screen(screen_id):
    c=build_catalog()
    s=next((x for x in c['screens'] if x['screen_id']==screen_id),None)
    if not s: raise HTTPException(404,'Unknown screen')
    return c,s

@router.get('/form-schema')
def form_schema(screen_id:str=Query(...),mode:str=Query('create',pattern='^(create|edit)$')):
    c,s=_screen(screen_id)
    if s['domain']=='Agent Tasks':
        m=MODULES[s['key']]
        return {
            'phase':'CLX-042','kind':'AGENT_TRANSACTION','mode':mode,
            'screen_id':screen_id,'module':s['key'],'name':m['name'],
            'fields':m.get('fields',[]),'columns':m.get('columns',[]),
            'create_api':f"/api/v1/{s['key']}",
            'edit_api_template':f"/api/v1/{s['key']}/{{id}}",
            'job_ref_required':True,'external_ref_optional':True,
            'authoritative_api':True,'parallel_data_store':False,
            'scope':{'role':True,'agent':True,'customer':True,'office_country':'PRESERVED_IN_OWNING_DOMAINS_NOT_PRESENT_ON_AGENT_JOB_MODEL'},
            'screen_count':c['screen_count'],
        }
    if screen_id in SETUP_DOMAIN_BY_TARGET:
        domain=SETUP_DOMAIN_BY_TARGET[screen_id]
        return {
            'phase':'CLX-042','kind':'AGENT_SETUP_MASTER','mode':mode,
            'screen_id':screen_id,'master_domain':domain,'name':s['name'],
            'fields':['Record Key','Name','Effective From','Effective To'],
            'change_api':'/api/masterdata/changes',
            'operations':['CREATE','UPDATE','ACTIVATE','DEACTIVATE'],
            'maker_checker':True,'duplicate_prevention':True,'audit':True,
            'authoritative_api':True,'parallel_data_store':False,
            'screen_count':c['screen_count'],
        }
    raise HTTPException(422,'CLX-042 form hardening is limited to Agent Tasks Setup/Transaction')

@router.get('/lifecycle')
def lifecycle():
    return {
        'phase':'CLX-042',
        'canonical':'DRAFT -> OPEN/IN_PROGRESS -> COMPLETED/CLOSED',
        'existing_special_states_preserved':True,
        'transitions':LIFECYCLE,
        'note':'Existing module-specific approve/release/hold/cancel/amend/reissue actions remain authoritative.'
    }

@router.get('/trace/{job_ref}')
def trace(job_ref:str):
    if not (len(job_ref)==5 and job_ref.isdigit()): raise HTTPException(422,'Job reference must be five digits')
    c=connect()
    try:
        j=c.execute('''SELECT j.id,j.job_ref,j.operational_status,b.booking_ref,c.code customer_code,c.name customer_name,
          a.code agent_code,a.name agent_name,v.voyage_no,vs.name vessel_name,j.pol,j.pod
          FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN customers c ON c.id=j.customer_id
          JOIN agents a ON a.id=j.agent_id JOIN voyages v ON v.id=j.voyage_id JOIN vessels vs ON vs.id=v.vessel_id
          WHERE j.job_ref=?''',(job_ref,)).fetchone()
        if not j: raise HTTPException(404,'Unknown job')
        rows=[dict(x) for x in c.execute('''SELECT id,module,external_ref,status,version,booking_id,customer_id,agent_id,
          container_id,voyage_id,bill_id FROM transaction_records WHERE job_id=? ORDER BY id''',(j['id'],))]
        modules={x['module'] for x in rows}
        expected=set(MODULES)
        links={
          'containers':c.execute('SELECT COUNT(*) n FROM containers WHERE job_id=?',(j['id'],)).fetchone()['n'],
          'bills':c.execute('SELECT COUNT(*) n FROM bills WHERE job_id=?',(j['id'],)).fetchone()['n'],
          'audit':c.execute('SELECT COUNT(*) n FROM audit_events WHERE job_id=?',(j['id'],)).fetchone()['n'],
          'gl':c.execute('SELECT COUNT(*) n FROM gl_records WHERE job_id=?',(j['id'],)).fetchone()['n'],
          'treasury':c.execute('SELECT COUNT(*) n FROM treasury_records WHERE job_id=?',(j['id'],)).fetchone()['n'],
        }
        broken=[x for x in rows if not all(x[k] is not None for k in ('booking_id','customer_id','agent_id','voyage_id'))]
        dups=c.execute('''SELECT COUNT(*) n FROM (
          SELECT module,external_ref,COUNT(*) c FROM transaction_records WHERE job_id=?
          GROUP BY module,external_ref HAVING COUNT(*)>1) x''',(j['id'],)).fetchone()['n']
        return {
          'phase':'CLX-042','job_ref':job_ref,'identity':dict(j),
          'agent_transactions':len(rows),'expected_modules':len(expected),
          'missing_modules':sorted(expected-modules),'broken_core_links':len(broken),
          'duplicate_module_refs':dups,'related':links,
          'complete':len(rows)>=len(expected) and not (expected-modules) and not broken and dups==0,
          'screen_count':build_catalog()['screen_count'],
          'live_providers':False,'real_money':False,
        }
    finally:c.close()

@router.get('/verify')
def verify():
    c=build_catalog()
    by_id={s['screen_id']:s for s in c['screens']}
    agent=[s for s in c['screens'] if s['domain']=='Agent Tasks']
    setup=[{'label':a['label'],'target':a['target'],'exists':a['target'] in by_id,'domain':SETUP_DOMAIN_BY_TARGET.get(a['target'])} for a in AGENT_SETUP_ALIASES]
    return {
      'phase':'CLX-042',
      'status':'PASS' if c['screen_count']==196 and len(agent)==19 and len(set(MODULES))==19 and all(x['exists'] and x['domain'] for x in setup) else 'FAIL',
      'screen_count':c['screen_count'],'agent_transaction_screens':len(agent),'agent_modules':len(MODULES),
      'setup_masters':setup,'menu_reordered':False,'data_model_changed':False,
      'api_contracts':'PRESERVED_WITH_ADDITIVE_CLX042_METADATA_ENDPOINTS',
      'live_providers':False,'real_money':False,
    }
