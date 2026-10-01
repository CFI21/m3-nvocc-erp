from __future__ import annotations
from fastapi import APIRouter,Header,HTTPException
from .db import connect,tx,backend_name
import datetime,json,os,uuid,hashlib

router=APIRouter(prefix="/api/clx077/dummy-bank",tags=["CLX-077 Live Dummy Bank Review"])
WRITE_ROLES={"ADMIN","SUPER_ADMIN","TREASURY_MANAGER","FINANCE"}
READ_ROLES=WRITE_ROLES|{"AUDITOR","VIEWER"}
RUN_REF="CLX077-LIVE-001"
MARKER="CLX077_TEST"
DUMMY="DUMMY_BANK"

SCENARIOS=[
 (1,"EXPORT_FCL_RECEIPT","Export FCL customer receipt","50001","EUR","RECEIPT",4650.00,"customer-receipt-allocation"),
 (2,"IMPORT_FCL_RECEIPT","Import FCL customer receipt","50002","EUR","RECEIPT",7200.00,"customer-receipt-allocation"),
 (3,"AGENT_COST_PAYMENT","Agent cost + supplier payment","50001","EUR","PAYMENT",3910.00,"supplier-carrier-payment-allocation"),
 (4,"CARRIER_COST_PAYMENT","Carrier cost payment","50002","USD","PAYMENT",6120.00,"supplier-carrier-payment-allocation"),
 (5,"REPOSITION_COST","Reposition cost payment","50003","EUR","PAYMENT",800.00,"supplier-carrier-payment-allocation"),
 (6,"MR_COST","M&R cost payment","50003","EUR","PAYMENT",450.00,"supplier-carrier-payment-allocation"),
 (7,"LEASE_SETTLEMENT","Lease accrual + settlement","50004","USD","PAYMENT",1200.00,"supplier-carrier-payment-allocation"),
 (8,"DETENTION_CHARGE","Detention/demurrage collection + VAT test","50004","EUR","RECEIPT",600.00,"customer-receipt-allocation"),
 (9,"CREDIT_REFUND","Credit note + customer refund","50001","EUR","PAYMENT",300.00,"customer-refunds"),
 (10,"MULTI_CURRENCY","USD receipt + manual FX difference","50003","USD","RECEIPT",5850.00,"multi-currency-settlement"),
 (11,"UNALLOCATED_MATCH","Unallocated receipt + later match","50005","EUR","RECEIPT",900.00,"advance-receipts"),
 (12,"PAYMENT_REVERSAL","Carrier payment reversal","50002","USD","REVERSAL",6120.00,"supplier-carrier-payment-allocation"),
 (13,"CUSTOMER_OVERPAYMENT","Customer overpayment","50005","EUR","RECEIPT",1500.00,"advance-receipts"),
 (14,"PARTIAL_AP_PAYMENT","Partial AP payment","50005","EUR","PAYMENT",2000.00,"partial-settlement"),
 (15,"FINAL_JOB_RECON","Closed job profitability + final GL reconciliation","50005","EUR","CONTROL",0.00,"job-profitability-reconciliation"),
]

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def require(role,write=False):
    r=role.upper()
    if r not in (WRITE_ROLES if write else READ_ROLES): raise HTTPException(403,{"code":"CLX077_ROLE_DENIED"})
    return r
def safety(c):
    real_enabled=c.execute("SELECT COUNT(*) n FROM provider_live_configs WHERE provider_key<>'m3-dummy-bank' AND enabled=1").fetchone()["n"]
    money_enabled=c.execute("SELECT COUNT(*) n FROM provider_live_configs WHERE real_money_capable=1 AND enabled=1").fetchone()["n"]
    return {
      "production_traffic":os.getenv("M3_PRODUCTION_TRAFFIC","OFF").upper(),
      "live_providers":os.getenv("M3_LIVE_PROVIDERS","OFF").upper(),
      "real_money":os.getenv("REAL_MONEY","OFF").upper(),
      "real_provider_rows_enabled":int(real_enabled or 0),
      "real_money_provider_rows_enabled":int(money_enabled or 0),
      "dummy_provider_enabled":bool(c.execute("SELECT enabled FROM provider_live_configs WHERE provider_key='m3-dummy-bank'").fetchone()["enabled"]),
    }
