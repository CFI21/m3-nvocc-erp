from pathlib import Path
import json,datetime,hashlib,uuid
HERE=Path(__file__).resolve().parent
META=json.loads((HERE/'integration_meta.json').read_text())['modules']

def now(): return '2026-09-24T08:00:00Z'
def sha(x): return hashlib.sha256(x.encode()).hexdigest()

def run(conn):
    providers=[
      ('mock-bank','BANK','M3 Sandbox Bank','MOCK','secret://sandbox/mock-bank','ENABLED','HEALTHY',1200,3),
      ('mock-payments','PAYMENT','M3 Sandbox Payments','MOCK','secret://sandbox/mock-payments','ENABLED','HEALTHY',1500,3),
      ('mock-fx','FX','M3 Sandbox FX','MOCK','secret://sandbox/mock-fx','ENABLED','HEALTHY',900,3),
      ('mock-tax','TAX','M3 Sandbox Tax/WHT','MOCK','secret://sandbox/mock-tax','ENABLED','HEALTHY',1000,3),
    ]
    for p in providers:
        conn.execute('INSERT INTO finance_providers(provider_key,provider_type,display_name,endpoint_mode,credential_ref,status,health_status,timeout_ms,max_retries,circuit_state,failure_count,last_health_at) VALUES(?,?,?,?,?,?,?,?,?,\'CLOSED\',0,?)',(*p,now()))
    pids={r['provider_key']:r['id'] for r in conn.execute('SELECT id,provider_key FROM finance_providers')}
    jobs=list(conn.execute('SELECT id,job_ref FROM jobs ORDER BY job_ref'))
    scope={
      '50001':('RTM','NL'),'50002':('DXB','AE'),'50003':('SHA','CN'),'50004':('KHI','PK'),'50005':('RTM','NL')
    }
    provider_cycle=['mock-bank','mock-payments','mock-fx','mock-tax','mock-bank']
    for mod in META:
        key=mod['key']
        for i,j in enumerate(jobs):
            jr=j['job_ref']; office,country=scope[jr]; pk=provider_cycle[i]
            payload={
              'Reference':f'M3-{key.upper()}-{jr}', 'Job Ref':jr, 'Provider':pk,
              'Office':office,'Country':country,'Mode':'SANDBOX','Status':'Ready',
              'Live Connection':'No','Real Money Movement':'No'
            }
            if key=='security-policies': payload.update({'MFA / Re-auth':'Required for sensitive release','CSRF':'Enabled','CORS':'Allowlist','Session':'Synthetic test session'})
            if key=='data-masking-secrets': payload.update({'Credential':'[REDACTED]','Account':'****5001','PII':'MASKED','Secret Source':'secret://sandbox/placeholder'})
            conn.execute('INSERT INTO integration_records(module,external_ref,job_id,provider_id,office_scope,country_scope,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
              (key,payload['Reference'],j['id'],pids[pk],office,country,'Ready',json.dumps(payload),now(),now()))
    # synthetic security sessions; hashes only
    sessions=[
      ('treasury-release-1','TREASURY_MANAGER','sandbox-session-treasury','RTM','NL'),
      ('security-admin-1','SECURITY_ADMIN','sandbox-session-security',None,None),
      ('finance-1','FINANCE','sandbox-session-finance','RTM','NL')
    ]
    for actor,role,token,office,country in sessions:
        conn.execute('INSERT INTO security_sessions(actor_id,role,token_hash,office_scope,country_scope,issued_at,expires_at,reauth_at,active) VALUES(?,?,?,?,?,?,?,?,1)',
                     (actor,role,sha(token),office,country,'2026-09-24T00:00:00Z','2027-09-24T00:00:00Z','2026-09-24T08:00:00Z'))
    # sandbox payment safety cases tied to existing treasury batches and new balanced/unbalanced GL vouchers
    batches=list(conn.execute('SELECT pb.*,tr.job_id,j.job_ref FROM treasury_payment_batches pb JOIN treasury_records tr ON tr.id=pb.record_id JOIN jobs j ON j.id=tr.job_id ORDER BY j.job_ref'))
    for b in batches:
        jr=b['job_ref']; amt=float(b['total_amount']); vno=f'INT-{jr}'
        balanced=(jr!='50004')
        credit=amt
        cur=conn.execute('INSERT INTO gl_vouchers(gl_record_id,voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,approved_by,approved_at,posted_at,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit) VALUES(NULL,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)',
          (vno,'INT','2026-09-24','USD','Posted','SANDBOX_PAYMENT',b['batch_no'],b['job_id'],amt,credit,'sandbox-checker',now(),now(),now(),'sandbox-maker',1.0,'USD',amt,credit))
        vid=cur.lastrowid
        conn.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(vid,1,'2000',amt,0,'Sandbox payment payable',b['job_id']))
        conn.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(vid,2,'1100',0,credit,'Sandbox payment bank',b['job_id']))
        valid=0 if jr=='50002' else 1
        limit=10000.0
        conn.execute('INSERT INTO sandbox_payment_requests(payment_ref,job_id,batch_id,voucher_id,beneficiary_name,beneficiary_account_masked,beneficiary_validated,duplicate_fingerprint,amount,currency,payment_limit,payment_date,approval_status,gl_control_status,status,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)',
          (f'M3PAY-{jr}',b['job_id'],b['id'],vid,f'Synthetic Beneficiary {jr}',f'****{jr[-4:]}',valid,sha(f'{b["batch_no"]}|{jr}|{amt}'),amt,'USD',limit,'2026-09-24','APPROVED','BALANCED' if balanced else 'UNBALANCED','READY'))
    # representative provider events
    event_seed=[
      ('EVT-BANK-001','mock-bank','BANK_STATEMENT','DELIVERED',1),
      ('EVT-PAY-001','mock-payments','PAYMENT_FILE','DELIVERED',1),
      ('EVT-FX-001','mock-fx','FX_RATE','DELIVERED',1),
      ('EVT-TAX-001','mock-tax','TAX_WHT','RETRY',1),
      ('EVT-PAY-002','mock-payments','PAYMENT_CALLBACK','DEAD_LETTER',3),
    ]
    for ref,pk,typ,status,attempts in event_seed:
        req={'event_ref':ref,'account':'****5001','amount':'[MASKED]','token':'[REDACTED]'}
        cur=conn.execute('INSERT INTO integration_events(event_ref,provider_id,event_type,idempotency_key,request_hash,request_redacted,response_redacted,status,attempt_count,max_attempts,next_retry_at,correlation_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (ref,pids[pk],typ,'seed-'+ref,sha(json.dumps(req,sort_keys=True)),json.dumps(req),json.dumps({'mode':'SANDBOX'}),status,attempts,3,'2026-09-24T08:05:00Z' if status=='RETRY' else None,str(uuid.uuid4()),now(),now()))
        for a in range(1,attempts+1):
            conn.execute('INSERT INTO integration_attempts(event_id,attempt_no,result,latency_ms,detail_redacted,ts) VALUES(?,?,?,?,?,?)',(cur.lastrowid,a,'FAILURE' if status!='DELIVERED' else 'SUCCESS',125,json.dumps({'detail':'synthetic'}),now()))
    # seed bank import with matched, unmatched and failed lines
    cur=conn.execute('INSERT INTO bank_import_batches(import_ref,bank_account_ref,source_name,status,line_count,matched_count,unmatched_count,failed_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
      ('BIMP-SEED-001','BANK-USD-01','synthetic-seed.csv','PARTIAL',4,2,1,1,now()))
    bid=cur.lastrowid
    lines=[
      ('L1','2026-09-23',4650,'USD','Customer receipt 50001','MATCHED','RCPT-50001',None),
      ('L2','2026-09-23',-3910,'USD','Carrier payment 50001','MATCHED','PAY-50001',None),
      ('L3','2026-09-23',125.55,'USD','Unknown incoming transfer','UNMATCHED',None,'NO_EXACT_MATCH'),
      ('L4','2026-09-23',0,'USD','Malformed zero line','FAILED',None,'ZERO_AMOUNT')
    ]
    for x in lines:
        conn.execute('INSERT INTO bank_import_lines(batch_id,line_ref,txn_date,amount,currency,description,status,matched_source_ref,failure_reason) VALUES(?,?,?,?,?,?,?,?,?)',(bid,*x))
    conn.execute('INSERT INTO security_audit_events(event_id,ts,actor_id,actor_role,action,resource,outcome,correlation_id,detail_redacted) VALUES(?,?,?,?,?,?,?,?,?)',
      (str(uuid.uuid4()),now(),'system','SYSTEM','CLX008_SEED','integration-sandbox','PASS',str(uuid.uuid4()),json.dumps({'secret':'[REDACTED]','mode':'SANDBOX'})))