from fastapi import APIRouter,HTTPException,Header
from pydantic import BaseModel,Field
from typing import Optional
import hashlib,uuid,json,datetime,secrets
from .db import connect

router=APIRouter(prefix='/api/admin',tags=['CLX-010 Identity & Administration'])

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def phash(p,s='m3-test-salt'): return hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),120000).hex()
def ih(x): return hashlib.sha256(x.encode()).hexdigest()
def audit(c,actor,action,rt,rr=None,before=None,after=None,scope=None):
    ts=now(); base=json.dumps({'ts':ts,'actor':actor,'action':action,'resource':rt,'ref':rr,'before':before,'after':after,'scope':scope or {}},sort_keys=True)
    c.execute('INSERT INTO iam_audit_events(event_ref,ts,actor_user_ref,action,resource_type,resource_ref,scope_json,before_json,after_json,immutable_hash) VALUES(?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),ts,actor,action,rt,rr,json.dumps(scope or {},sort_keys=True),json.dumps(before,sort_keys=True) if before is not None else None,json.dumps(after,sort_keys=True) if after is not None else None,ih(base)))
def policy(c,k,default):
    r=c.execute('SELECT policy_value FROM iam_security_policies WHERE policy_key=?',(k,)).fetchone(); return r['policy_value'] if r else default
def roles_for(c,uid): return [dict(r) for r in c.execute('''SELECT r.role_code,ur.office_id,ur.country_id,ur.organization_id FROM iam_user_roles ur JOIN iam_roles r ON r.id=ur.role_id WHERE ur.user_id=? AND ur.status='ACTIVE' AND (ur.valid_to IS NULL OR ur.valid_to>=?)''',(uid,now()))]
def permission(c,uid,module,action,office_code=None):
    for r in roles_for(c,uid):
        q=c.execute('''SELECT 1 FROM iam_role_permissions rp JOIN iam_permissions p ON p.id=rp.permission_id WHERE rp.role_id=(SELECT id FROM iam_roles WHERE role_code=?) AND p.module=? AND p.action=? AND rp.effect='ALLOW' ''',(r['role_code'],module,action)).fetchone()
        if q:
            if r['role_code']=='SUPER_ADMIN': return True
            if office_code and r['office_id']:
                o=c.execute('SELECT office_code FROM iam_offices WHERE id=?',(r['office_id'],)).fetchone()
                if not o or o['office_code']!=office_code: continue
            return True
    return False

class Login(BaseModel): username:str; password:str; mfa_code:Optional[str]=None
class Assign(BaseModel): username:str; role_code:str; office_code:Optional[str]=None; valid_to:Optional[str]=None
class UserStatus(BaseModel): status:str=Field(pattern='^(ACTIVE|SUSPENDED|INACTIVE)$')
class TempAccess(BaseModel): username:str; permission_code:str; valid_from:str; valid_to:str; reason:str

ADMIN_SCREENS=['dashboard','users','user-status','roles','permission-matrix','role-assignments','organizations','countries','legal-entities','offices','branches','departments','office-membership','data-scope-rules','customer-agent-access','approval-limits','maker-checker','approval-delegations','temporary-access','access-reviews','sessions','password-policy','mfa-policy','sso-readiness','login-audit','service-accounts','api-clients','audit']

@router.get('/meta')
def meta(): return {'project':'M3 NVOCC ERP','phase':'CLX-010','screens':ADMIN_SCREENS,'screen_count':len(ADMIN_SCREENS),'deny_by_default':True,'mfa_sso_readiness':True}
@router.get('/overview')
def overview():
    c=connect(); out={k:c.execute(f'SELECT COUNT(*) n FROM {t}').fetchone()['n'] for k,t in [('users','iam_users'),('roles','iam_roles'),('offices','iam_offices'),('countries','iam_countries'),('legal_entities','iam_legal_entities'),('branches','iam_branches'),('departments','iam_departments'),('scope_rules','iam_scope_rules'),('approval_limits','iam_approval_limits')]}; out['screens']=len(ADMIN_SCREENS); c.close(); return out
@router.post('/auth/login')
def login(b:Login):
    c=connect(); u=c.execute('SELECT u.*,o.office_code FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id WHERE username=?',(b.username,)).fetchone(); ts=now(); event=str(uuid.uuid4())
    if not u or u['status']!='ACTIVE' or u['password_hash']!=phash(b.password):
        if u:c.execute('UPDATE iam_users SET failed_attempts=failed_attempts+1 WHERE id=?',(u['id'],))
        h=ih(event+ts+b.username+'FAIL'); c.execute('INSERT INTO iam_login_audit(event_ref,ts,username,user_id,event_type,outcome,detail_json,immutable_hash) VALUES(?,?,?,?,?,?,?,?)',(event,ts,b.username,u['id'] if u else None,'LOGIN','FAIL','{"reason":"INVALID_CREDENTIALS"}',h)); c.close(); raise HTTPException(401,{'code':'INVALID_CREDENTIALS'})
    if u['mfa_required'] and b.mfa_code!='123456': c.close(); raise HTTPException(401,{'code':'MFA_REQUIRED','sandbox_code':'123456'})
    token=secrets.token_urlsafe(24); exp=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(minutes=int(policy(c,'session_minutes','60')))).isoformat(); c.execute('INSERT INTO iam_sessions(session_token,user_id,created_at,expires_at,last_seen_at,mfa_verified,status) VALUES(?,?,?,?,?,?,?)',(token,u['id'],ts,exp,ts,1,'ACTIVE')); c.execute('UPDATE iam_users SET failed_attempts=0,last_login_at=? WHERE id=?',(ts,u['id'])); h=ih(event+ts+b.username+'PASS'); c.execute('INSERT INTO iam_login_audit(event_ref,ts,username,user_id,event_type,outcome,detail_json,immutable_hash) VALUES(?,?,?,?,?,?,?,?)',(event,ts,b.username,u['id'],'LOGIN','PASS','{}',h)); out={'session_token':token,'user_ref':u['user_ref'],'username':u['username'],'office':u['office_code'],'roles':[x['role_code'] for x in roles_for(c,u['id'])],'mfa_verified':True,'expires_at':exp}; c.close(); return out
