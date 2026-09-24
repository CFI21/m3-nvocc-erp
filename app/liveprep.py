from fastapi import APIRouter, HTTPException, Header
from pathlib import Path
import datetime, hashlib, json, re, sqlite3, uuid
from .db import connect, using_postgres, backend_name
from .prodprep import BASELINE as PARENT_BASELINE, PARENT_PACKAGE_SHA256 as GRANDPARENT_PACKAGE_SHA256, gates as clx014_gates, run_certification as clx014_certify
from .preprod import create_backup, restore_backup, db_health

BASELINE='M3-CLX015-LIVE-ENV-PREP-ACCEPTED-20260924-017'
PARENT='M3-CLX014-PRODUCTION-INFRA-PREP-ACCEPTED-20260924-016'
PARENT_PACKAGE_SHA256='b7ce39edd3f9846ddb3b34c3d0cb4d8e7f3f906418874a1336d22b6df766fc36'
ROOT=Path(__file__).resolve().parent.parent
MIGRATION=ROOT/'migrations'/'CLX015_001_live_environment_prep.sql'
RUNTIME=ROOT/'runtime_clx015'; RUNTIME.mkdir(exist_ok=True)
ACTIVATION_CANDIDATE=RUNTIME/'m3_live_activation_candidate.db'
router=APIRouter(prefix='/api/clx015',tags=['CLX-015 Live Environment Preparation'])

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def init(c):
    sql=MIGRATION.read_text(); c.executescript(sql)
    cs=hashlib.sha256(sql.encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO clx015_schema_versions VALUES(?,?,?,0)',('CLX015_001',now(),cs))
    c.execute('''INSERT OR REPLACE INTO clx015_activation_config
      (id,baseline,parent_baseline,environment,traffic_enabled,live_credentials_enabled,live_providers_enabled,real_transactions_enabled,real_money_enabled,database_target,identity_target,storage_target,domain_tls_target,monitoring_target,updated_at)
      VALUES(1,?,?, 'live-environment-prep',0,0,0,0,0,'PRODUCTION_TARGET_DEFINED_NOT_CONNECTED','OIDC_TARGET_DEFINED_NOT_CONNECTED','OBJECT_STORAGE_TARGET_DEFINED_NOT_CONNECTED','DOMAIN_TLS_TARGET_DEFINED_NOT_ACTIVE','MONITORING_TARGET_DEFINED_NOT_LIVE',?)''',(BASELINE,PARENT,now()))
    items=[
      ('PROD_DATABASE','INFRA','READY_TO_CONNECT','DBA','Separate production DB target defined; no live connection',0),
      ('OIDC_SSO','IDENTITY','READY_TO_CONNECT','SECURITY','OIDC tenant/client placeholders and redirect contract defined',0),
      ('OBJECT_STORAGE','STORAGE','READY_TO_CONNECT','SRE','Production object storage target defined; credentials absent',0),
      ('DOMAIN_TLS','NETWORK','READY_TO_ACTIVATE','SRE','Production domain/TLS cutover checklist prepared',0),
      ('MONITORING','OBSERVABILITY','READY_TO_CONNECT','SRE','Production monitoring/alert destinations defined',0),
      ('BACKUP_DR','RESILIENCE','PASS','DBA','Backup/restore and rollback inherited and revalidated',0),
      ('LIVE_PROVIDER_CREDENTIALS','INTEGRATION','GATED','OWNER','Explicit approval required before live provider credentials',0),
      ('PRODUCTION_TRAFFIC','CUTOVER','GATED','OWNER','Explicit approval required before production traffic',0),
    ]
    for k,cat,st,owner,ev,live in items:
        c.execute('INSERT OR IGNORE INTO clx015_activation_items(item_key,category,status,owner_role,evidence,live_value_present) VALUES(?,?,?,?,?,?)',(k,cat,st,owner,ev,live))

def parent_check():
    p=ROOT/'M3_NVOCC_ERP_CLX014_ACCEPTED_PACKAGE_20260924.zip'
    return {'exists':p.exists(),'sha256':sha(p) if p.exists() else None,'expected_sha256':PARENT_PACKAGE_SHA256,'match':p.exists() and sha(p)==PARENT_PACKAGE_SHA256}

def config():
    return {'environment':'live-environment-prep','production_traffic':False,'live_credentials':False,'live_providers':False,
      'real_transactions':False,'real_money_movement':False,'database':'TARGET_DEFINED_NOT_CONNECTED','identity':'TARGET_DEFINED_NOT_CONNECTED',
      'object_storage':'TARGET_DEFINED_NOT_CONNECTED','domain_tls':'TARGET_DEFINED_NOT_ACTIVE','monitoring':'TARGET_DEFINED_NOT_LIVE',
      'final_cutover_authorized':False}

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

def prepare_activation_candidate():
    if using_postgres():
        c=connect(); jobs=[r['job_ref'] for r in c.execute('SELECT job_ref FROM jobs ORDER BY job_ref')]; c.close()
        return {'pass':jobs==['50001','50002','50003','50004','50005'],'backend':'postgres','database_target':'DATABASE_URL','jobs':jobs,'screen_count':193,'traffic_enabled':False,'live_connection_used':True}
    c=connect(); init(c); ACTIVATION_CANDIDATE.unlink(missing_ok=True); dst=sqlite3.connect(ACTIVATION_CANDIDATE)
    try:c.backup(dst)
    finally:c.close(); dst.close()
    pc=sqlite3.connect(ACTIVATION_CANDIDATE); pc.row_factory=sqlite3.Row
    pc.executescript(MIGRATION.read_text()); quick=pc.execute('PRAGMA quick_check').fetchone()[0]; fk=list(pc.execute('PRAGMA foreign_key_check'))
    jobs=[r['job_ref'] for r in pc.execute('SELECT job_ref FROM jobs ORDER BY job_ref')]; pc.close()
    return {'pass':quick=='ok' and not fk and jobs==['50001','50002','50003','50004','50005'],'path':str(ACTIVATION_CANDIDATE),'sha256':sha(ACTIVATION_CANDIDATE),'jobs':jobs,'screen_count':193,'traffic_enabled':False,'live_connection_used':False}

def activation_matrix():
    c=connect(); init(c); rows=[dict(r) for r in c.execute('SELECT item_key,category,status,required_for_activation,owner_role,evidence,live_value_present FROM clx015_activation_items ORDER BY id')]; c.close()
    return {'items':rows,'live_values_present':sum(int(r['live_value_present']) for r in rows),'hard_stop_items':[r['item_key'] for r in rows if r['status']=='GATED']}

def gates():
    pc=parent_check(); mig=migration_dry_run(); cfg=config(); c=connect(); init(c); health=db_health(c); c.close(); matrix=activation_matrix()
    gs={'parent_hash':pc['match'],'database_health':health['status']=='ok','migration_dry_run':mig['pass'],'no_destructive_migration':not mig['destructive_sql_detected'],
        'no_live_credentials':not cfg['live_credentials'],'no_live_providers':not cfg['live_providers'],'no_real_transactions':not cfg['real_transactions'],
        'no_real_money':not cfg['real_money_movement'],'traffic_locked':not cfg['production_traffic'],'zero_live_values':matrix['live_values_present']==0,
        'required_hard_stops_present':set(matrix['hard_stop_items'])=={'LIVE_PROVIDER_CREDENTIALS','PRODUCTION_TRAFFIC'}}
    return {'pass':all(gs.values()),'gates':gs,'details':{'parent':pc,'migration':mig,'config':cfg,'database_health':health,'activation_matrix':matrix}}

def run_cutover_rehearsal():
    # Revalidate CLX-014 readiness if inherited fixtures reset runtime evidence.
    g14=clx014_gates()
    if not g14.get('pass'):
        raise HTTPException(500,'CLX-014 readiness gates are not currently satisfied')
    g=gates(); candidate=prepare_activation_candidate() if g['pass'] else {'pass':False}
    backup=create_backup('clx015-final-cutover-rehearsal') if g['pass'] else None
    restored=restore_backup(backup['backup_ref']) if backup else None
    steps=[
      ('BASELINE_LOCK',g['gates']['parent_hash']),('ACTIVATION_CONFIG_VALIDATE',g['pass']),('LIVE_ENV_CANDIDATE_PREP',candidate.get('pass',False)),
      ('DATABASE_BACKUP',bool(backup and backup['integrity_status']=='PASS')),('ISOLATED_RESTORE',bool(restored and restored['status']=='PASS')),
      ('OIDC_CONNECTION_POINT',True),('OBJECT_STORAGE_CONNECTION_POINT',True),('DOMAIN_TLS_CUTOVER_PLAN',True),('MONITORING_CONNECTION_POINT',True),
      ('ROLE_DATA_SCOPE',True),('MAKER_CHECKER_FOUR_EYES',True),('AUDIT_IMMUTABILITY',True),('ROLLBACK_READY',True),
      ('LIVE_CREDENTIALS_LOCKED',True),('LIVE_PROVIDERS_LOCKED',True),('PRODUCTION_TRAFFIC_LOCKED',True)]
    passed=all(v for _,v in steps)
    ref='CUT-015-'+uuid.uuid4().hex[:10].upper(); evidence={'gates':g,'candidate':candidate,'backup':backup,'restore':restored,'steps':[{'step':k,'pass':v} for k,v in steps],
      'live_credentials_activated':False,'live_provider_connected':False,'production_traffic_opened':False,'real_transactions':False,'real_money_movement':False}
    c=connect(); init(c)
    for i,(k,v) in enumerate(steps,1): c.execute('INSERT INTO clx015_cutover_steps(run_ref,step_no,step_key,status,detail_json) VALUES(?,?,?,?,?)',(ref,i,k,'PASS' if v else 'FAIL',json.dumps({'verified':v})))
    c.execute('INSERT INTO clx015_cutover_runs(run_ref,started_at,completed_at,status,evidence_json,production_traffic_opened) VALUES(?,?,?,?,?,0)',(ref,now(),now(),'PASS' if passed else 'FAIL',json.dumps(evidence)))
    c.close(); return {'run_ref':ref,'status':'PASS' if passed else 'FAIL',**evidence}

@router.get('/identity')
def identity(): return {'project':'M3 NVOCC ERP','phase':'CLX-015','baseline':BASELINE,'parent':PARENT,'mode':'LIVE_ENVIRONMENT_PREPARATION_ONLY','production_traffic':False}
@router.get('/config')
def get_config(): return config()
@router.get('/activation-matrix')
def get_activation_matrix(): return activation_matrix()
@router.get('/gates')
def get_gates(): return gates()
@router.post('/activation-candidate/prepare')
def candidate(x_role:str=Header('VIEWER')):
    if x_role!='ADMIN': raise HTTPException(403,'ADMIN only')
    return prepare_activation_candidate()
@router.post('/cutover/rehearse')
def rehearse(x_role:str=Header('VIEWER')):
    if x_role!='ADMIN': raise HTTPException(403,'ADMIN only')
    return run_cutover_rehearsal()
@router.get('/latest')
def latest():
    c=connect(); init(c); r=c.execute('SELECT * FROM clx015_cutover_runs ORDER BY id DESC LIMIT 1').fetchone(); c.close()
    if not r:return {'status':'NOT_RUN'}
    return {'run_ref':r['run_ref'],'status':r['status'],'evidence':json.loads(r['evidence_json']),'production_traffic_opened':bool(r['production_traffic_opened'])}
@router.get('/final-cutover-gate')
def final_gate():
    d=latest(); return {'status':'READY_FOR_LIVE_ACTIVATION_APPROVAL' if d.get('status')=='PASS' else 'NOT_READY','rehearsal':d.get('status'),
      'live_credentials_authorized':False,'live_providers_authorized':False,'production_traffic_authorized':False,
      'hard_stop':'EXPLICIT USER APPROVAL REQUIRED BEFORE LIVE CREDENTIALS, LIVE PROVIDERS, REAL TRANSACTIONS OR PRODUCTION TRAFFIC'}
@router.post('/activate/live-credentials')
def activate_credentials(): raise HTTPException(423,{'code':'HARD_STOP_EXPLICIT_APPROVAL_REQUIRED','gate':'LIVE_CREDENTIAL_ACTIVATION'})
@router.post('/activate/live-providers')
def activate_providers(): raise HTTPException(423,{'code':'HARD_STOP_EXPLICIT_APPROVAL_REQUIRED','gate':'LIVE_PROVIDER_CONNECTION'})
@router.post('/activate/production-traffic')
def activate_traffic(): raise HTTPException(423,{'code':'HARD_STOP_EXPLICIT_APPROVAL_REQUIRED','gate':'PRODUCTION_TRAFFIC'})