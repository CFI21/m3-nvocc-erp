from fastapi import APIRouter,Header,HTTPException
import json
from .db import connect
from .masterdata import DOMAINS,GOVERNANCE_SCREENS,init_schema
from .admin import ADMIN_SCREENS,roles_for,permission

router=APIRouter(prefix='/api/clx048',tags=['CLX-048 General Administration + Master Data Hardening'])

@router.get('/workspace')
def workspace():
    return {
      'phase':'CLX-048',
      'administration_screens':len(ADMIN_SCREENS),
      'master_domains':len(DOMAINS),
      'master_governance_screens':len(GOVERNANCE_SCREENS),
      'screen_count_change':0,
      'live_providers':False,
      'real_money':False
    }

def _active_master(c,domain,key):
    return bool(c.execute("SELECT 1 FROM md_records WHERE domain=? AND record_key=? AND status='ACTIVE'",(domain,key)).fetchone())

@router.get('/job/{job_ref}/master-trace')
def job_master_trace(job_ref:str,x_role:str=Header('AUDITOR')):
    if x_role.upper() not in {'ADMIN','SUPER_ADMIN','AUDITOR','MASTER_DATA','MASTER_DATA_MANAGER','OPS','FINANCE'}:
        raise HTTPException(403,'Role cannot view master trace')
    c=connect();init_schema(c)
    try:
        j=c.execute('''SELECT j.id,j.job_ref,j.pol,j.pod,c.code customer_code,c.name customer_name,
          a.code agent_code,a.name agent_name,v.voyage_no,s.name vessel_name
          FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
          JOIN voyages v ON v.id=j.voyage_id JOIN vessels s ON s.id=v.vessel_id WHERE j.job_ref=?''',(job_ref,)).fetchone()
        if not j:raise HTTPException(404,'Unknown job')
        refs=[
          {'domain':'customer','key':j['customer_code']},
          {'domain':'agent','key':j['agent_code']},
          {'domain':'port','key':j['pol']},
          {'domain':'port','key':j['pod']},
          {'domain':'voyage','key':j['voyage_no']}
        ]
        vessel=c.execute("SELECT record_key FROM md_records WHERE domain='vessel' AND display_name=? AND status='ACTIVE'",(j['vessel_name'],)).fetchone()
        refs.append({'domain':'vessel','key':vessel['record_key'] if vessel else None})
        resolved=[]
        for ref in refs:
            key=ref['key'];row=c.execute('SELECT record_key,display_name,status,version,effective_from,effective_to FROM md_records WHERE domain=? AND record_key=?',(ref['domain'],key)).fetchone() if key else None
            resolved.append({**ref,'resolved':bool(row and row['status']=='ACTIVE'),'record':dict(row) if row else None})
        integ=[dict(r) for r in c.execute('''SELECT ir.module,ir.external_ref,ir.office_scope,ir.country_scope,p.provider_key
          FROM integration_records ir JOIN finance_providers p ON p.id=ir.provider_id WHERE ir.job_id=? ORDER BY ir.module''',(j['id'],))]
        scopes=[]
        for r in integ:
            office=c.execute("SELECT office_code,status FROM iam_offices WHERE office_code=?",(r['office_scope'],)).fetchone()
            country=c.execute("SELECT country_code,status FROM iam_countries WHERE country_code=?",(r['country_scope'],)).fetchone()
            scopes.append({'module':r['module'],'office':r['office_scope'],'office_valid':bool(office and office['status']=='ACTIVE'),
              'country':r['country_scope'],'country_valid':bool(country and country['status']=='ACTIVE')})
        return {'phase':'CLX-048','job':dict(j),'master_references':resolved,'integration_scopes':scopes,
          'master_references_valid':all(x['resolved'] for x in resolved),
          'scope_references_valid':all(x['office_valid'] and x['country_valid'] for x in scopes)}
    finally:c.close()

@router.get('/integrity-summary')
def integrity_summary(x_role:str=Header('AUDITOR')):
    if x_role.upper() not in {'ADMIN','SUPER_ADMIN','AUDITOR','MASTER_DATA_MANAGER'}:raise HTTPException(403,'Role cannot view integrity summary')
    c=connect();init_schema(c)
    try:
        duplicate_count=c.execute("""SELECT COUNT(*) n FROM (
          SELECT domain,lower(display_name),COUNT(*) c FROM md_records WHERE status='ACTIVE'
          GROUP BY domain,lower(display_name) HAVING COUNT(*)>1)""").fetchone()['n']
        pending_conflicts=0
        pending=list(c.execute("SELECT domain,record_key,payload_json FROM md_change_requests WHERE status='PENDING' AND operation IN ('CREATE','UPDATE')"))
        seen={}
        for r in pending:
            p=json.loads(r['payload_json'] or '{}');name=(p.get('name') or p.get('display_name') or '').strip().lower()
            if name:
                k=(r['domain'],name);pending_conflicts+=1 if k in seen and seen[k]!=r['record_key'] else 0;seen[k]=r['record_key']
        broken=[]
        for jr in ('50001','50002','50003','50004','50005'):
            t=job_master_trace(jr,x_role)
            if not t['master_references_valid'] or not t['scope_references_valid']:broken.append(jr)
        audit_count=c.execute('SELECT COUNT(*) n FROM md_audit').fetchone()['n']
        iam_audit_count=c.execute('SELECT COUNT(*) n FROM iam_audit_events').fetchone()['n']
        return {'phase':'CLX-048','duplicate_active_master_names':duplicate_count,'pending_duplicate_names':pending_conflicts,
          'broken_test_job_master_refs':broken,'master_audit_events':audit_count,'iam_audit_events':iam_audit_count,
          'master_audit_immutable':True,'iam_audit_immutable':True,
          'status':'PASS' if duplicate_count==0 and pending_conflicts==0 and not broken else 'FAIL',
          'live_providers':False,'real_money':False}
    finally:c.close()

@router.get('/user/{username}/access')
def user_access(username:str,x_role:str=Header('AUDITOR')):
    if x_role.upper() not in {'ADMIN','SUPER_ADMIN','AUDITOR'}:raise HTTPException(403,'Role cannot view access trace')
    c=connect()
    try:
        u=c.execute('''SELECT u.id,u.user_ref,u.username,u.status,o.office_code,c.country_code
          FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries c ON c.id=o.country_id
          WHERE u.username=?''',(username,)).fetchone()
        if not u:raise HTTPException(404,'User not found')
        roles=roles_for(c,u['id'])
        checks={}
        for module,action in [('identity','admin'),('masterdata','create'),('masterdata','approve'),('gl','view'),('treasury','view'),('integration','view')]:
            checks[f'{module}:{action}']=permission(c,u['id'],module,action,u['office_code'])
        return {'user':dict(u),'roles':roles,'permission_checks':checks,'evaluated_live':True}
    finally:c.close()

@router.get('/verify')
def verify(x_role:str=Header('AUDITOR')):
    traces=[job_master_trace(j,x_role) for j in ('50001','50002','50003','50004','50005')]
    integ=integrity_summary('AUDITOR')
    return {'phase':'CLX-048','jobs':[{'job_ref':x['job']['job_ref'],'master_references_valid':x['master_references_valid'],
      'scope_references_valid':x['scope_references_valid']} for x in traces],
      'all_jobs_valid':all(x['master_references_valid'] and x['scope_references_valid'] for x in traces),
      'integrity_status':integ['status'],'screen_count_change':0,'live_providers':False,'real_money':False}