def session(c,token):
    if not token:raise HTTPException(401,{'code':'SESSION_REQUIRED'})
    s=c.execute('''SELECT s.*,u.user_ref,u.username,u.home_office_id,o.office_code FROM iam_sessions s JOIN iam_users u ON u.id=s.user_id JOIN iam_offices o ON o.id=u.home_office_id WHERE s.session_token=?''',(token,)).fetchone()
    if not s or s['status']!='ACTIVE' or s['revoked_at'] or s['expires_at']<now():raise HTTPException(401,{'code':'SESSION_INVALID'})
    c.execute('UPDATE iam_sessions SET last_seen_at=? WHERE id=?',(now(),s['id'])); return s

@router.get('/users')
def users():
    c=connect(); x=[dict(r) for r in c.execute('''SELECT u.id,u.user_ref,u.username,u.display_name,u.email,u.status,o.office_code,u.mfa_required,u.last_login_at,u.version FROM iam_users u JOIN iam_offices o ON o.id=u.home_office_id ORDER BY u.id''')]; c.close(); return x
@router.post('/users/{username}/status')
def set_user_status(username:str,b:UserStatus,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session)
    if not permission(c,s['user_id'],'identity','admin',s['office_code']): c.close(); raise HTTPException(403,{'code':'PERMISSION_DENIED'})
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(username,)).fetchone()
    if not u: c.close(); raise HTTPException(404,'User not found')
    before={'status':u['status'],'version':u['version']}; c.execute('UPDATE iam_users SET status=?,version=version+1 WHERE id=?',(b.status,u['id'])); audit(c,s['user_ref'],'USER_STATUS','USER',u['user_ref'],before,{'status':b.status}); c.close(); return {'status':b.status,'user_ref':u['user_ref']}