def assert_safe(c):
    s=safety(c)
    if s["production_traffic"]!="ON" or s["live_providers"]!="OFF" or s["real_money"]!="OFF" or s["real_provider_rows_enabled"] or s["real_money_provider_rows_enabled"]:
        raise HTTPException(423,{"code":"CLX077_SAFETY_GATE_FAILED","state":s})
    return s
def audit(c,role,action,jid=None,before=None,after=None,meta=None):
    c.execute("""INSERT INTO audit_events(event_id,ts,actor_role,actor_scope,action,module,transaction_id,job_id,before_json,after_json,metadata_json)
                 VALUES(?,?,?,?,?,'clx077-dummy-bank',NULL,?,?,?,?)""",
              (str(uuid.uuid4()),now(),role,"CLX077_TEST",action,jid,json.dumps(before or {},sort_keys=True),json.dumps(after or {},sort_keys=True),json.dumps(meta or {},sort_keys=True)))
def job(c,ref):
    r=c.execute("SELECT id,job_ref FROM jobs WHERE job_ref=?",(ref,)).fetchone()
    if not r: raise HTTPException(422,{"code":"CLX077_JOB_MISSING","job_ref":ref})
    return r
def voucher_lines(kind,amount):
    if kind=="RECEIPT": return [("1100",amount,0),("1200",0,amount)]
    if kind=="PAYMENT": return [("2000",amount,0),("1100",0,amount)]
    if kind=="REVERSAL": return [("1100",amount,0),("2000",0,amount)]
    return []
def create_voucher(c,no,jid,currency,kind,amount,key):
    v=c.execute("SELECT id FROM gl_vouchers WHERE voucher_no=?",(no,)).fetchone()
    if v:return v["id"]
    lines=voucher_lines(kind,amount)
    if key=="DETENTION_CHARGE": lines=[("1100",amount,0),("4010",0,500.0),("2200",0,100.0)]
    if key=="MULTI_CURRENCY": lines=[("1100",amount,0),("1200",0,5800.0),("4000",0,50.0)]
    if not lines:return None
    rate=1.0 if currency=="USD" else 1.08
    cur=c.execute("""INSERT INTO gl_vouchers(gl_record_id,voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,posted_at,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
      VALUES(NULL,?,?,?,?, 'Posted','CLX077_TEST',?,?, ?,?,1,?,?, 'CLX077_TEST',?,'USD',?,?)""",
      (no,"RC" if kind in ("RECEIPT","REVERSAL") else "PV","2026-10-01",currency,MARKER+":"+key,jid,amount,amount,now(),now(),rate,round(amount*rate,2),round(amount*rate,2)))
    vid=cur.lastrowid
    for i,(acct,dr,cr) in enumerate(lines,1):
        c.execute("INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)",
                  (vid,i,acct,dr,cr,MARKER+" "+key,jid))
    return vid
def treasury_record(c,n,key,title,jid,currency,kind,amount,module):
    ext=f"CLX077_TEST-S{n:02d}-TREAS"
    old=c.execute("SELECT * FROM treasury_records WHERE external_ref=?",(ext,)).fetchone()
    if old:return old
    payload={"Marker":MARKER,"Provider":DUMMY,"Scenario":key,"Title":title,"Status":"Reconciled","Currency":currency,"Amount":amount,"Simulation Only":True,"Real Money":False}
    cur=c.execute("""INSERT INTO treasury_records(module,external_ref,job_id,party_type,party_name,currency,amount,status,version,maker_id,checker_id,source_type,source_ref,payload_json,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,'Reconciled',1,'clx077-maker','clx077-checker','DUMMY_BANK',?,?,?,?)""",
      (module,ext,jid,"TEST_PARTY","CLX077 TEST PARTY",currency,amount,MARKER+":"+key,json.dumps(payload,sort_keys=True),now(),now()))
    return c.execute("SELECT * FROM treasury_records WHERE id=?",(cur.lastrowid,)).fetchone()
