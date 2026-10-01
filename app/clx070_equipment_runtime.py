from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Optional
import datetime, json, uuid

from .db import connect, tx, backend_name, approved_database_target

router = APIRouter(prefix="/api/clx070/equipment", tags=["CLX-070 Equipment Runtime Integration"])

VALID_ROLES={"ADMIN","SUPER_ADMIN","OPS","DOCS","FINANCE","AGENT","VIEWER","AUDITOR"}
GLOBAL_ROLES={"ADMIN","SUPER_ADMIN"}
WRITE_ROLES={"ADMIN","SUPER_ADMIN","OPS","AGENT"}
APPROVE_ROLES={"ADMIN","SUPER_ADMIN","OPS","FINANCE"}
OWNER_TYPES={"PRINCIPAL","OVERSEAS_PARTNER","LEASING_COMPANY","INVESTOR","AGENT_SUPPLIED","SOC"}
FINANCE_CATEGORIES={"REVENUE","COST","COMMISSION","SHARE"}

def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def actor(x_role: str, x_agent_scope: Optional[str]):
    role=(x_role or "VIEWER").upper()
    if role not in VALID_ROLES:
        raise HTTPException(403,"Unknown role")
    return role,x_agent_scope

def scope_clause(role: str, agent_scope: Optional[str], branch_scope: Optional[str], alias: str=""):
    p=(alias+".") if alias else ""
    q=""; args=[]
    if role=="AGENT":
        if not agent_scope: raise HTTPException(403,"AGENT requires X-Agent-Scope")
        q+=f" AND {p}agent_code=?"; args.append(agent_scope)
    if branch_scope:
        q+=f" AND {p}branch_code=?"; args.append(branch_scope)
    return q,args

def require_policy(conn, role: str, action: str, agent_scope: Optional[str]=None, branch_scope: Optional[str]=None, container: Optional[dict]=None):
    rows=conn.execute("""SELECT * FROM equipment_policy_rules
                         WHERE active=TRUE AND subject_role=? AND action_code=? AND effect='ALLOW'
                         ORDER BY CASE scope_type WHEN 'AGENT' THEN 1 WHEN 'BRANCH' THEN 2 ELSE 3 END""",(role,action)).fetchall()
    if not rows:
        raise HTTPException(403,{"code":"EQUIPMENT_POLICY_DENY","role":role,"action":action})
    if container:
        if role=="AGENT" and container.get("agent_code")!=agent_scope:
            raise HTTPException(404,"Container outside agent custody scope")
        if branch_scope and container.get("branch_code")!=branch_scope:
            raise HTTPException(404,"Container outside branch custody scope")
    return dict(rows[0])

def get_container_scoped(conn, container_no: str, role: str, agent_scope: Optional[str], branch_scope: Optional[str]):
    q="SELECT * FROM containers WHERE container_no=?"; args=[container_no]
    sc,sa=scope_clause(role,agent_scope,branch_scope)
    q+=sc; args+=sa
    r=conn.execute(q,args).fetchone()
    if not r: raise HTTPException(404,"Container not found in actor custody scope")
    return dict(r)

def owner_party_from(body):
    owner_type=(getattr(body,"owner_type",None) or "").upper()
    if not owner_type:
        own=(getattr(body,"ownership","") or "").upper()
        owner_type={"OWNED":"PRINCIPAL","LEASED":"LEASING_COMPANY","AGENT":"AGENT_SUPPLIED","SOC":"SOC","OVERSEAS_PARTNER":"OVERSEAS_PARTNER","INVESTOR":"INVESTOR"}.get(own,"PRINCIPAL")
    if owner_type not in OWNER_TYPES: raise HTTPException(422,"Invalid owner_type")
    code=(getattr(body,"owner_party_code",None)
          or getattr(body,"principal_owner_code",None)
          or getattr(body,"overseas_partner_code",None)
          or getattr(body,"leasing_company_code",None)
          or getattr(body,"investor_code",None)
          or getattr(body,"agent_supplier_code",None)
          or getattr(body,"principal_code",None))
    if owner_type!="SOC" and not code:
        raise HTTPException(422,{"code":"OWNER_PARTY_REQUIRED","owner_type":owner_type})
    return owner_type,code or "SHIPPER/SOC"

def finance_summary(conn, container_id: int):
    rows=conn.execute("""SELECT entry_category,currency,
      SUM(CASE WHEN status IN ('APPROVED','POSTED') THEN amount ELSE 0 END) amount
      FROM container_financial_entries WHERE container_id=?
      GROUP BY entry_category,currency ORDER BY currency,entry_category""",(container_id,)).fetchall()
    by_currency={}
    for r in rows:
        d=by_currency.setdefault(r["currency"],{"revenue":0.0,"cost":0.0,"commission":0.0,"share":0.0,"net":0.0})
        k=r["entry_category"].lower(); d[k]=float(r["amount"] or 0)
    for d in by_currency.values():
        d["net"]=d["revenue"]-d["cost"]-d["commission"]-d["share"]
    return by_currency

def rowdict(r):
    return dict(r) if r else None

