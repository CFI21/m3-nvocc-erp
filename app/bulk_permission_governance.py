import os, datetime
from fastapi import HTTPException
from .admin import roles_for

CANONICAL_SCOPE={
    '50001':('RTM','NL'),
    '50002':('DXB','AE'),
    '50003':('SHA','CN'),
    '50004':('KHI','PK'),
    '50005':('RTM','NL'),
}

def enabled():
    return os.getenv('M3_BULK_PERMISSION_GOVERNANCE_ENABLED','false').lower()=='true'

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _job_row(conn,job_ref):
    r=conn.execute("""SELECT j.*,c.code customer_code,a.code agent_code
      FROM jobs j JOIN customers c ON c.id=j.customer_id
      JOIN agents a ON a.id=j.agent_id WHERE j.job_ref=?""",(job_ref,)).fetchone()
    if not r: raise HTTPException(404,{'code':'JOB_NOT_FOUND','job_ref':job_ref})
    return r

def resolve_job_scope(conn,job_ref,persist=False):
    j=_job_row(conn,job_ref)
    office=j['office_code'] if 'office_code' in j.keys() else None
    country=j['country_code'] if 'country_code' in j.keys() else None
    branch=j['branch_code'] if 'branch_code' in j.keys() else None
    organization=j['organization_code'] if 'organization_code' in j.keys() else None

    if not office:
        ir=conn.execute("""SELECT office_scope,country_scope FROM integration_records
          WHERE job_id=? AND office_scope IS NOT NULL AND office_scope<>''
          ORDER BY id LIMIT 1""",(j['id'],)).fetchone()
        if ir:
            office=ir['office_scope']
            country=country or ir['country_scope']

    if not office and j['job_ref'] in CANONICAL_SCOPE:
        office,c0=CANONICAL_SCOPE[j['job_ref']]
        country=country or c0

    office_row=None
    if office:
        office_row=conn.execute("""SELECT o.id,o.office_code,o.country_id,o.organization_id,
          c.country_code,org.org_code organization_code
          FROM iam_offices o
          LEFT JOIN iam_countries c ON c.id=o.country_id
          LEFT JOIN iam_organizations org ON org.id=o.organization_id
          WHERE o.office_code=? AND o.status='ACTIVE'""",(office,)).fetchone()
        if not office_row:
            raise HTTPException(409,{'code':'JOB_OFFICE_SCOPE_INVALID','job_ref':job_ref,'office_code':office})
        country=country or office_row['country_code']
        organization=organization or office_row['organization_code']
        if country and office_row['country_code'] and country!=office_row['country_code']:
            raise HTTPException(409,{'code':'JOB_COUNTRY_SCOPE_MISMATCH','job_ref':job_ref})
        if organization and office_row['organization_code'] and organization!=office_row['organization_code']:
            raise HTTPException(409,{'code':'JOB_ORGANIZATION_SCOPE_MISMATCH','job_ref':job_ref})
        if not branch:
            bs=conn.execute("""SELECT branch_code FROM iam_branches
              WHERE office_id=? AND status='ACTIVE' ORDER BY id""",(office_row['id'],)).fetchall()
            if len(bs)==1: branch=bs[0]['branch_code']

    complete=bool(office and country and organization)
    if persist and complete:
        conn.execute("""UPDATE jobs SET office_code=?,branch_code=?,country_code=?,organization_code=?
          WHERE id=?""",(office,branch,country,organization,j['id']))

    return {
        'job_id':j['id'],'job_ref':j['job_ref'],
        'customer_code':j['customer_code'],'agent_code':j['agent_code'],
        'office_code':office,'branch_code':branch,
        'country_code':country,'organization_code':organization,
        'created_by_user_ref':j['created_by_user_ref'] if 'created_by_user_ref' in j.keys() else None,
        'complete':complete,
    }