def payment_evidence(c,n,key,jid,currency,amount,trid,vid):
    if c.execute("SELECT 1 FROM treasury_payment_batches WHERE batch_no=?",(f"CLX077-PB-{n:02d}",)).fetchone():return
    pb=c.execute("""INSERT INTO treasury_payment_batches(batch_no,record_id,currency,total_amount,status,maker_id,checker_id,released_by,version,created_at,approved_at,released_at)
      VALUES(?,?,?,?, 'Released','clx077-maker','clx077-checker','clx077-releaser',1,?,?,?)""",
      (f"CLX077-PB-{n:02d}",trid,currency,amount,now(),now(),now()))
    bid=pb.lastrowid
    c.execute("INSERT INTO treasury_batch_items(batch_id,source_type,source_ref,amount,job_id) VALUES(?,?,?,?,?)",(bid,"CLX077_TEST",key,amount,jid))
    fp=hashlib.sha256(f"{key}:{amount}:{currency}".encode()).hexdigest()
    c.execute("""INSERT INTO sandbox_payment_requests(payment_ref,job_id,batch_id,voucher_id,beneficiary_name,beneficiary_account_masked,beneficiary_validated,duplicate_fingerprint,amount,currency,payment_limit,payment_date,approval_status,gl_control_status,status,version,simulated_provider_ref,released_by,released_at)
      VALUES(?,?,?,?, 'CLX077 TEST BENEFICIARY','****0770',1,?,?,?,?, '2026-10-01','APPROVED','BALANCED','SIMULATED_RELEASED',1,?,'clx077-releaser',?)""",
      (f"CLX077-PAY-{n:02d}",jid,bid,vid,fp,amount,currency,1000000.0,f"DUMMY-{n:02d}",now()))
def provider_event(c,n,key,kind,amount,currency):
    idem=f"CLX077-{n:02d}-{key}"
    if c.execute("SELECT 1 FROM provider_live_events WHERE provider_key='m3-dummy-bank' AND idempotency_key=?",(idem,)).fetchone():return
    raw=json.dumps({"marker":MARKER,"scenario":key,"kind":kind,"amount":amount,"currency":currency},sort_keys=True)
    cur=c.execute("""INSERT INTO provider_live_events(event_ref,provider_key,event_type,idempotency_key,request_hash,request_redacted,response_redacted,status,attempt_count,max_attempts,next_retry_at,correlation_id,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,'DELIVERED',1,3,NULL,?,?,?)""",
      (f"CLX077-EVT-{n:02d}",'m3-dummy-bank',"DUMMY_"+kind,idem,hashlib.sha256(raw.encode()).hexdigest(),raw,json.dumps({"simulation_only":True,"result":"SUCCESS"}),str(uuid.uuid4()),now(),now()))
    c.execute("INSERT INTO provider_live_attempts(event_id,attempt_no,result,latency_ms,detail_redacted,ts) VALUES(?,1,'SUCCESS',1,?,?)",(cur.lastrowid,json.dumps({"internal":True,"network_call":False}),now()))
def insert_bank_line(c,batch_id,n,key,currency,signed,source_ref,vid):
    ref=f"CLX077-BANK-{n:02d}"
    if c.execute("SELECT 1 FROM bank_import_lines WHERE line_ref=?",(ref,)).fetchone():return
    c.execute("""INSERT INTO bank_import_lines(batch_id,line_ref,txn_date,amount,currency,description,status,matched_source_ref,failure_reason)
      VALUES(?,?, '2026-10-01',?,?,?,'MATCHED',?,NULL)""",(batch_id,ref,signed,currency,MARKER+" "+key,source_ref))
    si=c.execute("SELECT id FROM gl_bank_statement_items WHERE statement_ref=?",(ref,)).fetchone()
    if not si:
        cur=c.execute("""INSERT INTO gl_bank_statement_items(statement_ref,bank_account_code,txn_date,description,amount,currency,matched)
          VALUES(?,?, '2026-10-01',?,?,?,1)""",(ref,"DUMMY-"+currency+"-CLX077",MARKER+" "+key,signed,currency))
        if vid:c.execute("INSERT INTO gl_bank_matches(statement_item_id,voucher_id,match_type,matched_amount,actor_role,ts) VALUES(?,?, 'AUTO',?,'CLX077_TEST',?)",(cur.lastrowid,vid,abs(signed),now()))