def schema_ready(conn) -> bool:
    try:
        row=conn.execute("""
          SELECT COUNT(*) AS n
          FROM information_schema.columns
          WHERE table_schema='public' AND table_name='containers'
            AND column_name IN ('ownership','current_port','equipment_status','condition','available_from','booking_ref','owner_type','owner_party_code','branch_code')
        """).fetchone()
        return bool(row and int(row["n"])>=9)
    except Exception:
        try:
            cols=[x["name"] for x in conn.execute("PRAGMA table_info(containers)").fetchall()]
            return all(x in cols for x in ["ownership","current_port","equipment_status","condition","available_from","booking_ref","owner_type","owner_party_code","branch_code"])
        except Exception:
            return False

def require_schema(conn):
    if not schema_ready(conn):
        raise HTTPException(503,{"code":"CLX070_SCHEMA_NOT_APPLIED"})

def audit(conn, role, scope, action, container_id=None, job_id=None, before=None, after=None, meta=None):
    conn.execute(
      "INSERT INTO audit_events(event_id,ts,actor_role,actor_scope,action,module,transaction_id,job_id,before_json,after_json,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
      (
        str(uuid.uuid4()),now(),role,scope,action,"equipment-runtime",container_id,job_id,
        json.dumps(before or {},sort_keys=True),json.dumps(after or {},sort_keys=True),json.dumps(meta or {},sort_keys=True)
      )
    )

class AllocateBody(BaseModel):
    booking_ref: str
    job_ref: Optional[str]=None
    port: str
    agent_code: Optional[str]=None
    depot_code: Optional[str]=None
    size_type: str
    qty: int=Field(ge=1,le=500)
    safety_stock: int=Field(default=0,ge=0,le=500)
    required_by: Optional[str]=None
    container_nos: list[str]=Field(default_factory=list)

class WorkItemBody(BaseModel):
    work_type: str
    booking_ref: Optional[str]=None
    job_ref: Optional[str]=None
    size_type: str
    qty: int=Field(ge=1,le=10000)
    source_port: Optional[str]=None
    destination_port: Optional[str]=None
    source_agent: Optional[str]=None
    destination_agent: Optional[str]=None
    source_depot: Optional[str]=None
    destination_depot: Optional[str]=None
    estimated_cost: float=Field(default=0,ge=0)
    currency: str="USD"
    required_by: Optional[str]=None
    payload: dict[str,Any]=Field(default_factory=dict)

class DecisionBody(BaseModel):
    decision: str
    note: Optional[str]=None

class ContainerRegisterBody(BaseModel):
    container_no: str
    size_type: str
    ownership: str
    owner_type: Optional[str]=None
    owner_party_code: Optional[str]=None
    principal_code: str="M3 NVOCC"
    principal_owner_code: Optional[str]=None
    overseas_partner_code: Optional[str]=None
    leasing_company_code: Optional[str]=None
    investor_code: Optional[str]=None
    agent_supplier_code: Optional[str]=None
    region_code: Optional[str]=None
    branch_code: Optional[str]=None
    current_port: str
    agent_code: Optional[str]=None
    depot_code: Optional[str]=None
    condition: str="GOOD"
    available_from: Optional[str]=None
    acquisition_type: str
    acquisition_ref: Optional[str]=None
    lease_contract_ref: Optional[str]=None
    purchase_order_ref: Optional[str]=None
    supplier_or_lessor: Optional[str]=None
    manufacturer: Optional[str]=None
    manufacture_date: Optional[str]=None
    manufacture_year: Optional[int]=None
    csc_validity: Optional[str]=None
    csc_plate_no: Optional[str]=None
    classification: Optional[str]=None
    grade_payload: Optional[str]=None
    max_gross_weight: float=0
    tare_weight: float=0
    capacity_cbm: float=0
    iso_code: Optional[str]=None
    model_no: Optional[str]=None
    machinery: Optional[str]=None
    inner_length: float=0
    inner_width: float=0
    inner_height: float=0
    allocate_for_sale: bool=False
    afghan_transit: bool=False
    technical_remarks: Optional[str]=None
    unit_cost: float=0
    currency: str="USD"

class MovementBody(BaseModel):
    event_type: str
    event_time: Optional[str]=None
    port: Optional[str]=None
    branch_code: Optional[str]=None
    agent_code: Optional[str]=None
    depot_code: Optional[str]=None
    status: Optional[str]=None
    booking_ref: Optional[str]=None
    job_ref: Optional[str]=None
    detail: dict[str,Any]=Field(default_factory=dict)

class ContainerMasterUpdateBody(BaseModel):
    owner_type: Optional[str]=None
    owner_party_code: Optional[str]=None
    principal_owner_code: Optional[str]=None
    overseas_partner_code: Optional[str]=None
    leasing_company_code: Optional[str]=None
    investor_code: Optional[str]=None
    agent_supplier_code: Optional[str]=None
    region_code: Optional[str]=None
    branch_code: Optional[str]=None
    current_port: Optional[str]=None
    agent_code: Optional[str]=None
    depot_code: Optional[str]=None
    equipment_status: Optional[str]=None
    condition: Optional[str]=None
    available_from: Optional[str]=None
    manufacturer: Optional[str]=None
    manufacture_date: Optional[str]=None
    manufacture_year: Optional[int]=None
    csc_validity: Optional[str]=None
    csc_plate_no: Optional[str]=None
    classification: Optional[str]=None
    grade_payload: Optional[str]=None
    max_gross_weight: Optional[float]=None
    tare_weight: Optional[float]=None
    capacity_cbm: Optional[float]=None
    iso_code: Optional[str]=None
    model_no: Optional[str]=None
    machinery: Optional[str]=None
    inner_length: Optional[float]=None
    inner_width: Optional[float]=None
    inner_height: Optional[float]=None
    allocate_for_sale: Optional[bool]=None
    afghan_transit: Optional[bool]=None
    technical_remarks: Optional[str]=None

