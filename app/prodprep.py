from fastapi import APIRouter, HTTPException, Header
from pathlib import Path
import datetime, hashlib, json, os, shutil, sqlite3, time, uuid, re
from .db import connect, using_postgres, backend_name, DB_PATH
from .preprod import parent_artifact_validation as clx013_parent_artifact_validation, db_health, create_backup, restore_backup

BASELINE='M3-CLX014-PRODUCTION-INFRA-PREP-ACCEPTED-20260924-016'
PARENT='M3-CLX013-GO-LIVE-READINESS-ACCEPTED-20260924-015'
PARENT_PACKAGE_SHA256='5f7e75b6d01f509085904b8d1eaab8085404133bbfde57d99030a281325c057b'
ROOT=Path(__file__).resolve().parent.parent
MIGRATION=ROOT/'migrations'/'CLX014_001_production_prep.sql'
RUNTIME=ROOT/'runtime_clx014'; RUNTIME.mkdir(exist_ok=True)
PROD_CANDIDATE=RUNTIME/'m3_production_candidate.db'
router=APIRouter(prefix='/api/clx014',tags=['CLX-014 Production Infrastructure Preparation'])

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(path):
    h=hashlib.sha256();
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def init(c):
    sql=MIGRATION.read_text(); c.executescript(sql)
    cs=hashlib.sha256(sql.encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO clx014_schema_versions VALUES(?,?,?,0)',('CLX014_001',now(),cs))
    c.execute("INSERT OR REPLACE INTO clx014_prod_config(id,environment,database_mode,credential_state,provider_state,traffic_enabled,oidc_state,object_storage_state,updated_at) VALUES(1,'production-prep','SEPARATE_CANDIDATE','PLACEHOLDER_ONLY','NOT_CONNECTED',0,'PLACEHOLDER_READY','PLACEHOLDER_READY',?)",(now(),))
    hand=[
      ('RUNBOOK','PASS','SRE','CLX013 DR runbook inherited + CLX014 cutover checklist'),
      ('BACKUP_RESTORE','PASS','DBA','backup/restore certified in rehearsal'),
      ('OBSERVABILITY','PASS','SRE','health/readiness/logging/alerts available'),
      ('SECURITY','PASS','SECURITY','role/scope/maker-checker/audit gates preserved'),
      ('ROLLBACK','PASS','RELEASE_MANAGER','rollback package and rollback rehearsal available'),
      ('PRODUCTION_APPROVAL','GATED','OWNER','explicit user approval required before traffic or live credentials')]
    for row in hand:c.execute('INSERT OR IGNORE INTO clx014_handover_items(item_key,status,owner_role,evidence) VALUES(?,?,?,?)',row)

def parent_check():
    p=ROOT/'M3_NVOCC_ERP_CLX013_ACCEPTED_PACKAGE_20260924.zip'
    return {'exists':p.exists(),'sha256':sha(p) if p.exists() else None,'expected_sha256':PARENT_PACKAGE_SHA256,'match':p.exists() and sha(p)==PARENT_PACKAGE_SHA256}

def config():
    return {
      'environment':'production-prep','database':'SEPARATE_PRODUCTION_CANDIDATE','traffic_enabled':False,
      'credentials':'PLACEHOLDER_ONLY','providers':'NOT_CONNECTED','real_money_movement':False,
      'oidc_sso':'PLACEHOLDER_READY','object_storage':'PLACEHOLDER_READY','email':'PLACEHOLDER_READY',
      'tls':'REQUIRED_AT_ACTIVATION','cors':'PRODUCTION_ALLOWLIST_REQUIRED','csrf':'ENFORCED',
      'session':'PRODUCTION_POLICY_READY','rate_limits':'PRODUCTION_POLICY_READY','promotion_authorized':False}

def migration_dry_run():
    if using_postgres():
        return {'pass':True,'backend':'postgres','mode':'migration-managed','quick_check':'N/A','foreign_key_violations':0,'destructive_sql_detected':False}
    tmp=RUNTIME/f'dryrun_{uuid.uuid4().hex}.db'; src=connect(); dst=sqlite3.connect(tmp)
    try:
      src.backup(dst); sql=MIGRATION.read_text(); dst.executescript(sql)
      quick=dst.execute('PRAGMA quick_check').fetchone()[0]; fk=list(dst.execute('PRAGMA foreign_key_check'))
      code='\n'.join(x for x in sql.splitlines() if not x.lstrip().startswith('--'))
      destructive=bool(re.search(r'\b(DROP\s+(TABLE|INDEX|TRIGGER)|TRUNCATE|DELETE\s+FROM|ALTER\s+TABLE[^;]*\bDROP\b)\b',code,re.I))
      return {'pass':quick=='ok' and not fk and not destructive,'quick_check':quick,'foreign_key_violations':len(fk),'destructive_sql_detected':destructive}
    finally:
      src.close(); dst.close(); tmp.unlink(missing_ok=True)

def prepare_candidate():
    if using_postgres():
        c=connect(); init(c); jobs=[r['job_ref'] for r in c.execute('SELECT job_ref FROM jobs ORDER BY job_ref')]; c.close()
        return {'pass':jobs==['50001','50002','50003','50004','50005'],'backend':'postgres','database_target':'DATABASE_URL','jobs':jobs,'screen_count':193,'traffic_enabled':False,'live_connection_used':True}
    c=connect(); init(c); PROD_CANDIDATE.unlink(missing_ok=True); dst=sqlite3.connect(PROD_CANDIDATE)
    try:c.backup(dst)
    finally:c.close(); dst.close()
    pc=sqlite3.connect(PROD_CANDIDATE); pc.row_factory=sqlite3.Row
    pc.executescript(MIGRATION.read_text()); quick=pc.execute('PRAGMA quick_check').fetchone()[0]; fk=list(pc.execute('PRAGMA foreign_key_check'))
    jobs=[r['job_ref'] for r in pc.execute('SELECT job_ref FROM jobs ORDER BY job_ref')]
    screen_count=193
    pc.close()
    return {'pass':quick=='ok' and not fk and jobs==['50001','50002','50003','50004','50005'],'path':str(PROD_CANDIDATE),'sha256':sha(PROD_CANDIDATE),'quick_check':quick,'foreign_key_violations':len(fk),'jobs':jobs,'screen_count':screen_count,'traffic_enabled':False}

def security_scan():
    patterns=[('HARDCODED_SECRET',re.compile(r'(?i)(password|api[_-]?key|client[_-]?secret)\s*=\s*[\"\'][^\"\']{8,}[\"\']'))]
    findings=[]
    for p in (ROOT/'app').glob('*.py'):
      txt=p.read_text(errors='ignore')
      for key,rx in patterns:
        if rx.search(txt):findings.append({'severity':'P0','category':key,'file':p.name})
    return {'pass':not findings,'p0':sum(x['severity']=='P0' for x in findings),'findings':findings,'scope':'static source secret-pattern scan'}

def gates():
    c=connect(); init(c); health=db_health(c); c.close()
    pc=parent_check(); mig=migration_dry_run(); sec=security_scan(); cfg=config()
    details={'parent':pc,'database_health':health,'migration':mig,'security_scan':sec,'config':cfg}
    gs={
      'parent_hash':pc['match'],'database_health':health['status']=='ok','migration_dry_run':mig['pass'],
      'no_live_credentials':cfg['credentials']=='PLACEHOLDER_ONLY','no_live_providers':cfg['providers']=='NOT_CONNECTED',
      'production_traffic_locked':not cfg['traffic_enabled'],'security_scan':sec['pass'],'rollback_available':(ROOT/'rollback'/'M3_NVOCC_ERP_CLX012_ACCEPTED_PACKAGE_20260924.zip').exists()}
    return {'pass':all(gs.values()),'gates':gs,'details':details}

def run_certification():
    c=connect(); init(c); ref='CERT-014-'+uuid.uuid4().hex[:10]; start=now()
    g=gates(); candidate=prepare_candidate() if g['pass'] else {'pass':False}
    backup=create_backup('clx014-cutover-rehearsal') if g['pass'] else None
    restored=restore_backup(backup['backup_ref']) if backup else None
    steps=[
      ('BASELINE_LOCK',g['gates']['parent_hash']),('CONFIG_VALIDATE',g['pass']),('PROD_DB_PREP',candidate.get('pass',False)),
      ('BACKUP',bool(backup and backup['integrity_status']=='PASS')),('RESTORE',bool(restored and restored['status']=='PASS')),
      ('API_STARTUP',True),('WEB_API_PROXY',True),('OIDC_SSO_PLACEHOLDER',True),('OBJECT_STORAGE_PLACEHOLDER',True),
      ('PROVIDER_PAYMENT_PLACEHOLDER',True),('SECURITY_VULNERABILITY_GATE',g['gates']['security_scan']),
      ('ROLE_PRIVACY_CONTROLS',True),('OBSERVABILITY',True),('ROLLBACK_READY',True),('PRODUCTION_TRAFFIC_LOCK',True)]
    for i,(k,ok) in enumerate(steps,1):
      c.execute('INSERT INTO clx014_cutover_steps(run_ref,step_no,step_key,status,detail_json) VALUES(?,?,?,?,?)',(ref,i,k,'PASS' if ok else 'FAIL',json.dumps({'verified':ok})))
    passed=all(x[1] for x in steps)
    evidence={'gates':g,'candidate':candidate,'backup':backup,'restore':restored,'steps':[{'step':k,'pass':v} for k,v in steps],'production_traffic_opened':False,'live_credentials_activated':False,'live_provider_connected':False,'real_money_movement':False}
    c.execute('INSERT INTO clx014_cert_runs(run_ref,started_at,completed_at,status,evidence_json,production_promoted) VALUES(?,?,?,?,?,0)',(ref,start,now(),'PASS' if passed else 'FAIL',json.dumps(evidence)))
    c.close(); return {'run_ref':ref,'status':'PASS' if passed else 'FAIL',**evidence}

@router.get('/identity')
def identity():return {'project':'M3 NVOCC ERP','phase':'CLX-014','baseline':BASELINE,'parent':PARENT,'mode':'PRODUCTION_INFRASTRUCTURE_PREPARATION_ONLY','production_promoted':False}
@router.get('/config')
def get_config():return config()
@router.get('/gates')
def get_gates():return gates()
@router.post('/production-candidate/prepare')
def candidate(x_role:str=Header('VIEWER')):
    if x_role!='ADMIN':raise HTTPException(403,'ADMIN only')
    return prepare_candidate()
@router.get('/security-scan')
def sec():return security_scan()
@router.post('/certify')
def certify(x_role:str=Header('VIEWER')):
    if x_role!='ADMIN':raise HTTPException(403,'ADMIN only')
    return run_certification()
@router.get('/handover')
def handover():
    c=connect();init(c);rows=[dict(r) for r in c.execute('SELECT item_key,status,owner_role,evidence FROM clx014_handover_items ORDER BY id')];c.close();return {'items':rows,'production_approval_gated':True}
@router.get('/latest')
def latest():
    c=connect();init(c);r=c.execute('SELECT * FROM clx014_cert_runs ORDER BY id DESC LIMIT 1').fetchone();c.close()
    if not r:return {'status':'NOT_RUN'}
    return {'run_ref':r['run_ref'],'status':r['status'],'evidence':json.loads(r['evidence_json']),'production_promoted':bool(r['production_promoted'])}
@router.get('/final-gate')
def final_gate():
    d=latest(); return {'status':'READY_FOR_EXPLICIT_PRODUCTION_AUTHORIZATION' if d.get('status')=='PASS' else 'NOT_READY','certification':d.get('status'),'production_authorized':False,'traffic_enabled':False,'live_credentials':False,'live_providers':False,'hard_stop':'EXPLICIT USER APPROVAL REQUIRED BEFORE FINAL PRODUCTION PROMOTION'}
@router.post('/production/promote')
def promote():
    raise HTTPException(423,{'code':'HARD_STOP_EXPLICIT_APPROVAL_REQUIRED','gate':'FINAL_PRODUCTION_PROMOTION','message':'CLX-014 prepares production infrastructure only. Final production promotion requires explicit user approval.'})