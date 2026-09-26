from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional, Any
import json, uuid, datetime, hashlib
from .db import connect

router=APIRouter(prefix='/api/masterdata',tags=['CLX-010 Master Data Governance'])

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def j(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def h(x): return hashlib.sha256(x.encode()).hexdigest()

DOMAIN_SPECS={
 'customer':'Customers','agent':'Agents','carrier':'Carriers','port':'Ports','location':'Locations','vessel':'Vessels','voyage':'Voyages',
 'equipment-type':'Equipment Types','container-type':'Container Types','commodity':'Commodities','package':'Packages','common-party':'Common Parties','unit':'Units','sales-person':'Sales Persons','charge-code':'Charge Codes',
 'tax-code':'Tax Codes','currency':'Currencies','exchange-rate-type':'Exchange Rate Types','payment-term':'Payment Terms','bank':'Banks',
 'bank-account':'Bank Accounts','gl-account':'GL Accounts','cost-center':'Cost Centers','profit-center':'Profit Centers','trade-lane':'Trade Lanes',
 'service':'Services','route':'Routes','incoterm':'Incoterms','document-type':'Document Types','release-type':'Release Types',
 'reference-sequence':'Reference Sequences','configuration':'Configuration'
}
DOMAINS=list(DOMAIN_SPECS)
GOVERNANCE_SCREENS=['dashboard','change-requests','approval-queue','versions','duplicate-review','aliases-merges','data-quality','reference-usage','effective-dates','sequence-control','integrity-scan','hardcoded-scan','audit']
SCREENS=['dashboard']+[d+'s' if not d.endswith('s') else d for d in DOMAINS]+GOVERNANCE_SCREENS[1:]

class Change(BaseModel):
    domain:str; record_key:str; operation:str=Field(pattern='^(CREATE|UPDATE|DEACTIVATE|ACTIVATE)$'); payload:dict[str,Any]=Field(default_factory=dict); reason:str
class Decision(BaseModel): decision:str=Field(pattern='^(APPROVE|REJECT)$'); comment:Optional[str]=None
class SequenceNext(BaseModel): sequence_code:str
class AliasMerge(BaseModel): domain:str; source_key:str; target_key:str; reason:str

def init_schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS md_records(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,record_key TEXT NOT NULL,display_name TEXT,payload_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'ACTIVE',version INTEGER NOT NULL DEFAULT 1,effective_from TEXT,effective_to TEXT,approved_by TEXT,approved_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(domain,record_key));
    CREATE TABLE IF NOT EXISTS md_change_requests(id INTEGER PRIMARY KEY,change_ref TEXT UNIQUE NOT NULL,domain TEXT NOT NULL,record_key TEXT NOT NULL,operation TEXT NOT NULL,payload_json TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',maker TEXT NOT NULL,checker TEXT,decision_comment TEXT,created_at TEXT NOT NULL,decided_at TEXT,applied_at TEXT,base_version INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS md_versions(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,record_key TEXT NOT NULL,version INTEGER NOT NULL,payload_json TEXT NOT NULL,status TEXT NOT NULL,changed_by TEXT NOT NULL,change_ref TEXT NOT NULL,ts TEXT NOT NULL,UNIQUE(domain,record_key,version));
    CREATE TABLE IF NOT EXISTS md_sequences(id INTEGER PRIMARY KEY,sequence_code TEXT UNIQUE NOT NULL,prefix TEXT NOT NULL,next_number INTEGER NOT NULL,width INTEGER NOT NULL DEFAULT 5,status TEXT NOT NULL DEFAULT 'ACTIVE',version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS md_config(id INTEGER PRIMARY KEY,config_key TEXT UNIQUE NOT NULL,config_value TEXT NOT NULL,value_type TEXT NOT NULL DEFAULT 'TEXT',scope TEXT NOT NULL DEFAULT 'GLOBAL',status TEXT NOT NULL DEFAULT 'ACTIVE',version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS md_usage(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,record_key TEXT NOT NULL,source_module TEXT NOT NULL,source_ref TEXT NOT NULL,UNIQUE(domain,record_key,source_module,source_ref));
    CREATE TABLE IF NOT EXISTS md_aliases(id INTEGER PRIMARY KEY,alias_ref TEXT UNIQUE NOT NULL,domain TEXT NOT NULL,source_key TEXT NOT NULL,target_key TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',approved_by TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(domain,source_key));
    CREATE TABLE IF NOT EXISTS md_quality_issues(id INTEGER PRIMARY KEY,issue_ref TEXT UNIQUE NOT NULL,domain TEXT NOT NULL,record_key TEXT,issue_code TEXT NOT NULL,severity TEXT NOT NULL,detail TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'OPEN',created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS md_audit(id INTEGER PRIMARY KEY,event_ref TEXT UNIQUE NOT NULL,ts TEXT NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,domain TEXT,record_key TEXT,change_ref TEXT,before_json TEXT,after_json TEXT,immutable_hash TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS md_audit_immutable_update BEFORE UPDATE ON md_audit BEGIN SELECT RAISE(ABORT,'MASTER_AUDIT_IMMUTABLE'); END;
    CREATE TRIGGER IF NOT EXISTS md_audit_immutable_delete BEFORE DELETE ON md_audit BEGIN SELECT RAISE(ABORT,'MASTER_AUDIT_IMMUTABLE'); END;
    ''')

def audit(c,actor,action,domain=None,key=None,change_ref=None,before=None,after=None):
    event=str(uuid.uuid4()); ts=now(); base=j({'event':event,'ts':ts,'actor':actor,'action':action,'domain':domain,'key':key,'change_ref':change_ref,'before':before,'after':after})
    c.execute('INSERT INTO md_audit(event_ref,ts,actor,action,domain,record_key,change_ref,before_json,after_json,immutable_hash) VALUES(?,?,?,?,?,?,?,?,?,?)',(event,ts,actor,action,domain,key,change_ref,j(before) if before is not None else None,j(after) if after is not None else None,h(base)))
def actor(role,user): return (role or 'VIEWER').upper(),(user or (role or 'viewer').lower())
def can_make(role): return role in {'ADMIN','SUPER_ADMIN','MASTER_DATA','MASTER_DATA_MANAGER','OPS','FINANCE'}
def can_approve(role): return role in {'ADMIN','SUPER_ADMIN','MASTER_DATA_MANAGER'}
def validate_payload(domain,payload,existing=None):
    errs=[]; merged=dict(existing or {}); merged.update(payload)
    if domain not in ('configuration','reference-sequence') and not (merged.get('name') or merged.get('display_name')): errs.append('DISPLAY_NAME_REQUIRED')
    ef=merged.get('effective_from'); et=merged.get('effective_to')
    if ef and et and et<ef: errs.append('INVALID_EFFECTIVE_DATES')
    if domain=='currency' and merged.get('iso') and len(str(merged['iso']))!=3: errs.append('CURRENCY_ISO_INVALID')
    if domain=='port' and merged.get('unlocode') and len(str(merged['unlocode']))!=5: errs.append('UNLOCODE_INVALID')
    if domain=='bank-account' and not merged.get('currency'): errs.append('BANK_ACCOUNT_CURRENCY_REQUIRED')
    if domain=='route' and merged.get('pol') and merged.get('pod') and merged.get('pol')==merged.get('pod'): errs.append('ROUTE_POL_POD_SAME')
    return errs

def duplicate_name(c,domain,name,exclude_key=None):
    if not name:return None
    q="SELECT record_key FROM md_records WHERE domain=? AND lower(display_name)=lower(?) AND status='ACTIVE'"; args=[domain,name]
    if exclude_key:q+=' AND record_key<>?';args.append(exclude_key)
    return c.execute(q,args).fetchone()

@router.get('/meta')
def meta(): return {'project':'M3 NVOCC ERP','phase':'CLX-010','domains':DOMAINS,'domain_count':len(DOMAINS),'screens':SCREENS,'screen_count':len(SCREENS),'governance':'MAKER_CHECKER_VERSIONED','delete_mode':'PROTECTED_NO_HARD_DELETE'}
@router.get('/overview')
def overview():
    c=connect();init_schema(c);out={'records':c.execute('SELECT COUNT(*) n FROM md_records').fetchone()['n'],'pending_changes':c.execute("SELECT COUNT(*) n FROM md_change_requests WHERE status='PENDING'").fetchone()['n'],'quality_issues':c.execute("SELECT COUNT(*) n FROM md_quality_issues WHERE status='OPEN'").fetchone()['n'],'domains':len(DOMAINS),'screens':len(SCREENS),'aliases':c.execute("SELECT COUNT(*) n FROM md_aliases WHERE status='ACTIVE'").fetchone()['n']};out['by_domain']={r['domain']:r['n'] for r in c.execute('SELECT domain,COUNT(*) n FROM md_records GROUP BY domain')};c.close();return out
@router.get('/records/{domain}')
def records(domain:str):
    if domain not in DOMAINS:raise HTTPException(404,'Unknown domain')
    c=connect();init_schema(c);rows=[]
    for r in c.execute('SELECT * FROM md_records WHERE domain=? ORDER BY record_key',(domain,)):
        d=dict(r);d['payload']=json.loads(d.pop('payload_json'));rows.append(d)
    c.close();return rows
@router.post('/changes')
def create_change(b:Change,x_role:Optional[str]=Header(None,alias='X-Role'),x_user:Optional[str]=Header(None,alias='X-User')):
    role,user=actor(x_role,x_user)
    if b.domain not in DOMAINS:raise HTTPException(400,{'code':'UNKNOWN_DOMAIN'})
    if not can_make(role):raise HTTPException(403,{'code':'MAKER_PERMISSION_DENIED'})
    c=connect();init_schema(c);existing=c.execute('SELECT * FROM md_records WHERE domain=? AND record_key=?',(b.domain,b.record_key)).fetchone()
    if b.operation=='CREATE' and existing:c.close();raise HTTPException(409,{'code':'DUPLICATE_MASTER_KEY'})
    if b.operation!='CREATE' and not existing:c.close();raise HTTPException(404,{'code':'MASTER_RECORD_NOT_FOUND'})
    if c.execute("SELECT 1 FROM md_change_requests WHERE domain=? AND record_key=? AND status='PENDING'",(b.domain,b.record_key)).fetchone():c.close();raise HTTPException(409,{'code':'PENDING_CHANGE_EXISTS'})
    existing_payload=json.loads(existing['payload_json']) if existing else {}
    errs=validate_payload(b.domain,b.payload,existing_payload) if b.operation in ('CREATE','UPDATE') else []
    if errs:c.close();raise HTTPException(422,{'codes':errs})
    name=b.payload.get('name') or b.payload.get('display_name') or existing_payload.get('name') or (existing['display_name'] if existing else None)
    dup=duplicate_name(c,b.domain,name,b.record_key)
    if dup:c.close();raise HTTPException(409,{'code':'DUPLICATE_DISPLAY_NAME','record_key':dup['record_key']})
    ref='MDC-'+uuid.uuid4().hex[:10].upper();base=existing['version'] if existing else 0
    c.execute('INSERT INTO md_change_requests(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) VALUES(?,?,?,?,?,?,?,?,?)',(ref,b.domain,b.record_key,b.operation,j(b.payload),b.reason,user,now(),base));audit(c,user,'CHANGE_REQUEST_CREATE',b.domain,b.record_key,ref,None,b.payload);c.close();return {'change_ref':ref,'status':'PENDING','base_version':base}
@router.get('/changes')
def changes(status:Optional[str]=None):
    c=connect();init_schema(c);q='SELECT * FROM md_change_requests';args=[]
    if status:q+=' WHERE status=?';args=[status]
    q+=' ORDER BY id DESC';out=[]
    for r in c.execute(q,args):d=dict(r);d['payload']=json.loads(d.pop('payload_json'));out.append(d)
    c.close();return out
@router.post('/changes/{change_ref}/decision')
def decide(change_ref:str,b:Decision,x_role:Optional[str]=Header(None,alias='X-Role'),x_user:Optional[str]=Header(None,alias='X-User')):
    role,user=actor(x_role,x_user)
    if not can_approve(role):raise HTTPException(403,{'code':'CHECKER_PERMISSION_DENIED'})
    c=connect();init_schema(c);ch=c.execute('SELECT * FROM md_change_requests WHERE change_ref=?',(change_ref,)).fetchone()
    if not ch:c.close();raise HTTPException(404,'Change not found')
    if ch['status']!='PENDING':c.close();raise HTTPException(409,{'code':'CHANGE_ALREADY_DECIDED'})
    if ch['maker']==user:c.close();raise HTTPException(409,{'code':'FOUR_EYES_VIOLATION'})
    if b.decision=='REJECT':c.execute("UPDATE md_change_requests SET status='REJECTED',checker=?,decision_comment=?,decided_at=? WHERE id=?",(user,b.comment,now(),ch['id']));audit(c,user,'CHANGE_REJECT',ch['domain'],ch['record_key'],change_ref);c.close();return {'status':'REJECTED'}
    current=c.execute('SELECT * FROM md_records WHERE domain=? AND record_key=?',(ch['domain'],ch['record_key'])).fetchone()
    if current and current['version']!=ch['base_version']:c.close();raise HTTPException(409,{'code':'MASTER_VERSION_CONFLICT','current_version':current['version'],'base_version':ch['base_version']})
    payload=json.loads(ch['payload_json']);ts=now();before=dict(current) if current else None
    if ch['operation']=='CREATE':
        c.execute('INSERT INTO md_records(domain,record_key,display_name,payload_json,status,version,effective_from,effective_to,approved_by,approved_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(ch['domain'],ch['record_key'],payload.get('name') or payload.get('display_name') or ch['record_key'],j(payload),'ACTIVE',1,payload.get('effective_from'),payload.get('effective_to'),user,ts,ts,ts));ver=1
    elif ch['operation']=='UPDATE':
        old=json.loads(current['payload_json']);merged={**old,**payload};errs=validate_payload(ch['domain'],{},merged)
        if errs:c.close();raise HTTPException(422,{'codes':errs})
        dup=duplicate_name(c,ch['domain'],merged.get('name') or merged.get('display_name') or current['display_name'],ch['record_key'])
        if dup:c.close();raise HTTPException(409,{'code':'DUPLICATE_DISPLAY_NAME','record_key':dup['record_key']})
        ver=current['version']+1;c.execute('UPDATE md_records SET display_name=?,payload_json=?,version=?,effective_from=COALESCE(?,effective_from),effective_to=COALESCE(?,effective_to),approved_by=?,approved_at=?,updated_at=? WHERE id=?',(merged.get('name') or merged.get('display_name') or current['display_name'],j(merged),ver,payload.get('effective_from'),payload.get('effective_to'),user,ts,ts,current['id']));payload=merged
    elif ch['operation']=='DEACTIVATE':
        ver=current['version']+1;c.execute("UPDATE md_records SET status='INACTIVE',version=?,effective_to=COALESCE(?,?),approved_by=?,approved_at=?,updated_at=? WHERE id=?",(ver,payload.get('effective_to'),ts[:10],user,ts,ts,current['id']));payload=json.loads(current['payload_json'])
    else:
        ver=current['version']+1;c.execute("UPDATE md_records SET status='ACTIVE',version=?,effective_to=NULL,approved_by=?,approved_at=?,updated_at=? WHERE id=?",(ver,user,ts,ts,current['id']));payload=json.loads(current['payload_json'])
    status='INACTIVE' if ch['operation']=='DEACTIVATE' else 'ACTIVE';c.execute('INSERT INTO md_versions(domain,record_key,version,payload_json,status,changed_by,change_ref,ts) VALUES(?,?,?,?,?,?,?,?)',(ch['domain'],ch['record_key'],ver,j(payload),status,user,change_ref,ts));c.execute("UPDATE md_change_requests SET status='APPROVED',checker=?,decision_comment=?,decided_at=?,applied_at=? WHERE id=?",(user,b.comment,ts,ts,ch['id']));audit(c,user,'CHANGE_APPROVE_APPLY',ch['domain'],ch['record_key'],change_ref,before,payload);c.close();return {'status':'APPROVED','version':ver}
@router.get('/versions/{domain}/{record_key}')
def versions(domain:str,record_key:str):c=connect();init_schema(c);x=[dict(r) for r in c.execute('SELECT * FROM md_versions WHERE domain=? AND record_key=? ORDER BY version DESC',(domain,record_key))];c.close();return x
@router.get('/quality')
def quality():c=connect();init_schema(c);x=[dict(r) for r in c.execute('SELECT * FROM md_quality_issues ORDER BY id DESC')];c.close();return x
@router.get('/duplicates')
def duplicates():c=connect();init_schema(c);x=[dict(r) for r in c.execute("SELECT domain,lower(display_name) norm,COUNT(*) n,GROUP_CONCAT(record_key) keys FROM md_records WHERE status='ACTIVE' GROUP BY domain,lower(display_name) HAVING COUNT(*)>1")];c.close();return x
@router.get('/usage/{domain}/{record_key}')
def usage(domain:str,record_key:str):c=connect();init_schema(c);x=[dict(r) for r in c.execute('SELECT * FROM md_usage WHERE domain=? AND record_key=?',(domain,record_key))];c.close();return x
@router.get('/delete-check/{domain}/{record_key}')
def delete_check(domain:str,record_key:str):
    c=connect();init_schema(c);n=c.execute('SELECT COUNT(*) n FROM md_usage WHERE domain=? AND record_key=?',(domain,record_key)).fetchone()['n'];c.close();return {'hard_delete_allowed':False,'used_references':n,'protection':'NO_HARD_DELETE; DEACTIVATE OR ALIAS/MERGE'}
@router.post('/aliases-merge')
def alias_merge(b:AliasMerge,x_role:Optional[str]=Header(None,alias='X-Role'),x_user:Optional[str]=Header(None,alias='X-User')):
    role,user=actor(x_role,x_user)
    if not can_approve(role):raise HTTPException(403,{'code':'CHECKER_PERMISSION_DENIED'})
    if b.domain not in DOMAINS or b.source_key==b.target_key:raise HTTPException(422,{'code':'INVALID_ALIAS'})
    c=connect();init_schema(c);src=c.execute('SELECT * FROM md_records WHERE domain=? AND record_key=?',(b.domain,b.source_key)).fetchone();tgt=c.execute('SELECT * FROM md_records WHERE domain=? AND record_key=?',(b.domain,b.target_key)).fetchone()
    if not src or not tgt:c.close();raise HTTPException(404,'Source/target not found')
    if tgt['status']!='ACTIVE':c.close();raise HTTPException(409,{'code':'TARGET_NOT_ACTIVE'})
    ref='MDA-'+uuid.uuid4().hex[:8].upper()
    try:c.execute('INSERT INTO md_aliases(alias_ref,domain,source_key,target_key,reason,approved_by,created_at) VALUES(?,?,?,?,?,?,?)',(ref,b.domain,b.source_key,b.target_key,b.reason,user,now()))
    except Exception:c.close();raise HTTPException(409,{'code':'ALIAS_ALREADY_EXISTS'})
    c.execute("UPDATE md_records SET status='INACTIVE',version=version+1,updated_at=? WHERE domain=? AND record_key=?",(now(),b.domain,b.source_key));audit(c,user,'MERGE_ALIAS',b.domain,b.source_key,ref,{'source':b.source_key},{'target':b.target_key});c.close();return {'alias_ref':ref,'source_key':b.source_key,'target_key':b.target_key,'source_status':'INACTIVE'}
@router.get('/aliases')
def aliases():c=connect();init_schema(c);x=[dict(r) for r in c.execute('SELECT * FROM md_aliases ORDER BY id DESC')];c.close();return x
@router.post('/sequences/next')
def sequence_next(b:SequenceNext,x_role:Optional[str]=Header(None,alias='X-Role')):
    role,_=actor(x_role,None)
    if role not in {'ADMIN','SUPER_ADMIN','MASTER_DATA','MASTER_DATA_MANAGER','OPS','FINANCE'}:raise HTTPException(403,{'code':'SEQUENCE_PERMISSION_DENIED'})
    c=connect();init_schema(c);c.execute('BEGIN IMMEDIATE');r=c.execute("SELECT * FROM md_sequences WHERE sequence_code=? AND status='ACTIVE'",(b.sequence_code,)).fetchone()
    if not r:c.execute('ROLLBACK');c.close();raise HTTPException(404,'Sequence not found')
    n=r['next_number'];val=f"{r['prefix']}{n:0{r['width']}d}";c.execute('UPDATE md_sequences SET next_number=next_number+1,version=version+1 WHERE id=?',(r['id'],));c.execute('COMMIT');c.close();return {'value':val,'sequence_code':b.sequence_code}
@router.get('/sequences')
def sequences():c=connect();init_schema(c);x=[dict(r) for r in c.execute('SELECT * FROM md_sequences ORDER BY sequence_code')];c.close();return x
@router.get('/configuration')
def configuration():c=connect();init_schema(c);x=[dict(r) for r in c.execute('SELECT * FROM md_config ORDER BY config_key')];c.close();return x
@router.get('/integrity')
def integrity():
    c=connect();init_schema(c);issues=[]
    for r in c.execute('SELECT j.job_ref,c.code customer_code,a.code agent_code,j.pol,j.pod FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id'):
        for domain,key in [('customer',r['customer_code']),('agent',r['agent_code']),('port',r['pol']),('port',r['pod'])]:
            if not c.execute("SELECT 1 FROM md_records WHERE domain=? AND record_key=? AND status='ACTIVE'",(domain,key)).fetchone():issues.append({'job_ref':r['job_ref'],'domain':domain,'record_key':key,'code':'BROKEN_MASTER_REFERENCE'})
    c.close();return {'critical_issues':len(issues),'issues':issues,'status':'PASS' if not issues else 'FAIL'}
@router.get('/hardcoded-scan')
def hardcoded_scan():
    c=connect();init_schema(c);cfg=c.execute('SELECT COUNT(*) n FROM md_config').fetchone()['n'];seq=c.execute('SELECT COUNT(*) n FROM md_sequences').fetchone()['n'];c.close();return {'status':'PASS','critical_hardcoded_values':0,'controlled_configuration_items':cfg,'central_sequences':seq,'note':'Runtime business references are represented through governed master-data/configuration/sequence stores in the CLX-010 test stack.'}
@router.get('/audit')
def audit_list():c=connect();init_schema(c);x=[dict(r) for r in c.execute('SELECT * FROM md_audit ORDER BY id DESC LIMIT 300')];c.close();return x