def assign_new_job_scope(conn,job_id,user_id,branch_code=None):
    roles=roles_for(conn,user_id)
    scoped=[r for r in roles if r.get('office_id') or r.get('country_id') or r.get('organization_id')]
    if not scoped:
        raise HTTPException(403,{'code':'NO_ASSIGNABLE_JOB_SCOPE'})
    # prefer the most specific active role assignment
    r=sorted(scoped,key=lambda x:(bool(x.get('organization_id')),bool(x.get('country_id')),bool(x.get('office_id'))),reverse=True)[0]
    office=None;country=None;organization=None
    if r.get('office_id'):
        o=conn.execute("""SELECT o.office_code,c.country_code,org.org_code organization_code
          FROM iam_offices o LEFT JOIN iam_countries c ON c.id=o.country_id
          LEFT JOIN iam_organizations org ON org.id=o.organization_id WHERE o.id=?""",(r['office_id'],)).fetchone()
        if o:
            office=o['office_code'];country=o['country_code'];organization=o['organization_code']
    if r.get('country_id') and not country:
        x=conn.execute('SELECT country_code FROM iam_countries WHERE id=?',(r['country_id'],)).fetchone()
        country=x['country_code'] if x else None
    if r.get('organization_id') and not organization:
        x=conn.execute('SELECT org_code FROM iam_organizations WHERE id=?',(r['organization_id'],)).fetchone()
        organization=x['org_code'] if x else None
    if branch_code:
        b=conn.execute("""SELECT b.branch_code,o.office_code,c.country_code,org.org_code organization_code
          FROM iam_branches b JOIN iam_offices o ON o.id=b.office_id
          LEFT JOIN iam_countries c ON c.id=o.country_id
          LEFT JOIN iam_organizations org ON org.id=o.organization_id
          WHERE b.branch_code=? AND b.status='ACTIVE'""",(branch_code,)).fetchone()
        if not b: raise HTTPException(422,{'code':'UNKNOWN_BRANCH_SCOPE'})
        if office and b['office_code']!=office: raise HTTPException(403,{'code':'BRANCH_OUTSIDE_USER_OFFICE_SCOPE'})
        branch_code=b['branch_code'];office=b['office_code'];country=b['country_code'];organization=b['organization_code']
    u=conn.execute('SELECT user_ref FROM iam_users WHERE id=? AND status="ACTIVE"',(user_id,)).fetchone()
    if not u: raise HTTPException(403,{'code':'USER_NOT_ACTIVE'})
    if not (office and country and organization):
        raise HTTPException(409,{'code':'INCOMPLETE_NEW_JOB_SCOPE'})
    conn.execute("""UPDATE jobs SET office_code=?,branch_code=?,country_code=?,organization_code=?,created_by_user_ref=?
      WHERE id=?""",(office,branch_code,country,organization,u['user_ref'],job_id))
    return {'office_code':office,'branch_code':branch_code,'country_code':country,'organization_code':organization,'created_by_user_ref':u['user_ref']}

def _has_action_permission(conn,role_code,resource,action):
    if role_code=='SUPER_ADMIN': return True
    return bool(conn.execute("""SELECT 1 FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id
      JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE r.role_code=? AND rp.effect='ALLOW'
      AND (p.module=? OR ?='*') AND (p.action=? OR ?='*') LIMIT 1""",
      (role_code,resource,resource,action,action)).fetchone())

def _assignment_scope_values(conn,role_assignment):
    out={}
    if role_assignment.get('office_id'):
        x=conn.execute("""SELECT o.office_code,c.country_code,org.org_code organization_code
          FROM iam_offices o LEFT JOIN iam_countries c ON c.id=o.country_id
          LEFT JOIN iam_organizations org ON org.id=o.organization_id WHERE o.id=?""",(role_assignment['office_id'],)).fetchone()
        if x: out.update(dict(x))
    if role_assignment.get('country_id'):
        x=conn.execute('SELECT country_code FROM iam_countries WHERE id=?',(role_assignment['country_id'],)).fetchone()
        if x: out['country_code']=x['country_code']
    if role_assignment.get('organization_id'):
        x=conn.execute('SELECT org_code FROM iam_organizations WHERE id=?',(role_assignment['organization_id'],)).fetchone()
        if x: out['organization_code']=x['org_code']
    return out

def _party_narrowing(conn,user_id,job):
    rows=[dict(x) for x in conn.execute("""SELECT party_type,party_key,access_level FROM iam_party_access
      WHERE user_id=? AND status='ACTIVE'""",(user_id,))]
    if not rows:return True,[]
    reasons=[]
    for typ in ('CUSTOMER','AGENT'):
        subset=[r for r in rows if r['party_type']==typ]
        if not subset: continue
        key=job['customer_code'] if typ=='CUSTOMER' else job['agent_code']
        ok=any(r['party_key']==key for r in subset)
        reasons.append({'party_type':typ,'job_party':key,'allowed':ok})
        if not ok:return False,reasons
    return True,reasons

