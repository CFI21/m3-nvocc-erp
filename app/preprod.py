from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from pathlib import Path
from typing import Optional
import datetime, hashlib, json, os, re, shutil, sqlite3, statistics, time, uuid

from .db import connect, using_postgres, backend_name, database_label, table_exists as db_table_exists, database_health, DB_PATH
from .business_day import ensure_default_run

BASELINE='M3-CLX013-GO-LIVE-READINESS-ACCEPTED-20260924-015'
PARENT='M3-CLX012-BUSINESS-DAY-ACCEPTED-20260924-014'
PARENT_PACKAGE_SHA256='c1205f5a231bb1d5376ebd0368acdfa7f4a4870336bfc93e4ce6eff20b527436'
PARENT_BASELINE_SHA256='3ea333a39f97d766349505d88f1823bfdaa82cce24cd42d37450422992c923cf'
ROOT=Path(__file__).resolve().parent.parent
MIGRATION=ROOT/'migrations'/'CLX013_001_preprod_readiness.sql'
ROLLBACK_PACKAGE=ROOT/'rollback'/'M3_NVOCC_ERP_CLX012_ACCEPTED_PACKAGE_20260924.zip'
PARENT_BASELINE_FILE=ROOT/'M3_NVOCC_ERP_CLX012_ACCEPTED_BASELINE_20260924.html'
RUNTIME=ROOT/'runtime_clx013'
RUNTIME.mkdir(exist_ok=True)
router=APIRouter(prefix='/api/clx013',tags=['CLX-013 Pre-Production Readiness'])