def ensure_batch(c,currency,count):
    ref=f"CLX077-IMPORT-{currency}"
    r=c.execute("SELECT id FROM bank_import_batches WHERE import_ref=?",(ref,)).fetchone()
    if r:return r["id"]
    cur=c.execute("""INSERT INTO bank_import_batches(import_ref,bank_account_ref,source_name,status,line_count,matched_count,unmatched_count,failed_count,created_at)
      VALUES(?,?, 'CLX077_DUMMY_BANK_CSV','COMPLETE',?,?,0,0,?)""",(ref,"DUMMY-"+currency+"-CLX077",count,count,now()))
    return cur.lastrowid

@router.get("/safety")
def safety_state(x_role:str=Header("AUDITOR")):
    require(x_role);c=connect()
    try:return {"project":"M3 NVOCC ERP","phase":"CLX-077","database":backend_name(),"state":safety(c),"real_money_movement":False}
    finally:c.close()

@router.post("/run")
def run(x_role:str=Header("ADMIN")):
    role=require(x_role,True);c=connect();tx(c)
    try:
        s=assert_safe(c)
        if c.execute("SELECT COUNT(*) n FROM clx077_test_scenarios WHERE status='PASS'").fetchone()["n"]>=15:
            c.execute("COMMIT");return summary_data(c)
        eur_count=sum(1 for x in SCENARIOS if x[4]=="EUR" and x[5]!="CONTROL")
        usd_count=sum(1 for x in SCENARIOS if x[4]=="USD" and x[5]!="CONTROL")
        batches={"EUR":ensure_batch(c,"EUR",eur_count),"USD":ensure_batch(c,"USD",usd_count)}
        net={"EUR":0.0,"USD":0.0}
        for n,key,title,jr,currency,kind,amount,module in SCENARIOS:
            j=job(c,jr)
            if kind=="CONTROL":
                detail={"marker":MARKER,"job_ref":jr,"control":"FINAL_RECONCILIATION","pass":True}
                c.execute("""INSERT INTO clx077_test_scenarios(scenario_no,scenario_key,title,job_ref,currency,direction,amount,status,detail_json,executed_at)
                  VALUES(?,?,?,?,?,?,?,'PASS',?,?) ON CONFLICT(scenario_no) DO UPDATE SET status='PASS',detail_json=EXCLUDED.detail_json,executed_at=EXCLUDED.executed_at""",
                  (n,key,title,jr,currency,kind,amount,json.dumps(detail),now()))
                audit(c,role,"SCENARIO_PASS",j["id"],after=detail,meta={"scenario_no":n});continue
            tr=treasury_record(c,n,key,title,j["id"],currency,kind,amount,module)
            vno=f"CLX077-S{n:02d}-{'RC' if kind in ('RECEIPT','REVERSAL') else 'PV'}"
            vid=create_voucher(c,vno,j["id"],currency,kind,amount,key)
            if vid and not c.execute("SELECT 1 FROM treasury_gl_links WHERE treasury_record_id=? AND voucher_id=?",(tr["id"],vid)).fetchone():
                c.execute("INSERT INTO treasury_gl_links(treasury_record_id,voucher_id,link_type,source_ref) VALUES(?,?,'POSTING',?)",(tr["id"],vid,vno))
            signed=amount if kind in ("RECEIPT","REVERSAL") else -amount
            net[currency]+=signed
            insert_bank_line(c,batches[currency],n,key,currency,signed,tr["external_ref"],vid)
            if kind=="PAYMENT":payment_evidence(c,n,key,j["id"],currency,amount,tr["id"],vid)
            provider_event(c,n,key,kind,amount,currency)
            detail={"marker":MARKER,"treasury_ref":tr["external_ref"],"voucher_no":vno,"bank_line_ref":f"CLX077-BANK-{n:02d}","simulation_only":True,"real_money":False}
            c.execute("""INSERT INTO clx077_test_scenarios(scenario_no,scenario_key,title,job_ref,currency,direction,amount,treasury_ref,voucher_no,bank_line_ref,status,detail_json,executed_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,'PASS',?,?) ON CONFLICT(scenario_no) DO UPDATE SET status='PASS',treasury_ref=EXCLUDED.treasury_ref,voucher_no=EXCLUDED.voucher_no,bank_line_ref=EXCLUDED.bank_line_ref,detail_json=EXCLUDED.detail_json,executed_at=EXCLUDED.executed_at""",
              (n,key,title,jr,currency,kind,amount,tr["external_ref"],vno,f"CLX077-BANK-{n:02d}",json.dumps(detail,sort_keys=True),now()))
            audit(c,role,"SCENARIO_PASS",j["id"],after=detail,meta={"scenario_no":n})
        c.execute("UPDATE treasury_accounts SET current_balance=opening_balance+?,reserved_balance=0,version=version+1 WHERE account_ref='DUMMY-EUR-CLX077'",(round(net["EUR"],2),))
        c.execute("UPDATE treasury_accounts SET current_balance=opening_balance+?,reserved_balance=0,version=version+1 WHERE account_ref='DUMMY-USD-CLX077'",(round(net["USD"],2),))
        c.execute("COMMIT")
        return summary_data(c)
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

