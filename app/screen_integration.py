from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Optional, Any
import json, datetime, uuid
from .db import connect
from .screen_catalog import build_catalog

HERE=Path(__file__).resolve().parent
CATALOG=build_catalog()
META_RECOVERED=True
SCREENS={s['screen_id']:s for s in CATALOG['screens']}
BASELINE=CATALOG['baseline']
PARENT=CATALOG['parent']
router=APIRouter(prefix='/api/clx011',tags=['CLX-011 Screen Integration / Menu / Quick Actions'])

DOMAIN_SLUG={
    'Agent Tasks':'agent','Treasury / AR-AP':'treasury',
    'Integration & Security':'integration','General / Administration':'admin','Master Data':'master'
}

# Domain-level capabilities only filter the UX surface. Existing server-side APIs remain authoritative.
ROLE_ACTIONS={
 'SUPER_ADMIN': {'quick-view','related-records','print','export','email','audit-history','create','edit','copy','approve','release','hold','cancel','amend','reissue','reverse','retry','activate','deactivate','change-request','reject','version-history','advance'},
 'ADMIN': {'quick-view','related-records','print','export','email','audit-history','create','edit','copy','approve','release','hold','cancel','amend','reissue','reverse','retry','activate','deactivate','change-request','reject','version-history','advance'},
 'OPS': {'quick-view','related-records','print','export','email','audit-history','create','edit','copy','approve','release','hold','amend','reissue','change-request','version-history','advance'},
 'DOCS': {'quick-view','related-records','print','export','email','audit-history','create','edit','copy','approve','hold','amend','reissue','version-history'},
 'FINANCE': {'quick-view','related-records','print','export','email','audit-history','create','edit','copy','approve','reverse','change-request','version-history'},
 'GL_MANAGER': {'quick-view','related-records','print','export','email','audit-history','create','edit','copy','approve','release','reverse'},
 'GL_ACCOUNTANT': {'quick-view','related-records','print','export','email','audit-history','create','edit','copy'},
 'TREASURY_MANAGER': {'quick-view','related-records','print','export','email','audit-history','create','edit','copy','approve','release','reverse'},
 'SECURITY_ADMIN': {'quick-view','related-records','print','export','email','audit-history','retry','activate','deactivate'},
 'MASTER_DATA_MANAGER': {'quick-view','related-records','print','export','email','audit-history','change-request','approve','reject','activate','deactivate','version-history'},
 'MASTER_DATA': {'quick-view','related-records','print','export','email','audit-history','change-request','version-history'},
 'AUDITOR': {'quick-view','related-records','print','export','audit-history','version-history'},
 'AGENT': {'quick-view','related-records','print','export','email','audit-history','create','edit','copy','amend'},
 'VIEWER': {'quick-view','related-records','print','export','audit-history'},
}

MD_DOMAIN_MAP={'customers':'customer','agents':'agent','carriers':'carrier','ports':'port','locations':'location','vessels':'vessel','voyages':'voyage','equipment-types':'equipment-type','container-types':'container-type','commodities':'commodity','commoditys':'commodity','packages':'package','common-partys':'common-party','units':'unit','sales-persons':'sales-person','charge-codes':'charge-code','tax-codes':'tax-code','currencies':'currency','exchange-rate-types':'exchange-rate-type','payment-terms':'payment-term','banks':'bank','bank-accounts':'bank-account','gl-accounts':'gl-account','cost-centers':'cost-center','profit-centers':'profit-center','trade-lanes':'trade-lane','services':'service','routes':'route','incoterms':'incoterm','document-types':'document-type','release-types':'release-type','reference-sequences':'reference-sequence','configuration':'configuration','configurations':'configuration'}

