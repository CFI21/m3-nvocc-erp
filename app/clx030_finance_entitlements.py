from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field
from typing import Optional
import json, uuid, datetime
from .db import connect
from .admin import session, permission, audit, now

router=APIRouter(prefix='/api/clx030',tags=['CLX-030 Finance Entitlement Management'])

FINANCE_CODES={
 'GL_VIEW','GL_CREATE','GL_EDIT','GL_DISABLE','GL_POST','JOURNAL_CREATE','JOURNAL_APPROVE',
 'ACCOUNT_MAPPING_MANAGE','COST_PROFIT_CENTER_MANAGE','TAX_CURRENCY_MANAGE','PERIOD_CLOSE','FINANCE_CONFIG_ADMIN'
}
SENSITIVE={'GL_DISABLE','GL_POST','JOURNAL_APPROVE','ACCOUNT_MAPPING_MANAGE','COST_PROFIT_CENTER_MANAGE','TAX_CURRENCY_MANAGE','PERIOD_CLOSE','FINANCE_CONFIG_ADMIN'}

class GrantRequest(BaseModel):
    target_type:str=Field(pattern='^(USER|ROLE)$')
    username:Optional[str]=None
    role_code:Optional[str]=None
    permission_code:str
    office_code:Optional[str]=None
    country_code:Optional[str]=None
    valid_from:Optional[str]=None
    valid_to:Optional[str]=None
    reason:str=Field(min_length=3,max_length=300)

class RoleAssignmentRequest(BaseModel):
    username:str
    role_code:str=Field(pattern='^(GL_ACCOUNTANT|GL_MANAGER)
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})

def finance_permission(c,code):
    if code not in FINANCE_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_ENTITLEMENT'})
    p=c.execute('SELECT * FROM iam_permissions WHERE permission_code=?',(code,)).fetchone()
    if not p: raise HTTPException(404,'Permission not found')
    return p

def target(c,b):
    if b.target_type=='USER':
        if not b.username: raise HTTPException(422,'username required')
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    if not b.role_code: raise HTTPException(422,'role_code required')
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r

