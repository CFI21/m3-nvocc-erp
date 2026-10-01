from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Optional
import datetime, json, uuid

from .db import connect, tx
from .admin import session as iam_session, roles_for

router=APIRouter(prefix="/api/clx070/container-control",tags=["CLX-070 Container Master Global Control"])

GLOBAL_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT","AUDITOR"}
WRITE_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","EQUIPMENT_CONTROLLER","OPS","BRANCH_OPS","AGENT","DEPOT"}
FINANCE_ROLES={"SUPER_ADMIN","ADMIN","FINANCE","EQUIPMENT_MANAGER"}
APPROVE_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","FINANCE"}
OWNER_TYPES={"PRINCIPAL","OVERSEAS_PARTNER","LEASING_COMPANY","INVESTOR","AGENT_SUPPLIED","SOC"}

SOPS=[
 {"code":"EQ-SOP-01","name":"Container Acquisition & Coding","steps":["Create/receive approved source transaction","Register actual physical container number once","Assign ownership/provider party","Assign Principal/branch/agent/depot custody","Complete technical/CSC verification","Make AVAILABLE only after required checks"]},
 {"code":"EQ-SOP-02","name":"Booking Equipment Allocation","steps":["Read authoritative Booking/Job requirement","Check local eligible stock","Protect configured safety stock","Reserve exact physical containers","Create shortage work item for residual demand","Expose same allocated containers to Release/B-L"]},
 {"code":"EQ-SOP-03","name":"Shortage Resolution","steps":["Expected return / repair recovery","Nearby surplus and reposition","Agent/partner supply","Lease","SOC where permitted","Purchase only for approved structural shortage"]},
 {"code":"EQ-SOP-04","name":"Custody Transfer & Movement","steps":["Record source custodian/location","Approve controlled move where required","Post departure/in-transit event","Post destination receipt","Transfer branch/agent/depot custody","Update Container Master and audit"]},
 {"code":"EQ-SOP-05","name":"Lease On-Hire / Off-Hire","steps":["Approved lease request and contract","Register actual leased boxes","On-hire verification","Accrue per-diem and handling costs","Monitor idle/off-hire due","Off-hire only after operational obligations clear"]},
 {"code":"EQ-SOP-06","name":"Purchase / Principal Fleet Addition","steps":["Approved purchase request/PO","Receive actual container numbers","Register supplier and invoice references","Technical inspection","Assign Principal owner and custody","Make AVAILABLE and recalculate shortage"]},
 {"code":"EQ-SOP-07","name":"Container Financial Control","steps":["Post container-level revenue/cost accrual","Link source document/job/booking/B-L/movement","Apply commission/revenue-share rule","Maker/checker controlled approval where required","Reconcile actual versus accrual","Roll into container profitability KPI"]},
 {"code":"EQ-SOP-08","name":"Empty Return / Reuse","steps":["Post gate-out/full","Track free days/detention","Confirm empty return and condition","Close outstanding movement costs","Set AVAILABLE at return depot","Recalculate local/global stock"]},
 {"code":"EQ-SOP-09","name":"Sale / Disposal","steps":["Mark allocate-for-sale","Block new operational allocation","Approve buyer/sale terms","Record sale invoice/proceeds","Complete release/disposal event","Exclude disposed unit from active fleet"]},
 {"code":"EQ-SOP-10","name":"Exception / Override","steps":["System blocks policy exception","Authorized user submits reason","Required checker/finance/management approval","Audit before/after and decision","Execute controlled override only after approval","Review exception in KPI/control tower"]},
]

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _json(v,default=None):
    try:return json.loads(v or "")
    except Exception:return {} if default is None else default

def _roles_priority(roles):
    order=["SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT","FINANCE","EQUIPMENT_CONTROLLER","BRANCH_OPS","OPS","DOCS","AGENT","DEPOT","AUDITOR","VIEWER"]
    rs=set(roles)
    return next((r for r in order if r in rs),"VIEWER")

def actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope):
    if x_m3_session:
        s=iam_session(conn,x_m3_session)
        rs=[r["role_code"] for r in roles_for(conn,s["user_id"])]
        role=_roles_priority(rs)
        branch=x_branch_scope
        if not branch:
            br=conn.execute("SELECT branch_code FROM iam_branches WHERE office_id=? ORDER BY id LIMIT 1",(s["home_office_id"],)).fetchone()
            branch=br["branch_code"] if br else None
        return {"role":role,"roles":rs,"user":s["user_ref"],"office":s["office_code"],"branch":branch,"agent":x_agent_scope,"depot":x_depot_scope}
    role=(x_role or "VIEWER").upper()
    return {"role":role,"roles":[role],"user":"uat-user","office":None,"branch":x_branch_scope,"agent":x_agent_scope,"depot":x_depot_scope}

def require_action(a,action):
    role=a["role"]
    if action=="view":return
    if action=="write" and role in WRITE_ROLES:return
    if action=="finance" and role in FINANCE_ROLES:return
    if action=="approve" and role in APPROVE_ROLES:return
    raise HTTPException(403,{"code":"EQUIPMENT_POLICY_DENIED","role":role,"action":action})

def scope_clause(a,alias="c"):
    role=a["role"]
    if role in GLOBAL_ROLES or role in {"FINANCE","EQUIPMENT_MANAGER"}: return "",[]
    if role=="AGENT":
        if not a["agent"]:raise HTTPException(403,{"code":"AGENT_SCOPE_REQUIRED"})
        return f" AND {alias}.agent_code=?", [a["agent"]]
    if role=="DEPOT":
        if not a["depot"]:raise HTTPException(403,{"code":"DEPOT_SCOPE_REQUIRED"})
        return f" AND {alias}.depot_code=?", [a["depot"]]
    if role in {"OPS","BRANCH_OPS","DOCS","EQUIPMENT_CONTROLLER"} and a["branch"]:
        return f" AND ({alias}.branch_code=? OR {alias}.branch_code IS NULL)",[a["branch"]]
    return "",[]

def audit(conn,a,action,container_id=None,job_id=None,before=None,after=None,meta=None):
    conn.execute("INSERT INTO audit_events(event_id,ts,actor_role,actor_scope,action,module,transaction_id,job_id,before_json,after_json,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
      (str(uuid.uuid4()),now(),a["role"],json.dumps({"office":a["office"],"branch":a["branch"],"agent":a["agent"],"depot":a["depot"]},sort_keys=True),
       action,"container-master",container_id,job_id,json.dumps(before or {},sort_keys=True),json.dumps(after or {},sort_keys=True),json.dumps(meta or {},sort_keys=True)))

class TechnicalUpdate(BaseModel):
    fields: dict[str,Any]

class PartyLink(BaseModel):
    party_role:str
    party_type:str
    party_code:str
    agreement_ref:Optional[str]=None
    valid_from:Optional[str]=None
    valid_to:Optional[str]=None
    commission_pct:float=0
    revenue_share_pct:float=0
    cost_share_pct:float=0
    metadata:dict[str,Any]=Field(default_factory=dict)

class FinancialEntry(BaseModel):
    entry_type:str
    charge_code:str
    amount:float
    currency:str="USD"
    job_ref:Optional[str]=None
    booking_ref:Optional[str]=None
    bl_ref:Optional[str]=None
    party_type:Optional[str]=None
    party_code:Optional[str]=None
    quantity:float=1
    rate:float=0
    basis:Optional[str]=None
    source_type:str
    source_ref:Optional[str]=None
    status:str="ACCRUED"

class ShareRule(BaseModel):
    share_type:str
    basis:str
    rate_pct:float=0
    fixed_amount:float=0
    currency:str="USD"
    owner_party_type:Optional[str]=None
    owner_party_code:Optional[str]=None
    agent_code:Optional[str]=None
    charge_code:Optional[str]=None
    valid_from:Optional[str]=None
    valid_to:Optional[str]=None

class DocumentLink(BaseModel):
    document_type:str
    file_name:str
    storage_ref:str

class ApplyShareBody(BaseModel):
    base_amount:float
    currency:str="USD"
    charge_code:Optional[str]=None
    booking_ref:Optional[str]=None
    job_ref:Optional[str]=None
    bl_ref:Optional[str]=None
    source_ref:Optional[str]=None