ADMIN_TABLE={
 'users':'iam_users','user-status':'iam_users','roles':'iam_roles','permission-matrix':'iam_permissions','role-assignments':'iam_user_roles',
 'organizations':'iam_organizations','countries':'iam_countries','legal-entities':'iam_legal_entities','offices':'iam_offices','branches':'iam_branches','departments':'iam_departments','office-membership':'iam_office_membership',
 'data-scope-rules':'iam_scope_rules','customer-agent-access':'iam_party_access','approval-limits':'iam_approval_limits','maker-checker':'iam_sod_conflicts','approval-delegations':'iam_delegations','temporary-access':'iam_temporary_access','access-reviews':'iam_access_reviews','sessions':'iam_sessions','login-audit':'iam_login_audit','service-accounts':'iam_service_accounts','api-clients':'iam_api_clients','identity-audit':'iam_audit_events','audit':'iam_audit_events',
 'password-policy':'iam_security_policies','mfa-policy':'iam_security_policies','sso-readiness':'iam_sso_readiness'
}
MASTER_TABLE={
 'change-requests':'md_change_requests','approval-queue':'md_change_requests','versions':'md_versions','duplicate-review':'md_records','aliases-merges':'md_aliases','data-quality':'md_quality_issues','reference-usage':'md_usage','effective-dates':'md_records','sequence-control':'md_sequences','integrity-scan':'md_quality_issues','hardcoded-scan':'md_config','audit':'md_audit'
}

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def rowdict(r): return dict(r) if r is not None else None