def apply_grant(c,payload,actor_ref):
    code=payload['permission_code']; p=finance_permission(c,code)
    if payload['target_type']=='ROLE':
        r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(payload['role_code'],)).fetchone()
        before=c.execute('SELECT effect FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
        if before:
            c.execute("UPDATE iam_role_permissions SET effect='ALLOW' WHERE role_id=? AND permission_id=?",(r['id'],p['id']))
        else:
            c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(r['id'],p['id']))
        after={'role':r['role_code'],'permission':code,'effect':'ALLOW'}
        audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','ROLE',r['role_code'],dict(before) if before else None,after)
        return after
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to') or '2099-12-31T23:59:59+00:00'
    ref='FENT-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
                 VALUES(?,?,?,?,?,?,?,?)''',(ref,u['id'],code,vf,vt,payload['reason'],'ACTIVE',actor_ref))
    after={'username':u['username'],'permission':code,'valid_from':vf,'valid_to':vt,'office_code':payload.get('office_code'),'country_code':payload.get('country_code'),'ref':ref}
    audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','USER',u['user_ref'],None,after,{'office_code':payload.get('office_code'),'country_code':payload.get('country_code')})
    return after


def apply_role_assignment(c,payload,actor_ref):
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    r=c.execute("SELECT * FROM iam_roles WHERE role_code=? AND role_code IN ('GL_ACCOUNTANT','GL_MANAGER')",(payload['role_code'],)).fetchone()
    if not u or not r: raise HTTPException(404,'User or GL role not found')
    office=None; country=None
    if payload.get('office_code'):
        office=c.execute('SELECT * FROM iam_offices WHERE office_code=?',(payload['office_code'],)).fetchone()
        if not office: raise HTTPException(404,'Office not found')
    else:
        office=c.execute('SELECT * FROM iam_offices WHERE id=?',(u['home_office_id'],)).fetchone()
    if payload.get('country_code'):
        country=c.execute('SELECT * FROM iam_countries WHERE country_code=?',(payload['country_code'],)).fetchone()
        if not country: raise HTTPException(404,'Country not found')
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to')
    existing=c.execute('''SELECT ur.*,o.office_code,co.country_code FROM iam_user_roles ur
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      WHERE ur.user_id=? AND ur.role_id=? AND COALESCE(ur.office_id,0)=COALESCE(?,0)
      AND COALESCE(ur.country_id,0)=COALESCE(?,0) ORDER BY ur.id DESC LIMIT 1''',
      (u['id'],r['id'],office['id'] if office else None,country['id'] if country else None)).fetchone()
    before=dict(existing) if existing else None
    if existing:
        c.execute("UPDATE iam_user_roles SET valid_from=?,valid_to=?,status='ACTIVE',assigned_by=? WHERE id=?",(vf,vt,actor_ref,existing['id']))
        assignment_id=existing['id']
    else:
        cur=c.execute('''INSERT INTO iam_user_roles(user_id,role_id,office_id,country_id,organization_id,valid_from,valid_to,status,assigned_by)
          VALUES(?,?,?,?,?,?,?,?,?)''',(u['id'],r['id'],office['id'] if office else None,country['id'] if country else None,None,vf,vt,'ACTIVE',actor_ref))
        assignment_id=cur.lastrowid
    after={'assignment_id':assignment_id,'username':u['username'],'role_code':r['role_code'],'office_code':office['office_code'] if office else None,
      'country_code':country['country_code'] if country else None,'valid_from':vf,'valid_to':vt,'status':'ACTIVE'}
    audit(c,actor_ref,'FINANCE_ROLE_ASSIGN','USER',u['user_ref'],before,after)
    return after

@router.get('/matrix')
def matrix(q:Optional[str]=Query(None),target_type:Optional[str]=Query(None),status:Optional[str]=Query(None)):
    c=connect()
    users=[dict(r) for r in c.execute('''SELECT u.user_ref,u.username,u.display_name,u.status,o.office_code,co.country_code
      FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id ORDER BY u.username''')]
    roles=[dict(r) for r in c.execute("SELECT role_code,name,status,sensitive FROM iam_roles WHERE role_code IN ('GL_ACCOUNTANT','GL_MANAGER','FINANCE','OPS','AGENT','AUDITOR','VIEWER','SUPER_ADMIN') ORDER BY role_code")]
    role_perms=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.sensitive,rp.effect FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE p.permission_code IN (%s) ORDER BY r.role_code,p.permission_code'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    direct=[dict(r) for r in c.execute('''SELECT t.temp_ref,u.username,p.permission_code,p.sensitive,t.valid_from,t.valid_to,t.status,t.reason,t.approved_by
      FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id JOIN iam_permissions p ON p.permission_code=t.permission_code
      WHERE t.permission_code IN (%s) ORDER BY t.id DESC'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    assignments=[dict(r) for r in c.execute('''SELECT ur.id assignment_id,u.username,r.role_code,o.office_code,co.country_code,ur.valid_from,ur.valid_to,ur.status,ur.assigned_by
      FROM iam_user_roles ur JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles r ON r.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      ORDER BY u.username,r.role_code''')]
    reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_ENTITLEMENT%' ORDER BY id DESC")]
    c.close()
    if q:
        needle=q.lower()
        users=[x for x in users if needle in json.dumps(x).lower()]
        roles=[x for x in roles if needle in json.dumps(x).lower()]
        role_perms=[x for x in role_perms if needle in json.dumps(x).lower()]
        direct=[x for x in direct if needle in json.dumps(x).lower()]
        assignments=[x for x in assignments if needle in json.dumps(x).lower()]
        reviews=[x for x in reviews if needle in json.dumps(x).lower()]
    if status:
        direct=[x for x in direct if x['status']==status]
        assignments=[x for x in assignments if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-030','users':users,'roles':roles,'role_permissions':role_perms,'direct_permissions':direct,'assignments':assignments,'access_reviews':reviews,'finance_permissions':sorted(FINANCE_CODES),'sensitive_permissions':sorted(SENSITIVE)}

@router.post('/grants')
def request_grant(b:GrantRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); finance_permission(c,b.permission_code); target(c,b)
    payload=b.model_dump(); sensitive=b.permission_code in SENSITIVE
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ENTITLEMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,s['user_id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_grant(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','grant':out,'four_eyes':False}


@router.post('/role-assignments')
def request_role_assignment(b:RoleAssignmentRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
    r=c.execute("SELECT * FROM iam_roles WHERE role_code=? AND role_code IN ('GL_ACCOUNTANT','GL_MANAGER')",(b.role_code,)).fetchone()
    if not u or not r: c.close(); raise HTTPException(404,'User or GL role not found')
    if b.valid_to and b.valid_from and b.valid_to<=b.valid_from: c.close(); raise HTTPException(422,{'code':'INVALID_EFFECTIVE_DATES'})
    payload=b.model_dump(); sensitive=bool(r['sensitive'])
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ROLE_ASSIGNMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,u['id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ROLE_ASSIGN_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_role_assignment(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','assignment':out,'four_eyes':False}

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Review not found')
    if r['status']!='PENDING': c.close(); raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
    scope=json.loads(r['scope']); maker=scope.get('maker')
    if maker==s['user_ref']: c.close(); raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
    if b.decision=='REJECT':
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
        c.close(); return {'status':'REJECTED','review_ref':review_ref}
    if scope.get('kind')=='FINANCE_ROLE_ASSIGNMENT':
        out=apply_role_assignment(c,scope['request'],s['user_ref'])
        result_key='assignment'
        approve_action='FINANCE_ROLE_ASSIGN_APPROVE'
    else:
        out=apply_grant(c,scope['request'],s['user_ref'])
        result_key='grant'
        approve_action='FINANCE_ENTITLEMENT_APPROVE'
    c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
    audit(c,s['user_ref'],approve_action,'ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED',result_key:out})
    c.close(); return {'status':'APPROVED','review_ref':review_ref,result_key:out}

@router.post('/direct/{temp_ref}/revoke')
def revoke_direct(temp_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id WHERE t.temp_ref=?''',(temp_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Grant not found')
    before=dict(r); c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE id=?",(r['id'],))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REVOKE','USER',r['user_ref'],before,{'status':'REVOKED','permission_code':r['permission_code']})
    c.close(); return {'status':'REVOKED','temp_ref':temp_ref}

@router.post('/roles/{role_code}/permissions/{permission_code}/revoke')
def revoke_role(role_code:str,permission_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); p=finance_permission(c,permission_code)
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(role_code,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Role not found')
    rp=c.execute('SELECT * FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
    before=dict(rp) if rp else None
    if rp:c.execute("UPDATE iam_role_permissions SET effect='DENY' WHERE id=?",(rp['id'],))
    else:c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'DENY')",(r['id'],p['id']))
    audit(c,s['user_ref'],'FINANCE_ROLE_PERMISSION_REVOKE','ROLE',role_code,before,{'permission':permission_code,'effect':'DENY'})
    c.close(); return {'status':'REVOKED','role':role_code,'permission':permission_code}

@router.post('/role-assignments/{assignment_id}/revoke')
def revoke_role_assignment(assignment_id:int,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT ur.*,u.user_ref,u.username,ro.role_code,o.office_code,co.country_code FROM iam_user_roles ur
      JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles ro ON ro.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id WHERE ur.id=?''',(assignment_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Assignment not found')
    if r['role_code'] not in ('GL_ACCOUNTANT','GL_MANAGER'): c.close(); raise HTTPException(422,'Not a GL role assignment')
    before=dict(r); c.execute("UPDATE iam_user_roles SET status='INACTIVE' WHERE id=?",(assignment_id,))
    audit(c,s['user_ref'],'FINANCE_ROLE_ASSIGN_REVOKE','USER',r['user_ref'],before,{'assignment_id':assignment_id,'status':'INACTIVE','role_code':r['role_code']})
    c.close(); return {'status':'REVOKED','assignment_id':assignment_id}

@router.get('/audit')
def entitlement_audit(limit:int=Query(100,ge=1,le=500)):
    c=connect(); rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT ?",(limit,))]; c.close(); return rows
)
    office_code:Optional[str]=None
    country_code:Optional[str]=None
    valid_from:Optional[str]=None
    valid_to:Optional[str]=None
    reason:str=Field(min_length=3,max_length=300)

class Decision(BaseModel):
    decision:str=Field(pattern='^(APPROVE|REJECT)

def require_admin(c,s):
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})

def finance_permission(c,code):
    if code not in FINANCE_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_ENTITLEMENT'})
    p=c.execute('SELECT * FROM iam_permissions WHERE permission_code=?',(code,)).fetchone()
    if not p: raise HTTPException(404,'Permission not found')
    return p

def target(c,b):
    if b.target_type=='USER':
        if not b.username: raise HTTPException(422,'username required')
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    if not b.role_code: raise HTTPException(422,'role_code required')
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r

def apply_grant(c,payload,actor_ref):
    code=payload['permission_code']; p=finance_permission(c,code)
    if payload['target_type']=='ROLE':
        r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(payload['role_code'],)).fetchone()
        before=c.execute('SELECT effect FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
        if before:
            c.execute("UPDATE iam_role_permissions SET effect='ALLOW' WHERE role_id=? AND permission_id=?",(r['id'],p['id']))
        else:
            c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(r['id'],p['id']))
        after={'role':r['role_code'],'permission':code,'effect':'ALLOW'}
        audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','ROLE',r['role_code'],dict(before) if before else None,after)
        return after
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to') or '2099-12-31T23:59:59+00:00'
    ref='FENT-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
                 VALUES(?,?,?,?,?,?,?,?)''',(ref,u['id'],code,vf,vt,payload['reason'],'ACTIVE',actor_ref))
    after={'username':u['username'],'permission':code,'valid_from':vf,'valid_to':vt,'office_code':payload.get('office_code'),'country_code':payload.get('country_code'),'ref':ref}
    audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','USER',u['user_ref'],None,after,{'office_code':payload.get('office_code'),'country_code':payload.get('country_code')})
    return after

@router.get('/matrix')
def matrix(q:Optional[str]=Query(None),target_type:Optional[str]=Query(None),status:Optional[str]=Query(None)):
    c=connect()
    users=[dict(r) for r in c.execute('''SELECT u.user_ref,u.username,u.display_name,u.status,o.office_code,co.country_code
      FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id ORDER BY u.username''')]
    roles=[dict(r) for r in c.execute("SELECT role_code,name,status,sensitive FROM iam_roles WHERE role_code IN ('GL_ACCOUNTANT','GL_MANAGER','FINANCE','OPS','AGENT','AUDITOR','VIEWER','SUPER_ADMIN') ORDER BY role_code")]
    role_perms=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.sensitive,rp.effect FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE p.permission_code IN (%s) ORDER BY r.role_code,p.permission_code'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    direct=[dict(r) for r in c.execute('''SELECT t.temp_ref,u.username,p.permission_code,p.sensitive,t.valid_from,t.valid_to,t.status,t.reason,t.approved_by
      FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id JOIN iam_permissions p ON p.permission_code=t.permission_code
      WHERE t.permission_code IN (%s) ORDER BY t.id DESC'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    assignments=[dict(r) for r in c.execute('''SELECT u.username,r.role_code,o.office_code,co.country_code,ur.valid_from,ur.valid_to,ur.status,ur.assigned_by
      FROM iam_user_roles ur JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles r ON r.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      ORDER BY u.username,r.role_code''')]
    reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_ENTITLEMENT%' ORDER BY id DESC")]
    c.close()
    if q:
        needle=q.lower()
        users=[x for x in users if needle in json.dumps(x).lower()]
        roles=[x for x in roles if needle in json.dumps(x).lower()]
        role_perms=[x for x in role_perms if needle in json.dumps(x).lower()]
        direct=[x for x in direct if needle in json.dumps(x).lower()]
        assignments=[x for x in assignments if needle in json.dumps(x).lower()]
        reviews=[x for x in reviews if needle in json.dumps(x).lower()]
    if status:
        direct=[x for x in direct if x['status']==status]
        assignments=[x for x in assignments if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-030','users':users,'roles':roles,'role_permissions':role_perms,'direct_permissions':direct,'assignments':assignments,'access_reviews':reviews,'finance_permissions':sorted(FINANCE_CODES),'sensitive_permissions':sorted(SENSITIVE)}

@router.post('/grants')
def request_grant(b:GrantRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); finance_permission(c,b.permission_code); target(c,b)
    payload=b.model_dump(); sensitive=b.permission_code in SENSITIVE
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ENTITLEMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,s['user_id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_grant(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','grant':out,'four_eyes':False}

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Review not found')
    if r['status']!='PENDING': c.close(); raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
    scope=json.loads(r['scope']); maker=scope.get('maker')
    if maker==s['user_ref']: c.close(); raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
    if b.decision=='REJECT':
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
        c.close(); return {'status':'REJECTED','review_ref':review_ref}
    out=apply_grant(c,scope['request'],s['user_ref'])
    c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_APPROVE','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED','grant':out})
    c.close(); return {'status':'APPROVED','review_ref':review_ref,'grant':out}

@router.post('/direct/{temp_ref}/revoke')
def revoke_direct(temp_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id WHERE t.temp_ref=?''',(temp_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Grant not found')
    before=dict(r); c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE id=?",(r['id'],))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REVOKE','USER',r['user_ref'],before,{'status':'REVOKED','permission_code':r['permission_code']})
    c.close(); return {'status':'REVOKED','temp_ref':temp_ref}

@router.post('/roles/{role_code}/permissions/{permission_code}/revoke')
def revoke_role(role_code:str,permission_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); p=finance_permission(c,permission_code)
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(role_code,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Role not found')
    rp=c.execute('SELECT * FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
    before=dict(rp) if rp else None
    if rp:c.execute("UPDATE iam_role_permissions SET effect='DENY' WHERE id=?",(rp['id'],))
    else:c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'DENY')",(r['id'],p['id']))
    audit(c,s['user_ref'],'FINANCE_ROLE_PERMISSION_REVOKE','ROLE',role_code,before,{'permission':permission_code,'effect':'DENY'})
    c.close(); return {'status':'REVOKED','role':role_code,'permission':permission_code}

@router.get('/audit')
def entitlement_audit(limit:int=Query(100,ge=1,le=500)):
    c=connect(); rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT ?",(limit,))]; c.close(); return rows
)
    comment:Optional[str]=None

def require_admin(c,s):
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})

def finance_permission(c,code):
    if code not in FINANCE_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_ENTITLEMENT'})
    p=c.execute('SELECT * FROM iam_permissions WHERE permission_code=?',(code,)).fetchone()
    if not p: raise HTTPException(404,'Permission not found')
    return p

def target(c,b):
    if b.target_type=='USER':
        if not b.username: raise HTTPException(422,'username required')
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    if not b.role_code: raise HTTPException(422,'role_code required')
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r

def apply_grant(c,payload,actor_ref):
    code=payload['permission_code']; p=finance_permission(c,code)
    if payload['target_type']=='ROLE':
        r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(payload['role_code'],)).fetchone()
        before=c.execute('SELECT effect FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
        if before:
            c.execute("UPDATE iam_role_permissions SET effect='ALLOW' WHERE role_id=? AND permission_id=?",(r['id'],p['id']))
        else:
            c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(r['id'],p['id']))
        after={'role':r['role_code'],'permission':code,'effect':'ALLOW'}
        audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','ROLE',r['role_code'],dict(before) if before else None,after)
        return after
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to') or '2099-12-31T23:59:59+00:00'
    ref='FENT-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
                 VALUES(?,?,?,?,?,?,?,?)''',(ref,u['id'],code,vf,vt,payload['reason'],'ACTIVE',actor_ref))
    after={'username':u['username'],'permission':code,'valid_from':vf,'valid_to':vt,'office_code':payload.get('office_code'),'country_code':payload.get('country_code'),'ref':ref}
    audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','USER',u['user_ref'],None,after,{'office_code':payload.get('office_code'),'country_code':payload.get('country_code')})
    return after

@router.get('/matrix')
def matrix(q:Optional[str]=Query(None),target_type:Optional[str]=Query(None),status:Optional[str]=Query(None)):
    c=connect()
    users=[dict(r) for r in c.execute('''SELECT u.user_ref,u.username,u.display_name,u.status,o.office_code,co.country_code
      FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id ORDER BY u.username''')]
    roles=[dict(r) for r in c.execute("SELECT role_code,name,status,sensitive FROM iam_roles WHERE role_code IN ('GL_ACCOUNTANT','GL_MANAGER','FINANCE','OPS','AGENT','AUDITOR','VIEWER','SUPER_ADMIN') ORDER BY role_code")]
    role_perms=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.sensitive,rp.effect FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE p.permission_code IN (%s) ORDER BY r.role_code,p.permission_code'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    direct=[dict(r) for r in c.execute('''SELECT t.temp_ref,u.username,p.permission_code,p.sensitive,t.valid_from,t.valid_to,t.status,t.reason,t.approved_by
      FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id JOIN iam_permissions p ON p.permission_code=t.permission_code
      WHERE t.permission_code IN (%s) ORDER BY t.id DESC'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    assignments=[dict(r) for r in c.execute('''SELECT u.username,r.role_code,o.office_code,co.country_code,ur.valid_from,ur.valid_to,ur.status,ur.assigned_by
      FROM iam_user_roles ur JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles r ON r.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      ORDER BY u.username,r.role_code''')]
    reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_ENTITLEMENT%' ORDER BY id DESC")]
    c.close()
    if q:
        needle=q.lower()
        users=[x for x in users if needle in json.dumps(x).lower()]
        roles=[x for x in roles if needle in json.dumps(x).lower()]
        role_perms=[x for x in role_perms if needle in json.dumps(x).lower()]
        direct=[x for x in direct if needle in json.dumps(x).lower()]
        assignments=[x for x in assignments if needle in json.dumps(x).lower()]
        reviews=[x for x in reviews if needle in json.dumps(x).lower()]
    if status:
        direct=[x for x in direct if x['status']==status]
        assignments=[x for x in assignments if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-030','users':users,'roles':roles,'role_permissions':role_perms,'direct_permissions':direct,'assignments':assignments,'access_reviews':reviews,'finance_permissions':sorted(FINANCE_CODES),'sensitive_permissions':sorted(SENSITIVE)}

@router.post('/grants')
def request_grant(b:GrantRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); finance_permission(c,b.permission_code); target(c,b)
    payload=b.model_dump(); sensitive=b.permission_code in SENSITIVE
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ENTITLEMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,s['user_id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_grant(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','grant':out,'four_eyes':False}

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Review not found')
    if r['status']!='PENDING': c.close(); raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
    scope=json.loads(r['scope']); maker=scope.get('maker')
    if maker==s['user_ref']: c.close(); raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
    if b.decision=='REJECT':
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
        c.close(); return {'status':'REJECTED','review_ref':review_ref}
    out=apply_grant(c,scope['request'],s['user_ref'])
    c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_APPROVE','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED','grant':out})
    c.close(); return {'status':'APPROVED','review_ref':review_ref,'grant':out}

@router.post('/direct/{temp_ref}/revoke')
def revoke_direct(temp_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id WHERE t.temp_ref=?''',(temp_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Grant not found')
    before=dict(r); c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE id=?",(r['id'],))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REVOKE','USER',r['user_ref'],before,{'status':'REVOKED','permission_code':r['permission_code']})
    c.close(); return {'status':'REVOKED','temp_ref':temp_ref}

@router.post('/roles/{role_code}/permissions/{permission_code}/revoke')
def revoke_role(role_code:str,permission_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); p=finance_permission(c,permission_code)
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(role_code,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Role not found')
    rp=c.execute('SELECT * FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
    before=dict(rp) if rp else None
    if rp:c.execute("UPDATE iam_role_permissions SET effect='DENY' WHERE id=?",(rp['id'],))
    else:c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'DENY')",(r['id'],p['id']))
    audit(c,s['user_ref'],'FINANCE_ROLE_PERMISSION_REVOKE','ROLE',role_code,before,{'permission':permission_code,'effect':'DENY'})
    c.close(); return {'status':'REVOKED','role':role_code,'permission':permission_code}

@router.get('/audit')
def entitlement_audit(limit:int=Query(100,ge=1,le=500)):
    c=connect(); rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT ?",(limit,))]; c.close(); return rows
)
    office_code:Optional[str]=None
    country_code:Optional[str]=None
    valid_from:Optional[str]=None
    valid_to:Optional[str]=None
    reason:str=Field(min_length=3,max_length=300)

class Decision(BaseModel):
    decision:str=Field(pattern='^(APPROVE|REJECT)
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})

