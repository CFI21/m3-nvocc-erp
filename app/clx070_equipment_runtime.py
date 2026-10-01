from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Optional
import datetime, json, uuid

from .db import connect, tx, backend_name, approved_database_target

router = APIRouter(prefix="/api/clx070/equipment", tags=["CLX-070 Equipment Runtime Integration"])

VALID_ROLES={"ADMIN","SUPER_ADMIN","OPS","DOCS","FINANCE","AGENT","VIEWER","AUDITOR"}
WRITE_ROLES={"ADMIN","SUPER_ADMIN","OPS"}
APPROVE_ROLES={"ADMIN","SUPER_ADMIN","OPS","FINANCE"}

def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def actor(x_role: str, x_agent_scope: Optional[str]):
    role=(x_role or "VIEWER").upper()
    if role not in VALID_ROLES:
        raise HTTPException(403,"Unknown role")
    return role,x_agent_scope

def rowdict(r):
    return dict(r) if r else None

def schema_ready(conn) -> bool:
    try:
        row=conn.execute("""
          SELECT COUNT(*) AS n
          FROM information_schema.columns
          WHERE table_schema='public' AND table_name='containers'
            AND column_name IN ('ownership','current_port','equipment_status','condition','available_from','booking_ref')
        """).fetchone()
        return bool(row and int(row["n"])>=6)
    except Exception:
        try:
            cols=[x["name"] for x in conn.execute("PRAGMA table_info(containers)").fetchall()]
            return all(x in cols for x in ["ownership","current_port","equipment_status","condition","available_from","booking_ref"])
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
    principal_code: str="M3 NVOCC"
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
    unit_cost: float=0
    currency: str="USD"

class MovementBody(BaseModel):
    event_type: str
    event_time: Optional[str]=None
    port: Optional[str]=None
    agent_code: Optional[str]=None
    depot_code: Optional[str]=None
    status: Optional[str]=None
    booking_ref: Optional[str]=None
    job_ref: Optional[str]=None
    detail: dict[str,Any]=Field(default_factory=dict)

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
    x_role: str=Header("VIEWER"), x_agent_scope: Optional[str]=Header(None)
):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn)
        q="SELECT * FROM containers WHERE 1=1"; args=[]
        for col,val in [("current_port",port),("agent_code",agent_code),("depot_code",depot_code),("size_type",size_type),("equipment_status",status),("ownership",ownership)]:
            if val is not None:
                q+=f" AND {col}=?"; args.append(val)
        if role=="AGENT":
            if not scope: raise HTTPException(403,"AGENT requires X-Agent-Scope")
            q+=" AND agent_code=?"; args.append(scope)
        q+=" ORDER BY container_no LIMIT ?"; args.append(limit)
        rows=[dict(x) for x in conn.execute(q,args).fetchall()]
        return {"count":len(rows),"records":rows}
    finally:
        conn.close()