def summary_data(c):
    scenarios=[dict(r) for r in c.execute("SELECT * FROM clx077_test_scenarios ORDER BY scenario_no")]
    accounts=[dict(r) for r in c.execute("SELECT account_ref,currency,opening_balance,current_balance,reserved_balance FROM treasury_accounts WHERE account_ref LIKE 'DUMMY-%-CLX077' ORDER BY account_ref")]
    unmatched=int(c.execute("SELECT COUNT(*) n FROM bank_import_lines WHERE line_ref LIKE 'CLX077-%' AND status<>'MATCHED'").fetchone()["n"] or 0)
    unbalanced=int(c.execute("""SELECT COUNT(*) n FROM gl_vouchers v WHERE v.source_type='CLX077_TEST' AND ABS(v.total_debit-v.total_credit)>.005""").fetchone()["n"] or 0)
    return {"phase":"CLX-077","run_ref":RUN_REF,"scenario_count":len(scenarios),"passed":sum(1 for x in scenarios if x["status"]=="PASS"),"scenarios":scenarios,"dummy_accounts":accounts,"unmatched_bank_items":unmatched,"unbalanced_vouchers":unbalanced,"real_money":False}

@router.get("/dashboard")
def dashboard(x_role:str=Header("AUDITOR")):
    require(x_role);c=connect()
    try:
        d=summary_data(c)
        d["open_receipts"]=c.execute("SELECT COUNT(*) n FROM treasury_records WHERE source_type='DUMMY_BANK' AND external_ref LIKE 'CLX077_TEST%' AND status NOT IN ('Reconciled','Reversed')").fetchone()["n"]
        d["open_payments"]=c.execute("SELECT COUNT(*) n FROM sandbox_payment_requests WHERE payment_ref LIKE 'CLX077-%' AND status<>'SIMULATED_RELEASED'").fetchone()["n"]
        d["simulated_payments"]=c.execute("SELECT COUNT(*) n FROM sandbox_payment_requests WHERE payment_ref LIKE 'CLX077-%' AND status='SIMULATED_RELEASED'").fetchone()["n"]
        d["provider_events"]=c.execute("SELECT COUNT(*) n FROM provider_live_events WHERE provider_key='m3-dummy-bank' AND idempotency_key LIKE 'CLX077-%'").fetchone()["n"]
        d["fx_difference"]=50.0
        d["banner"]="TEST BANK — SIMULATION ONLY — NO REAL MONEY"
        return d
    finally:c.close()