class PartyLinkBody(BaseModel):
    party_role: str
    party_code: str
    party_name: Optional[str]=None
    is_owner: bool=False
    is_custodian: bool=False
    valid_from: Optional[str]=None
    valid_to: Optional[str]=None
    terms: dict[str,Any]=Field(default_factory=dict)

class FinanceEntryBody(BaseModel):
    job_ref: Optional[str]=None
    booking_ref: Optional[str]=None
    bl_ref: Optional[str]=None
    entry_category: str
    charge_code: str
    description: Optional[str]=None
    amount: float
    currency: str="USD"
    party_type: Optional[str]=None
    party_code: Optional[str]=None
    source_type: str="MANUAL"
    source_ref: Optional[str]=None
    movement_event_ref: Optional[str]=None
    metadata: dict[str,Any]=Field(default_factory=dict)

class FinanceDecisionBody(BaseModel):
    decision: str
    note: Optional[str]=None

class ShareRuleBody(BaseModel):
    party_type: str
    party_code: str
    basis: str="REVENUE"
    rate_percent: float=Field(default=0,ge=0,le=100)
    fixed_amount: float=Field(default=0,ge=0)
    currency: str="USD"
    charge_code: Optional[str]=None
    valid_from: Optional[str]=None
    valid_to: Optional[str]=None

@router.get("/readiness")
def readiness():
    conn=connect()
    try:
        ready=schema_ready(conn)
        counts={}
        if ready:
            counts=dict(conn.execute("""
              SELECT COUNT(*) AS total,
                     COUNT(*) FILTER (WHERE equipment_status='AVAILABLE') AS available,
                     COUNT(*) FILTER (WHERE equipment_status='RESERVED') AS reserved,
                     COUNT(*) FILTER (WHERE equipment_status='IN_TRANSIT') AS in_transit,
                     COUNT(*) FILTER (WHERE equipment_status='REPAIR') AS repair
              FROM containers
            """).fetchone())
        return {
          "project":"M3 NVOCC ERP","phase":"CLX-070","backend":backend_name(),
          "approved_database_target":approved_database_target(),"schema_ready":ready,
          "production_traffic_required":False,"live_providers_required":False,
          "counts":counts
        }
    finally:
        conn.close()

@router.get("/containers")
def containers(
    port: Optional[str]=None, agent_code: Optional[str]=None, depot_code: Optional[str]=None,
    size_type: Optional[str]=None, status: Optional[str]=None, ownership: Optional[str]=None,
    limit: int=Query(500,ge=1,le=2000),
    x_role: str=Header("VIEWER"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)
):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"VIEW",scope,x_branch_scope)
        q="SELECT * FROM containers WHERE 1=1"; args=[]
        for col,val in [("current_port",port),("agent_code",agent_code),("depot_code",depot_code),("size_type",size_type),("equipment_status",status),("ownership",ownership)]:
            if val is not None:
                q+=f" AND {col}=?"; args.append(val)
        sc,sa=scope_clause(role,scope,x_branch_scope)
        q+=sc; args+=sa
        q+=" ORDER BY container_no LIMIT ?"; args.append(limit)
        rows=[dict(x) for x in conn.execute(q,args).fetchall()]
        return {"count":len(rows),"records":rows}
    finally:
        conn.close()

@router.get("/availability")
def availability(
    port: str, size_type: str, qty: int=Query(1,ge=1,le=500), safety_stock: int=Query(0,ge=0,le=500),
    agent_code: Optional[str]=None, depot_code: Optional[str]=None, required_by: Optional[str]=None,
    x_role: str=Header("VIEWER"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)
):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"VIEW",scope,x_branch_scope)
        q="""SELECT * FROM containers
             WHERE current_port=? AND size_type=? AND equipment_status='AVAILABLE'
               AND condition IN ('GOOD','VERIFIED')"""
        args=[port,size_type]
        if agent_code: q+=" AND agent_code=?"; args.append(agent_code)
        if depot_code: q+=" AND depot_code=?"; args.append(depot_code)
        if required_by: q+=" AND (available_from IS NULL OR available_from='' OR available_from<=?)"; args.append(required_by)
        sc,sa=scope_clause(role,scope,x_branch_scope)
        q+=sc; args+=sa
        q+=" ORDER BY CASE owner_type WHEN 'PRINCIPAL' THEN 1 WHEN 'OVERSEAS_PARTNER' THEN 2 WHEN 'AGENT_SUPPLIED' THEN 3 WHEN 'LEASING_COMPANY' THEN 4 WHEN 'INVESTOR' THEN 5 WHEN 'SOC' THEN 6 ELSE 9 END, CASE ownership WHEN 'OWNED' THEN 1 WHEN 'AGENT' THEN 2 WHEN 'LEASED' THEN 3 WHEN 'SOC' THEN 4 ELSE 9 END, idle_days DESC, container_no"
        rows=[dict(x) for x in conn.execute(q,args).fetchall()]
        usable=max(0,len(rows)-safety_stock)
        proposed=rows[:min(qty,usable)]
        shortage=max(0,qty-len(proposed))
        return {
          "port":port,"size_type":size_type,"demand_qty":qty,"physical_available":len(rows),
          "safety_stock":safety_stock,"usable_available":usable,"proposed_count":len(proposed),
          "shortage":shortage,"proposed_containers":proposed
        }
    finally:
        conn.close()