def finance_permission(c,code):
    if code not in FINANCE_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_ENTITLEMENT'})
    p=c.execute('SELECT * FROM iam_permissions WHERE permission_code=?',(code,)).fetchone()
    if not p: raise HTTPException(404,'Permission not found')
    return p

def target(c,b):
    if b.target_type=='USER':
        if not b.username: raise HTTPException(422,'username required')
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    if not b.role_code: raise HTTPException(422,'role_code required')
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r

def apply_grant(c,payload,actor_ref):
    code=payload['permission_code']; p=finance_permission(c,code)
    if payload['target_type']=='ROLE':
        r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(payload['role_code'],)).fetchone()
        before=c.execute('SELECT effect FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
        if before:
            c.execute("UPDATE iam_role_permissions SET effect='ALLOW' WHERE role_id=? AND permission_id=?",(r['id'],p['id']))
        else:
            c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(r['id'],p['id']))
        after={'role':r['role_code'],'permission':code,'effect':'ALLOW'}
        audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','ROLE',r['role_code'],dict(before) if before else None,after)
        return after
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to') or '2099-12-31T23:59:59+00:00'
    ref='FENT-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
                 VALUES(?,?,?,?,?,?,?,?)''',(ref,u['id'],code,vf,vt,payload['reason'],'ACTIVE',actor_ref))
    after={'username':u['username'],'permission':code,'valid_from':vf,'valid_to':vt,'office_code':payload.get('office_code'),'country_code':payload.get('country_code'),'ref':ref}
    audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','USER',u['user_ref'],None,after,{'office_code':payload.get('office_code'),'country_code':payload.get('country_code')})
    return after


def apply_role_assignment(c,payload,actor_ref):
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    r=c.execute("SELECT * FROM iam_roles WHERE role_code=? AND role_code IN ('GL_ACCOUNTANT','GL_MANAGER')",(payload['role_code'],)).fetchone()
    if not u or not r: raise HTTPException(404,'User or GL role not found')
    office=None; country=None
    if payload.get('office_code'):
        office=c.execute('SELECT * FROM iam_offices WHERE office_code=?',(payload['office_code'],)).fetchone()
        if not office: raise HTTPException(404,'Office not found')
    else:
        office=c.execute('SELECT * FROM iam_offices WHERE id=?',(u['home_office_id'],)).fetchone()
    if payload.get('country_code'):
        country=c.execute('SELECT * FROM iam_countries WHERE country_code=?',(payload['country_code'],)).fetchone()
        if not country: raise HTTPException(404,'Country not found')
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to')
    existing=c.execute('''SELECT ur.*,o.office_code,co.country_code FROM iam_user_roles ur
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      WHERE ur.user_id=? AND ur.role_id=? AND COALESCE(ur.office_id,0)=COALESCE(?,0)
      AND COALESCE(ur.country_id,0)=COALESCE(?,0) ORDER BY ur.id DESC LIMIT 1''',
      (u['id'],r['id'],office['id'] if office else None,country['id'] if country else None)).fetchone()
    before=dict(existing) if existing else None
    if existing:
        c.execute("UPDATE iam_user_roles SET valid_from=?,valid_to=?,status='ACTIVE',assigned_by=? WHERE id=?",(vf,vt,actor_ref,existing['id']))
        assignment_id=existing['id']
    else:
        cur=c.execute('''INSERT INTO iam_user_roles(user_id,role_id,office_id,country_id,organization_id,valid_from,valid_to,status,assigned_by)
          VALUES(?,?,?,?,?,?,?,?,?)''',(u['id'],r['id'],office['id'] if office else None,country['id'] if country else None,None,vf,vt,'ACTIVE',actor_ref))
        assignment_id=cur.lastrowid
    after={'assignment_id':assignment_id,'username':u['username'],'role_code':r['role_code'],'office_code':office['office_code'] if office else None,
      'country_code':country['country_code'] if country else None,'valid_from':vf,'valid_to':vt,'status':'ACTIVE'}
    audit(c,actor_ref,'FINANCE_ROLE_ASSIGN','USER',u['user_ref'],before,after)
    return after

@router.get('/matrix')
def matrix(q:Optional[str]=Query(None),target_type:Optional[str]=Query(None),status:Optional[str]=Query(None)):
    c=connect()
    users=[dict(r) for r in c.execute('''SELECT u.user_ref,u.username,u.display_name,u.status,o.office_code,co.country_code
      FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id ORDER BY u.username''')]
    roles=[dict(r) for r in c.execute("SELECT role_code,name,status,sensitive FROM iam_roles WHERE role_code IN ('GL_ACCOUNTANT','GL_MANAGER','FINANCE','OPS','AGENT','AUDITOR','VIEWER','SUPER_ADMIN') ORDER BY role_code")]
    role_perms=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.sensitive,rp.effect FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE p.permission_code IN (%s) ORDER BY r.role_code,p.permission_code'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    direct=[dict(r) for r in c.execute('''SELECT t.temp_ref,u.username,p.permission_code,p.sensitive,t.valid_from,t.valid_to,t.status,t.reason,t.approved_by
      FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id JOIN iam_permissions p ON p.permission_code=t.permission_code
      WHERE t.permission_code IN (%s) ORDER BY t.id DESC'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    assignments=[dict(r) for r in c.execute('''SELECT ur.id assignment_id,u.username,r.role_code,o.office_code,co.country_code,ur.valid_from,ur.valid_to,ur.status,ur.assigned_by
      FROM iam_user_roles ur JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles r ON r.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      ORDER BY u.username,r.role_code''')]
    reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_ENTITLEMENT%' ORDER BY id DESC")]
    c.close()
    if q:
        needle=q.lower()
        users=[x for x in users if needle in json.dumps(x).lower()]
        roles=[x for x in roles if needle in json.dumps(x).lower()]
        role_perms=[x for x in role_perms if needle in json.dumps(x).lower()]
        direct=[x for x in direct if needle in json.dumps(x).lower()]
        assignments=[x for x in assignments if needle in json.dumps(x).lower()]
        reviews=[x for x in reviews if needle in json.dumps(x).lower()]
    if status:
        direct=[x for x in direct if x['status']==status]
        assignments=[x for x in assignments if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-030','users':users,'roles':roles,'role_permissions':role_perms,'direct_permissions':direct,'assignments':assignments,'access_reviews':reviews,'finance_permissions':sorted(FINANCE_CODES),'sensitive_permissions':sorted(SENSITIVE)}

@router.post('/grants')
def request_grant(b:GrantRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); finance_permission(c,b.permission_code); target(c,b)
    payload=b.model_dump(); sensitive=b.permission_code in SENSITIVE
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ENTITLEMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,s['user_id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_grant(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','grant':out,'four_eyes':False}


@router.post('/role-assignments')
def request_role_assignment(b:RoleAssignmentRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
    r=c.execute("SELECT * FROM iam_roles WHERE role_code=? AND role_code IN ('GL_ACCOUNTANT','GL_MANAGER')",(b.role_code,)).fetchone()
    if not u or not r: c.close(); raise HTTPException(404,'User or GL role not found')
    if b.valid_to and b.valid_from and b.valid_to<=b.valid_from: c.close(); raise HTTPException(422,{'code':'INVALID_EFFECTIVE_DATES'})
    payload=b.model_dump(); sensitive=bool(r['sensitive'])
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ROLE_ASSIGNMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,u['id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ROLE_ASSIGN_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_role_assignment(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','assignment':out,'four_eyes':False}

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Review not found')
    if r['status']!='PENDING': c.close(); raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
    scope=json.loads(r['scope']); maker=scope.get('maker')
    if maker==s['user_ref']: c.close(); raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
    if b.decision=='REJECT':
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
        c.close(); return {'status':'REJECTED','review_ref':review_ref}
    if scope.get('kind')=='FINANCE_ROLE_ASSIGNMENT':
        out=apply_role_assignment(c,scope['request'],s['user_ref'])
        result_key='assignment'
        approve_action='FINANCE_ROLE_ASSIGN_APPROVE'
    else:
        out=apply_grant(c,scope['request'],s['user_ref'])
        result_key='grant'
        approve_action='FINANCE_ENTITLEMENT_APPROVE'
    c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
    audit(c,s['user_ref'],approve_action,'ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED',result_key:out})
    c.close(); return {'status':'APPROVED','review_ref':review_ref,result_key:out}

@router.post('/direct/{temp_ref}/revoke')
def revoke_direct(temp_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id WHERE t.temp_ref=?''',(temp_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Grant not found')
    before=dict(r); c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE id=?",(r['id'],))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REVOKE','USER',r['user_ref'],before,{'status':'REVOKED','permission_code':r['permission_code']})
    c.close(); return {'status':'REVOKED','temp_ref':temp_ref}

@router.post('/roles/{role_code}/permissions/{permission_code}/revoke')
def revoke_role(role_code:str,permission_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); p=finance_permission(c,permission_code)
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(role_code,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Role not found')
    rp=c.execute('SELECT * FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
    before=dict(rp) if rp else None
    if rp:c.execute("UPDATE iam_role_permissions SET effect='DENY' WHERE id=?",(rp['id'],))
    else:c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'DENY')",(r['id'],p['id']))
    audit(c,s['user_ref'],'FINANCE_ROLE_PERMISSION_REVOKE','ROLE',role_code,before,{'permission':permission_code,'effect':'DENY'})
    c.close(); return {'status':'REVOKED','role':role_code,'permission':permission_code}

@router.post('/role-assignments/{assignment_id}/revoke')
def revoke_role_assignment(assignment_id:int,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT ur.*,u.user_ref,u.username,ro.role_code,o.office_code,co.country_code FROM iam_user_roles ur
      JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles ro ON ro.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id WHERE ur.id=?''',(assignment_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Assignment not found')
    if r['role_code'] not in ('GL_ACCOUNTANT','GL_MANAGER'): c.close(); raise HTTPException(422,'Not a GL role assignment')
    before=dict(r); c.execute("UPDATE iam_user_roles SET status='INACTIVE' WHERE id=?",(assignment_id,))
    audit(c,s['user_ref'],'FINANCE_ROLE_ASSIGN_REVOKE','USER',r['user_ref'],before,{'assignment_id':assignment_id,'status':'INACTIVE','role_code':r['role_code']})
    c.close(); return {'status':'REVOKED','assignment_id':assignment_id}

@router.get('/audit')
def entitlement_audit(limit:int=Query(100,ge=1,le=500)):
    c=connect(); rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT ?",(limit,))]; c.close(); return rows
)
    office_code:Optional[str]=None
    country_code:Optional[str]=None
    valid_from:Optional[str]=None
    valid_to:Optional[str]=None
    reason:str=Field(min_length=3,max_length=300)

class Decision(BaseModel):
    decision:str=Field(pattern='^(APPROVE|REJECT)

def require_admin(c,s):
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})

def finance_permission(c,code):
    if code not in FINANCE_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_ENTITLEMENT'})
    p=c.execute('SELECT * FROM iam_permissions WHERE permission_code=?',(code,)).fetchone()
    if not p: raise HTTPException(404,'Permission not found')
    return p

def target(c,b):
    if b.target_type=='USER':
        if not b.username: raise HTTPException(422,'username required')
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    if not b.role_code: raise HTTPException(422,'role_code required')
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r

def apply_grant(c,payload,actor_ref):
    code=payload['permission_code']; p=finance_permission(c,code)
    if payload['target_type']=='ROLE':
        r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(payload['role_code'],)).fetchone()
        before=c.execute('SELECT effect FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
        if before:
            c.execute("UPDATE iam_role_permissions SET effect='ALLOW' WHERE role_id=? AND permission_id=?",(r['id'],p['id']))
        else:
            c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(r['id'],p['id']))
        after={'role':r['role_code'],'permission':code,'effect':'ALLOW'}
        audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','ROLE',r['role_code'],dict(before) if before else None,after)
        return after
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to') or '2099-12-31T23:59:59+00:00'
    ref='FENT-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
                 VALUES(?,?,?,?,?,?,?,?)''',(ref,u['id'],code,vf,vt,payload['reason'],'ACTIVE',actor_ref))
    after={'username':u['username'],'permission':code,'valid_from':vf,'valid_to':vt,'office_code':payload.get('office_code'),'country_code':payload.get('country_code'),'ref':ref}
    audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','USER',u['user_ref'],None,after,{'office_code':payload.get('office_code'),'country_code':payload.get('country_code')})
    return after

@router.get('/matrix')
def matrix(q:Optional[str]=Query(None),target_type:Optional[str]=Query(None),status:Optional[str]=Query(None)):
    c=connect()
    users=[dict(r) for r in c.execute('''SELECT u.user_ref,u.username,u.display_name,u.status,o.office_code,co.country_code
      FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id ORDER BY u.username''')]
    roles=[dict(r) for r in c.execute("SELECT role_code,name,status,sensitive FROM iam_roles WHERE role_code IN ('GL_ACCOUNTANT','GL_MANAGER','FINANCE','OPS','AGENT','AUDITOR','VIEWER','SUPER_ADMIN') ORDER BY role_code")]
    role_perms=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.sensitive,rp.effect FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE p.permission_code IN (%s) ORDER BY r.role_code,p.permission_code'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    direct=[dict(r) for r in c.execute('''SELECT t.temp_ref,u.username,p.permission_code,p.sensitive,t.valid_from,t.valid_to,t.status,t.reason,t.approved_by
      FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id JOIN iam_permissions p ON p.permission_code=t.permission_code
      WHERE t.permission_code IN (%s) ORDER BY t.id DESC'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    assignments=[dict(r) for r in c.execute('''SELECT u.username,r.role_code,o.office_code,co.country_code,ur.valid_from,ur.valid_to,ur.status,ur.assigned_by
      FROM iam_user_roles ur JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles r ON r.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      ORDER BY u.username,r.role_code''')]
    reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_ENTITLEMENT%' ORDER BY id DESC")]
    c.close()
    if q:
        needle=q.lower()
        users=[x for x in users if needle in json.dumps(x).lower()]
        roles=[x for x in roles if needle in json.dumps(x).lower()]
        role_perms=[x for x in role_perms if needle in json.dumps(x).lower()]
        direct=[x for x in direct if needle in json.dumps(x).lower()]
        assignments=[x for x in assignments if needle in json.dumps(x).lower()]
        reviews=[x for x in reviews if needle in json.dumps(x).lower()]
    if status:
        direct=[x for x in direct if x['status']==status]
        assignments=[x for x in assignments if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-030','users':users,'roles':roles,'role_permissions':role_perms,'direct_permissions':direct,'assignments':assignments,'access_reviews':reviews,'finance_permissions':sorted(FINANCE_CODES),'sensitive_permissions':sorted(SENSITIVE)}

@router.post('/grants')
def request_grant(b:GrantRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); finance_permission(c,b.permission_code); target(c,b)
    payload=b.model_dump(); sensitive=b.permission_code in SENSITIVE
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ENTITLEMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,s['user_id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_grant(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','grant':out,'four_eyes':False}

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Review not found')
    if r['status']!='PENDING': c.close(); raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
    scope=json.loads(r['scope']); maker=scope.get('maker')
    if maker==s['user_ref']: c.close(); raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
    if b.decision=='REJECT':
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
        c.close(); return {'status':'REJECTED','review_ref':review_ref}
    out=apply_grant(c,scope['request'],s['user_ref'])
    c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_APPROVE','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED','grant':out})
    c.close(); return {'status':'APPROVED','review_ref':review_ref,'grant':out}

@router.post('/direct/{temp_ref}/revoke')
def revoke_direct(temp_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id WHERE t.temp_ref=?''',(temp_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Grant not found')
    before=dict(r); c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE id=?",(r['id'],))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REVOKE','USER',r['user_ref'],before,{'status':'REVOKED','permission_code':r['permission_code']})
    c.close(); return {'status':'REVOKED','temp_ref':temp_ref}

@router.post('/roles/{role_code}/permissions/{permission_code}/revoke')
def revoke_role(role_code:str,permission_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); p=finance_permission(c,permission_code)
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(role_code,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Role not found')
    rp=c.execute('SELECT * FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
    before=dict(rp) if rp else None
    if rp:c.execute("UPDATE iam_role_permissions SET effect='DENY' WHERE id=?",(rp['id'],))
    else:c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'DENY')",(r['id'],p['id']))
    audit(c,s['user_ref'],'FINANCE_ROLE_PERMISSION_REVOKE','ROLE',role_code,before,{'permission':permission_code,'effect':'DENY'})
    c.close(); return {'status':'REVOKED','role':role_code,'permission':permission_code}

@router.get('/audit')
def entitlement_audit(limit:int=Query(100,ge=1,le=500)):
    c=connect(); rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT ?",(limit,))]; c.close(); return rows
)
    comment:Optional[str]=None

def require_admin(c,s):
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})

def finance_permission(c,code):
    if code not in FINANCE_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_ENTITLEMENT'})
    p=c.execute('SELECT * FROM iam_permissions WHERE permission_code=?',(code,)).fetchone()
    if not p: raise HTTPException(404,'Permission not found')
    return p

def target(c,b):
    if b.target_type=='USER':
        if not b.username: raise HTTPException(422,'username required')
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    if not b.role_code: raise HTTPException(422,'role_code required')
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r

def apply_grant(c,payload,actor_ref):
    code=payload['permission_code']; p=finance_permission(c,code)
    if payload['target_type']=='ROLE':
        r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(payload['role_code'],)).fetchone()
        before=c.execute('SELECT effect FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
        if before:
            c.execute("UPDATE iam_role_permissions SET effect='ALLOW' WHERE role_id=? AND permission_id=?",(r['id'],p['id']))
        else:
            c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(r['id'],p['id']))
        after={'role':r['role_code'],'permission':code,'effect':'ALLOW'}
        audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','ROLE',r['role_code'],dict(before) if before else None,after)
        return after
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to') or '2099-12-31T23:59:59+00:00'
    ref='FENT-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
                 VALUES(?,?,?,?,?,?,?,?)''',(ref,u['id'],code,vf,vt,payload['reason'],'ACTIVE',actor_ref))
    after={'username':u['username'],'permission':code,'valid_from':vf,'valid_to':vt,'office_code':payload.get('office_code'),'country_code':payload.get('country_code'),'ref':ref}
    audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','USER',u['user_ref'],None,after,{'office_code':payload.get('office_code'),'country_code':payload.get('country_code')})
    return after

@router.get('/matrix')
def matrix(q:Optional[str]=Query(None),target_type:Optional[str]=Query(None),status:Optional[str]=Query(None)):
    c=connect()
    users=[dict(r) for r in c.execute('''SELECT u.user_ref,u.username,u.display_name,u.status,o.office_code,co.country_code
      FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id ORDER BY u.username''')]
    roles=[dict(r) for r in c.execute("SELECT role_code,name,status,sensitive FROM iam_roles WHERE role_code IN ('GL_ACCOUNTANT','GL_MANAGER','FINANCE','OPS','AGENT','AUDITOR','VIEWER','SUPER_ADMIN') ORDER BY role_code")]
    role_perms=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.sensitive,rp.effect FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE p.permission_code IN (%s) ORDER BY r.role_code,p.permission_code'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    direct=[dict(r) for r in c.execute('''SELECT t.temp_ref,u.username,p.permission_code,p.sensitive,t.valid_from,t.valid_to,t.status,t.reason,t.approved_by
      FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id JOIN iam_permissions p ON p.permission_code=t.permission_code
      WHERE t.permission_code IN (%s) ORDER BY t.id DESC'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    assignments=[dict(r) for r in c.execute('''SELECT u.username,r.role_code,o.office_code,co.country_code,ur.valid_from,ur.valid_to,ur.status,ur.assigned_by
      FROM iam_user_roles ur JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles r ON r.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      ORDER BY u.username,r.role_code''')]
    reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_ENTITLEMENT%' ORDER BY id DESC")]
    c.close()
    if q:
        needle=q.lower()
        users=[x for x in users if needle in json.dumps(x).lower()]
        roles=[x for x in roles if needle in json.dumps(x).lower()]
        role_perms=[x for x in role_perms if needle in json.dumps(x).lower()]
        direct=[x for x in direct if needle in json.dumps(x).lower()]
        assignments=[x for x in assignments if needle in json.dumps(x).lower()]
        reviews=[x for x in reviews if needle in json.dumps(x).lower()]
    if status:
        direct=[x for x in direct if x['status']==status]
        assignments=[x for x in assignments if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-030','users':users,'roles':roles,'role_permissions':role_perms,'direct_permissions':direct,'assignments':assignments,'access_reviews':reviews,'finance_permissions':sorted(FINANCE_CODES),'sensitive_permissions':sorted(SENSITIVE)}

@router.post('/grants')
def request_grant(b:GrantRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); finance_permission(c,b.permission_code); target(c,b)
    payload=b.model_dump(); sensitive=b.permission_code in SENSITIVE
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ENTITLEMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,s['user_id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_grant(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','grant':out,'four_eyes':False}

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Review not found')
    if r['status']!='PENDING': c.close(); raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
    scope=json.loads(r['scope']); maker=scope.get('maker')
    if maker==s['user_ref']: c.close(); raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
    if b.decision=='REJECT':
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
        c.close(); return {'status':'REJECTED','review_ref':review_ref}
    out=apply_grant(c,scope['request'],s['user_ref'])
    c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_APPROVE','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED','grant':out})
    c.close(); return {'status':'APPROVED','review_ref':review_ref,'grant':out}

@router.post('/direct/{temp_ref}/revoke')
def revoke_direct(temp_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id WHERE t.temp_ref=?''',(temp_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Grant not found')
    before=dict(r); c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE id=?",(r['id'],))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REVOKE','USER',r['user_ref'],before,{'status':'REVOKED','permission_code':r['permission_code']})
    c.close(); return {'status':'REVOKED','temp_ref':temp_ref}

@router.post('/roles/{role_code}/permissions/{permission_code}/revoke')
def revoke_role(role_code:str,permission_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); p=finance_permission(c,permission_code)
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(role_code,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Role not found')
    rp=c.execute('SELECT * FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
    before=dict(rp) if rp else None
    if rp:c.execute("UPDATE iam_role_permissions SET effect='DENY' WHERE id=?",(rp['id'],))
    else:c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'DENY')",(r['id'],p['id']))
    audit(c,s['user_ref'],'FINANCE_ROLE_PERMISSION_REVOKE','ROLE',role_code,before,{'permission':permission_code,'effect':'DENY'})
    c.close(); return {'status':'REVOKED','role':role_code,'permission':permission_code}

@router.get('/audit')
def entitlement_audit(limit:int=Query(100,ge=1,le=500)):
    c=connect(); rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT ?",(limit,))]; c.close(); return rows
)
    comment:Optional[str]=None

def require_admin(c,s):
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})