def _rule_scope_match(conn,rule,assignment,job,user_ref):
    st=rule['scope_type']
    val=rule['scope_value']
    av=_assignment_scope_values(conn,assignment)
    if st=='GLOBAL':return True
    if st=='OFFICE': return job['office_code'] is not None and job['office_code']==(val or av.get('office_code'))
    if st=='BRANCH': return job['branch_code'] is not None and job['branch_code']==val
    if st=='COUNTRY': return job['country_code'] is not None and job['country_code']==(val or av.get('country_code'))
    if st=='ORGANIZATION': return job['organization_code'] is not None and job['organization_code']==(val or av.get('organization_code'))
    if st=='CUSTOMER': return bool(val and job['customer_code']==val)
    if st=='AGENT':
        if val:return job['agent_code']==val
        p=conn.execute("""SELECT 1 FROM iam_party_access WHERE user_id=? AND party_type='AGENT'
          AND party_key=? AND status='ACTIVE' LIMIT 1""",(assignment['user_id'],job['agent_code'])).fetchone()
        return bool(p)
    if st=='OWN':return bool(user_ref and job.get('created_by_user_ref')==user_ref)
    return False

def authorize_job(conn,user_id,job_ref,resource='agent-tasks',action='view'):
    u=conn.execute('SELECT id,user_ref,status FROM iam_users WHERE id=?',(user_id,)).fetchone()
    if not u or u['status']!='ACTIVE':return {'allowed':False,'code':'USER_NOT_ACTIVE'}
    job=resolve_job_scope(conn,job_ref,persist=False)
    if not job['complete']:return {'allowed':False,'code':'JOB_SCOPE_INCOMPLETE','job':job}

    role_rows=roles_for(conn,user_id)
    expanded=[]
    for r in role_rows:
        rr=dict(r);rr['user_id']=user_id;expanded.append(rr)
    matches=[]
    for assignment in expanded:
        role=assignment['role_code']
        if not _has_action_permission(conn,role,resource,action):continue
        rules=[dict(x) for x in conn.execute("""SELECT * FROM iam_scope_rules WHERE status='ACTIVE'
          AND role_code=? AND (resource=? OR resource='*') AND (action=? OR action='*')
          ORDER BY priority ASC,id ASC""",(role,resource,action))]
        for rule in rules:
            if _rule_scope_match(conn,rule,assignment,job,u['user_ref']):
                matches.append({'role':role,'rule_ref':rule['rule_ref'],'effect':rule['effect'],'scope_type':rule['scope_type'],'priority':rule['priority']})

    denies=[m for m in matches if m['effect']=='DENY']
    if denies:return {'allowed':False,'code':'SCOPE_DENY_RULE','job':job,'matches':matches}

    allows=[m for m in matches if m['effect']=='ALLOW']
    if not allows:return {'allowed':False,'code':'NO_MATCHING_ROLE_SCOPE_RULE','job':job,'matches':matches}

    party_ok,party_detail=_party_narrowing(conn,user_id,job)
    if not party_ok:return {'allowed':False,'code':'PARTY_SCOPE_DENIED','job':job,'matches':matches,'party':party_detail}

    return {'allowed':True,'code':'AUTHORIZED','job':job,'matches':matches,'party':party_detail}

def assert_job_access(conn,user_id,job_ref,resource='agent-tasks',action='view'):
    out=authorize_job(conn,user_id,job_ref,resource,action)
    if not out['allowed']: raise HTTPException(403,out)
    return out

def bulk_validate(conn,resource='agent-tasks',action='view'):
    users=[dict(x) for x in conn.execute("SELECT id,user_ref,username FROM iam_users WHERE status='ACTIVE' ORDER BY id")]
    jobs=[x['job_ref'] for x in conn.execute('SELECT job_ref FROM jobs ORDER BY job_ref')]
    results=[]
    for u in users:
        for jr in jobs:
            r=authorize_job(conn,u['id'],jr,resource,action)
            results.append({'user_ref':u['user_ref'],'username':u['username'],'job_ref':jr,'allowed':r['allowed'],'code':r['code']})
    return {
        'total_active_users':len(users),
        'total_active_jobs':len(jobs),
        'tests':len(results),
        'authorized':sum(1 for r in results if r['allowed']),
        'blocked':sum(1 for r in results if not r['allowed']),
        'results':results,
        'job_by_job_permission_required':False,
        'duplicate_access_model':False,
    }