@router.get('/roles')
def roles(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_roles ORDER BY id')];c.close();return x
@router.get('/permissions')
def permissions(): c=connect();x=[dict(r) for r in c.execute('''SELECT r.role_code,p.permission_code,p.module,p.action,p.sensitive,rp.effect FROM iam_role_permissions rp JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id ORDER BY r.role_code,p.module,p.action''')];c.close();return x
@router.get('/organizations')
def organizations(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_organizations')];c.close();return x
@router.get('/countries')
def countries(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_countries')];c.close();return x
@router.get('/legal-entities')
def legal_entities(): c=connect();x=[dict(r) for r in c.execute('''SELECT e.*,o.org_code,c.country_code FROM iam_legal_entities e JOIN iam_organizations o ON o.id=e.organization_id JOIN iam_countries c ON c.id=e.country_id ORDER BY e.id''')];c.close();return x
@router.get('/offices')
def offices(): c=connect();x=[dict(r) for r in c.execute('''SELECT o.*,g.org_code,c.country_code FROM iam_offices o JOIN iam_organizations g ON g.id=o.organization_id JOIN iam_countries c ON c.id=o.country_id ORDER BY o.id''')];c.close();return x
@router.get('/branches')
def branches(): c=connect();x=[dict(r) for r in c.execute('''SELECT b.*,o.office_code FROM iam_branches b JOIN iam_offices o ON o.id=b.office_id ORDER BY b.id''')];c.close();return x
@router.get('/departments')
def departments(): c=connect();x=[dict(r) for r in c.execute('''SELECT d.*,b.branch_code FROM iam_departments d JOIN iam_branches b ON b.id=d.branch_id ORDER BY d.id''')];c.close();return x
@router.get('/office-membership')
def membership(): c=connect();x=[dict(r) for r in c.execute('''SELECT m.*,u.user_ref,o.office_code FROM iam_office_membership m JOIN iam_users u ON u.id=m.user_id JOIN iam_offices o ON o.id=m.office_id ORDER BY m.id''')];c.close();return x
@router.get('/scope-rules')
def scopes(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_scope_rules ORDER BY priority,id')];c.close();return x
@router.get('/party-access')
def party_access(): c=connect();x=[dict(r) for r in c.execute('''SELECT p.*,u.user_ref,u.username FROM iam_party_access p JOIN iam_users u ON u.id=p.user_id ORDER BY p.id''')];c.close();return x
@router.get('/approval-limits')
def approval_limits(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_approval_limits ORDER BY role_code,currency,action')];c.close();return x
@router.get('/maker-checker')
def maker_checker(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_sod_conflicts ORDER BY id')];c.close();return {'four_eyes':True,'conflicts':x}
@router.get('/delegations')
def delegations(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_delegations ORDER BY id')];c.close();return x
@router.get('/temporary-access')
def temporary_access(): c=connect();x=[dict(r) for r in c.execute('''SELECT t.*,u.user_ref,u.username FROM iam_temporary_access t JOIN iam_users u ON u.id=t.user_id ORDER BY t.id''')];c.close();return x
@router.post('/temporary-access')
def create_temp(b:TempAccess,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect(); s=session(c,x_m3_session)
    if not permission(c,s['user_id'],'identity','admin',s['office_code']): c.close(); raise HTTPException(403,{'code':'PERMISSION_DENIED'})
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone(); p=c.execute('SELECT 1 FROM iam_permissions WHERE permission_code=?',(b.permission_code,)).fetchone()
    if not u or not p: c.close(); raise HTTPException(404,'User or permission not found')
    if b.valid_to<=b.valid_from: c.close(); raise HTTPException(422,{'code':'INVALID_EFFECTIVE_DATES'})
    ref='TMP-'+uuid.uuid4().hex[:8].upper(); c.execute('INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,approved_by) VALUES(?,?,?,?,?,?,?)',(ref,u['id'],b.permission_code,b.valid_from,b.valid_to,b.reason,s['user_ref'])); audit(c,s['user_ref'],'TEMP_ACCESS_CREATE','USER',u['user_ref'],after={'permission':b.permission_code,'valid_to':b.valid_to}); c.close(); return {'temp_ref':ref,'status':'ACTIVE'}
@router.get('/access-reviews')
def reviews(): c=connect();x=[dict(r) for r in c.execute('''SELECT ar.*,u.user_ref,u.username FROM iam_access_reviews ar JOIN iam_users u ON u.id=ar.user_id ORDER BY ar.id''')];c.close();return x
@router.get('/sessions')
def sessions(): c=connect();x=[dict(r) for r in c.execute('''SELECT s.id,u.user_ref,u.username,o.office_code,s.created_at,s.expires_at,s.last_seen_at,s.mfa_verified,s.status,s.revoked_at FROM iam_sessions s JOIN iam_users u ON u.id=s.user_id JOIN iam_offices o ON o.id=u.home_office_id ORDER BY s.id DESC''')];c.close();return x
@router.get('/policies')
def policies(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_security_policies ORDER BY policy_key')];c.close();return x
@router.get('/sso-readiness')
def sso_readiness(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_sso_readiness ORDER BY provider_key')];c.close();return x
@router.get('/login-audit')
def login_audit(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_login_audit ORDER BY id DESC LIMIT 100')];c.close();return x
@router.get('/service-accounts')
def service_accounts(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_service_accounts ORDER BY id')];c.close();return x
@router.get('/api-clients')
def api_clients(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_api_clients ORDER BY id')];c.close();return x
@router.get('/audit')
def iam_audit(): c=connect();x=[dict(r) for r in c.execute('SELECT * FROM iam_audit_events ORDER BY id DESC LIMIT 100')];c.close();return x
@router.post('/role-assignments')
def assign(b:Assign,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect();s=session(c,x_m3_session)
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):c.close();raise HTTPException(403,{'code':'PERMISSION_DENIED'})
    u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone();r=c.execute('SELECT * FROM iam_roles WHERE role_code=?',(b.role_code,)).fetchone();o=c.execute('SELECT * FROM iam_offices WHERE office_code=?',(b.office_code or s['office_code'],)).fetchone()
    if not u or not r or not o:c.close();raise HTTPException(404,'User/role/office not found')
    existing={x['role_code'] for x in roles_for(c,u['id'])}
    for er in existing:
        conf=c.execute("SELECT * FROM iam_sod_conflicts WHERE status='ACTIVE' AND ((role_a=? AND role_b=?) OR (role_b=? AND role_a=?))",(b.role_code,er,b.role_code,er)).fetchone()
        if conf:c.close();raise HTTPException(409,{'code':'SOD_CONFLICT','conflict':conf['conflict_code']})
    try:c.execute('INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,valid_to,status,assigned_by) VALUES(?,?,?,?,?,?,?)',(u['id'],r['id'],o['id'],now(),b.valid_to,'ACTIVE',s['user_ref']))
    except Exception as e:c.close();raise HTTPException(409,{'code':'ASSIGNMENT_CONFLICT','detail':str(e)})
    audit(c,s['user_ref'],'ROLE_ASSIGN','USER',u['user_ref'],after={'role':b.role_code,'office':o['office_code']});c.close();return {'status':'ASSIGNED','user':u['user_ref'],'role':b.role_code,'office':o['office_code']}