def init_events(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS screen_integration_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,event_ref TEXT UNIQUE NOT NULL,ts TEXT NOT NULL,
      actor_role TEXT NOT NULL,actor_id TEXT NOT NULL,action TEXT NOT NULL,screen_id TEXT,
      record_ref TEXT,job_ref TEXT,detail_json TEXT NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS screen_integration_events_no_update BEFORE UPDATE ON screen_integration_events BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SCREEN_EVENT'); END;
    CREATE TRIGGER IF NOT EXISTS screen_integration_events_no_delete BEFORE DELETE ON screen_integration_events BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SCREEN_EVENT'); END;
    ''')
    c.commit()

def log(c,role,actor,action,screen_id=None,record_ref=None,job_ref=None,detail=None):
    init_events(c)
    c.execute('INSERT INTO screen_integration_events(event_ref,ts,actor_role,actor_id,action,screen_id,record_ref,job_ref,detail_json) VALUES(?,?,?,?,?,?,?,?,?)',
              (str(uuid.uuid4()),now(),role,actor,action,screen_id,record_ref,job_ref,json.dumps(detail or {},sort_keys=True)))
    c.commit()

def require_screen(screen_id):
    s=SCREENS.get(screen_id)
    if not s: raise HTTPException(404,'Unknown CLX-011 screen')
    return s

def screen_role_allowed(s,role):
    r=role.upper()
    if r=='SUPER_ADMIN': r='ADMIN'
    allowed={str(x).upper() for x in s.get('roles',[])}
    return not allowed or r in allowed

def safe_rows(c,sql,args=(),limit=100):
    return [dict(r) for r in c.execute(sql,args).fetchmany(limit)]

def job_e2e(c,job_ref):
    j=c.execute('''SELECT j.*,b.booking_ref,c.code customer_code,c.name customer_name,a.code agent_code,v.voyage_no,vs.name vessel_name
      FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
      JOIN voyages v ON v.id=j.voyage_id JOIN vessels vs ON vs.id=v.vessel_id WHERE j.job_ref=?''',(job_ref,)).fetchone()
    if not j: raise HTTPException(404,'Unknown job')
    jid=j['id']
    def n(table,where='job_id=?',args=None): return c.execute(f'SELECT COUNT(*) n FROM {table} WHERE {where}',args or (jid,)).fetchone()['n']
    bills=[dict(r) for r in c.execute('SELECT bill_no,kind,status FROM bills WHERE job_id=? ORDER BY kind',(jid,))]
    return {
      'identity':dict(j),'bills':bills,
      'counts':{'agent_tasks':n('transaction_records'),'gl':n('gl_records'),'treasury':n('treasury_records'),'integration':n('integration_records'),'containers':n('containers'),'container_events':n('container_events'),'audit':n('audit_events'),'exceptions':n('exception_events')},
      'workflow':rowdict(c.execute('SELECT * FROM workflow_states WHERE job_id=?',(jid,)).fetchone()),
      'finance':rowdict(c.execute('SELECT * FROM finance_states WHERE job_id=?',(jid,)).fetchone()),
    }

@router.get('/health')
def health():
    return {'project':'M3 NVOCC ERP','baseline':BASELINE,'parent':PARENT,'screen_count':len(SCREENS),'menu_domains':len(CATALOG['menu']),
            'no_business_logic_change':True,'production_promoted':False,'live_credentials':False,'real_money_movement':False}

@router.get('/menu')
def menu(q:Optional[str]=None):
    if not q: return {'screen_count':len(SCREENS),'menu':CATALOG['menu']}
    q=q.lower().strip()
    screen_ids={s['screen_id'] for s in CATALOG['screens'] if q in s['name'].lower() or q in s['domain'].lower() or q in s['submenu'].lower()}
    out=[]
    alias_hits=0
    for d in CATALOG['menu']:
        subs=[]
        for sub in d['submenus']:
            ids=[x for x in sub.get('screens',[]) if x in screen_ids]
            items=[x for x in sub.get('items',[]) if q in x['label'].lower() or q in d['domain'].lower() or q in sub['name'].lower()]
            if ids or items:
                entry={'name':sub['name'],'screens':ids}
                if items:
                    entry['items']=items
                    alias_hits+=len(items)
                subs.append(entry)
        if subs:
            out.append({'domain':d['domain'],'submenus':subs,'screen_count':sum(len(x.get('screens',[])) for x in subs),
                        'navigation_alias_count':sum(len(x.get('items',[])) for x in subs)})
    return {'screen_count':len(screen_ids),'navigation_alias_count':alias_hits,'menu':out}

@router.get('/screens')
def screens(domain:Optional[str]=None,submenu:Optional[str]=None):
    xs=CATALOG['screens']
    if domain: xs=[x for x in xs if x['domain']==domain]
    if submenu: xs=[x for x in xs if x['submenu']==submenu]
    return xs

@router.get('/screen')
def screen(screen_id:str=Query(...)):
    return require_screen(screen_id)

@router.get('/screen-data')
def screen_data(screen_id:str=Query(...),job_ref:Optional[str]=None,x_role:str=Header('VIEWER')):
    s=require_screen(screen_id);role=x_role.upper()
    if not screen_role_allowed(s,role): raise HTTPException(403,'Role cannot access this screen')
    c=connect();rows=[]
    try:
        if s['domain']=='Agent Tasks':
            sql='''SELECT t.id,t.external_ref,j.job_ref,c.name customer,a.code agent,t.status,t.version,t.payload_json
                   FROM transaction_records t JOIN jobs j ON j.id=t.job_id JOIN customers c ON c.id=t.customer_id JOIN agents a ON a.id=t.agent_id WHERE t.module=?''';args=[s['key']]
            if job_ref:sql+=' AND j.job_ref=?';args.append(job_ref)
            rows=safe_rows(c,sql,args)
        elif s['screen_id'].startswith('gl-accounts::'):
            sql='''SELECT g.id,g.external_ref,j.job_ref,g.source_type,g.source_ref,g.status,g.version,g.payload_json
                   FROM gl_records g LEFT JOIN jobs j ON j.id=g.job_id WHERE g.module=?''';args=[s['key']]
            if job_ref:sql+=' AND j.job_ref=?';args.append(job_ref)
            rows=safe_rows(c,sql,args)
        elif s['domain']=='Treasury / AR-AP':
            sql='''SELECT t.id,t.external_ref,j.job_ref,t.party_type,t.party_name,t.currency,t.amount,t.status,t.version,t.source_type,t.source_ref,t.payload_json
                   FROM treasury_records t LEFT JOIN jobs j ON j.id=t.job_id WHERE t.module=?''';args=[s['key']]
            if job_ref:sql+=' AND j.job_ref=?';args.append(job_ref)
            rows=safe_rows(c,sql,args)
        elif s['domain']=='Integration & Security':
            sql='''SELECT i.id,i.external_ref,j.job_ref,i.office_scope,i.country_scope,i.status,i.version,i.payload_json
                   FROM integration_records i LEFT JOIN jobs j ON j.id=i.job_id WHERE i.module=?''';args=[s['key']]
            if job_ref:sql+=' AND j.job_ref=?';args.append(job_ref)
            rows=safe_rows(c,sql,args)
        elif s['screen_id'].startswith('administration::'):
            if s['screen_id']=='administration::dashboard':
                rows=[{'Metric':'Users','Value':c.execute('SELECT COUNT(*) n FROM iam_users').fetchone()['n']},{'Metric':'Roles','Value':c.execute('SELECT COUNT(*) n FROM iam_roles').fetchone()['n']},{'Metric':'Open Access Reviews','Value':c.execute("SELECT COUNT(*) n FROM iam_access_reviews WHERE status!='COMPLETED'").fetchone()['n']}]
            else:
                table=ADMIN_TABLE.get(s['key'])
                if table: rows=safe_rows(c,f'SELECT * FROM {table} ORDER BY id DESC')
        else:
            if s['key'] in MD_DOMAIN_MAP:
                rows=safe_rows(c,'SELECT * FROM md_records WHERE domain=? ORDER BY id DESC',(MD_DOMAIN_MAP[s['key']],))
            elif s['screen_id']=='master-data::dashboard':
                rows=[{'Metric':'Master Records','Value':c.execute('SELECT COUNT(*) n FROM md_records').fetchone()['n']},{'Metric':'Pending Changes','Value':c.execute("SELECT COUNT(*) n FROM md_change_requests WHERE status='PENDING'").fetchone()['n']},{'Metric':'Quality Issues','Value':c.execute("SELECT COUNT(*) n FROM md_quality_issues WHERE status!='RESOLVED'").fetchone()['n']}]
            elif s['key']=='approval-queue': rows=safe_rows(c,"SELECT * FROM md_change_requests WHERE status='PENDING' ORDER BY id DESC")
            elif s['key']=='duplicate-review': rows=safe_rows(c,"SELECT domain,lower(display_name) normalized_name,COUNT(*) n,GROUP_CONCAT(record_key) record_keys FROM md_records WHERE status='ACTIVE' GROUP BY domain,lower(display_name) HAVING COUNT(*)>1")
            elif s['key']=='integrity-scan':
                issues=[]
                for r in c.execute('SELECT j.job_ref,c.code customer_code,a.code agent_code,j.pol,j.pod FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id'):
                    for domain,key in [('customer',r['customer_code']),('agent',r['agent_code']),('port',r['pol']),('port',r['pod'])]:
                        if not c.execute("SELECT 1 FROM md_records WHERE domain=? AND record_key=? AND status='ACTIVE'",(domain,key)).fetchone():issues.append({'job_ref':r['job_ref'],'domain':domain,'record_key':key})
                rows=issues or [{'status':'PASS','critical_issues':0}]
            else:
                table=MASTER_TABLE.get(s['key'])
                if table: rows=safe_rows(c,f'SELECT * FROM {table} ORDER BY id DESC')
        for r in rows:
            for k in list(r):
                if k.endswith('_json') and isinstance(r[k],str):
                    try:r[k[:-5]]=json.loads(r[k]);del r[k]
                    except Exception:pass
        return {'screen':s,'role':role,'count':len(rows),'rows':rows}
    finally:c.close()

def functional_actions(s):
    common={'quick-view','related-records','print','export','email','audit-history'}
    sid=s['screen_id']; domain=s['domain']; key=s['key']; submenu=s.get('submenu','')
    if domain=='Agent Tasks': return set(s['quick_actions'])
    if sid.startswith('gl-accounts::'):
        if 'Reports & Reconciliation' in submenu: return common
        return common|{'create','edit','approve','reverse'}
    if domain=='Treasury / AR-AP':
        if submenu in {'Overview','Reports & Reconciliation','Work Queues'}: return common
        return common|{'create','edit','approve','release','reverse'}
    if domain=='Integration & Security':
        return common
    if sid.startswith('administration::'):
        return common
    if domain=='Master Data':
        if key in MD_DOMAIN_MAP:
            return common|{'change-request','activate','deactivate','version-history'}
        if key=='approval-queue': return common|{'approve','reject'}
        if key=='versions': return common|{'version-history'}
        return common
    return common

@router.get('/quick-actions')
def quick_actions(screen_id:str=Query(...),role:str=Query('VIEWER'),status:Optional[str]=None):
    s=require_screen(screen_id);r=role.upper()
    if not screen_role_allowed(s,r): raise HTTPException(403,'Role cannot access this screen')
    caps=ROLE_ACTIONS.get(r,ROLE_ACTIONS['VIEWER']);configured=s['quick_actions'];implemented=functional_actions(s)
    visible=[a for a in configured if a in caps and a in implemented]
    # Status-sensitive pruning to reduce invalid choices in the UI. Server APIs remain authoritative.
    st=(status or '').lower()
    if st in {'closed','cancelled','reversed'}:
        visible=[a for a in visible if a not in {'edit','approve','release','hold','cancel','advance'}]
    if st in {'released','approved'}:
        visible=[a for a in visible if a not in {'approve'}]
    return {'screen_id':screen_id,'role':r,'status':status,'visible_actions':visible,'hidden_actions':[a for a in configured if a not in visible],
            'server_authority':'Existing accepted domain APIs remain authoritative for mutation and approval rules.'}

@router.get('/action-route')
def action_route(screen_id:str=Query(...),action:str=Query(...),record_id:Optional[int]=None,version:Optional[int]=None):
    s=require_screen(screen_id);a=action.lower();domain=s['domain'];key=s['key']
    if a in {'quick-view','print','export','related-records','audit-history'}:return {'mode':'CLIENT_OR_CLX011','action':a}
    if a=='email':return {'mode':'CLX011_SIMULATED','method':'POST','path':'/api/clx011/simulate-email'}
    if domain=='Agent Tasks' and record_id:
        if a in {'approve','release','hold','cancel','amend','reissue','reverse','advance'}:return {'mode':'EXISTING_API','method':'POST','path':f'/api/v1/{key}/{record_id}/actions/{a}','requires':['version']}
        if a=='edit':return {'mode':'EXISTING_API','method':'PUT','path':f'/api/v1/{key}/{record_id}','requires':['version','fields']}
    if screen_id.startswith('gl-accounts::'):
        if a=='create': return {'mode':'EXISTING_API','method':'POST','path':f'/api/v1/gl/{key}','requires':['fields']}
        if record_id and a=='edit': return {'mode':'EXISTING_API','method':'PUT','path':f'/api/v1/gl/{key}/{record_id}','requires':['version','fields']}
        if record_id and a in {'approve','reverse'}:return {'mode':'EXISTING_API','method':'POST','path':f'/api/v1/gl/{key}/{record_id}/actions/{a}','requires':['version']}
    if domain=='Treasury / AR-AP':
        if a=='create': return {'mode':'EXISTING_API','method':'POST','path':f'/api/v1/treasury/{key}','requires':['fields']}
        if record_id and a=='edit': return {'mode':'EXISTING_API','method':'PUT','path':f'/api/v1/treasury/{key}/{record_id}','requires':['version','fields']}
        if record_id and a in {'approve','release','reverse'}:return {'mode':'EXISTING_API','method':'POST','path':f'/api/v1/treasury/{key}/{record_id}/actions/{a}','requires':['version']}
    if domain=='Master Data' and a in {'change-request','approve','reject','activate','deactivate','version-history'}:return {'mode':'EXISTING_GOVERNANCE','note':'Use /api/masterdata/changes and independent checker decision endpoints.'}
    return {'mode':'EXISTING_SCREEN_WORKFLOW','note':'Open the current screen/right-side panel; no new business mutation is introduced by CLX-011.'}

@router.get('/related/{job_ref}')
def related(job_ref:str):
    if not (len(job_ref)==5 and job_ref.isdigit()):raise HTTPException(422,'Job reference must be five digits')
    c=connect()
    try:
        e=job_e2e(c,job_ref)
        jid=e['identity']['id']
        e['linked_records']={
          'booking':rowdict(c.execute('SELECT * FROM bookings WHERE id=?',(e['identity']['booking_id'],)).fetchone()),
          'containers':safe_rows(c,'SELECT * FROM containers WHERE job_id=?',(jid,)),
          'bills':safe_rows(c,'SELECT * FROM bills WHERE job_id=?',(jid,)),
          'agent_tasks':safe_rows(c,'SELECT id,module,external_ref,status,version FROM transaction_records WHERE job_id=? ORDER BY id',(jid,)),
          'gl':safe_rows(c,'SELECT id,module,external_ref,status,version FROM gl_records WHERE job_id=? ORDER BY id',(jid,)),
          'treasury':safe_rows(c,'SELECT id,module,external_ref,status,version FROM treasury_records WHERE job_id=? ORDER BY id',(jid,)),
          'integration':safe_rows(c,'SELECT id,module,external_ref,status,version FROM integration_records WHERE job_id=? ORDER BY id',(jid,)),
          'audit':safe_rows(c,'SELECT * FROM audit_events WHERE job_id=? ORDER BY id DESC',(jid,),30),
          'exceptions':safe_rows(c,'SELECT * FROM exception_events WHERE job_id=? ORDER BY id DESC',(jid,),30),
        }
        return e
    finally:c.close()

@router.get('/workflow/{job_ref}')
def workflow(job_ref:str):
    if not (len(job_ref)==5 and job_ref.isdigit()):raise HTTPException(422,'Job reference must be five digits')
    c=connect()
    try:
        e=job_e2e(c,job_ref)
        chain=['special-rates-request','booking','planning','vessel-lock','cro','crt','export-crt','transshipment-crt','bl','switch-bl','split-bl','import-bl','import-crt','delivery-order','container-activity','detention-collection','storage-cost','agent-receipt-pay','soa']
        present={r['module']:dict(r) for r in c.execute('''SELECT t.module,t.external_ref,t.status,t.version FROM transaction_records t JOIN jobs j ON j.id=t.job_id WHERE j.job_ref=?''',(job_ref,))}
        steps=[]
        for i,k in enumerate(chain):
            r=present.get(k);steps.append({'position':i+1,'module':k,'screen_id':'agent-tasks::'+k,'record_ref':r['external_ref'] if r else None,'status':r['status'] if r else 'MISSING','present':bool(r),'previous':chain[i-1] if i else None,'next':chain[i+1] if i+1<len(chain) else None})
        return {'job_ref':job_ref,'all_steps_present':all(x['present'] for x in steps),'steps':steps,'e2e':e}
    finally:c.close()

@router.get('/search')
def search(q:str=Query(...,min_length=2,max_length=80),role:str=Query('VIEWER')):
    needle=q.lower();r=role.upper();screen_hits=[{'type':'screen','screen_id':s['screen_id'],'name':s['name'],'domain':s['domain'],'submenu':s['submenu']} for s in CATALOG['screens'] if screen_role_allowed(s,r) and needle in json.dumps(s).lower()][:50]
    c=connect();record_hits=[];like='%'+needle+'%'
    try:
        for table,typ in [('transaction_records','agent'),('gl_records','gl'),('treasury_records','treasury'),('integration_records','integration')]:
            for r in c.execute(f'''SELECT x.id,x.module,x.external_ref,j.job_ref,x.status FROM {table} x LEFT JOIN jobs j ON j.id=x.job_id WHERE lower(x.external_ref) LIKE ? OR lower(COALESCE(j.job_ref,'')) LIKE ? OR lower(x.payload_json) LIKE ? LIMIT 25''',(like,like,like)):
                d=dict(r);d['type']=typ;record_hits.append(d)
        for r in c.execute("SELECT domain,record_key,display_name,status,version FROM md_records WHERE lower(record_key) LIKE ? OR lower(display_name) LIKE ? LIMIT 25",(like,like)):
            d=dict(r);d['type']='master';record_hits.append(d)
    finally:c.close()
    return {'query':q,'screen_hits':screen_hits,'record_hits':record_hits[:75],'count':len(screen_hits)+min(75,len(record_hits))}

class EmailIntent(BaseModel):
    screen_id:str
    record_ref:Optional[str]=None
    job_ref:Optional[str]=Field(default=None,pattern=r'^\d{5}$')
    subject:str=Field(default='M3 ERP transaction update',min_length=1,max_length=160)

@router.post('/simulate-email')
def simulate_email(body:EmailIntent,x_role:str=Header('VIEWER'),x_actor_id:str=Header('ui-user',alias='X-Actor-Id')):
    s=require_screen(body.screen_id);r=x_role.upper();
    if 'email' not in ROLE_ACTIONS.get(r,set()):raise HTTPException(403,'Role cannot email')
    c=connect()
    try:
        log(c,r,x_actor_id,'SIMULATED_EMAIL',body.screen_id,body.record_ref,body.job_ref,{'subject':body.subject,'external_delivery':False})
        return {'ok':True,'delivery':'SIMULATED_ONLY','external_delivery':False,'screen':s['name'],'record_ref':body.record_ref}
    finally:c.close()

@router.post('/navigation-event')
def navigation_event(screen_id:str=Query(...),record_ref:Optional[str]=None,job_ref:Optional[str]=None,x_role:str=Header('VIEWER'),x_actor_id:str=Header('ui-user',alias='X-Actor-Id')):
    require_screen(screen_id);c=connect()
    try:log(c,x_role.upper(),x_actor_id,'NAVIGATE',screen_id,record_ref,job_ref,{});return {'ok':True}
    finally:c.close()

@router.get('/events')
def events(limit:int=Query(100,ge=1,le=500)):
    c=connect();init_events(c);rows=[dict(r) for r in c.execute('SELECT * FROM screen_integration_events ORDER BY id DESC LIMIT ?',(limit,))];c.close();return rows

@router.get('/feature-gaps')
def feature_gaps():
    return {'open':0,'fixed':7,'items':[
      {'id':'SFI-001','area':'Menu structure','finding':'193 accepted screens were spread across phase-specific flat/partial menus.','fix':'Unified all screens under six business domains with related workflow submenus and deterministic order.','status':'FIXED'},
      {'id':'SFI-002','area':'Screen relationships','finding':'No canonical metadata defined previous/next/source/linked relationships for every screen.','fix':'Added 193-screen relationship catalog with upstream/downstream position and linked-record model.','status':'FIXED'},
      {'id':'SFI-003','area':'Quick Actions','finding':'Actions were implemented per phase but not surfaced through one context-sensitive hub.','fix':'Added role/status-filtered Quick Action hub that routes to existing authoritative APIs.','status':'FIXED'},
      {'id':'SFI-004','area':'Related Records','finding':'Cross-domain job context required manual navigation between operational and finance screens.','fix':'Added unified related-record view for Job/Booking/Container/Bills/GL/Treasury/Integration/Audit/Exceptions.','status':'FIXED'},
      {'id':'SFI-005','area':'Context retention','finding':'Opening linked records could lose current list context.','fix':'Standardized right-side drawer, popup/quick view and explicit full-screen route behavior.','status':'FIXED'},
      {'id':'SFI-006','area':'Navigation productivity','finding':'No unified breadcrumb/favorites/recent-screen model across all 193 screens.','fix':'Added breadcrumbs, active path, menu search, collapsible groups, favorites and recent screens in the CLX-011 shell.','status':'FIXED'},
      {'id':'SFI-007','area':'Screen UAT inventory','finding':'No single acceptance artifact proved all 193 screens were catalogued once with valid menu placement and related workflow metadata.','fix':'Added machine-readable catalog, route/data UAT and relationship/menu maps.','status':'FIXED'},
    ]}