def finance_permission(c,code):
    if code not in FINANCE_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_ENTITLEMENT'})
    p=c.execute('SELECT * FROM iam_permissions WHERE permission_code=?',(code,)).fetchone()
    if not p: raise HTTPException(404,'Permission not found')
    return p

def target(c,b):
    if b.target_type=='USER':
        if not b.username: raise HTTPException(422,'username required')
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    if not b.role_code: raise HTTPException(422,'role_code required')
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r

def apply_grant(c,payload,actor_ref):
    code=payload['permission_code']; p=finance_permission(c,code)
    if payload['target_type']=='ROLE':
        r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(payload['role_code'],)).fetchone()
        before=c.execute('SELECT effect FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
        if before:
            c.execute("UPDATE iam_role_permissions SET effect='ALLOW' WHERE role_id=? AND permission_id=?",(r['id'],p['id']))
        else:
            c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(r['id'],p['id']))
        after={'role':r['role_code'],'permission':code,'effect':'ALLOW'}
        audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','ROLE',r['role_code'],dict(before) if before else None,after)
        return after
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to') or '2099-12-31T23:59:59+00:00'
    ref='FENT-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
                 VALUES(?,?,?,?,?,?,?,?)''',(ref,u['id'],code,vf,vt,payload['reason'],'ACTIVE',actor_ref))
    after={'username':u['username'],'permission':code,'valid_from':vf,'valid_to':vt,'office_code':payload.get('office_code'),'country_code':payload.get('country_code'),'ref':ref}
    audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','USER',u['user_ref'],None,after,{'office_code':payload.get('office_code'),'country_code':payload.get('country_code')})
    return after


def apply_role_assignment(c,payload,actor_ref):
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    r=c.execute("SELECT * FROM iam_roles WHERE role_code=? AND role_code IN ('GL_ACCOUNTANT','GL_MANAGER')",(payload['role_code'],)).fetchone()
    if not u or not r: raise HTTPException(404,'User or GL role not found')
    office=None; country=None
    if payload.get('office_code'):
        office=c.execute('SELECT * FROM iam_offices WHERE office_code=?',(payload['office_code'],)).fetchone()
        if not office: raise HTTPException(404,'Office not found')
    else:
        office=c.execute('SELECT * FROM iam_offices WHERE id=?',(u['home_office_id'],)).fetchone()
    if payload.get('country_code'):
        country=c.execute('SELECT * FROM iam_countries WHERE country_code=?',(payload['country_code'],)).fetchone()
        if not country: raise HTTPException(404,'Country not found')
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to')
    existing=c.execute('''SELECT ur.*,o.office_code,co.country_code FROM iam_user_roles ur
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      WHERE ur.user_id=? AND ur.role_id=? AND COALESCE(ur.office_id,0)=COALESCE(?,0)
      AND COALESCE(ur.country_id,0)=COALESCE(?,0) ORDER BY ur.id DESC LIMIT 1''',
      (u['id'],r['id'],office['id'] if office else None,country['id'] if country else None)).fetchone()
    before=dict(existing) if existing else None
    if existing:
        c.execute("UPDATE iam_user_roles SET valid_from=?,valid_to=?,status='ACTIVE',assigned_by=? WHERE id=?",(vf,vt,actor_ref,existing['id']))
        assignment_id=existing['id']
    else:
        cur=c.execute('''INSERT INTO iam_user_roles(user_id,role_id,office_id,country_id,organization_id,valid_from,valid_to,status,assigned_by)
          VALUES(?,?,?,?,?,?,?,?,?)''',(u['id'],r['id'],office['id'] if office else None,country['id'] if country else None,None,vf,vt,'ACTIVE',actor_ref))
        assignment_id=cur.lastrowid
    after={'assignment_id':assignment_id,'username':u['username'],'role_code':r['role_code'],'office_code':office['office_code'] if office else None,
      'country_code':country['country_code'] if country else None,'valid_from':vf,'valid_to':vt,'status':'ACTIVE'}
    audit(c,actor_ref,'FINANCE_ROLE_ASSIGN','USER',u['user_ref'],before,after)
    return after

@router.get('/matrix')
def matrix(q:Optional[str]=Query(None),target_type:Optional[str]=Query(None),status:Optional[str]=Query(None)):
    c=connect()
    users=[dict(r) for r in c.execute('''SELECT u.user_ref,u.username,u.display_name,u.status,o.office_code,co.country_code
      FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id ORDER BY u.username''')]
    roles=[dict(r) for r in c.execute("SELECT role_code,name,status,sensitive FROM iam_roles WHERE role_code IN ('GL_ACCOUNTANT','GL_MANAGER','FINANCE','OPS','AGENT','AUDITOR','VIEWER','SUPER_ADMIN') ORDER BY role_code")]
    role_perms=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.sensitive,rp.effect FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE p.permission_code IN (%s) ORDER BY r.role_code,p.permission_code'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    direct=[dict(r) for r in c.execute('''SELECT t.temp_ref,u.username,p.permission_code,p.sensitive,t.valid_from,t.valid_to,t.status,t.reason,t.approved_by
      FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id JOIN iam_permissions p ON p.permission_code=t.permission_code
      WHERE t.permission_code IN (%s) ORDER BY t.id DESC'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    assignments=[dict(r) for r in c.execute('''SELECT ur.id assignment_id,u.username,r.role_code,o.office_code,co.country_code,ur.valid_from,ur.valid_to,ur.status,ur.assigned_by
      FROM iam_user_roles ur JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles r ON r.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      ORDER BY u.username,r.role_code''')]
    reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_ENTITLEMENT%' ORDER BY id DESC")]
    c.close()
    if q:
        needle=q.lower()
        users=[x for x in users if needle in json.dumps(x).lower()]
        roles=[x for x in roles if needle in json.dumps(x).lower()]
        role_perms=[x for x in role_perms if needle in json.dumps(x).lower()]
        direct=[x for x in direct if needle in json.dumps(x).lower()]
        assignments=[x for x in assignments if needle in json.dumps(x).lower()]
        reviews=[x for x in reviews if needle in json.dumps(x).lower()]
    if status:
        direct=[x for x in direct if x['status']==status]
        assignments=[x for x in assignments if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-030','users':users,'roles':roles,'role_permissions':role_perms,'direct_permissions':direct,'assignments':assignments,'access_reviews':reviews,'finance_permissions':sorted(FINANCE_CODES),'sensitive_permissions':sorted(SENSITIVE)}

@router.post('/grants')
def request_grant(b:GrantRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); finance_permission(c,b.permission_code); target(c,b)
    payload=b.model_dump(); sensitive=b.permission_code in SENSITIVE
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ENTITLEMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,s['user_id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_grant(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','grant':out,'four_eyes':False}


@router.post('/role-assignments')
def request_role_assignment(b:RoleAssignmentRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
    r=c.execute("SELECT * FROM iam_roles WHERE role_code=? AND role_code IN ('GL_ACCOUNTANT','GL_MANAGER')",(b.role_code,)).fetchone()
    if not u or not r: c.close(); raise HTTPException(404,'User or GL role not found')
    if b.valid_to and b.valid_from and b.valid_to<=b.valid_from: c.close(); raise HTTPException(422,{'code':'INVALID_EFFECTIVE_DATES'})
    payload=b.model_dump(); sensitive=bool(r['sensitive'])
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ROLE_ASSIGNMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,u['id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ROLE_ASSIGN_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_role_assignment(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','assignment':out,'four_eyes':False}

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Review not found')
    if r['status']!='PENDING': c.close(); raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
    scope=json.loads(r['scope']); maker=scope.get('maker')
    if maker==s['user_ref']: c.close(); raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
    if b.decision=='REJECT':
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
        c.close(); return {'status':'REJECTED','review_ref':review_ref}
    if scope.get('kind')=='FINANCE_ROLE_ASSIGNMENT':
        out=apply_role_assignment(c,scope['request'],s['user_ref'])
        result_key='assignment'
        approve_action='FINANCE_ROLE_ASSIGN_APPROVE'
    else:
        out=apply_grant(c,scope['request'],s['user_ref'])
        result_key='grant'
        approve_action='FINANCE_ENTITLEMENT_APPROVE'
    c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
    audit(c,s['user_ref'],approve_action,'ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED',result_key:out})
    c.close(); return {'status':'APPROVED','review_ref':review_ref,result_key:out}

@router.post('/direct/{temp_ref}/revoke')
def revoke_direct(temp_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id WHERE t.temp_ref=?''',(temp_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Grant not found')
    before=dict(r); c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE id=?",(r['id'],))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REVOKE','USER',r['user_ref'],before,{'status':'REVOKED','permission_code':r['permission_code']})
    c.close(); return {'status':'REVOKED','temp_ref':temp_ref}

@router.post('/roles/{role_code}/permissions/{permission_code}/revoke')
def revoke_role(role_code:str,permission_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); p=finance_permission(c,permission_code)
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(role_code,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Role not found')
    rp=c.execute('SELECT * FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
    before=dict(rp) if rp else None
    if rp:c.execute("UPDATE iam_role_permissions SET effect='DENY' WHERE id=?",(rp['id'],))
    else:c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'DENY')",(r['id'],p['id']))
    audit(c,s['user_ref'],'FINANCE_ROLE_PERMISSION_REVOKE','ROLE',role_code,before,{'permission':permission_code,'effect':'DENY'})
    c.close(); return {'status':'REVOKED','role':role_code,'permission':permission_code}

@router.post('/role-assignments/{assignment_id}/revoke')
def revoke_role_assignment(assignment_id:int,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT ur.*,u.user_ref,u.username,ro.role_code,o.office_code,co.country_code FROM iam_user_roles ur
      JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles ro ON ro.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id WHERE ur.id=?''',(assignment_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Assignment not found')
    if r['role_code'] not in ('GL_ACCOUNTANT','GL_MANAGER'): c.close(); raise HTTPException(422,'Not a GL role assignment')
    before=dict(r); c.execute("UPDATE iam_user_roles SET status='INACTIVE' WHERE id=?",(assignment_id,))
    audit(c,s['user_ref'],'FINANCE_ROLE_ASSIGN_REVOKE','USER',r['user_ref'],before,{'assignment_id':assignment_id,'status':'INACTIVE','role_code':r['role_code']})
    c.close(); return {'status':'REVOKED','assignment_id':assignment_id}

@router.get('/audit')
def entitlement_audit(limit:int=Query(100,ge=1,le=500)):
    c=connect(); rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT ?",(limit,))]; c.close(); return rows
)
    office_code:Optional[str]=None
    country_code:Optional[str]=None
    valid_from:Optional[str]=None
    valid_to:Optional[str]=None
    reason:str=Field(min_length=3,max_length=300)

class Decision(BaseModel):
    decision:str=Field(pattern='^(APPROVE|REJECT)

def require_admin(c,s):
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})

def finance_permission(c,code):
    if code not in FINANCE_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_ENTITLEMENT'})
    p=c.execute('SELECT * FROM iam_permissions WHERE permission_code=?',(code,)).fetchone()
    if not p: raise HTTPException(404,'Permission not found')
    return p

def target(c,b):
    if b.target_type=='USER':
        if not b.username: raise HTTPException(422,'username required')
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    if not b.role_code: raise HTTPException(422,'role_code required')
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r

def apply_grant(c,payload,actor_ref):
    code=payload['permission_code']; p=finance_permission(c,code)
    if payload['target_type']=='ROLE':
        r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(payload['role_code'],)).fetchone()
        before=c.execute('SELECT effect FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
        if before:
            c.execute("UPDATE iam_role_permissions SET effect='ALLOW' WHERE role_id=? AND permission_id=?",(r['id'],p['id']))
        else:
            c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(r['id'],p['id']))
        after={'role':r['role_code'],'permission':code,'effect':'ALLOW'}
        audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','ROLE',r['role_code'],dict(before) if before else None,after)
        return after
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to') or '2099-12-31T23:59:59+00:00'
    ref='FENT-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
                 VALUES(?,?,?,?,?,?,?,?)''',(ref,u['id'],code,vf,vt,payload['reason'],'ACTIVE',actor_ref))
    after={'username':u['username'],'permission':code,'valid_from':vf,'valid_to':vt,'office_code':payload.get('office_code'),'country_code':payload.get('country_code'),'ref':ref}
    audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','USER',u['user_ref'],None,after,{'office_code':payload.get('office_code'),'country_code':payload.get('country_code')})
    return after

@router.get('/matrix')
def matrix(q:Optional[str]=Query(None),target_type:Optional[str]=Query(None),status:Optional[str]=Query(None)):
    c=connect()
    users=[dict(r) for r in c.execute('''SELECT u.user_ref,u.username,u.display_name,u.status,o.office_code,co.country_code
      FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id ORDER BY u.username''')]
    roles=[dict(r) for r in c.execute("SELECT role_code,name,status,sensitive FROM iam_roles WHERE role_code IN ('GL_ACCOUNTANT','GL_MANAGER','FINANCE','OPS','AGENT','AUDITOR','VIEWER','SUPER_ADMIN') ORDER BY role_code")]
    role_perms=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.sensitive,rp.effect FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE p.permission_code IN (%s) ORDER BY r.role_code,p.permission_code'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    direct=[dict(r) for r in c.execute('''SELECT t.temp_ref,u.username,p.permission_code,p.sensitive,t.valid_from,t.valid_to,t.status,t.reason,t.approved_by
      FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id JOIN iam_permissions p ON p.permission_code=t.permission_code
      WHERE t.permission_code IN (%s) ORDER BY t.id DESC'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    assignments=[dict(r) for r in c.execute('''SELECT u.username,r.role_code,o.office_code,co.country_code,ur.valid_from,ur.valid_to,ur.status,ur.assigned_by
      FROM iam_user_roles ur JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles r ON r.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      ORDER BY u.username,r.role_code''')]
    reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_ENTITLEMENT%' ORDER BY id DESC")]
    c.close()
    if q:
        needle=q.lower()
        users=[x for x in users if needle in json.dumps(x).lower()]
        roles=[x for x in roles if needle in json.dumps(x).lower()]
        role_perms=[x for x in role_perms if needle in json.dumps(x).lower()]
        direct=[x for x in direct if needle in json.dumps(x).lower()]
        assignments=[x for x in assignments if needle in json.dumps(x).lower()]
        reviews=[x for x in reviews if needle in json.dumps(x).lower()]
    if status:
        direct=[x for x in direct if x['status']==status]
        assignments=[x for x in assignments if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-030','users':users,'roles':roles,'role_permissions':role_perms,'direct_permissions':direct,'assignments':assignments,'access_reviews':reviews,'finance_permissions':sorted(FINANCE_CODES),'sensitive_permissions':sorted(SENSITIVE)}

@router.post('/grants')
def request_grant(b:GrantRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); finance_permission(c,b.permission_code); target(c,b)
    payload=b.model_dump(); sensitive=b.permission_code in SENSITIVE
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ENTITLEMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,s['user_id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_grant(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','grant':out,'four_eyes':False}

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Review not found')
    if r['status']!='PENDING': c.close(); raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
    scope=json.loads(r['scope']); maker=scope.get('maker')
    if maker==s['user_ref']: c.close(); raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
    if b.decision=='REJECT':
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
        c.close(); return {'status':'REJECTED','review_ref':review_ref}
    out=apply_grant(c,scope['request'],s['user_ref'])
    c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_APPROVE','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED','grant':out})
    c.close(); return {'status':'APPROVED','review_ref':review_ref,'grant':out}

@router.post('/direct/{temp_ref}/revoke')
def revoke_direct(temp_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id WHERE t.temp_ref=?''',(temp_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Grant not found')
    before=dict(r); c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE id=?",(r['id'],))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REVOKE','USER',r['user_ref'],before,{'status':'REVOKED','permission_code':r['permission_code']})
    c.close(); return {'status':'REVOKED','temp_ref':temp_ref}

@router.post('/roles/{role_code}/permissions/{permission_code}/revoke')
def revoke_role(role_code:str,permission_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); p=finance_permission(c,permission_code)
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(role_code,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Role not found')
    rp=c.execute('SELECT * FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
    before=dict(rp) if rp else None
    if rp:c.execute("UPDATE iam_role_permissions SET effect='DENY' WHERE id=?",(rp['id'],))
    else:c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'DENY')",(r['id'],p['id']))
    audit(c,s['user_ref'],'FINANCE_ROLE_PERMISSION_REVOKE','ROLE',role_code,before,{'permission':permission_code,'effect':'DENY'})
    c.close(); return {'status':'REVOKED','role':role_code,'permission':permission_code}

@router.get('/audit')
def entitlement_audit(limit:int=Query(100,ge=1,le=500)):
    c=connect(); rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT ?",(limit,))]; c.close(); return rows
)
    comment:Optional[str]=None

def require_admin(c,s):
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})

def finance_permission(c,code):
    if code not in FINANCE_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_ENTITLEMENT'})
    p=c.execute('SELECT * FROM iam_permissions WHERE permission_code=?',(code,)).fetchone()
    if not p: raise HTTPException(404,'Permission not found')
    return p

def target(c,b):
    if b.target_type=='USER':
        if not b.username: raise HTTPException(422,'username required')
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    if not b.role_code: raise HTTPException(422,'role_code required')
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r

def apply_grant(c,payload,actor_ref):
    code=payload['permission_code']; p=finance_permission(c,code)
    if payload['target_type']=='ROLE':
        r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(payload['role_code'],)).fetchone()
        before=c.execute('SELECT effect FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
        if before:
            c.execute("UPDATE iam_role_permissions SET effect='ALLOW' WHERE role_id=? AND permission_id=?",(r['id'],p['id']))
        else:
            c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",(r['id'],p['id']))
        after={'role':r['role_code'],'permission':code,'effect':'ALLOW'}
        audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','ROLE',r['role_code'],dict(before) if before else None,after)
        return after
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(payload['username'],)).fetchone()
    vf=payload.get('valid_from') or now(); vt=payload.get('valid_to') or '2099-12-31T23:59:59+00:00'
    ref='FENT-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
                 VALUES(?,?,?,?,?,?,?,?)''',(ref,u['id'],code,vf,vt,payload['reason'],'ACTIVE',actor_ref))
    after={'username':u['username'],'permission':code,'valid_from':vf,'valid_to':vt,'office_code':payload.get('office_code'),'country_code':payload.get('country_code'),'ref':ref}
    audit(c,actor_ref,'FINANCE_ENTITLEMENT_GRANT','USER',u['user_ref'],None,after,{'office_code':payload.get('office_code'),'country_code':payload.get('country_code')})
    return after

@router.get('/matrix')
def matrix(q:Optional[str]=Query(None),target_type:Optional[str]=Query(None),status:Optional[str]=Query(None)):
    c=connect()
    users=[dict(r) for r in c.execute('''SELECT u.user_ref,u.username,u.display_name,u.status,o.office_code,co.country_code
      FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id ORDER BY u.username''')]
    roles=[dict(r) for r in c.execute("SELECT role_code,name,status,sensitive FROM iam_roles WHERE role_code IN ('GL_ACCOUNTANT','GL_MANAGER','FINANCE','OPS','AGENT','AUDITOR','VIEWER','SUPER_ADMIN') ORDER BY role_code")]
    role_perms=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.sensitive,rp.effect FROM iam_role_permissions rp
      JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
      WHERE p.permission_code IN (%s) ORDER BY r.role_code,p.permission_code'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    direct=[dict(r) for r in c.execute('''SELECT t.temp_ref,u.username,p.permission_code,p.sensitive,t.valid_from,t.valid_to,t.status,t.reason,t.approved_by
      FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id JOIN iam_permissions p ON p.permission_code=t.permission_code
      WHERE t.permission_code IN (%s) ORDER BY t.id DESC'''%(','.join('?'*len(FINANCE_CODES))),tuple(sorted(FINANCE_CODES)))]
    assignments=[dict(r) for r in c.execute('''SELECT u.username,r.role_code,o.office_code,co.country_code,ur.valid_from,ur.valid_to,ur.status,ur.assigned_by
      FROM iam_user_roles ur JOIN iam_users u ON u.id=ur.user_id JOIN iam_roles r ON r.id=ur.role_id
      LEFT JOIN iam_offices o ON o.id=ur.office_id LEFT JOIN iam_countries co ON co.id=ur.country_id
      ORDER BY u.username,r.role_code''')]
    reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_ENTITLEMENT%' ORDER BY id DESC")]
    c.close()
    if q:
        needle=q.lower()
        users=[x for x in users if needle in json.dumps(x).lower()]
        roles=[x for x in roles if needle in json.dumps(x).lower()]
        role_perms=[x for x in role_perms if needle in json.dumps(x).lower()]
        direct=[x for x in direct if needle in json.dumps(x).lower()]
        assignments=[x for x in assignments if needle in json.dumps(x).lower()]
        reviews=[x for x in reviews if needle in json.dumps(x).lower()]
    if status:
        direct=[x for x in direct if x['status']==status]
        assignments=[x for x in assignments if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-030','users':users,'roles':roles,'role_permissions':role_perms,'direct_permissions':direct,'assignments':assignments,'access_reviews':reviews,'finance_permissions':sorted(FINANCE_CODES),'sensitive_permissions':sorted(SENSITIVE)}

@router.post('/grants')
def request_grant(b:GrantRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); finance_permission(c,b.permission_code); target(c,b)
    payload=b.model_dump(); sensitive=b.permission_code in SENSITIVE
    if sensitive:
        ref='REV-'+uuid.uuid4().hex[:10].upper()
        scope=json.dumps({'kind':'FINANCE_ENTITLEMENT','request':payload,'maker':s['user_ref']},sort_keys=True)
        due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
        c.execute('INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',
                  (ref,s['user_id'],scope,'PENDING','INDEPENDENT_CHECKER',due))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':payload})
        c.close(); return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    out=apply_grant(c,payload,s['user_ref']); c.close(); return {'status':'ACTIVE','grant':out,'four_eyes':False}

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Review not found')
    if r['status']!='PENDING': c.close(); raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
    scope=json.loads(r['scope']); maker=scope.get('maker')
    if maker==s['user_ref']: c.close(); raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
    if b.decision=='REJECT':
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
        c.close(); return {'status':'REJECTED','review_ref':review_ref}
    out=apply_grant(c,scope['request'],s['user_ref'])
    c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",(now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_APPROVE','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED','grant':out})
    c.close(); return {'status':'APPROVED','review_ref':review_ref,'grant':out}

@router.post('/direct/{temp_ref}/revoke')
def revoke_direct(temp_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s)
    r=c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id WHERE t.temp_ref=?''',(temp_ref,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Grant not found')
    before=dict(r); c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE id=?",(r['id'],))
    audit(c,s['user_ref'],'FINANCE_ENTITLEMENT_REVOKE','USER',r['user_ref'],before,{'status':'REVOKED','permission_code':r['permission_code']})
    c.close(); return {'status':'REVOKED','temp_ref':temp_ref}

@router.post('/roles/{role_code}/permissions/{permission_code}/revoke')
def revoke_role(role_code:str,permission_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session); require_admin(c,s); p=finance_permission(c,permission_code)
    r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(role_code,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Role not found')
    rp=c.execute('SELECT * FROM iam_role_permissions WHERE role_id=? AND permission_id=?',(r['id'],p['id'])).fetchone()
    before=dict(rp) if rp else None
    if rp:c.execute("UPDATE iam_role_permissions SET effect='DENY' WHERE id=?",(rp['id'],))
    else:c.execute("INSERT INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'DENY')",(r['id'],p['id']))
    audit(c,s['user_ref'],'FINANCE_ROLE_PERMISSION_REVOKE','ROLE',role_code,before,{'permission':permission_code,'effect':'DENY'})
    c.close(); return {'status':'REVOKED','role':role_code,'permission':permission_code}

@router.get('/audit')
def entitlement_audit(limit:int=Query(100,ge=1,le=500)):
    c=connect(); rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT ?",(limit,))]; c.close(); return rows