@router.post("/allocate")
def allocate(body: AllocateBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"ALLOCATE",scope,x_branch_scope); tx(conn)
        job_id=None
        if body.job_ref:
            jr=conn.execute("SELECT id FROM jobs WHERE job_ref=?",(body.job_ref,)).fetchone()
            if not jr: raise HTTPException(404,"Unknown job_ref")
            job_id=jr["id"]
        q="""SELECT * FROM containers
             WHERE current_port=? AND size_type=? AND equipment_status='AVAILABLE'
               AND condition IN ('GOOD','VERIFIED')"""
        args=[body.port,body.size_type]
        if body.agent_code: q+=" AND agent_code=?";args.append(body.agent_code)
        if body.depot_code: q+=" AND depot_code=?";args.append(body.depot_code)
        if body.required_by: q+=" AND (available_from IS NULL OR available_from='' OR available_from<=?)";args.append(body.required_by)
        if body.container_nos:
            ph=",".join("?" for _ in body.container_nos); q+=f" AND container_no IN ({ph})";args.extend(body.container_nos)
        sc,sa=scope_clause(role,scope,x_branch_scope)
        q+=sc;args+=sa
        q+=" ORDER BY CASE owner_type WHEN 'PRINCIPAL' THEN 1 WHEN 'OVERSEAS_PARTNER' THEN 2 WHEN 'AGENT_SUPPLIED' THEN 3 WHEN 'LEASING_COMPANY' THEN 4 WHEN 'INVESTOR' THEN 5 WHEN 'SOC' THEN 6 ELSE 9 END, CASE ownership WHEN 'OWNED' THEN 1 WHEN 'AGENT' THEN 2 WHEN 'LEASED' THEN 3 WHEN 'SOC' THEN 4 ELSE 9 END, idle_days DESC, container_no"
        rows=[dict(x) for x in conn.execute(q,args).fetchall()]
        usable=max(0,len(rows)-body.safety_stock)
        chosen=rows[:min(body.qty,usable)]
        for c in chosen:
            before=dict(c)
            conn.execute("""UPDATE containers SET equipment_status='RESERVED',booking_ref=?,job_id=COALESCE(?,job_id),updated_at=? WHERE id=? AND equipment_status='AVAILABLE'""",
                         (body.booking_ref,job_id,now(),c["id"]))
            after=dict(c);after.update({"equipment_status":"RESERVED","booking_ref":body.booking_ref,"job_id":job_id or c.get("job_id")})
            audit(conn,role,scope,"ALLOCATE",c["id"],job_id,before,after,{"booking_ref":body.booking_ref})
        conn.execute("COMMIT")
        return {"ok":True,"booking_ref":body.booking_ref,"allocated":[c["container_no"] for c in chosen],"allocated_qty":len(chosen),"shortage":max(0,body.qty-len(chosen))}
    except HTTPException:
        conn.execute("ROLLBACK");raise
    except Exception:
        conn.execute("ROLLBACK");raise
    finally:
        conn.close()

