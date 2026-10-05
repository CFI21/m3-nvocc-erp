from __future__ import annotations

import datetime, json, uuid
from typing import Any, Optional
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from .db import connect, tx
from .admin import session as iam_session, roles_for as iam_roles_for

router=APIRouter(prefix="/api/mrg",tags=["M3 Market Rates General"])

MRG_TYPES={"GENERAL","EXPORT","IMPORT","TRANSSHIPMENT","DETENTION","DETENTION_EXPORT","MTY_STORAGE","SPECIAL_DETENTION"}
RATE_SIDES={"REVENUE","COST"}
PARTY_TYPES={"CUSTOMER","AGENT","VENDOR","CARRIER","PRINCIPAL","DEPOT","TERMINAL","TRANSPORTER","OTHER"}
LOCKED_BOOKING={"APPROVED","RELEASED","ISSUED","COMPLETED","CLOSED"}
MAKER={"ADMIN","MASTER_DATA_MANAGER","FINANCE","OPS","EQUIPMENT_MANAGER"}
CHECKER={"ADMIN","MASTER_DATA_MANAGER","FINANCE","GL_MANAGER"}
VIEW=MAKER|CHECKER|{"AUDITOR","VIEWER","AGENT","DOCS"}

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def j(x): return json.dumps(x,sort_keys=True,separators=(",",":"))
def row(r): return dict(r) if r else {}

def ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS mrg_rules(
      rule_ref TEXT PRIMARY KEY,mrg_type TEXT NOT NULL,rate_side TEXT NOT NULL,
      party_type TEXT NOT NULL,party_code TEXT,charge_code TEXT NOT NULL,charge_type TEXT,
      effective_from TEXT NOT NULL,effective_to TEXT,pol TEXT,pot TEXT,pod TEXT,depot_code TEXT,terminal_code TEXT,
      route_code TEXT,service_code TEXT,cargo_type TEXT,size_type TEXT,container_type TEXT,currency TEXT NOT NULL,
      rate_basis TEXT NOT NULL,unit_rate REAL NOT NULL DEFAULT 0,minimum_rate REAL,maximum_rate REAL,free_days INTEGER,
      lolo_rate REAL,invoice_basis TEXT,slab_wise INTEGER NOT NULL DEFAULT 0,booking_ref TEXT,job_ref TEXT,
      original_rule_ref TEXT,exception_reason TEXT,office_code TEXT,branch_code TEXT,organization_code TEXT,
      priority INTEGER NOT NULL DEFAULT 100,remarks TEXT,status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
      version INTEGER NOT NULL DEFAULT 1,maker TEXT NOT NULL,checker TEXT,approved_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS mrg_slabs(
      slab_ref TEXT PRIMARY KEY,rule_ref TEXT NOT NULL,size_type TEXT,container_type TEXT,from_day INTEGER NOT NULL,
      till_day INTEGER,rate REAL NOT NULL,currency TEXT,created_at TEXT NOT NULL,
      UNIQUE(rule_ref,size_type,container_type,from_day,till_day)
    )""")

def actor(c,token,x_role):
    if token:
        s=iam_session(c,token)
        rs=iam_roles_for(c,s["user_id"])
        roles={str(r["role_code"]).upper() for r in rs}
        role=next(iter(roles), "VIEWER")
        return {"user":s["user_ref"],"role":role,"roles":roles}
    role=(x_role or "VIEWER").upper()
    if role=="SUPER_ADMIN": role="ADMIN"
    return {"user":"header-"+role,"role":role,"roles":{role}}

def require(a,allowed):
    if not (a["roles"]&allowed): raise HTTPException(403,{"code":"MRG_ROLE_DENIED"})

def audit(c,a,action,rule_ref=None,before=None,after=None,metadata=None,job_id=None):
    c.execute("""INSERT INTO audit_events(event_id,ts,actor_role,actor_scope,action,module,transaction_id,job_id,before_json,after_json,metadata_json)
                 VALUES(?,?,?,?,?,'mrg',NULL,?,?,?,?)""",
      (str(uuid.uuid4()),now(),a["role"],None,action,job_id,j(before) if before is not None else None,
       j(after) if after is not None else None,j({"rule_ref":rule_ref,**(metadata or {})})))

class RuleWrite(BaseModel):
    mrg_type:str; rate_side:str; party_type:str; charge_code:str; effective_from:str
    party_code:Optional[str]=None; charge_type:Optional[str]=None; effective_to:Optional[str]=None
    pol:Optional[str]=None; pot:Optional[str]=None; pod:Optional[str]=None; depot_code:Optional[str]=None; terminal_code:Optional[str]=None
    route_code:Optional[str]=None; service_code:Optional[str]=None; cargo_type:Optional[str]=None; size_type:Optional[str]=None; container_type:Optional[str]=None
    currency:str="USD"; rate_basis:str="FLAT"; unit_rate:float=0; minimum_rate:Optional[float]=None; maximum_rate:Optional[float]=None
    free_days:Optional[int]=None; lolo_rate:Optional[float]=None; invoice_basis:Optional[str]=None; slab_wise:bool=False
    booking_ref:Optional[str]=None; job_ref:Optional[str]=None; original_rule_ref:Optional[str]=None; exception_reason:Optional[str]=None
    office_code:Optional[str]=None; branch_code:Optional[str]=None; organization_code:Optional[str]=None; priority:int=100; remarks:Optional[str]=None

class RuleUpdate(BaseModel):
    version:int=Field(ge=1); fields:dict[str,Any]=Field(default_factory=dict)

class SlabWrite(BaseModel):
    size_type:Optional[str]=None; container_type:Optional[str]=None; from_day:int=Field(ge=0)
    till_day:Optional[int]=Field(default=None,ge=0); rate:float; currency:Optional[str]=None

class ImportBody(BaseModel):
    rule_refs:list[str]=Field(default_factory=list); import_all:bool=False; conflict_action:str="KEEP_EXISTING"

def validate_rule(d):
    for k in ("mrg_type","rate_side","party_type"): d[k]=str(d[k]).upper()
    if d["mrg_type"] not in MRG_TYPES: raise HTTPException(422,{"code":"INVALID_MRG_TYPE"})
    if d["rate_side"] not in RATE_SIDES: raise HTTPException(422,{"code":"INVALID_RATE_SIDE"})
    if d["party_type"] not in PARTY_TYPES: raise HTTPException(422,{"code":"INVALID_PARTY_TYPE"})
    if d.get("effective_to") and d["effective_to"]<d["effective_from"]: raise HTTPException(422,{"code":"INVALID_EFFECTIVE_DATES"})
    if d["mrg_type"]=="SPECIAL_DETENTION":
        if not (d.get("booking_ref") or d.get("job_ref")): raise HTTPException(422,{"code":"SPECIAL_DETENTION_BOOKING_OR_JOB_REQUIRED"})
        if not d.get("original_rule_ref"): raise HTTPException(422,{"code":"ORIGINAL_MRG_DETENTION_REQUIRED"})
        if not d.get("exception_reason"): raise HTTPException(422,{"code":"SPECIAL_DETENTION_REASON_REQUIRED"})

@router.get("/rules")
def list_rules(mrg_type:Optional[str]=None,rate_side:Optional[str]=None,party_type:Optional[str]=None,party_code:Optional[str]=None,status:Optional[str]=None,active_on:Optional[str]=None,
               x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session")):
    c=connect()
    try:
        ensure(c);a=actor(c,x_m3_session,x_role);require(a,VIEW)
        q="SELECT * FROM mrg_rules WHERE 1=1";args=[]
        for col,val in (("mrg_type",mrg_type),("rate_side",rate_side),("party_type",party_type),("party_code",party_code),("status",status)):
            if val:q+=f" AND {col}=?";args.append(str(val).upper() if col in {"mrg_type","rate_side","party_type","status"} else val)
        if active_on:q+=" AND effective_from<=? AND (effective_to IS NULL OR effective_to='' OR effective_to>=?)";args.extend([active_on,active_on])
        q+=" ORDER BY mrg_type,rate_side,priority,rule_ref"
        out=[row(x) for x in c.execute(q,args).fetchall()]
        for x in out:x["slabs"]=[row(s) for s in c.execute("SELECT * FROM mrg_slabs WHERE rule_ref=? ORDER BY from_day,slab_ref",(x["rule_ref"],)).fetchall()]
        return {"count":len(out),"records":out,"shared_model":True,"screen_count":196}
    finally:c.close()

@router.post("/rules")
def create_rule(b:RuleWrite,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session")):
    c=connect();tx(c)
    try:
        ensure(c);a=actor(c,x_m3_session,x_role);require(a,MAKER);d=b.model_dump();validate_rule(d)
        overlap=c.execute("""SELECT rule_ref FROM mrg_rules WHERE status IN ('APPROVED','PENDING_APPROVAL')
          AND mrg_type=? AND rate_side=? AND party_type=? AND COALESCE(party_code,'')=COALESCE(?,'')
          AND charge_code=? AND COALESCE(pol,'')=COALESCE(?,'') AND COALESCE(pod,'')=COALESCE(?,'')
          AND COALESCE(size_type,'')=COALESCE(?,'') AND COALESCE(container_type,'')=COALESCE(?,'')
          AND NOT (COALESCE(effective_to,'9999-12-31')<? OR COALESCE(?,'9999-12-31')<effective_from) LIMIT 1""",
          (d["mrg_type"],d["rate_side"],d["party_type"],d.get("party_code"),d["charge_code"],d.get("pol"),d.get("pod"),
           d.get("size_type"),d.get("container_type"),d["effective_from"],d.get("effective_to"))).fetchone()
        if overlap:raise HTTPException(409,{"code":"OVERLAPPING_MRG_RULE","rule_ref":overlap["rule_ref"]})
        ref="MRG-"+uuid.uuid4().hex[:12].upper()
        cols=["rule_ref","mrg_type","rate_side","party_type","party_code","charge_code","charge_type","effective_from","effective_to","pol","pot","pod","depot_code","terminal_code","route_code","service_code","cargo_type","size_type","container_type","currency","rate_basis","unit_rate","minimum_rate","maximum_rate","free_days","lolo_rate","invoice_basis","slab_wise","booking_ref","job_ref","original_rule_ref","exception_reason","office_code","branch_code","organization_code","priority","remarks","status","version","maker","checker","approved_at","created_at","updated_at"]
        vals=[ref,d["mrg_type"],d["rate_side"],d["party_type"],d.get("party_code"),d["charge_code"],d.get("charge_type"),d["effective_from"],d.get("effective_to"),d.get("pol"),d.get("pot"),d.get("pod"),d.get("depot_code"),d.get("terminal_code"),d.get("route_code"),d.get("service_code"),d.get("cargo_type"),d.get("size_type"),d.get("container_type"),d["currency"],d["rate_basis"],d["unit_rate"],d.get("minimum_rate"),d.get("maximum_rate"),d.get("free_days"),d.get("lolo_rate"),d.get("invoice_basis"),1 if d.get("slab_wise") else 0,d.get("booking_ref"),d.get("job_ref"),d.get("original_rule_ref"),d.get("exception_reason"),d.get("office_code"),d.get("branch_code"),d.get("organization_code"),d.get("priority",100),d.get("remarks"),"PENDING_APPROVAL",1,a["user"],None,None,now(),now()]
        c.execute(f"INSERT INTO mrg_rules({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",vals)
        rec=row(c.execute("SELECT * FROM mrg_rules WHERE rule_ref=?",(ref,)).fetchone());audit(c,a,"MRG_RULE_CREATE",ref,None,rec)
        c.execute("COMMIT");return {"ok":True,"rule_ref":ref,"status":"PENDING_APPROVAL","record":rec}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.put("/rules/{rule_ref}")
def update_rule(rule_ref:str,b:RuleUpdate,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session")):
    c=connect();tx(c)
    try:
        ensure(c);a=actor(c,x_m3_session,x_role);require(a,MAKER);r=c.execute("SELECT * FROM mrg_rules WHERE rule_ref=?",(rule_ref,)).fetchone()
        if not r:raise HTTPException(404,{"code":"MRG_RULE_NOT_FOUND"})
        before=row(r)
        if before["status"]=="APPROVED":raise HTTPException(409,{"code":"MRG_APPROVED_RULE_LOCKED","required":"GOVERNED_NEW_VERSION"})
        if before["version"]!=b.version:raise HTTPException(409,{"code":"OPTIMISTIC_LOCK_CONFLICT","current_version":before["version"]})
        allowed={"party_code","charge_type","effective_from","effective_to","pol","pot","pod","depot_code","terminal_code","route_code","service_code","cargo_type","size_type","container_type","currency","rate_basis","unit_rate","minimum_rate","maximum_rate","free_days","lolo_rate","invoice_basis","slab_wise","booking_ref","job_ref","original_rule_ref","exception_reason","office_code","branch_code","organization_code","priority","remarks"}
        patch={k:v for k,v in b.fields.items() if k in allowed}
        if not patch:raise HTTPException(422,{"code":"NO_ALLOWED_FIELDS"})
        merged={**before,**patch};validate_rule(merged)
        cur=c.execute("UPDATE mrg_rules SET "+",".join(f"{k}=?" for k in patch)+",version=version+1,updated_at=? WHERE rule_ref=? AND version=?",list(patch.values())+[now(),rule_ref,b.version])
        if cur.rowcount!=1:raise HTTPException(409,{"code":"OPTIMISTIC_LOCK_CONFLICT"})
        after=row(c.execute("SELECT * FROM mrg_rules WHERE rule_ref=?",(rule_ref,)).fetchone());audit(c,a,"MRG_RULE_UPDATE",rule_ref,before,after)
        c.execute("COMMIT");return {"ok":True,"record":after}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.post("/rules/{rule_ref}/slabs")
def add_slab(rule_ref:str,b:SlabWrite,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session")):
    c=connect();tx(c)
    try:
        ensure(c);a=actor(c,x_m3_session,x_role);require(a,MAKER);r=c.execute("SELECT * FROM mrg_rules WHERE rule_ref=?",(rule_ref,)).fetchone()
        if not r:raise HTTPException(404,{"code":"MRG_RULE_NOT_FOUND"})
        if r["status"]=="APPROVED":raise HTTPException(409,{"code":"MRG_APPROVED_RULE_LOCKED"})
        if b.till_day is not None and b.till_day<b.from_day:raise HTTPException(422,{"code":"INVALID_SLAB_RANGE"})
        slab_ref="MSL-"+uuid.uuid4().hex[:12].upper()
        c.execute("INSERT INTO mrg_slabs(slab_ref,rule_ref,size_type,container_type,from_day,till_day,rate,currency,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(slab_ref,rule_ref,b.size_type,b.container_type,b.from_day,b.till_day,b.rate,b.currency or r["currency"],now()))
        audit(c,a,"MRG_SLAB_CREATE",rule_ref,metadata=b.model_dump());c.execute("COMMIT");return {"ok":True}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.post("/rules/{rule_ref}/approve")
def approve_rule(rule_ref:str,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session")):
    c=connect();tx(c)
    try:
        ensure(c);a=actor(c,x_m3_session,x_role);require(a,CHECKER);r=c.execute("SELECT * FROM mrg_rules WHERE rule_ref=?",(rule_ref,)).fetchone()
        if not r:raise HTTPException(404,{"code":"MRG_RULE_NOT_FOUND"})
        before=row(r)
        if before["status"]!="PENDING_APPROVAL":raise HTTPException(409,{"code":"MRG_RULE_NOT_PENDING"})
        if before["maker"]==a["user"]:raise HTTPException(409,{"code":"MAKER_CHECKER_CONFLICT"})
        c.execute("UPDATE mrg_rules SET status='APPROVED',checker=?,approved_at=?,version=version+1,updated_at=? WHERE rule_ref=?",(a["user"],now(),now(),rule_ref))
        after=row(c.execute("SELECT * FROM mrg_rules WHERE rule_ref=?",(rule_ref,)).fetchone());audit(c,a,"MRG_RULE_APPROVE",rule_ref,before,after)
        c.execute("COMMIT");return {"ok":True,"rule_ref":rule_ref,"status":"APPROVED"}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

def booking_context(c,job_ref):
    r=c.execute("""SELECT j.id job_id,j.job_ref,j.pol,j.pod,b.booking_ref,c.code customer_code,c.name customer_name,a.code agent_code,a.name agent_name,
      t.id booking_tx_id,t.status booking_status,t.version booking_version,t.payload_json
      FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
      LEFT JOIN transaction_records t ON t.job_id=j.id AND t.module='booking' WHERE j.job_ref=? ORDER BY t.id LIMIT 1""",(job_ref,)).fetchone()
    if not r:raise HTTPException(404,{"code":"JOB_NOT_FOUND"})
    d=row(r);d["fields"]=json.loads(d.pop("payload_json") or "{}");return d

def movement(fields):
    raw=" ".join(str(fields.get(k) or "") for k in ("Operation","Freight Type","Shipment / Freight Type")).upper()
    out={"GENERAL"}
    if "IMPORT" in raw:out.add("IMPORT")
    if "TRANS" in raw or fields.get("POT (1)") or fields.get("Via Port"):out.add("TRANSSHIPMENT")
    if "EXPORT" in raw or not ({"IMPORT","TRANSSHIPMENT"}&out):out.add("EXPORT")
    return out

def specificity(r,ctx,f):
    score=0
    checks=[("party_code",{ctx["customer_code"],ctx["agent_code"],str(f.get("Carrier") or ""),str(f.get("Principal") or "")}),
            ("pol",{ctx["pol"],str(f.get("POL") or "")}),("pot",{str(f.get("POT (1)") or f.get("Via Port") or "")}),
            ("pod",{ctx["pod"],str(f.get("POD") or "")}),("service_code",{str(f.get("Service") or "")}),
            ("route_code",{str(f.get("Route") or "")}),("size_type",{str(f.get("Equipment") or "")}),("container_type",{str(f.get("Container Type") or "")})]
    for k,vals in checks:
        if r.get(k):score+=10 if str(r[k]) in vals else -1000
    return score-int(r.get("priority") or 100)

def special_rates(c,ctx):
    out={}
    rows=c.execute("""SELECT t.external_ref,t.payload_json,s.stage,s.approved_rate,s.booking_ref FROM special_rate_workflow s
      JOIN transaction_records t ON t.id=s.transaction_id WHERE t.job_id=? AND (UPPER(s.stage) IN ('APPROVED','LINKED') OR UPPER(t.status)='APPROVED')""",(ctx["job_id"],)).fetchall()
    for r in rows:
        p=json.loads(r["payload_json"] or "{}")
        if r["booking_ref"] and r["booking_ref"]!=ctx["booking_ref"]:continue
        code=str(p.get("Charge Code") or "OFR").strip().upper()
        if code:out[code]={"external_ref":r["external_ref"],"approved_rate":r["approved_rate"],"currency":p.get("Currency"),"stage":r["stage"]}
    return out

def applicable_rows(c,ctx):
    f=ctx["fields"];day=str(f.get("Booking Date") or now()[:10]);types=movement(f)
    rows=[row(x) for x in c.execute("SELECT * FROM mrg_rules WHERE status='APPROVED' AND effective_from<=? AND (effective_to IS NULL OR effective_to='' OR effective_to>=?)",(day,day)).fetchall()]
    return [x for x in rows if x["mrg_type"] in types and specificity(x,ctx,f)>-500]

@router.get("/booking/{job_ref}/applicable")
def applicable(job_ref:str,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session")):
    c=connect()
    try:
        ensure(c);a=actor(c,x_m3_session,x_role);require(a,VIEW);ctx=booking_context(c,job_ref);special=special_rates(c,ctx);out=[]
        for x in applicable_rows(c,ctx):
            sp=special.get(str(x["charge_code"]).upper())
            y={**x,"match_score":specificity(x,ctx,ctx["fields"]),"source_type":"MRG","source_reference":x["rule_ref"],"source_version":x["version"]}
            if sp:y.update({"resolution":"SUPERSEDED_BY_SPECIAL_RATE","special_rate":sp,"applied_rate":sp["approved_rate"],"applied_source":"SPECIAL_RATE"})
            else:y.update({"resolution":"MRG_APPLICABLE","applied_rate":x["unit_rate"],"applied_source":"MRG"})
            out.append(y)
        out.sort(key=lambda x:(x["rate_side"],-x["match_score"],x["charge_code"]))
        return {"job_ref":job_ref,"booking_ref":ctx["booking_ref"],"candidates":out,"rate_resolution_priority":["APPROVED_SPECIAL_RATE","APPROVED_MRG","AUTHORIZED_MANUAL"],"optional_import":True}
    finally:c.close()

@router.post("/booking/{job_ref}/import")
def import_booking(job_ref:str,b:ImportBody,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session")):
    c=connect();tx(c)
    try:
        ensure(c);a=actor(c,x_m3_session,x_role);require(a,MAKER);ctx=booking_context(c,job_ref)
        if str(ctx["booking_status"] or "").upper() in LOCKED_BOOKING:raise HTTPException(409,{"code":"CRT_REQUIRED_AFTER_APPROVAL","module":"booking","record_id":ctx["booking_tx_id"]})
        if not ctx["booking_tx_id"]:raise HTTPException(404,{"code":"BOOKING_TRANSACTION_NOT_FOUND"})
        rows=applicable_rows(c,ctx)
        if not b.import_all:
            wanted=set(b.rule_refs);rows=[x for x in rows if x["rule_ref"] in wanted]
        special=special_rates(c,ctx);f=ctx["fields"];existing=list(f.get("Commercial Charges") or f.get("MRG Charges") or [])
        def k(x):return "|".join(str(x.get(n) or "").upper() for n in ("charge_code","party_type","party_code","rate_side"))
        by={k(x):x for x in existing};results=[]
        for r in rows:
            sp=special.get(str(r["charge_code"]).upper())
            line={"charge_code":r["charge_code"],"party_type":r["party_type"],"party_code":r["party_code"],"rate_side":r["rate_side"],
              "rate_basis":r["rate_basis"],"currency":sp.get("currency") if sp else r["currency"],"original_rate":r["unit_rate"],
              "applied_rate":sp["approved_rate"] if sp else r["unit_rate"],"source_type":"SPECIAL_RATE" if sp else "MRG",
              "source_reference":sp["external_ref"] if sp else r["rule_ref"],"source_version":r["version"],"mrg_reference":r["rule_ref"],
              "mrg_version":r["version"],"approval_reference":sp["external_ref"] if sp else r["rule_ref"],"imported_at":now(),
              "resolution":"SUPERSEDED_BY_SPECIAL_RATE" if sp else "MRG_APPLIED"}
            key=k(line)
            if key in by:
                act=b.conflict_action.upper()
                if act=="REPLACE":
                    for i,x in enumerate(existing):
                        if k(x)==key:existing[i]=line;break
                    by[key]=line;results.append({"key":key,"status":"REPLACE"})
                elif act=="ADD_AS_ADDITIONAL":existing.append(line);results.append({"key":key,"status":"ADD_AS_ADDITIONAL"})
                else:results.append({"key":key,"status":"ALREADY_PRESENT"})
            else:existing.append(line);by[key]=line;results.append({"key":key,"status":"IMPORTED"})
        f["Commercial Charges"]=existing
        revenue=sum(float(x.get("applied_rate") or 0) for x in existing if x.get("rate_side")=="REVENUE")
        cost=sum(float(x.get("applied_rate") or 0) for x in existing if x.get("rate_side")=="COST")
        f["MRG Expected Revenue"]=revenue;f["MRG Expected Cost"]=cost;f["MRG Expected Gross Margin"]=revenue-cost
        cur=c.execute("UPDATE transaction_records SET payload_json=?,version=version+1,updated_at=? WHERE id=? AND version=?",(j(f),now(),ctx["booking_tx_id"],ctx["booking_version"]))
        if cur.rowcount!=1:raise HTTPException(409,{"code":"OPTIMISTIC_LOCK_CONFLICT"})
        audit(c,a,"MRG_BOOKING_IMPORT",metadata={"job_ref":job_ref,"booking_ref":ctx["booking_ref"],"results":results},job_id=ctx["job_id"])
        c.execute("COMMIT");return {"ok":True,"results":results,"expected_revenue":revenue,"expected_cost":cost,"expected_gross_margin":revenue-cost,"direct_finance_posting":False}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.get("/report")
def report(mrg_type:Optional[str]=None,rate_side:Optional[str]=None,party_type:Optional[str]=None,party_code:Optional[str]=None,status:Optional[str]=None,valid_on:Optional[str]=None,
           x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session")):
    return list_rules(mrg_type,rate_side,party_type,party_code,status,valid_on,x_role,x_m3_session)