@router.get("/availability")
def availability(
    port: str, size_type: str, qty: int=Query(1,ge=1,le=500), safety_stock: int=Query(0,ge=0,le=500),
    agent_code: Optional[str]=None, depot_code: Optional[str]=None, required_by: Optional[str]=None,
    x_role: str=Header("VIEWER"), x_agent_scope: Optional[str]=Header(None)
):
    role,scope=actor(x_role,x_agent_scope)
    conn=connect()
    try:
        require_schema(conn)
        q="""SELECT * FROM containers
             WHERE current_port=? AND size_type=? AND equipment_status='AVAILABLE'
               AND condition IN ('GOOD','VERIFIED')
               AND COALESCE(verification_status,'VERIFIED')='VERIFIED'
               AND COALESCE(allocate_for_sale,0)=0
               AND COALESCE(owner_party_code,principal_code) IS NOT NULL"""
        args=[port,size_type]
        if agent_code: q+=" AND agent_code=?"; args.append(agent_code)
        if depot_code: q+=" AND depot_code=?"; args.append(depot_code)
        if required_by: q+=" AND (available_from IS NULL OR available_from='' OR available_from<=?)"; args.append(required_by)
        if role=="AGENT":
            if not scope: raise HTTPException(403,"AGENT requires X-Agent-Scope")
            q+=" AND agent_code=?"; args.append(scope)
        q+=" ORDER BY CASE ownership WHEN 'OWNED' THEN 1 WHEN 'AGENT' THEN 2 WHEN 'LEASED' THEN 3 WHEN 'SOC' THEN 4 ELSE 9 END, idle_days DESC, container_no"
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
def allocate(body: AllocateBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    if role not in WRITE_ROLES: raise HTTPException(403,"Write role required")
    conn=connect()
    try:
        require_schema(conn); tx(conn)
        # Allocation must use the authoritative Booking/Job context.
        if body.job_ref:
            jr=conn.execute("""SELECT j.id,b.booking_ref,a.code agent_code,j.pol
                               FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN agents a ON a.id=j.agent_id
                               WHERE j.job_ref=?""",(body.job_ref,)).fetchone()
        else:
            jr=conn.execute("""SELECT j.id,b.booking_ref,a.code agent_code,j.pol
                               FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN agents a ON a.id=j.agent_id
                               WHERE b.booking_ref=?""",(body.booking_ref,)).fetchone()
        if not jr: raise HTTPException(404,{"code":"AUTHORITATIVE_BOOKING_JOB_NOT_FOUND"})
        if body.booking_ref!=jr["booking_ref"]: raise HTTPException(409,{"code":"BOOKING_JOB_MISMATCH","authoritative_booking_ref":jr["booking_ref"]})
        if role=="AGENT" and (not scope or scope!=jr["agent_code"]): raise HTTPException(404,{"code":"BOOKING_OUTSIDE_AGENT_SCOPE"})
        job_id=jr["id"]
        if body.port!=jr["pol"]: raise HTTPException(409,{"code":"ALLOCATION_PORT_BOOKING_POL_MISMATCH","booking_pol":jr["pol"]})
        q="""SELECT * FROM containers
             WHERE current_port=? AND size_type=? AND equipment_status='AVAILABLE'
               AND condition IN ('GOOD','VERIFIED')"""
        args=[body.port,body.size_type]
        if body.agent_code: q+=" AND agent_code=?";args.append(body.agent_code)
        if body.depot_code: q+=" AND depot_code=?";args.append(body.depot_code)
        if body.required_by: q+=" AND (available_from IS NULL OR available_from='' OR available_from<=?)";args.append(body.required_by)
        if body.container_nos:
            ph=",".join("?" for _ in body.container_nos); q+=f" AND container_no IN ({ph})";args.extend(body.container_nos)
        if role=="AGENT":
            if not scope: raise HTTPException(403,"AGENT requires X-Agent-Scope")
            q+=" AND agent_code=?";args.append(scope)
        q+=" ORDER BY CASE ownership WHEN 'OWNED' THEN 1 WHEN 'AGENT' THEN 2 WHEN 'LEASED' THEN 3 WHEN 'SOC' THEN 4 ELSE 9 END, idle_days DESC, container_no"
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
def register_container(body: ContainerRegisterBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    if role not in WRITE_ROLES: raise HTTPException(403,"Write role required")
    ownership=body.ownership.upper()
    if ownership not in {"OWNED","LEASED","AGENT","SOC"}: raise HTTPException(422,"Invalid ownership")
    conn=connect()
    try:
        require_schema(conn);tx(conn)
        if conn.execute("SELECT 1 FROM containers WHERE container_no=?",(body.container_no,)).fetchone():
            raise HTTPException(409,"Container already exists")
        cur=conn.execute("""INSERT INTO containers(
          container_no,job_id,size_type,ownership,principal_code,current_port,agent_code,depot_code,equipment_status,condition,
          available_from,acquisition_type,acquisition_ref,lease_contract_ref,purchase_order_ref,supplier_or_lessor,unit_cost,currency,idle_days,updated_at
        ) VALUES(?,NULL,?,?,?,?,?,?,'AVAILABLE',?,?,?,?,?,?,?,?,?,0,?) RETURNING id""",
        (body.container_no,body.size_type,ownership,body.principal_code,body.current_port,body.agent_code,body.depot_code,
         body.condition,body.available_from,body.acquisition_type,body.acquisition_ref,body.lease_contract_ref,body.purchase_order_ref,
         body.supplier_or_lessor,body.unit_cost,body.currency,now()))
        rid=cur.fetchone()["id"]
        after=dict(conn.execute("SELECT * FROM containers WHERE id=?",(rid,)).fetchone())
        audit(conn,role,scope,"REGISTER_CONTAINER",rid,None,{},after,{"acquisition_type":body.acquisition_type})
        conn.execute("COMMIT");return {"ok":True,"record":after}
    except HTTPException:
        conn.execute("ROLLBACK");raise
    except Exception:
        conn.execute("ROLLBACK");raise
    finally:
        conn.close()

@router.post("/containers/{container_no}/events")
def post_movement(container_no: str, body: MovementBody, x_role: str=Header("OPS"), x_agent_scope: Optional[str]=Header(None)):
    role,scope=actor(x_role,x_agent_scope)
    if role not in WRITE_ROLES: raise HTTPException(403,"Write role required")
    conn=connect()
    try:
        require_schema(conn);tx(conn)
        c=conn.execute("SELECT * FROM containers WHERE container_no=?",(container_no,)).fetchone()
        if not c: raise HTTPException(404,"Unknown container")
        before=dict(c)
        job_id=c["job_id"]
        if body.job_ref:
            jr=conn.execute("SELECT id FROM jobs WHERE job_ref=?",(body.job_ref,)).fetchone()
            if not jr: raise HTTPException(404,"Unknown job_ref")
            job_id=jr["id"]
        new_status=body.status or body.event_type
        conn.execute("""UPDATE containers SET current_port=COALESCE(?,current_port),agent_code=COALESCE(?,agent_code),depot_code=COALESCE(?,depot_code),
          equipment_status=?,booking_ref=COALESCE(?,booking_ref),job_id=COALESCE(?,job_id),idle_days=0,updated_at=? WHERE id=?""",
          (body.port,body.agent_code,body.depot_code,new_status,body.booking_ref,job_id,now(),c["id"]))
        evt_time=body.event_time or now()
        loc=" / ".join(x for x in [body.port or c["current_port"],body.agent_code or c["agent_code"],body.depot_code or c["depot_code"]] if x)
        movement_event_id=None
        movement_event_ref=str(uuid.uuid4())
        if job_id:
            cur_evt=conn.execute("""INSERT INTO container_events(event_id,job_id,container_id,event_type,event_time,location,status,source_module,detail_json)
                            VALUES(?,?,?,?,?,?,?,?,?) RETURNING id""",
                         (movement_event_ref,job_id,c["id"],body.event_type,evt_time,loc,new_status,"equipment-runtime",json.dumps(body.detail,sort_keys=True)))
            movement_event_id=cur_evt.fetchone()["id"]
        # Optional movement economics are posted against the same physical container.
        # Supported detail keys: cost_amount, revenue_amount, commission_amount, share_amount,
        # currency, charge_code, party_type, party_code, source_ref.
        econ=[("COST","cost_amount"),("REVENUE","revenue_amount"),("COMMISSION","commission_amount"),("SHARE","share_amount")]
        for entry_type,key in econ:
            amount=float(body.detail.get(key,0) or 0)
            if amount:
                conn.execute("""INSERT INTO container_financial_ledger(entry_ref,container_id,job_id,booking_ref,bl_ref,movement_event_id,entry_type,charge_code,party_type,party_code,amount,currency,quantity,rate,basis,source_type,source_ref,status,created_by,created_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,'MOVEMENT',?,'ACCRUED',?,?)""",
                    ("CFL-"+uuid.uuid4().hex[:12].upper(),c["id"],job_id,body.booking_ref or c["booking_ref"],body.detail.get("bl_ref"),
                     movement_event_id,entry_type,body.detail.get("charge_code") or ("MOVE_"+body.event_type.upper()),
                     body.detail.get("party_type"),body.detail.get("party_code"),amount,body.detail.get("currency") or "USD",
                     body.detail.get("basis") or body.event_type,body.detail.get("source_ref") or movement_event_ref,role,now()))
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