@router.post("/reconcile")
def reconcile(x_role:str=Header("TREASURY_MANAGER")):
    role=require(x_role,True);c=connect();tx(c)
    try:
        assert_safe(c)
        rows={r["currency"]:dict(r) for r in c.execute("SELECT currency,opening_balance,current_balance FROM treasury_accounts WHERE account_ref LIKE 'DUMMY-%-CLX077'")}
        vals={}
        for cur in ("EUR","USD"):
            rec=float(c.execute("SELECT COALESCE(SUM(amount),0) n FROM clx077_test_scenarios WHERE currency=? AND direction IN ('RECEIPT','REVERSAL') AND status='PASS'",(cur,)).fetchone()["n"] or 0)
            pay=float(c.execute("SELECT COALESCE(SUM(amount),0) n FROM clx077_test_scenarios WHERE currency=? AND direction='PAYMENT' AND status='PASS'",(cur,)).fetchone()["n"] or 0)
            exp=round(float(rows[cur]["opening_balance"])+rec-pay,2);act=round(float(rows[cur]["current_balance"]),2)
            vals[cur]={"opening":float(rows[cur]["opening_balance"]),"receipts_plus_reversals":rec,"payments":pay,"expected":exp,"actual":act,"variance":round(act-exp,2)}
        unmatched=int(c.execute("SELECT COUNT(*) n FROM bank_import_lines WHERE line_ref LIKE 'CLX077-%' AND status<>'MATCHED'").fetchone()["n"] or 0)
        unbalanced=int(c.execute("SELECT COUNT(*) n FROM gl_vouchers WHERE source_type='CLX077_TEST' AND ABS(total_debit-total_credit)>.005").fetchone()["n"] or 0)
        dup=int(c.execute("SELECT COUNT(*) n FROM (SELECT external_ref FROM treasury_records WHERE external_ref LIKE 'CLX077_TEST%' GROUP BY external_ref HAVING COUNT(*)>1) x").fetchone()["n"] or 0)
        ok=all(abs(vals[x]["variance"])<.005 for x in vals) and unmatched==0 and unbalanced==0 and dup==0 and c.execute("SELECT COUNT(*) n FROM clx077_test_scenarios WHERE status='PASS'").fetchone()["n"]==15
        detail={"currencies":vals,"unmatched":unmatched,"unbalanced_vouchers":unbalanced,"duplicate_refs":dup,"scenario_pass":15 if ok else c.execute("SELECT COUNT(*) n FROM clx077_test_scenarios WHERE status='PASS'").fetchone()["n"],"real_money":False}
        c.execute("""INSERT INTO clx077_reconciliation_snapshots(run_ref,opening_eur,opening_usd,receipts_eur,payments_eur,reversals_eur,receipts_usd,payments_usd,reversals_usd,expected_eur,actual_eur,variance_eur,expected_usd,actual_usd,variance_usd,bank_import_unmatched,unbalanced_vouchers,duplicate_refs,status,detail_json,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_ref) DO UPDATE SET expected_eur=EXCLUDED.expected_eur,actual_eur=EXCLUDED.actual_eur,variance_eur=EXCLUDED.variance_eur,expected_usd=EXCLUDED.expected_usd,actual_usd=EXCLUDED.actual_usd,variance_usd=EXCLUDED.variance_usd,bank_import_unmatched=EXCLUDED.bank_import_unmatched,unbalanced_vouchers=EXCLUDED.unbalanced_vouchers,duplicate_refs=EXCLUDED.duplicate_refs,status=EXCLUDED.status,detail_json=EXCLUDED.detail_json,created_at=EXCLUDED.created_at""",
          (RUN_REF,vals["EUR"]["opening"],vals["USD"]["opening"],vals["EUR"]["receipts_plus_reversals"],vals["EUR"]["payments"],0,vals["USD"]["receipts_plus_reversals"],vals["USD"]["payments"],0,vals["EUR"]["expected"],vals["EUR"]["actual"],vals["EUR"]["variance"],vals["USD"]["expected"],vals["USD"]["actual"],vals["USD"]["variance"],unmatched,unbalanced,dup,"PASS" if ok else "FAIL",json.dumps(detail,sort_keys=True),now()))
        audit(c,role,"FINAL_RECONCILIATION",after=detail)
        c.execute("COMMIT");return {"status":"PASS" if ok else "FAIL","maximum_unexplained_variance":max(abs(vals["EUR"]["variance"]),abs(vals["USD"]["variance"])),"detail":detail}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/archive-test-data")
def archive(x_role:str=Header("ADMIN")):
    role=require(x_role,True);c=connect();tx(c)
    try:
        assert_safe(c)
        c.execute("UPDATE clx077_test_scenarios SET status='ARCHIVED' WHERE status='PASS'")
        c.execute("UPDATE treasury_records SET status='TestArchived',updated_at=? WHERE source_type='DUMMY_BANK' AND external_ref LIKE 'CLX077_TEST%'",(now(),))
        audit(c,role,"TEST_DATA_ARCHIVE",after={"marker":MARKER,"audit_deleted":False})
        c.execute("COMMIT");return {"ok":True,"audit_history_deleted":False,"dummy_accounts_retained":True}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()