class BulkContainer(BaseModel):
    container_no:str
    size_type:str
    owner_party_type:str="PRINCIPAL"
    owner_party_code:str
    principal_code:Optional[str]=None
    current_port:Optional[str]=None
    branch_code:Optional[str]=None
    office_code:Optional[str]=None
    agent_code:Optional[str]=None
    depot_code:Optional[str]=None
    acquisition_type:str="LEGACY"
    acquisition_ref:Optional[str]=None
    purchase_order_ref:Optional[str]=None
    lease_contract_ref:Optional[str]=None
    supplier_or_lessor:Optional[str]=None
    value_amount:float=0
    currency:str="USD"
    technical:dict[str,Any]=Field(default_factory=dict)

class BulkRegister(BaseModel):
    records:list[BulkContainer]

TECH_FIELDS={
"kind","value_amount","csc_validity","csc_plate_no","csc_safety_approval_no","manufacturing_no","manufacturer","manufacture_date","manufacture_year",
"max_gross_weight","tare_weight","classification","grade_payload","capacity","stacking_weight","iso_code","test_load","model_no","machinery",
"inner_length","inner_width","inner_height","periodic_inspection_test","periodic_inspection_last_date","periodic_inspection_due_date","tank_kind",
"stacking_capability","remarks","allocate_for_sale","afghan_transit","verification_status","purchase_supplier_code","purchase_invoice_no",
"purchase_invoice_date","sale_customer_code","sale_invoice_no","sale_invoice_date","owner_party_type","owner_party_code","principal_code","current_port",
"branch_code","office_code","agent_code","depot_code","custodian_type","custodian_code","equipment_status","condition","available_from"
}

def get_container(conn,no,a):
    sc,args=scope_clause(a,"c")
    r=conn.execute("SELECT c.* FROM containers c WHERE c.container_no=?"+sc,[no]+args).fetchone()
    if not r:raise HTTPException(404,"Container not found in actor scope")
    return r