@router.post("/containers/register")
def register_container(body: ContainerRegisterBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    ownership=body.ownership.upper()
    if ownership not in {"OWNED","LEASED","AGENT","SOC","OVERSEAS_PARTNER","INVESTOR"}: raise HTTPException(422,"Invalid ownership")
    owner_type,owner_party_code=owner_party_from(body)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"MASTER_EDIT",scope,x_branch_scope); tx(conn)
        if conn.execute("SELECT 1 FROM containers WHERE container_no=?",(body.container_no,)).fetchone():
            raise HTTPException(409,"Container already exists")
        cur=conn.execute("""INSERT INTO containers(
          container_no,job_id,size_type,ownership,owner_type,owner_party_code,principal_code,principal_owner_code,overseas_partner_code,
          leasing_company_code,investor_code,agent_supplier_code,region_code,branch_code,current_port,agent_code,depot_code,equipment_status,condition,
          available_from,acquisition_type,acquisition_ref,lease_contract_ref,purchase_order_ref,supplier_or_lessor,manufacturer,manufacture_date,
          manufacture_year,csc_validity,csc_plate_no,classification,grade_payload,max_gross_weight,tare_weight,capacity_cbm,iso_code,model_no,machinery,
          inner_length,inner_width,inner_height,allocate_for_sale,afghan_transit,technical_remarks,unit_cost,currency,idle_days,updated_at
        ) VALUES(?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'AVAILABLE',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?) RETURNING id""",
        (body.container_no,body.size_type,ownership,owner_type,owner_party_code,body.principal_code,body.principal_owner_code,
         body.overseas_partner_code,body.leasing_company_code,body.investor_code,body.agent_supplier_code,body.region_code,
         body.branch_code or x_branch_scope,body.current_port,body.agent_code,body.depot_code,body.condition,body.available_from,
         body.acquisition_type,body.acquisition_ref,body.lease_contract_ref,body.purchase_order_ref,body.supplier_or_lessor,body.manufacturer,
         body.manufacture_date,body.manufacture_year,body.csc_validity,body.csc_plate_no,body.classification,body.grade_payload,
         body.max_gross_weight,body.tare_weight,body.capacity_cbm,body.iso_code,body.model_no,body.machinery,body.inner_length,body.inner_width,
         body.inner_height,body.allocate_for_sale,body.afghan_transit,body.technical_remarks,body.unit_cost,body.currency,now()))
        rid=cur.fetchone()["id"]
        t=now()
        conn.execute("""INSERT INTO container_party_links(container_id,party_role,party_code,party_name,is_owner,is_custodian,created_at,updated_at)
                        VALUES(?,?,?,?,TRUE,FALSE,?,?) ON CONFLICT(container_id,party_role,party_code) DO NOTHING""",
                     (rid,owner_type,owner_party_code,owner_party_code,t,t))
        if body.agent_code:
            conn.execute("""INSERT INTO container_party_links(container_id,party_role,party_code,party_name,is_owner,is_custodian,created_at,updated_at)
                            VALUES(?,'AGENT_CUSTODIAN',?,?,FALSE,TRUE,?,?) ON CONFLICT(container_id,party_role,party_code) DO NOTHING""",
                         (rid,body.agent_code,body.agent_code,t,t))
        if body.depot_code:
            conn.execute("""INSERT INTO container_party_links(container_id,party_role,party_code,party_name,is_owner,is_custodian,created_at,updated_at)
                            VALUES(?,'DEPOT_CUSTODIAN',?,?,FALSE,TRUE,?,?) ON CONFLICT(container_id,party_role,party_code) DO NOTHING""",
                         (rid,body.depot_code,body.depot_code,t,t))
        if body.unit_cost>0:
            eref="CFL-"+datetime.datetime.now().strftime("%Y%m%d%H%M%S")+"-"+str(uuid.uuid4())[:6].upper()
            charge="PURCHASE_ACQUISITION" if body.acquisition_type.upper()=="PURCHASE" else "LEASE_ON_HIRE" if body.acquisition_type.upper()=="LEASE" else "ACQUISITION"
            conn.execute("""INSERT INTO container_financial_entries(entry_ref,container_id,entry_category,charge_code,description,amount,currency,
              party_type,party_code,source_type,source_ref,status,maker_role,created_at,updated_at)
              VALUES(?,?,'COST',?,?,?,?,?,?, 'ACQUISITION',?,'DRAFT',?,?,?)""",
              (eref,rid,charge,"Container acquisition/on-hire cost",body.unit_cost,body.currency,owner_type,owner_party_code,
               body.acquisition_ref or body.purchase_order_ref or body.lease_contract_ref,role,t,t))
        after=dict(conn.execute("SELECT * FROM containers WHERE id=?",(rid,)).fetchone())
        audit(conn,role,scope,"REGISTER_CONTAINER",rid,None,{},after,{"acquisition_type":body.acquisition_type,"owner_type":owner_type,"owner_party_code":owner_party_code})
        conn.execute("COMMIT");return {"ok":True,"record":after}
    except HTTPException:
        conn.execute("ROLLBACK");raise
    except Exception:
        conn.execute("ROLLBACK");raise
    finally:
        conn.close()

