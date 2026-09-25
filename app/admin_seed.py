import hashlib,datetime,json
from pathlib import Path
from .db import connect

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def phash(p,s='m3-test-salt'): return hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),120000).hex()
def run():
    c=connect(); c.executescript(Path(__file__).with_name('schema.sql').read_text())
    orgs=[('M3-GLOBAL','M3 Global NVOCC'),('M3-EU','M3 Europe')]
    for code,name in orgs:c.execute('INSERT OR IGNORE INTO iam_organizations(org_code,name) VALUES(?,?)',(code,name))
    for code,name in [('NL','Netherlands'),('AE','United Arab Emirates'),('PK','Pakistan'),('SG','Singapore'),('CN','China'),('GB','United Kingdom'),('US','United States'),('DE','Germany'),('BE','Belgium')]:c.execute('INSERT OR IGNORE INTO iam_countries(country_code,name) VALUES(?,?)',(code,name))
    oq={r['org_code']:r['id'] for r in c.execute('SELECT * FROM iam_organizations')}; cq={r['country_code']:r['id'] for r in c.execute('SELECT * FROM iam_countries')}
    for code,name,org,co,tz in [('RTM','Rotterdam Office','M3-EU','NL','Europe/Amsterdam'),('DXB','Dubai Office','M3-GLOBAL','AE','Asia/Dubai'),('KHI','Karachi Office','M3-GLOBAL','PK','Asia/Karachi'),('SIN','Singapore Office','M3-GLOBAL','SG','Asia/Singapore')]:c.execute('INSERT OR IGNORE INTO iam_offices(office_code,name,organization_id,country_id,timezone) VALUES(?,?,?,?,?)',(code,name,oq[org],cq[co],tz))
    for code,name,org,co in [('M3-NL','M3 Netherlands BV','M3-EU','NL'),('M3-AE','M3 Middle East FZE','M3-GLOBAL','AE')]:c.execute('INSERT OR IGNORE INTO iam_legal_entities(entity_code,name,organization_id,country_id) VALUES(?,?,?,?)',(code,name,oq[org],cq[co]))
    offices={r['office_code']:r['id'] for r in c.execute('SELECT * FROM iam_offices')}
    for code,name,office in [('RTM-HQ','Rotterdam HQ','RTM'),('DXB-MAIN','Dubai Main','DXB'),('KHI-MAIN','Karachi Main','KHI'),('SIN-MAIN','Singapore Main','SIN')]:c.execute('INSERT OR IGNORE INTO iam_branches(branch_code,name,office_id) VALUES(?,?,?)',(code,name,offices[office]))
    branches={r['branch_code']:r['id'] for r in c.execute('SELECT * FROM iam_branches')}
    for code,name,b in [('OPS-RTM','Operations','RTM-HQ'),('FIN-RTM','Finance','RTM-HQ'),('OPS-DXB','Operations','DXB-MAIN'),('FIN-DXB','Finance','DXB-MAIN')]:c.execute('INSERT OR IGNORE INTO iam_departments(department_code,name,branch_id) VALUES(?,?,?)',(code,name,branches[b]))
    roles=[('SUPER_ADMIN','Super Administrator',1),('ORG_ADMIN','Organization Administrator',1),('OFFICE_ADMIN','Office Administrator',1),('MASTER_DATA_MANAGER','Master Data Manager',1),('MASTER_DATA','Master Data Maker',0),('OPS','Operations',0),('FINANCE','Finance',1),('TREASURY','Treasury',1),('AUDITOR','Auditor',0),('VIEWER','Read Only',0)]
    for rc,n,s in roles:c.execute('INSERT OR IGNORE INTO iam_roles(role_code,name,sensitive) VALUES(?,?,?)',(rc,n,s))
    for m in ['agent-tasks','gl','treasury','integration','identity','organization','security','masterdata']:
        for a in ['view','create','edit','approve','release','admin']:
            c.execute('INSERT OR IGNORE INTO iam_permissions(permission_code,module,action,sensitive) VALUES(?,?,?,?)',(f'{m}:{a}',m,a,1 if a in ('approve','release','admin') else 0))
    rolesq={r['role_code']:r['id'] for r in c.execute('SELECT * FROM iam_roles')}; ps=list(c.execute('SELECT * FROM iam_permissions'))
    rp={'SUPER_ADMIN':'*','ORG_ADMIN':['identity','organization','security','masterdata','agent-tasks','gl','treasury','integration'],'OFFICE_ADMIN':['identity','organization','agent-tasks'],'MASTER_DATA_MANAGER':['masterdata'],'MASTER_DATA':['masterdata'],'OPS':['agent-tasks'],'FINANCE':['gl'],'TREASURY':['treasury','gl'],'AUDITOR':['agent-tasks','gl','treasury','integration','identity','organization','security','masterdata'],'VIEWER':['agent-tasks']}
    for role,mods in rp.items():
        for p in ps:
            allow=mods=='*' or p['module'] in mods
            if role in ('AUDITOR','VIEWER') and p['action']!='view':allow=False
            if role=='MASTER_DATA' and p['action'] in ('approve','admin'):allow=False
            if allow:c.execute('INSERT OR IGNORE INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,?)',(rolesq[role],p['id'],'ALLOW'))
    users=[('USR-001','admin','M3 Administrator','admin@m3.test','Admin123!','RTM',1),('USR-002','ops.rtm','RTM Operations','ops.rtm@m3.test','Ops123!','RTM',1),('USR-003','finance.dxb','DXB Finance','finance.dxb@m3.test','Fin123!','DXB',1),('USR-004','auditor','M3 Auditor','auditor@m3.test','Audit123!','RTM',1),('USR-005','md.maker','Master Data Maker','md.maker@m3.test','Maker123!','RTM',1),('USR-006','md.checker','Master Data Checker','md.checker@m3.test','Checker123!','RTM',1)]
    for ref,u,d,e,p,o,mfa in users:c.execute('INSERT OR IGNORE INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required) VALUES(?,?,?,?,?,?,?)',(ref,u,d,e,phash(p),offices[o],mfa))
    uq={r['username']:r['id'] for r in c.execute('SELECT * FROM iam_users')}
    assigns=[('admin','SUPER_ADMIN','RTM'),('ops.rtm','OPS','RTM'),('finance.dxb','FINANCE','DXB'),('auditor','AUDITOR','RTM'),('md.maker','MASTER_DATA','RTM'),('md.checker','MASTER_DATA_MANAGER','RTM')]
    for u,r,o in assigns:c.execute('INSERT OR IGNORE INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by) VALUES(?,?,?,?,?,?)',(uq[u],rolesq[r],offices[o],now(),'ACTIVE','seed'))
    for u,_,o in assigns:c.execute('INSERT OR IGNORE INTO iam_office_membership(user_id,office_id,membership_type) VALUES(?,?,?)',(uq[u],offices[o],'PRIMARY'))
    policies={'password_min_length':'10','max_failed_attempts':'5','lockout_minutes':'15','session_minutes':'60','session_idle_minutes':'30','mfa_required_sensitive':'true','reauth_minutes_sensitive':'10','deny_by_default':'true','sso_mode':'READINESS_ONLY'}
    for k,v in policies.items():c.execute('INSERT OR IGNORE INTO iam_security_policies(policy_key,policy_value,updated_at,updated_by) VALUES(?,?,?,?)',(k,v,now(),'seed'))
    for a,b,reason in [('FINANCE','TREASURY','Posting and payment release must be independently controlled'),('OPS','FINANCE','Operational maker cannot self-approve financial posting'),('MASTER_DATA','MASTER_DATA_MANAGER','Master-data maker/checker four-eyes control')]:c.execute('INSERT OR IGNORE INTO iam_sod_conflicts(conflict_code,role_a,role_b,reason,severity) VALUES(?,?,?,?,?)',(f'SOD-{a}-{b}',a,b,reason,'HIGH'))
    for rc,scope,res,act in [('SUPER_ADMIN','GLOBAL','*','*'),('OPS','OFFICE','agent-tasks','*'),('FINANCE','OFFICE','gl','*'),('TREASURY','OFFICE','treasury','*'),('AUDITOR','GLOBAL','*','view'),('MASTER_DATA_MANAGER','GLOBAL','masterdata','*'),('MASTER_DATA','GLOBAL','masterdata','create')]:c.execute('INSERT OR IGNORE INTO iam_scope_rules(rule_ref,role_code,scope_type,scope_value,resource,action,effect,priority) VALUES(?,?,?,?,?,?,?,?)',(f'RULE-{rc}',rc,scope,None,res,act,'ALLOW',100))
    for role,cur,limit,action in [('FINANCE','USD',100000,'APPROVE_VOUCHER'),('TREASURY','USD',75000,'RELEASE_PAYMENT'),('OFFICE_ADMIN','USD',10000,'APPROVE_EXPENSE')]:c.execute('INSERT OR IGNORE INTO iam_approval_limits(role_code,currency,amount_limit,action) VALUES(?,?,?,?)',(role,cur,limit,action))
    c.execute('INSERT OR IGNORE INTO iam_party_access(user_id,party_type,party_key,access_level) VALUES(?,?,?,?)',(uq['ops.rtm'],'CUSTOMER','CLX-CUS-001','EDIT'))
    c.execute('INSERT OR IGNORE INTO iam_party_access(user_id,party_type,party_key,access_level) VALUES(?,?,?,?)',(uq['ops.rtm'],'AGENT','CLX-AGT-SIN','VIEW'))
    for provider,protocol in [('enterprise-oidc','OIDC'),('enterprise-saml','SAML2')]:c.execute('INSERT OR IGNORE INTO iam_sso_readiness(provider_key,protocol,metadata_state,certificate_state,provisioning_state,status) VALUES(?,?,?,?,?,?)',(provider,protocol,'PLACEHOLDER','PLACEHOLDER','NOT_CONNECTED','READINESS_ONLY'))
    c.execute('INSERT OR IGNORE INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date) VALUES(?,?,?,?,?,?)',('AR-001',uq['finance.dxb'],'DXB FINANCE','OPEN','admin','2026-10-31'))
    c.execute('INSERT OR IGNORE INTO iam_delegations(delegation_ref,from_user_id,to_user_id,permission_code,valid_from,valid_to,status,approved_by) VALUES(?,?,?,?,?,?,?,?)',('DLG-001',uq['admin'],uq['auditor'],'identity:view','2026-09-24','2026-09-30','ACTIVE','admin'))
    c.execute('INSERT OR IGNORE INTO iam_service_accounts(account_ref,name,owner_user_id,office_id,status,secret_state) VALUES(?,?,?,?,?,?)',('SVC-001','M3 Sandbox Integration',uq['admin'],offices['RTM'],'ACTIVE','PLACEHOLDER_ONLY'))
    c.execute('INSERT OR IGNORE INTO iam_api_clients(client_ref,name,owner_user_id,allowed_scopes_json,status,credential_state) VALUES(?,?,?,?,?,?)',('API-001','M3 Sandbox API Client',uq['admin'],json.dumps(['read:test']),'ACTIVE','PLACEHOLDER_ONLY'))
    c.close()
if __name__=='__main__':run()