@router.get("/policies")
def policies(x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
             x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
             x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        rows=[dict(r) for r in c.execute("SELECT * FROM equipment_system_policies ORDER BY policy_key")]
        return {"actor":a,"policies":rows,"role_policy":{"global":a["role"] in GLOBAL_ROLES,"write":a["role"] in WRITE_ROLES,"finance":a["role"] in FINANCE_ROLES,"approve":a["role"] in APPROVE_ROLES}}
    finally:c.close()

@router.get("/sops")
def sops():return {"count":len(SOPS),"sops":SOPS}

@router.get("/containers")
def containers(q:Optional[str]=None,owner_party_type:Optional[str]=None,principal_code:Optional[str]=None,port:Optional[str]=None,
               agent_code:Optional[str]=None,depot_code:Optional[str]=None,status:Optional[str]=None,limit:int=Query(500,ge=1,le=2000),
               x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
               x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
               x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        sql="SELECT c.* FROM containers c WHERE 1=1";args=[]
        for col,val in [("owner_party_type",owner_party_type),("principal_code",principal_code),("current_port",port),("agent_code",agent_code),("depot_code",depot_code),("equipment_status",status)]:
            if val:sql+=f" AND c.{col}=?";args.append(val)
        if q:
            sql+=" AND (c.container_no LIKE ? OR c.owner_party_code LIKE ? OR c.principal_code LIKE ? OR c.booking_ref LIKE ?)"
            like=f"%{q}%";args.extend([like,like,like,like])
        sc,sa=scope_clause(a,"c");sql+=sc;args.extend(sa)
        sql+=" ORDER BY c.container_no LIMIT ?";args.append(limit)
        rows=[dict(r) for r in c.execute(sql,args).fetchall()];return {"actor":a,"count":len(rows),"records":rows}
    finally:c.close()

@router.get("/containers/{container_no}")
def container_detail(container_no:str,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                     x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                     x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);r=get_container(c,container_no,a);cid=r["id"]
        parties=[dict(x) for x in c.execute("SELECT * FROM container_party_links WHERE container_id=? ORDER BY id",(cid,)).fetchall()]
        docs=[dict(x) for x in c.execute("SELECT * FROM container_documents WHERE container_id=? ORDER BY id DESC",(cid,)).fetchall()]
        fin=[dict(x) for x in c.execute("SELECT * FROM container_financial_ledger WHERE container_id=? ORDER BY id DESC LIMIT 200",(cid,)).fetchall()]
        events=[dict(x) for x in c.execute("SELECT * FROM container_events WHERE container_id=? ORDER BY event_time DESC,id DESC LIMIT 200",(cid,)).fetchall()]
        sums={"revenue":0.0,"cost":0.0,"commission":0.0,"share":0.0}
        for x in fin:
            k=x["entry_type"].lower()
            if k in sums:sums[k]+=float(x["amount"] or 0)
        sums["margin"]=sums["revenue"]-sums["cost"]-sums["commission"]-sums["share"]
        return {"actor":a,"container":dict(r),"party_links":parties,"documents":docs,"financial_ledger":fin,"financial_summary":sums,"movement_events":events}
    finally:c.close()

@router.patch("/containers/{container_no}")
def update_container(container_no:str,b:TechnicalUpdate,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                     x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                     x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c);r=get_container(c,container_no,a)
        patch={k:v for k,v in b.fields.items() if k in TECH_FIELDS}
        if "owner_party_type" in patch and str(patch["owner_party_type"]).upper() not in OWNER_TYPES:raise HTTPException(422,"Invalid owner_party_type")
        if patch.get("owner_party_type") and not patch.get("owner_party_code",r["owner_party_code"]):raise HTTPException(422,{"code":"OWNER_PARTY_REQUIRED"})
        if not patch:raise HTTPException(422,"No allowed fields")
        sql="UPDATE containers SET "+",".join(f"{k}=?" for k in patch)+",updated_at=? WHERE id=?"
        c.execute(sql,list(patch.values())+[now(),r["id"]]);after=dict(c.execute("SELECT * FROM containers WHERE id=?",(r["id"],)).fetchone())
        audit(c,a,"CONTAINER_MASTER_UPDATE",r["id"],r["job_id"],dict(r),after,{"fields":list(patch)})
        c.execute("COMMIT");return {"ok":True,"record":after}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/containers/{container_no}/party-links")
def add_party(container_no:str,b:PartyLink,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
              x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
              x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c);r=get_container(c,container_no,a)
        c.execute("INSERT INTO container_party_links(container_id,party_role,party_type,party_code,agreement_ref,valid_from,valid_to,commission_pct,revenue_share_pct,cost_share_pct,status,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?, 'ACTIVE',?,?)",
          (r["id"],b.party_role.upper(),b.party_type.upper(),b.party_code,b.agreement_ref,b.valid_from,b.valid_to,b.commission_pct,b.revenue_share_pct,b.cost_share_pct,json.dumps(b.metadata,sort_keys=True),now()))
        audit(c,a,"CONTAINER_PARTY_LINK",r["id"],r["job_id"],{},b.model_dump(),{})
        c.execute("COMMIT");return {"ok":True}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/containers/{container_no}/documents")
def add_document(container_no:str,b:DocumentLink,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                 x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c);r=get_container(c,container_no,a)
        ref="CDOC-"+uuid.uuid4().hex[:12].upper()
        c.execute("INSERT INTO container_documents(document_ref,container_id,document_type,file_name,storage_ref,status,uploaded_by,uploaded_at) VALUES(?,?,?,?,?,'ACTIVE',?,?)",
          (ref,r["id"],b.document_type,b.file_name,b.storage_ref,a["user"],now()))
        c.execute("COMMIT");return {"ok":True,"document_ref":ref}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/containers/{container_no}/financial")
def post_financial(container_no:str,b:FinancialEntry,x_role:str=Header("FINANCE"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                   x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                   x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"finance");tx(c);r=get_container(c,container_no,a)
        et=b.entry_type.upper()
        if et not in {"REVENUE","COST","COMMISSION","SHARE"}:raise HTTPException(422,"Invalid entry_type")
        jid=r["job_id"]
        if b.job_ref:
            jr=c.execute("SELECT id FROM jobs WHERE job_ref=?",(b.job_ref,)).fetchone()
            if not jr:raise HTTPException(404,"Unknown job_ref")
            jid=jr["id"]
        ref="CFL-"+uuid.uuid4().hex[:12].upper()
        c.execute("""INSERT INTO container_financial_ledger(entry_ref,container_id,job_id,booking_ref,bl_ref,movement_event_id,entry_type,charge_code,party_type,party_code,amount,currency,quantity,rate,basis,source_type,source_ref,status,created_by,created_at)
                     VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (ref,r["id"],jid,b.booking_ref or r["booking_ref"],b.bl_ref,et,b.charge_code,b.party_type,b.party_code,b.amount,b.currency,b.quantity,b.rate,b.basis,b.source_type,b.source_ref,b.status,a["user"],now()))
        audit(c,a,"CONTAINER_FINANCIAL_POST",r["id"],jid,{},b.model_dump(),{"entry_ref":ref})
        c.execute("COMMIT");return {"ok":True,"entry_ref":ref}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/containers/{container_no}/share-rules")
def add_share_rule(container_no:str,b:ShareRule,x_role:str=Header("FINANCE"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                   x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                   x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"finance");tx(c);r=get_container(c,container_no,a)
        ref="CSR-"+uuid.uuid4().hex[:12].upper()
        c.execute("""INSERT INTO container_share_rules(rule_ref,container_id,owner_party_type,owner_party_code,agent_code,charge_code,share_type,basis,rate_pct,fixed_amount,currency,valid_from,valid_to,status,maker,checker,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING_APPROVAL',?,NULL,?)""",
          (ref,r["id"],b.owner_party_type or r["owner_party_type"],b.owner_party_code or r["owner_party_code"],b.agent_code or r["agent_code"],b.charge_code,b.share_type,b.basis,b.rate_pct,b.fixed_amount,b.currency,b.valid_from,b.valid_to,a["user"],now()))
        audit(c,a,"CONTAINER_SHARE_RULE_CREATE",r["id"],r["job_id"],{},b.model_dump(),{"rule_ref":ref})
        c.execute("COMMIT");return {"ok":True,"rule_ref":ref,"status":"PENDING_APPROVAL"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/share-rules/{rule_ref}/approve")
def approve_share_rule(rule_ref:str,x_role:str=Header("FINANCE"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                       x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                       x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"approve");tx(c)
        r=c.execute("SELECT * FROM container_share_rules WHERE rule_ref=?",(rule_ref,)).fetchone()
        if not r:raise HTTPException(404,"Unknown share rule")
        if r["maker"]==a["user"]:raise HTTPException(409,{"code":"MAKER_CHECKER_CONFLICT"})
        c.execute("UPDATE container_share_rules SET status='ACTIVE',checker=? WHERE id=?",(a["user"],r["id"]))
        audit(c,a,"CONTAINER_SHARE_RULE_APPROVE",r["container_id"],None,dict(r),{"status":"ACTIVE","checker":a["user"]},{"rule_ref":rule_ref})
        c.execute("COMMIT");return {"ok":True,"rule_ref":rule_ref,"status":"ACTIVE"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/containers/{container_no}/apply-sharing")
def apply_sharing(container_no:str,b:ApplyShareBody,x_role:str=Header("FINANCE"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                  x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                  x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"finance");tx(c);r=get_container(c,container_no,a)
        job_id=r["job_id"]
        if b.job_ref:
            jr=c.execute("SELECT id FROM jobs WHERE job_ref=?",(b.job_ref,)).fetchone()
            if not jr:raise HTTPException(404,"Unknown job_ref")
            job_id=jr["id"]
        rules=[dict(x) for x in c.execute("""SELECT * FROM container_share_rules
                 WHERE status='ACTIVE' AND (container_id=? OR container_id IS NULL)
                   AND (owner_party_type IS NULL OR owner_party_type=?)
                   AND (owner_party_code IS NULL OR owner_party_code=?)
                   AND (agent_code IS NULL OR agent_code=?)
                   AND (charge_code IS NULL OR charge_code=?)
                 ORDER BY id""",(r["id"],r["owner_party_type"],r["owner_party_code"],r["agent_code"],b.charge_code)).fetchall()]
        posted=[]
        for rule in rules:
            amount=(float(b.base_amount)*float(rule["rate_pct"] or 0)/100.0)+float(rule["fixed_amount"] or 0)
            if not amount:continue
            et="COMMISSION" if "COMMISSION" in str(rule["share_type"]).upper() else "SHARE"
            ref="CFL-"+uuid.uuid4().hex[:12].upper()
            party_type="AGENT" if et=="COMMISSION" and rule["agent_code"] else r["owner_party_type"]
            party_code=rule["agent_code"] if et=="COMMISSION" and rule["agent_code"] else r["owner_party_code"]
            c.execute("""INSERT INTO container_financial_ledger(entry_ref,container_id,job_id,booking_ref,bl_ref,movement_event_id,entry_type,charge_code,party_type,party_code,amount,currency,quantity,rate,basis,source_type,source_ref,status,created_by,created_at)
                         VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?,1,?,?, 'SHARE_RULE',?,'ACCRUED',?,?)""",
                      (ref,r["id"],job_id,b.booking_ref or r["booking_ref"],b.bl_ref,et,b.charge_code or rule["charge_code"] or "REVENUE_SHARE",
                       party_type,party_code,amount,b.currency,float(rule["rate_pct"] or 0),rule["basis"],b.source_ref or rule["rule_ref"],a["user"],now()))
            posted.append({"entry_ref":ref,"rule_ref":rule["rule_ref"],"entry_type":et,"amount":amount,"party_code":party_code})
        audit(c,a,"CONTAINER_SHARING_APPLY",r["id"],job_id,{},{"base_amount":b.base_amount,"posted":posted},{})
        c.execute("COMMIT");return {"ok":True,"posted":posted,"count":len(posted)}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/bulk-register")
def bulk_register(b:BulkRegister,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                  x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                  x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    created=[];failed=[]
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        for b0 in b.records:
            try:
                ot=b0.owner_party_type.upper()
                if ot not in OWNER_TYPES:raise ValueError("invalid owner type")
                if c.execute("SELECT 1 FROM containers WHERE container_no=?",(b0.container_no,)).fetchone():raise ValueError("duplicate")
                tech={k:v for k,v in b0.technical.items() if k in TECH_FIELDS}
                c.execute("""INSERT INTO containers(container_no,job_id,size_type,ownership,principal_code,current_port,agent_code,depot_code,equipment_status,condition,available_from,acquisition_type,acquisition_ref,lease_contract_ref,purchase_order_ref,supplier_or_lessor,unit_cost,currency,idle_days,updated_at,owner_party_type,owner_party_code,branch_code,office_code,value_amount,verification_status)
                             VALUES(?,NULL,?,?,?,?,?,?,'INSPECTION','PENDING_INSPECTION',NULL,?,?,?,?,?,0,?,0,?,?,?,?,?,?,'PENDING')""",
                    (b0.container_no,b0.size_type,"LEASED" if ot=="LEASING_COMPANY" else "OWNED",b0.principal_code or b0.owner_party_code,b0.current_port,b0.agent_code,b0.depot_code,b0.acquisition_type,b0.acquisition_ref,b0.lease_contract_ref,b0.purchase_order_ref,b0.supplier_or_lessor,b0.currency,now(),ot,b0.owner_party_code,b0.branch_code,b0.office_code,b0.value_amount))
                rr=c.execute("SELECT * FROM containers WHERE container_no=?",(b0.container_no,)).fetchone()
                if tech:
                    c.execute("UPDATE containers SET "+",".join(f"{k}=?" for k in tech)+" WHERE id=?",list(tech.values())+[rr["id"]])
                c.execute("INSERT INTO container_party_links(container_id,party_role,party_type,party_code,status,metadata_json,created_at) VALUES(?,?,?,?, 'ACTIVE','{}',?)",
                          (rr["id"],"ECONOMIC_OWNER",ot,b0.owner_party_code,now()))
                created.append(b0.container_no)
            except Exception as e:failed.append({"container_no":b0.container_no,"error":str(e)})
        audit(c,a,"BULK_CONTAINER_REGISTER",None,None,{},{"created":created,"failed":failed},{})
        c.execute("COMMIT");return {"ok":len(failed)==0,"created":created,"failed":failed}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.get("/booking-context/{job_ref}")
def booking_context(job_ref:str,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                    x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                    x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        j=c.execute("""SELECT j.id,j.job_ref,j.pol,j.pod,j.operational_status,b.booking_ref,a.code agent_code,a.name agent_name,bl.bill_no hbl_no
                       FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN agents a ON a.id=j.agent_id
                       LEFT JOIN bills bl ON bl.job_id=j.id AND bl.kind='HBL' WHERE j.job_ref=?""",(job_ref,)).fetchone()
        if not j:raise HTTPException(404,"Unknown job")
        if a["role"]=="AGENT" and a["agent"]!=j["agent_code"]:raise HTTPException(404,"Job outside agent scope")
        tr=c.execute("SELECT payload_json FROM transaction_records WHERE job_id=? AND module='booking' ORDER BY id LIMIT 1",(j["id"],)).fetchone()
        fields=_json(tr["payload_json"] if tr else None,{})
        allocations=[dict(r) for r in c.execute("SELECT * FROM containers WHERE job_id=? OR booking_ref=? ORDER BY container_no",(j["id"],j["booking_ref"])).fetchall()]
        fin=dict(c.execute("""SELECT COALESCE(SUM(CASE WHEN entry_type='REVENUE' THEN amount ELSE 0 END),0) revenue,
                                   COALESCE(SUM(CASE WHEN entry_type='COST' THEN amount ELSE 0 END),0) cost,
                                   COALESCE(SUM(CASE WHEN entry_type='COMMISSION' THEN amount ELSE 0 END),0) commission,
                                   COALESCE(SUM(CASE WHEN entry_type='SHARE' THEN amount ELSE 0 END),0) share
                            FROM container_financial_ledger WHERE job_id=? OR booking_ref=?""",(j["id"],j["booking_ref"])).fetchone())
        fin["margin"]=float(fin["revenue"])-float(fin["cost"])-float(fin["commission"])-float(fin["share"])
        req={"equipment":fields.get("Equipment"),"quantity":fields.get("Quantity"),"provider":fields.get("Equipment Provider"),"owner":fields.get("Container Owner"),"soc_coc":fields.get("SOC / COC")}
        return {"actor":a,"context":dict(j),"requirement":req,"booking_fields":fields,"allocated_containers":allocations,"financial_summary":fin}
    finally:c.close()

@router.get("/kpis")
def kpis(x_role:str=Header("AUDITOR"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
         x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
         x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        sc,args=scope_clause(a,"c")
        rows=[dict(r) for r in c.execute("SELECT c.* FROM containers c WHERE 1=1"+sc,args).fetchall()]
        total=len(rows);available=sum(x["equipment_status"]=="AVAILABLE" for x in rows);reserved=sum(x["equipment_status"]=="RESERVED" for x in rows)
        transit=sum(x["equipment_status"]=="IN_TRANSIT" for x in rows);repair=sum(x["equipment_status"]=="REPAIR" for x in rows)
        active=max(1,total-sum(x["equipment_status"] in {"OFF_HIRED","SOLD","SCRAPPED"} for x in rows))
        owner={}
        for x in rows:owner[x["owner_party_type"] or "UNSET"]=owner.get(x["owner_party_type"] or "UNSET",0)+1
        ids=[x["id"] for x in rows]
        revenue=cost=commission=share=0.0
        if ids:
            ph=",".join("?" for _ in ids)
            for f in c.execute(f"SELECT entry_type,COALESCE(SUM(amount),0) amount FROM container_financial_ledger WHERE container_id IN ({ph}) GROUP BY entry_type",ids).fetchall():
                if f["entry_type"]=="REVENUE":revenue=float(f["amount"])
                elif f["entry_type"]=="COST":cost=float(f["amount"])
                elif f["entry_type"]=="COMMISSION":commission=float(f["amount"])
                elif f["entry_type"]=="SHARE":share=float(f["amount"])
        return {"actor":a,"fleet":{"total":total,"available":available,"reserved":reserved,"in_transit":transit,"repair":repair,"utilization_pct":round(((reserved+transit)/active)*100,2)},
                "ownership":owner,"financial":{"revenue":revenue,"cost":cost,"commission":commission,"share":share,"margin":revenue-cost-commission-share},
                "exceptions":{"idle_gt_21":sum(float(x["idle_days"] or 0)>21 for x in rows),"inspection_due":sum(bool(x["periodic_inspection_due_date"]) for x in rows),
                              "unverified":sum(x["verification_status"]!="VERIFIED" for x in rows)}}
    finally:c.close()