@router.post("/containers/{container_no}/events")
def post_movement(container_no: str, body: MovementBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"MOVE",scope,x_branch_scope); tx(conn)
        c=get_container_scoped(conn,container_no,role,scope,x_branch_scope)
        before=dict(c)
        job_id=c["job_id"]
        if body.job_ref:
            jr=conn.execute("SELECT id FROM jobs WHERE job_ref=?",(body.job_ref,)).fetchone()
            if not jr: raise HTTPException(404,"Unknown job_ref")
            job_id=jr["id"]
        new_status=body.status or body.event_type
        conn.execute("""UPDATE containers SET current_port=COALESCE(?,current_port),branch_code=COALESCE(?,branch_code),agent_code=COALESCE(?,agent_code),depot_code=COALESCE(?,depot_code),
          equipment_status=?,booking_ref=COALESCE(?,booking_ref),job_id=COALESCE(?,job_id),idle_days=0,updated_at=? WHERE id=?""",
          (body.port,body.branch_code,body.agent_code,body.depot_code,new_status,body.booking_ref,job_id,now(),c["id"]))
        evt_time=body.event_time or now()
        loc=" / ".join(x for x in [body.port or c["current_port"],body.agent_code or c["agent_code"],body.depot_code or c["depot_code"]] if x)
        if job_id:
            conn.execute("""INSERT INTO container_events(event_id,job_id,container_id,event_type,event_time,location,status,source_module,detail_json)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                         (str(uuid.uuid4()),job_id,c["id"],body.event_type,evt_time,loc,new_status,"equipment-runtime",json.dumps(body.detail,sort_keys=True)))
        actual_cost=float(body.detail.get("actual_cost") or 0)
        if actual_cost>0:
            eref="CFL-"+datetime.datetime.now().strftime("%Y%m%d%H%M%S")+"-"+str(uuid.uuid4())[:6].upper()
            conn.execute("""INSERT INTO container_financial_entries(entry_ref,container_id,job_id,booking_ref,entry_category,charge_code,description,amount,currency,
              party_type,party_code,source_type,source_ref,status,maker_role,metadata_json,created_at,updated_at)
              VALUES(?,?,?,?, 'COST',?,?,?,?,?,?, 'MOVEMENT',?,'DRAFT',?,?,?,?)""",
              (eref,c["id"],job_id,body.booking_ref or c.get("booking_ref"),str(body.detail.get("charge_code") or "MOVE_COST"),
               str(body.detail.get("description") or body.event_type+" movement cost"),actual_cost,str(body.detail.get("currency") or "USD"),
               str(body.detail.get("party_type") or "SERVICE_PROVIDER"),body.detail.get("party_code"),
               body.detail.get("source_ref") or body.event_type,role,json.dumps(body.detail,sort_keys=True),now(),now()))
        after=dict(conn.execute("SELECT * FROM containers WHERE id=?",(c["id"],)).fetchone())
        audit(conn,role,scope,"MOVEMENT_EVENT",c["id"],job_id,before,after,{"event_type":body.event_type,"detail":body.detail})
        conn.execute("COMMIT")
        return {"ok":True,"record":after}
    except HTTPException:
        conn.execute("ROLLBACK");raise
    except Exception:
        conn.execute("ROLLBACK");raise
    finally:
        conn.close()

@router.post("/work-items")
def create_work_item(body: WorkItemBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    if role not in WRITE_ROLES: raise HTTPException(403,"Write role required")
    wt=body.work_type.upper()
    if wt not in {"REPOSITION","LEASE","PURCHASE","REPAIR","AGENT_SUPPLY","SOC","SHORTAGE"}:
        raise HTTPException(422,"Invalid work_type")
    conn=connect()
    try:
        require_schema(conn);tx(conn)
        ref=f"EQ-{wt[:3]}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:6].upper()}"
        payload=body.model_dump()
        cur=conn.execute("""INSERT INTO equipment_work_items(
          work_ref,work_type,status,booking_ref,job_ref,size_type,qty,source_port,destination_port,source_agent,destination_agent,
          source_depot,destination_depot,estimated_cost,currency,required_by,payload_json,maker_role,created_at,updated_at
        ) VALUES(?,?,'DRAFT',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
        (ref,wt,body.booking_ref,body.job_ref,body.size_type,body.qty,body.source_port,body.destination_port,body.source_agent,
         body.destination_agent,body.source_depot,body.destination_depot,body.estimated_cost,body.currency,body.required_by,
         json.dumps(payload,sort_keys=True),role,now(),now()))
        wid=cur.fetchone()["id"]
        conn.execute("COMMIT")
        return {"ok":True,"id":wid,"work_ref":ref,"status":"DRAFT"}
    except HTTPException:
        conn.execute("ROLLBACK");raise
    except Exception:
        conn.execute("ROLLBACK");raise
    finally:
        conn.close()

@router.get("/work-items")
def work_items(status: Optional[str]=None, work_type: Optional[str]=None, limit: int=Query(200,ge=1,le=1000), x_role: str=Header("VIEWER")):
    actor(x_role,None)
    conn=connect()
    try:
        require_schema(conn)
        q="SELECT * FROM equipment_work_items WHERE 1=1";args=[]
        if status:q+=" AND status=?";args.append(status)
        if work_type:q+=" AND work_type=?";args.append(work_type.upper())
        q+=" ORDER BY id DESC LIMIT ?";args.append(limit)
        rows=[dict(x) for x in conn.execute(q,args).fetchall()]
        for r in rows:
            try:r["payload"]=json.loads(r.pop("payload_json") or "{}")
            except Exception:r["payload"]={}
        return {"count":len(rows),"records":rows}
    finally:
        conn.close()

@router.post("/work-items/{work_ref}/decision")
def work_item_decision(work_ref: str, body: DecisionBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    if role not in APPROVE_ROLES: raise HTTPException(403,"Approval role required")
    decision=body.decision.upper()
    if decision not in {"SUBMIT","APPROVE","REJECT","CANCEL","COMPLETE"}: raise HTTPException(422,"Invalid decision")
    target={"SUBMIT":"SUBMITTED","APPROVE":"APPROVED","REJECT":"REJECTED","CANCEL":"CANCELLED","COMPLETE":"COMPLETED"}[decision]
    conn=connect()
    try:
        require_schema(conn);tx(conn)
        r=conn.execute("SELECT * FROM equipment_work_items WHERE work_ref=?",(work_ref,)).fetchone()
        if not r: raise HTTPException(404,"Unknown work item")
        if decision=="APPROVE" and r["maker_role"]==role:
            raise HTTPException(409,{"code":"MAKER_CHECKER_CONFLICT"})
        conn.execute("""UPDATE equipment_work_items SET status=?,checker_role=CASE WHEN ?='APPROVED' THEN ? ELSE checker_role END,
          decision_note=?,updated_at=? WHERE id=?""",(target,target,role,body.note,now(),r["id"]))
        conn.execute("COMMIT");return {"ok":True,"work_ref":work_ref,"status":target}
    except HTTPException:
        conn.execute("ROLLBACK");raise
    except Exception:
        conn.execute("ROLLBACK");raise
    finally:
        conn.close()


@router.get("/policies")
def policies(x_role: str=Header("VIEWER"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn)
        rows=[dict(r) for r in conn.execute("""SELECT rule_code,action_code,subject_role,scope_type,effect,threshold_amount,currency,four_eyes,config_json
                                               FROM equipment_policy_rules WHERE active=TRUE AND subject_role=? ORDER BY action_code,rule_code""",(role,)).fetchall()]
        for r in rows:
            try:r["config"]=json.loads(r.pop("config_json") or "{}")
            except Exception:r["config"]={}
        return {"role":role,"agent_scope":scope,"branch_scope":x_branch_scope,"rules":rows}
    finally: conn.close()

@router.get("/containers/{container_no}/profile")
def container_profile(container_no: str, x_role: str=Header("VIEWER"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"VIEW",scope,x_branch_scope)
        c=get_container_scoped(conn,container_no,role,scope,x_branch_scope)
        parties=[dict(r) for r in conn.execute("SELECT * FROM container_party_links WHERE container_id=? ORDER BY is_owner DESC,is_custodian DESC,party_role,party_code",(c["id"],)).fetchall()]
        entries=[dict(r) for r in conn.execute("""SELECT * FROM container_financial_entries WHERE container_id=? ORDER BY id DESC LIMIT 200""",(c["id"],)).fetchall()]
        shares=[dict(r) for r in conn.execute("""SELECT * FROM container_share_rules WHERE container_id=? AND active=TRUE ORDER BY id DESC""",(c["id"],)).fetchall()]
        events=[]
        if c.get("job_id"):
            events=[dict(r) for r in conn.execute("""SELECT event_id,event_type,event_time,location,status,source_module,detail_json
                                                    FROM container_events WHERE container_id=? ORDER BY event_time DESC LIMIT 200""",(c["id"],)).fetchall()]
        return {"container":c,"parties":parties,"financial_entries":entries,"share_rules":shares,"financial_summary":finance_summary(conn,c["id"]),"journey_events":events}
    finally: conn.close()

@router.patch("/containers/{container_no}/master")
def update_container_master(container_no: str, body: ContainerMasterUpdateBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"MASTER_EDIT",scope,x_branch_scope); tx(conn)
        c=get_container_scoped(conn,container_no,role,scope,x_branch_scope)
        before=dict(c)
        patch=body.model_dump(exclude_none=True)
        if "owner_type" in patch:
            patch["owner_type"]=str(patch["owner_type"]).upper()
            if patch["owner_type"] not in OWNER_TYPES: raise HTTPException(422,"Invalid owner_type")
        allowed=set(ContainerMasterUpdateBody.model_fields.keys())
        for k in list(patch):
            if k not in allowed: patch.pop(k,None)
        if patch:
            sets=",".join(f"{k}=?" for k in patch)
            conn.execute(f"UPDATE containers SET {sets},updated_at=? WHERE id=?",[*patch.values(),now(),c["id"]])
        after=dict(conn.execute("SELECT * FROM containers WHERE id=?",(c["id"],)).fetchone())
        audit(conn,role,scope,"CONTAINER_MASTER_UPDATE",c["id"],c.get("job_id"),before,after,{"fields":sorted(patch)})
        conn.execute("COMMIT"); return {"ok":True,"record":after}
    except HTTPException:
        conn.execute("ROLLBACK"); raise
    except Exception:
        conn.execute("ROLLBACK"); raise
    finally: conn.close()

@router.post("/containers/{container_no}/parties")
def link_container_party(container_no: str, body: PartyLinkBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"MASTER_EDIT",scope,x_branch_scope); tx(conn)
        c=get_container_scoped(conn,container_no,role,scope,x_branch_scope)
        t=now()
        conn.execute("""INSERT INTO container_party_links(container_id,party_role,party_code,party_name,is_owner,is_custodian,valid_from,valid_to,terms_json,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(container_id,party_role,party_code) DO UPDATE SET party_name=excluded.party_name,is_owner=excluded.is_owner,
                        is_custodian=excluded.is_custodian,valid_from=excluded.valid_from,valid_to=excluded.valid_to,terms_json=excluded.terms_json,updated_at=excluded.updated_at""",
                     (c["id"],body.party_role.upper(),body.party_code,body.party_name,body.is_owner,body.is_custodian,body.valid_from,body.valid_to,
                      json.dumps(body.terms,sort_keys=True),t,t))
        if body.is_owner:
            conn.execute("UPDATE containers SET owner_type=?,owner_party_code=?,updated_at=? WHERE id=?",(body.party_role.upper(),body.party_code,t,c["id"]))
        audit(conn,role,scope,"CONTAINER_PARTY_LINK",c["id"],c.get("job_id"),None,body.model_dump(),{})
        conn.execute("COMMIT"); return {"ok":True}
    except HTTPException:
        conn.execute("ROLLBACK"); raise
    except Exception:
        conn.execute("ROLLBACK"); raise
    finally: conn.close()

@router.post("/containers/{container_no}/finance")
def add_finance_entry(container_no: str, body: FinanceEntryBody, x_role: str=Header("FINANCE"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"FINANCE_CREATE",scope,x_branch_scope); tx(conn)
        c=get_container_scoped(conn,container_no,role,scope,x_branch_scope)
        cat=body.entry_category.upper()
        if cat not in FINANCE_CATEGORIES: raise HTTPException(422,"Invalid entry_category")
        job_id=c.get("job_id")
        if body.job_ref:
            j=conn.execute("SELECT id FROM jobs WHERE job_ref=?",(body.job_ref,)).fetchone()
            if not j: raise HTTPException(404,"Unknown job_ref")
            job_id=j["id"]
        ref="CFL-"+datetime.datetime.now().strftime("%Y%m%d%H%M%S")+"-"+str(uuid.uuid4())[:6].upper()
        status="DRAFT"
        t=now()
        conn.execute("""INSERT INTO container_financial_entries(entry_ref,container_id,job_id,booking_ref,bl_ref,entry_category,charge_code,description,amount,currency,
          party_type,party_code,source_type,source_ref,movement_event_ref,status,maker_role,metadata_json,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (ref,c["id"],job_id,body.booking_ref or c.get("booking_ref"),body.bl_ref,cat,body.charge_code,body.description,body.amount,body.currency,
           body.party_type,body.party_code,body.source_type,body.source_ref,body.movement_event_ref,status,role,json.dumps(body.metadata,sort_keys=True),t,t))
        audit(conn,role,scope,"CONTAINER_FINANCE_CREATE",c["id"],job_id,None,{"entry_ref":ref,"category":cat,"amount":body.amount,"currency":body.currency},{})
        conn.execute("COMMIT"); return {"ok":True,"entry_ref":ref,"status":status}
    except HTTPException:
        conn.execute("ROLLBACK"); raise
    except Exception:
        conn.execute("ROLLBACK"); raise
    finally: conn.close()

@router.post("/containers/{container_no}/finance/{entry_ref}/decision")
def finance_decision(container_no: str, entry_ref: str, body: FinanceDecisionBody, x_role: str=Header("FINANCE"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"FINANCE_APPROVE",scope,x_branch_scope); tx(conn)
        c=get_container_scoped(conn,container_no,role,scope,x_branch_scope)
        e=conn.execute("SELECT * FROM container_financial_entries WHERE entry_ref=? AND container_id=?",(entry_ref,c["id"])).fetchone()
        if not e: raise HTTPException(404,"Finance entry not found")
        if e["maker_role"]==role: raise HTTPException(409,{"code":"MAKER_CHECKER_CONFLICT"})
        d=body.decision.upper()
        if d not in {"APPROVE","REJECT","POST"}: raise HTTPException(422,"Invalid decision")
        target={"APPROVE":"APPROVED","REJECT":"REJECTED","POST":"POSTED"}[d]
        t=now()
        conn.execute("""UPDATE container_financial_entries SET status=?,checker_role=?,approved_at=CASE WHEN ?='APPROVED' THEN ? ELSE approved_at END,
                        posted_at=CASE WHEN ?='POSTED' THEN ? ELSE posted_at END,updated_at=? WHERE id=?""",
                     (target,role,target,t,target,t,t,e["id"]))
        audit(conn,role,scope,"CONTAINER_FINANCE_"+d,c["id"],e["job_id"],dict(e),{"status":target},{"note":body.note})
        conn.execute("COMMIT"); return {"ok":True,"entry_ref":entry_ref,"status":target}
    except HTTPException:
        conn.execute("ROLLBACK"); raise
    except Exception:
        conn.execute("ROLLBACK"); raise
    finally: conn.close()

@router.post("/containers/{container_no}/share-rules")
def add_share_rule(container_no: str, body: ShareRuleBody, x_role: str=Header("FINANCE"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"FINANCE_CREATE",scope,x_branch_scope); tx(conn)
        c=get_container_scoped(conn,container_no,role,scope,x_branch_scope)
        ref="CSR-"+datetime.datetime.now().strftime("%Y%m%d%H%M%S")+"-"+str(uuid.uuid4())[:6].upper()
        t=now()
        conn.execute("""INSERT INTO container_share_rules(rule_ref,container_id,party_type,party_code,basis,rate_percent,fixed_amount,currency,charge_code,
                        valid_from,valid_to,active,maker_role,status,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,TRUE,?,'DRAFT',?,?)""",
                     (ref,c["id"],body.party_type,body.party_code,body.basis.upper(),body.rate_percent,body.fixed_amount,body.currency,body.charge_code,
                      body.valid_from,body.valid_to,role,t,t))
        audit(conn,role,scope,"CONTAINER_SHARE_RULE_CREATE",c["id"],c.get("job_id"),None,{"rule_ref":ref,"party_code":body.party_code,"rate_percent":body.rate_percent},{})
        conn.execute("COMMIT"); return {"ok":True,"rule_ref":ref,"status":"DRAFT"}
    except HTTPException:
        conn.execute("ROLLBACK"); raise
    except Exception:
        conn.execute("ROLLBACK"); raise
    finally: conn.close()

@router.get("/booking/{booking_ref}/equipment")
def booking_equipment(booking_ref: str, x_role: str=Header("VIEWER"), x_agent_scope: Optional[str]=Header(None), x_branch_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn); require_policy(conn,role,"VIEW",scope,x_branch_scope)
        q="""SELECT c.* FROM containers c LEFT JOIN jobs j ON j.id=c.job_id LEFT JOIN bookings b ON b.id=j.booking_id
             WHERE (c.booking_ref=? OR b.booking_ref=?)"""
        args=[booking_ref,booking_ref]
        sc,sa=scope_clause(role,scope,x_branch_scope,"c"); q+=sc; args+=sa
        q+=" ORDER BY c.container_no"
        rows=[dict(r) for r in conn.execute(q,args).fetchall()]
        return {"booking_ref":booking_ref,"count":len(rows),"containers":rows,
                "financial_summary":{r["container_no"]:finance_summary(conn,r["id"]) for r in rows}}
    finally: conn.close()