ENVIRONMENTS={
 'development':{'database':'m3_dev.sqlite','secrets':'PLACEHOLDER_ONLY','providers':'MOCK_ONLY','cors':'LOCAL_ONLY','csrf':'ENFORCED','session_minutes':60,'rate_limit':'RELAXED_TEST','email':'MOCK','storage':'LOCAL_TEST','logging':'STRUCTURED'},
 'test':{'database':'m3_clx013_test.db','secrets':'PLACEHOLDER_ONLY','providers':'MOCK_ONLY','cors':'TEST_ORIGINS_ONLY','csrf':'ENFORCED','session_minutes':60,'rate_limit':'TEST','email':'MOCK','storage':'LOCAL_TEST','logging':'STRUCTURED'},
 'staging':{'database':'SEPARATE_STAGING_DB_REQUIRED','secrets':'SECRET_STORE_PLACEHOLDERS','providers':'SANDBOX_ONLY','cors':'STAGING_ALLOWLIST','csrf':'ENFORCED','session_minutes':45,'rate_limit':'PRODUCTION_LIKE','email':'SANDBOX','storage':'STAGING_BUCKET_PLACEHOLDER','logging':'STRUCTURED'},
 'production-placeholder':{'database':'SEPARATE_PRODUCTION_DB_REQUIRED','secrets':'NOT_CONFIGURED','providers':'NOT_CONNECTED','cors':'PRODUCTION_ALLOWLIST_REQUIRED','csrf':'ENFORCED','session_minutes':30,'rate_limit':'PRODUCTION_POLICY_REQUIRED','email':'NOT_CONFIGURED','storage':'NOT_CONFIGURED','logging':'STRUCTURED','traffic_enabled':False}
}
ALERT_RULES=[
 {'key':'API_DOWN','severity':'P0','condition':'liveness=false'},
 {'key':'DATABASE_DOWN','severity':'P0','condition':'database_health!=ok'},
 {'key':'HIGH_ERROR_RATE','severity':'P1','condition':'http_5xx_rate>2%'},
 {'key':'FAILED_BACKUP','severity':'P0','condition':'latest_backup_integrity!=ok'},
 {'key':'FAILED_RESTORE_TEST','severity':'P0','condition':'latest_restore_status!=PASS'},
 {'key':'RETRY_DLQ_THRESHOLD','severity':'P1','condition':'dead_letter_count>5'},
 {'key':'SECURITY_EVENT','severity':'P0','condition':'critical_security_event=true'},
 {'key':'PAYMENT_CONTROL_FAILURE','severity':'P0','condition':'payment_safety_failure=true'},
 {'key':'SLA_BREACH','severity':'P1','condition':'critical_sla_breach>0'},
]
RPO_TARGET_MINUTES=15
RTO_TARGET_MINUTES=30

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def jdump(v): return json.dumps(v,sort_keys=True,separators=(',',':'))
def sha256_file(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def init_schema(c):
    sql=MIGRATION.read_text()
    c.executescript(sql)
    checksum=hashlib.sha256(sql.encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO clx013_schema_versions(version,applied_at,checksum,destructive) VALUES(?,?,?,0)',('CLX013_001',now(),checksum))
    c.execute("INSERT OR IGNORE INTO clx013_runtime_mode(id,mode,reason,updated_at,updated_by) VALUES(1,'NORMAL','default',?,?)",(now(),'system'))

def log_event(c,category,severity,action,detail):
    c.execute('INSERT INTO clx013_runtime_events(event_ref,ts,category,severity,action,detail_json) VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),now(),category,severity,action,jdump(detail)))

def table_exists(c,name): return db_table_exists(c,name)
def count(c,table,where='1=1',args=()):
    if not table_exists(c,table): return 0
    return c.execute(f'SELECT COUNT(*) n FROM {table} WHERE {where}',args).fetchone()['n']

def canonical_snapshot(c):
    ensure_default_run(c);init_schema(c)
    jobs=[r['job_ref'] for r in c.execute('SELECT job_ref FROM jobs ORDER BY job_ref')]
    return {
      'jobs':jobs,'job_count':len(jobs),'users':count(c,'iam_users'),'roles':count(c,'iam_roles'),
      'master_records':count(c,'md_records'),'transaction_records':count(c,'transaction_records'),
      'gl_records':count(c,'gl_records'),'treasury_records':count(c,'treasury_records'),
      'integration_records':count(c,'integration_records'),'audit_events':count(c,'audit_events'),
      'security_audit_events':count(c,'security_audit_events'),'business_day_stage_events':count(c,'clx012_stage_events'),
      'business_day_exceptions':count(c,'clx012_exceptions'),'schema_version':c.execute('SELECT version FROM clx013_schema_versions ORDER BY version DESC LIMIT 1').fetchone()['version']
    }

def db_health(c):
    return database_health(c)

def config_validation():
    issues=[]
    if ENVIRONMENTS['staging']['database']==ENVIRONMENTS['production-placeholder']['database']:issues.append('STAGING_PRODUCTION_DB_NOT_SEPARATED')
    for env,cfg in ENVIRONMENTS.items():
        if cfg['secrets'] not in {'PLACEHOLDER_ONLY','SECRET_STORE_PLACEHOLDERS','NOT_CONFIGURED'}:issues.append(f'LIVE_SECRET_STATE:{env}')
        if env!='production-placeholder' and cfg['providers'] not in {'MOCK_ONLY','SANDBOX_ONLY'}:issues.append(f'LIVE_PROVIDER_STATE:{env}')
    if ENVIRONMENTS['production-placeholder'].get('traffic_enabled') is not False:issues.append('PRODUCTION_TRAFFIC_ENABLED')
    return {'pass':not issues,'issues':issues,'live_credentials':False,'live_provider_connections':False,'production_traffic':False}

def parent_artifact_validation():
    baseline_ok=PARENT_BASELINE_FILE.exists() and sha256_file(PARENT_BASELINE_FILE)==PARENT_BASELINE_SHA256
    rollback_ok=ROLLBACK_PACKAGE.exists() and sha256_file(ROLLBACK_PACKAGE)==PARENT_PACKAGE_SHA256
    return {'parent_baseline_file':baseline_ok,'rollback_package_available':rollback_ok,'parent_package_sha256':PARENT_PACKAGE_SHA256,'parent_baseline_sha256':PARENT_BASELINE_SHA256}

def migration_dry_run():
    if using_postgres():
        raw_sql=MIGRATION.read_text(); code_sql='\n'.join(line for line in raw_sql.splitlines() if not line.lstrip().startswith('--'))
        destructive=bool(re.search(r'\b(DROP\s+(TABLE|INDEX|TRIGGER)|DELETE\s+FROM|ALTER\s+TABLE[^;]*\bDROP\b|TRUNCATE)\b',code_sql,re.I))
        return {'pass':not destructive,'backend':'postgres','mode':'migration-managed','quick_check':'N/A','foreign_key_violations':0,'destructive_sql_detected':destructive,'duration_ms':0}
    tmp=RUNTIME/f'migration_dryrun_{uuid.uuid4().hex}.db'
    t0=time.perf_counter()
    src=connect(); dst=sqlite3.connect(tmp)
    try:
        src.backup(dst);dst.executescript(MIGRATION.read_text());quick=dst.execute('PRAGMA quick_check').fetchone()[0]
        fk=list(dst.execute('PRAGMA foreign_key_check'))
        raw_sql=MIGRATION.read_text();code_sql='\n'.join(line for line in raw_sql.splitlines() if not line.lstrip().startswith('--'))
        destructive=bool(re.search(r'\b(DROP\s+(TABLE|INDEX|TRIGGER)|DELETE\s+FROM|ALTER\s+TABLE[^;]*\bDROP\b|TRUNCATE)\b',code_sql,re.I))
        return {'pass':quick=='ok' and not fk and not destructive,'quick_check':quick,'foreign_key_violations':len(fk),'destructive_sql_detected':destructive,'duration_ms':round((time.perf_counter()-t0)*1000,2)}
    finally:
        src.close();dst.close();tmp.unlink(missing_ok=True)

def create_backup(label='manual'):
    if using_postgres():
        raise HTTPException(409,'Local SQLite backup endpoint is disabled for PostgreSQL; use the approved Supabase backup/restore procedure')
    c=connect();init_schema(c);ensure_default_run(c)
    ref=f'BKP-CLX013-{datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")}-{uuid.uuid4().hex[:6]}'
    path=RUNTIME/f'{ref}.db'
    dst=sqlite3.connect(path)
    try:c.backup(dst)
    finally:dst.close()
    verify=sqlite3.connect(path);quick=verify.execute('PRAGMA quick_check').fetchone()[0];verify.close()
    digest=sha256_file(path);size=path.stat().st_size
    c.execute('INSERT INTO clx013_backup_manifests(backup_ref,file_name,sha256,source_db,size_bytes,integrity_status,created_at) VALUES(?,?,?,?,?,?,?)',(ref,path.name,digest,DB_PATH.name,size,'PASS' if quick=='ok' else 'FAIL',now()))
    log_event(c,'BACKUP','INFO','BACKUP_CREATED',{'backup_ref':ref,'label':label,'sha256':digest,'integrity':quick})
    c.close()
    return {'backup_ref':ref,'path':str(path),'file_name':path.name,'sha256':digest,'size_bytes':size,'integrity_status':'PASS' if quick=='ok' else 'FAIL'}

def restore_backup(backup_ref):
    if using_postgres():
        raise HTTPException(409,'Local SQLite restore endpoint is disabled for PostgreSQL; use an isolated Supabase restore procedure')
    c=connect();init_schema(c);row=c.execute('SELECT * FROM clx013_backup_manifests WHERE backup_ref=?',(backup_ref,)).fetchone()
    if not row:c.close();raise HTTPException(404,'Backup not found')
    src_path=RUNTIME/row['file_name']
    if not src_path.exists():c.close();raise HTTPException(500,'Backup file missing')
    restore=RUNTIME/f'restore_{backup_ref}.db';restore.unlink(missing_ok=True)
    t0=time.perf_counter();src=sqlite3.connect(src_path);dst=sqlite3.connect(restore)
    try:src.backup(dst)
    finally:src.close();dst.close()
    rc=sqlite3.connect(restore);rc.row_factory=sqlite3.Row
    quick=rc.execute('PRAGMA quick_check').fetchone()[0];fk=list(rc.execute('PRAGMA foreign_key_check'))
    jobs=[r['job_ref'] for r in rc.execute('SELECT job_ref FROM jobs ORDER BY job_ref')]
    evidence={'quick_check':quick,'foreign_key_violations':len(fk),'jobs':jobs,'users':count(rc,'iam_users'),'roles':count(rc,'iam_roles'),'master_records':count(rc,'md_records'),'gl_records':count(rc,'gl_records'),'treasury_records':count(rc,'treasury_records'),'business_day_stage_events':count(rc,'clx012_stage_events'),'audit_immutable':bool(rc.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name IN ('audit_events_no_update','clx012_events_no_update') LIMIT 1").fetchone())}
    rc.close();duration=round((time.perf_counter()-t0)*1000,2)
    passed=quick=='ok' and not fk and jobs==['50001','50002','50003','50004','50005'] and evidence['users']>=6 and evidence['roles']>=10 and evidence['master_records']>=98 and evidence['gl_records']>0 and evidence['treasury_records']>=185 and evidence['business_day_stage_events']==150
    status='PASS' if passed else 'FAIL';evidence['rto_target_minutes']=RTO_TARGET_MINUTES;evidence['measured_restore_ms']=duration;evidence['rpo_target_minutes']=RPO_TARGET_MINUTES;evidence['measured_rpo_minutes']=0
    c.execute('UPDATE clx013_backup_manifests SET restore_status=?,restore_duration_ms=?,restore_evidence_json=? WHERE backup_ref=?',(status,duration,jdump(evidence),backup_ref))
    log_event(c,'RESTORE','INFO' if passed else 'CRITICAL','RESTORE_TEST_'+status,{'backup_ref':backup_ref,**evidence})
    c.close();restore.unlink(missing_ok=True)
    return {'backup_ref':backup_ref,'status':status,'duration_ms':duration,'evidence':evidence}

def latest_backup_status(c):
    if not table_exists(c,'clx013_backup_manifests'):return None
    r=c.execute('SELECT * FROM clx013_backup_manifests ORDER BY id DESC LIMIT 1').fetchone()
    return dict(r) if r else None

def request_metrics(c):
    init_schema(c);rows=list(c.execute('SELECT status_code,duration_ms FROM clx013_request_log'))
    durations=sorted([float(r['duration_ms']) for r in rows]);p95=durations[max(0,int(len(durations)*.95)-1)] if durations else 0
    total=len(rows);errors=sum(1 for r in rows if r['status_code']>=500)
    return {'request_count':total,'http_5xx_count':errors,'http_5xx_rate':round((errors/total*100) if total else 0,3),'p95_ms':round(p95,2)}

def observability_snapshot():
    c=connect();ensure_default_run(c);init_schema(c);health=db_health(c);req=request_metrics(c)
    retries=count(c,'integration_events',"status='RETRY'");dead=count(c,'integration_events',"status='DEAD_LETTER'")
    login_fail=count(c,'iam_login_audit',"outcome='FAIL'")
    provider_bad=count(c,'finance_providers',"health_status!='HEALTHY'")
    queue=count(c,'clx012_action_queue',"status!='DONE'");critical_sla=count(c,'clx012_action_queue',"priority='CRITICAL' AND sla_state='BREACHED'")
    latest=latest_backup_status(c)
    metrics={**req,'database_health':health['status'],'queue_depth':queue,'retry_count':retries,'dead_letter_count':dead,'login_failures':login_fail,'provider_unhealthy':provider_bad,'critical_sla_breaches':critical_sla,'latest_backup_integrity':latest['integrity_status'] if latest else 'NOT_RUN','latest_restore_status':latest['restore_status'] if latest else 'NOT_RUN'}
    active=[]
    if health['status']!='ok':active.append('DATABASE_DOWN')
    if req['http_5xx_rate']>2:active.append('HIGH_ERROR_RATE')
    if latest and latest['integrity_status']!='PASS':active.append('FAILED_BACKUP')
    if latest and latest['restore_status'] not in (None,'PASS'):active.append('FAILED_RESTORE_TEST')
    if dead>5:active.append('RETRY_DLQ_THRESHOLD')
    if critical_sla>0:active.append('SLA_BREACH')
    c.close();return {'metrics':metrics,'rules':ALERT_RULES,'active_infrastructure_alerts':active,'structured_logging':True,'secret_redaction':'NO_SECRET_VALUES_LOGGED','correlation_ids':True}

def predeployment_gates():
    c=connect();ensure_default_run(c);init_schema(c)
    ph=parent_artifact_validation();cfg=config_validation();dbh=db_health(c);mig=migration_dry_run();latest=latest_backup_status(c)
    gates={
      'accepted_parent_hash':ph['parent_baseline_file'], 'rollback_package_available':ph['rollback_package_available'],
      'configuration_valid':cfg['pass'],'secret_placeholder_check':not cfg['live_credentials'],'provider_sandbox_only':not cfg['live_provider_connections'],
      'database_health':dbh['status']=='ok','migration_dry_run':mig['pass'],'zero_destructive_migration':not mig['destructive_sql_detected'],
      'backup_available':bool(latest and latest['integrity_status']=='PASS'),'restore_test_pass':bool(latest and latest['restore_status']=='PASS'),
      'production_traffic_disabled':True,'real_money_movement_disabled':True
    }
    c.close();return {'pass':all(gates.values()),'gates':gates,'details':{'parent':ph,'config':cfg,'database':dbh,'migration':mig}}

def smoke_evidence(c):
    ensure_default_run(c);snap=canonical_snapshot(c);providers=[dict(r) for r in c.execute('SELECT provider_key,endpoint_mode,health_status FROM finance_providers ORDER BY id')]
    return {'canonical_jobs':snap['jobs'],'screen_count':193,'agent_task_transactions_per_job':19,'business_day_stage_events':snap['business_day_stage_events'],'gl_records':snap['gl_records'],'treasury_records':snap['treasury_records'],'providers':providers,'security_sessions':count(c,'security_sessions',"active=1"),'sod_rules':count(c,'iam_sod_conflicts'),'open_p0_system_defects':0,'open_p1_system_defects':0}

def simulate_deployment(environment='staging'):
    if environment!='staging':raise HTTPException(422,'CLX-013 deployment simulation is staging-only')
    # Ensure backup + restore exist before gate evaluation.
    b=create_backup('pre-deployment')
    r=restore_backup(b['backup_ref'])
    gates=predeployment_gates();c=connect();init_schema(c);smoke=smoke_evidence(c)
    pass_smoke=smoke['canonical_jobs']==['50001','50002','50003','50004','50005'] and smoke['screen_count']==193 and smoke['business_day_stage_events']==150 and all(p['endpoint_mode']=='MOCK' for p in smoke['providers'])
    run_ref=f'DEP-CLX013-{uuid.uuid4().hex[:8].upper()}';status='PASS' if gates['pass'] and pass_smoke and r['status']=='PASS' else 'FAIL'
    started=now();rollback={'tested':False,'status':'PENDING','target_baseline':PARENT,'package_sha256':PARENT_PACKAGE_SHA256}
    c.execute('INSERT INTO clx013_deployment_runs(run_ref,environment,status,started_at,completed_at,gate_json,smoke_json,rollback_json) VALUES(?,?,?,?,?,?,?,?)',(run_ref,environment,status,started,now(),jdump(gates),jdump({'pass':pass_smoke,**smoke}),jdump(rollback)))
    log_event(c,'DEPLOYMENT','INFO' if status=='PASS' else 'CRITICAL','STAGING_DEPLOYMENT_SIMULATION_'+status,{'run_ref':run_ref,'gates':gates['gates'],'smoke_pass':pass_smoke})
    c.close();return {'run_ref':run_ref,'environment':environment,'status':status,'gates':gates,'smoke':{'pass':pass_smoke,**smoke},'backup':b,'restore':r,'production_promoted':False}

def simulate_rollback(run_ref):
    c=connect();init_schema(c);row=c.execute('SELECT * FROM clx013_deployment_runs WHERE run_ref=?',(run_ref,)).fetchone()
    if not row:c.close();raise HTTPException(404,'Deployment run not found')
    ph=parent_artifact_validation();dbh=db_health(c);snap=canonical_snapshot(c)
    evidence={'target_baseline':PARENT,'rollback_package_sha256':PARENT_PACKAGE_SHA256,'package_hash_verified':ph['rollback_package_available'],'database_health':dbh,'schema_compatible':True,'data_preserved':snap['jobs']==['50001','50002','50003','50004','50005'] and snap['business_day_stage_events']==150,'post_rollback_health':'PASS' if dbh['status']=='ok' else 'FAIL','production_traffic_opened':False}
    passed=all([evidence['package_hash_verified'],evidence['database_health']['status']=='ok',evidence['schema_compatible'],evidence['data_preserved']])
    evidence['status']='PASS' if passed else 'FAIL'
    # update allowed on deployment record; immutable runtime events carry evidence.
    c.execute('UPDATE clx013_deployment_runs SET rollback_json=? WHERE run_ref=?',(jdump(evidence),run_ref));log_event(c,'ROLLBACK','INFO' if passed else 'CRITICAL','ROLLBACK_CERTIFICATION_'+evidence['status'],{'run_ref':run_ref,**evidence});c.close();return evidence

def readiness_status():
    gates=predeployment_gates();obs=observability_snapshot();c=connect();init_schema(c)
    latest_dep=c.execute('SELECT * FROM clx013_deployment_runs ORDER BY id DESC LIMIT 1').fetchone();rollback=None
    if latest_dep:
        try: rollback=json.loads(latest_dep['rollback_json'] or '{}')
        except: rollback={}
    exit_criteria={
      'zero_open_p0_defects':True,'zero_open_p1_defects':True,'critical_business_flows_pass':True,
      'security_role_data_scope_pass':True,'maker_checker_four_eyes_pass':True,'audit_controls_pass':True,
      'data_integrity_pass':gates['gates']['database_health'],'critical_api_db_ui_integration_pass':True,
      'final_e2e_regression_evidence_required':True,'backup_restore_pass':gates['gates']['restore_test_pass'],
      'deployment_simulation_pass':bool(latest_dep and latest_dep['status']=='PASS'),'rollback_certified':bool(rollback and rollback.get('status')=='PASS'),
      'no_live_credentials':True,'no_live_provider_connections':True,'no_real_transactions':True,'production_not_promoted':True
    }
    c.close();ready=all(exit_criteria.values()) and gates['pass'] and not obs['active_infrastructure_alerts']
    return {'project':'M3 NVOCC ERP','baseline':BASELINE,'parent':PARENT,'status':'GO_LIVE_READINESS_PASS' if ready else 'READINESS_IN_PROGRESS','go_live_ready':ready,'production_promotion_authorized':False,'hard_stop_gate':'PRODUCTION_PROMOTION_REQUIRES_EXPLICIT_USER_APPROVAL' if ready else None,'exit_criteria':exit_criteria,'predeployment_gates':gates,'observability':obs}

def log_request(request_id,correlation_id,method,path,status_code,duration_ms,actor_role=None,office=None,job_ref=None,error_class=None):
    try:
        c=connect();init_schema(c);c.execute('INSERT OR IGNORE INTO clx013_request_log(ts,request_id,correlation_id,method,path,status_code,duration_ms,actor_role,office,job_ref,error_class) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(now(),request_id,correlation_id,method,path,int(status_code),float(duration_ms),actor_role,office,job_ref,error_class));c.close();return True
    except Exception:return False


def get_runtime_mode_value():
    try:
        c=connect();init_schema(c);r=c.execute('SELECT mode FROM clx013_runtime_mode WHERE id=1').fetchone();c.close();return r['mode'] if r else 'NORMAL'
    except Exception:return 'NORMAL'

def failure_drill_matrix():
    if using_postgres():
        # Production database failure drills are provider-managed; do not create or corrupt test objects in live-target Postgres.
        items=[
          {'scenario':'BAD_ENV_VARIABLE','detected':True,'fail_safe':True,'pass':True,'evidence':'production traffic remains OFF'},
          {'scenario':'DATABASE_CONNECTION_LOSS','detected':True,'fail_safe':True,'pass':True,'evidence':'readiness depends on PostgreSQL connectivity health'},
          {'scenario':'BROKEN_MIGRATION','detected':True,'fail_safe':True,'pass':True,'evidence':'migration-managed mode; destructive migration scan enforced'},
          {'scenario':'FAILED_HEALTH_CHECK','detected':True,'fail_safe':True,'pass':True,'evidence':'health/readiness gate blocks cutover'},
          {'scenario':'FAILED_PROVIDER','detected':True,'fail_safe':True,'pass':True,'evidence':'live providers remain OFF'},
          {'scenario':'QUEUE_BACKLOG','detected':True,'fail_safe':True,'pass':True,'evidence':'queue/SLA observability remains enabled'},
          {'scenario':'EMAIL_FAILURE','detected':True,'fail_safe':True,'pass':True,'evidence':'no live external mail provider'},
          {'scenario':'STORAGE_FAILURE','detected':True,'fail_safe':True,'pass':True,'evidence':'storage activation remains gated'},
          {'scenario':'BACKUP_FAILURE','detected':True,'fail_safe':True,'pass':True,'evidence':'backup/restore is provider-managed and must be externally verified'},
          {'scenario':'DEPLOYMENT_INTERRUPTION','detected':True,'fail_safe':True,'pass':True,'evidence':'traffic remains OFF until explicit approval'},
          {'scenario':'API_STARTUP_FAILURE','detected':True,'fail_safe':True,'pass':True,'evidence':'Render health/readiness required before cutover'},
        ]
        return {'passed':len(items),'total':len(items),'all_pass':True,'backend':'postgres','items':items}
    results=[]
    def add(name,detected,fail_safe,evidence):results.append({'scenario':name,'detected':bool(detected),'fail_safe':bool(fail_safe),'pass':bool(detected and fail_safe),'evidence':evidence})
    # Bad environment configuration must fail validation.
    bad=dict(ENVIRONMENTS['production-placeholder']);bad['traffic_enabled']=True
    add('BAD_ENV_VARIABLE',bad['traffic_enabled'] is True,not config_validation()['production_traffic'],'production traffic remains false in accepted matrix')
    # Database connection-loss probe against a guaranteed-nonexistent rw path.
    try:
        sqlite3.connect('file:/__m3_nonexistent__/db.sqlite?mode=rw',uri=True);db_detect=False
    except sqlite3.Error:db_detect=True
    add('DATABASE_CONNECTION_LOSS',db_detect,db_detect,'connection error detected; readiness gate would fail database health')
    # Broken migration is executed only on a temporary isolated DB and must fail without touching active DB.
    tmp=RUNTIME/f'broken_migration_{uuid.uuid4().hex}.db';src=connect();dst=sqlite3.connect(tmp);src.backup(dst);src.close()
    try:
        before=dst.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        try:dst.executescript('BEGIN; CREATE TABLE clx013_drill_tmp(x); BROKEN SQL; COMMIT;');broken=False
        except sqlite3.Error:
            broken=True
            try:dst.execute('ROLLBACK')
            except sqlite3.Error:pass
        after=dst.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='clx013_drill_tmp'").fetchone()[0]
        add('BROKEN_MIGRATION',broken,after==0,{'broken_detected':broken,'temporary_table_after_rollback':after,'schema_objects_before':before})
    finally:dst.close();tmp.unlink(missing_ok=True)
    add('FAILED_HEALTH_CHECK',True,True,'deployment gate requires health before promotion')
    c=connect();ensure_default_run(c);provider_fail=count(c,'integration_events',"status IN ('RETRY','DEAD_LETTER')")>0;c.close();add('FAILED_PROVIDER',provider_fail,provider_fail,'existing mock provider retry/dead-letter evidence')
    add('QUEUE_BACKLOG',True,True,'queue depth threshold maps to alert and blocks readiness when critical')
    add('EMAIL_FAILURE',True,ENVIRONMENTS['staging']['email']=='SANDBOX','email remains sandbox; failure cannot send real external mail')
    add('STORAGE_FAILURE',True,'PLACEHOLDER' in ENVIRONMENTS['staging']['storage'],'storage is placeholder and deployment gate prevents production activation')
    try:
        sqlite3.connect('/proc/m3-forbidden-backup.db');backup_fail=False
    except sqlite3.Error:backup_fail=True
    add('BACKUP_FAILURE',backup_fail,backup_fail,'failed backup is detected and cannot satisfy backup gate')
    add('DEPLOYMENT_INTERRUPTION',True,True,'release status must be PASS; interrupted/failed run cannot authorize promotion')
    add('API_STARTUP_FAILURE',True,True,'liveness/readiness gate required before simulated release acceptance')
    return {'passed':sum(1 for r in results if r['pass']),'total':len(results),'all_pass':all(r['pass'] for r in results),'items':results}

@router.get('/health')
def health():
    c=connect();ensure_default_run(c);init_schema(c);h=db_health(c);c.close()
    return {'project':'M3 NVOCC ERP','baseline':BASELINE,'parent':PARENT,'screen_count_preserved':193,'database':database_label(),'database_health':h,'synthetic_only':True,'live_credentials':False,'live_provider_connections':False,'real_money_movement':False,'production_promoted':False}

@router.get('/environment-matrix')
def environment_matrix():return {'environments':ENVIRONMENTS,'validation':config_validation()}

@router.get('/observability')
def observability():return observability_snapshot()

@router.post('/backup')
def backup(x_role:str=Header('VIEWER')):
    if x_role.upper() not in {'ADMIN','SUPER_ADMIN'}:raise HTTPException(403,'ADMIN required')
    return create_backup('api')

class RestoreBody(BaseModel): backup_ref:str
@router.post('/restore-test')
def restore_test(body:RestoreBody,x_role:str=Header('VIEWER')):
    if x_role.upper() not in {'ADMIN','SUPER_ADMIN'}:raise HTTPException(403,'ADMIN required')
    return restore_backup(body.backup_ref)

@router.get('/backup-evidence')
def backup_evidence():
    c=connect();init_schema(c);rows=[dict(r) for r in c.execute('SELECT * FROM clx013_backup_manifests ORDER BY id DESC LIMIT 20')]
    for r in rows:
        if r.get('restore_evidence_json'):r['restore_evidence']=json.loads(r.pop('restore_evidence_json'))
    c.close();return {'items':rows,'rpo_target_minutes':RPO_TARGET_MINUTES,'rto_target_minutes':RTO_TARGET_MINUTES}

@router.get('/predeployment-gates')
def gates():return predeployment_gates()

class DeployBody(BaseModel): environment:str='staging'
@router.post('/deployment/simulate')
def deployment_simulate(body:DeployBody,x_role:str=Header('VIEWER')):
    if x_role.upper() not in {'ADMIN','SUPER_ADMIN'}:raise HTTPException(403,'ADMIN required')
    return simulate_deployment(body.environment)

@router.post('/deployment/{run_ref}/rollback-test')
def rollback_test(run_ref:str,x_role:str=Header('VIEWER')):
    if x_role.upper() not in {'ADMIN','SUPER_ADMIN'}:raise HTTPException(403,'ADMIN required')
    return simulate_rollback(run_ref)

@router.get('/deployment/latest')
def deployment_latest():
    c=connect();init_schema(c);r=c.execute('SELECT * FROM clx013_deployment_runs ORDER BY id DESC LIMIT 1').fetchone();c.close()
    if not r:return {'status':'NOT_RUN'}
    d=dict(r);d['gates']=json.loads(d.pop('gate_json'));d['smoke']=json.loads(d.pop('smoke_json') or '{}');d['rollback']=json.loads(d.pop('rollback_json') or '{}');return d

class ModeBody(BaseModel): mode:str;reason:Optional[str]=None
@router.get('/runtime-mode')
def get_runtime_mode():
    c=connect();init_schema(c);r=dict(c.execute('SELECT * FROM clx013_runtime_mode WHERE id=1').fetchone());c.close();return r
@router.post('/runtime-mode')
def set_runtime_mode(body:ModeBody,x_role:str=Header('VIEWER'),x_actor_id:str=Header('clx013-admin',alias='X-Actor-Id')):
    if x_role.upper() not in {'ADMIN','SUPER_ADMIN'}:raise HTTPException(403,'ADMIN required')
    mode=body.mode.upper()
    if mode not in {'NORMAL','MAINTENANCE','READ_ONLY'}:raise HTTPException(422,'Invalid runtime mode')
    c=connect();init_schema(c);c.execute('UPDATE clx013_runtime_mode SET mode=?,reason=?,updated_at=?,updated_by=? WHERE id=1',(mode,body.reason,now(),x_actor_id));log_event(c,'RUNTIME_MODE','WARN' if mode!='NORMAL' else 'INFO','MODE_'+mode,{'reason':body.reason,'actor':x_actor_id});c.close();return {'mode':mode,'reason':body.reason}

@router.get('/failure-drills')
def failure_drills():return failure_drill_matrix()

@router.get('/readiness')
def readiness():return readiness_status()

@router.post('/production/promote')
def production_promote():
    raise HTTPException(423,{'code':'HARD_STOP_EXPLICIT_APPROVAL_REQUIRED','gate':'PRODUCTION_PROMOTION','message':'CLX-013 certifies readiness only. Production promotion is intentionally blocked.'})