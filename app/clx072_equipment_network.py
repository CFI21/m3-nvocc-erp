from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Any
import datetime, json, uuid

from .db import connect, tx
from .clx070_container_master_control import actor, require_action, scope_clause, audit, now
from .clx071_container_journey import detention_metrics, parse_dt

router=APIRouter(prefix="/api/clx072/network",tags=["CLX-072 Global Equipment Network"])

ACTIVE_STATES={"AVAILABLE","RESERVED","RELEASED","EMPTY_PICKUP","STUFFED","GATE_IN","LOADED","IN_TRANSIT","TRANSSHIPMENT","DISCHARGED","GATE_OUT_FULL","EMPTY_RETURN","INSPECTION","REPAIR","HOLD"}
AVAILABLE_STATES={"AVAILABLE"}
RESERVED_STATES={"RESERVED","RELEASED","EMPTY_PICKUP","STUFFED","GATE_IN"}
TRANSIT_STATES={"LOADED","IN_TRANSIT","TRANSSHIPMENT","DISCHARGED","GATE_OUT_FULL"}
HOLD_STATES={"HOLD","REPAIR","INSPECTION"}
STRATEGIC_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT"}
APPROVE_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT","FINANCE"}

class TargetBody(BaseModel):
    port_code:str
    size_type:str
    branch_code:Optional[str]=None
    agent_code:Optional[str]=None
    depot_code:Optional[str]=None
    safety_stock:int=Field(default=0,ge=0,le=100000)
    target_stock:int=Field(default=0,ge=0,le=100000)
    reorder_point:int=Field(default=0,ge=0,le=100000)
    planning_horizon_days:int=Field(default=14,ge=1,le=180)
    reposition_lead_days:int=Field(default=3,ge=0,le=90)
    idle_alert_days:int=Field(default=14,ge=0,le=3650)
    critical_idle_days:int=Field(default=30,ge=0,le=3650)

class TargetDecision(BaseModel):
    decision:str
    note:Optional[str]=None

class RecommendationBody(BaseModel):
    source_port:str
    destination_port:str
    size_type:str
    qty:int=Field(ge=1,le=10000)
    source_branch:Optional[str]=None
    destination_branch:Optional[str]=None
    source_agent:Optional[str]=None
    destination_agent:Optional[str]=None
    source_depot:Optional[str]=None
    destination_depot:Optional[str]=None
    estimated_cost:float=Field(default=0,ge=0)
    currency:str="USD"
    required_by:Optional[str]=None
    recommendation_type:str="REPOSITION"
    reason:Optional[str]=None

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)

def norm_state(c):
    return str(c.get("journey_state") or c.get("equipment_status") or "AVAILABLE").upper()

def owner_segment(c):
    x=str(c.get("owner_party_type") or c.get("ownership") or "PRINCIPAL").upper()
    if x in {"LEASED","LEASE","LEASING_COMPANY"}: return "LEASED"
    if x in {"AGENT","AGENT_SUPPLIED"}: return "AGENT"
    if x in {"OVERSEAS_PARTNER","PARTNER","INVESTOR"}: return "PARTNER"
    if x=="SOC": return "SOC"
    return "OWNED"

def network_key(c):
    return (
      c.get("current_port") or "UNASSIGNED",
      c.get("branch_code") or "",
      c.get("agent_code") or "",
      c.get("depot_code") or "",
      c.get("size_type") or "UNKNOWN"
    )

def scoped_containers(conn,a):
    sc,args=scope_clause(a,"c")
    return [dict(r) for r in conn.execute("SELECT c.* FROM containers c WHERE 1=1"+sc,args).fetchall()]

def target_match(t,key):
    port,branch,agent,depot,size=key
    return (
      t["port_code"]==port and t["size_type"]==size
      and (not t["branch_code"] or t["branch_code"]==branch)
      and (not t["agent_code"] or t["agent_code"]==agent)
      and (not t["depot_code"] or t["depot_code"]==depot)
    )

def load_targets(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM equipment_network_targets WHERE status='APPROVED' ORDER BY id").fetchall()]

def parse_payload(v):
    try:return json.loads(v or "{}")
    except Exception:return {}

def extract_qty(payload):
    for k in ("Equipment Qty","Container Qty","Quantity","Qty","Booked","Containers"):
        try:
            if k in payload and payload[k] not in (None,""):
                return max(0,int(float(payload[k])))
        except Exception:
            pass
    return 0

def extract_size(payload):
    raw=str(payload.get("Equipment") or payload.get("Container Type") or payload.get("Size Type") or "").upper()
    if "20" in raw:return "20GP"
    if "RF" in raw or "REEFER" in raw:return "40RF"
    if "40" in raw:return "40HC"
    return None

def demand_forecast(conn,horizon_days=14):
    # No duplicate booking model: derive demand from existing booking transaction payloads and existing open equipment work items.
    by={}
    try:
        rows=conn.execute("""SELECT t.payload_json,j.pol,j.job_ref,b.booking_ref
          FROM transaction_records t JOIN jobs j ON j.id=t.job_id JOIN bookings b ON b.id=j.booking_id
          WHERE t.module='booking' AND t.status NOT IN ('Cancelled','Closed')""").fetchall()
        for r in rows:
            p=parse_payload(r["payload_json"]);qty=extract_qty(p);size=extract_size(p)
            if qty<=0 or not size:continue
            k=(r["pol"],size)
            x=by.setdefault(k,{"port":r["pol"],"size_type":size,"booking_qty":0,"work_item_qty":0,"jobs":[]})
            x["booking_qty"]+=qty;x["jobs"].append(r["job_ref"])
    except Exception:
        pass
    try:
        rows=conn.execute("""SELECT destination_port,source_port,size_type,qty,work_type,booking_ref,job_ref,required_by,status
          FROM equipment_work_items WHERE status IN ('DRAFT','SUBMITTED','APPROVED')""").fetchall()
        cutoff=utcnow()+datetime.timedelta(days=horizon_days)
        for r in rows:
            dt=parse_dt(r["required_by"]) if r["required_by"] else None
            if dt and dt>cutoff:continue
            if r["work_type"] not in ("SHORTAGE","LEASE","PURCHASE","AGENT_SUPPLY","SOC"):continue
            port=r["destination_port"] or r["source_port"]
            if not port:continue
            k=(port,r["size_type"])
            x=by.setdefault(k,{"port":port,"size_type":r["size_type"],"booking_qty":0,"work_item_qty":0,"jobs":[]})
            x["work_item_qty"]+=int(r["qty"] or 0)
            if r["job_ref"]:x["jobs"].append(r["job_ref"])
    except Exception:
        pass
    for x in by.values():
        x["forecast_qty"]=max(x["booking_qty"],x["work_item_qty"])
        x["jobs"]=sorted(set(x["jobs"]))
    return by

def aggregate_network(containers,targets,demand):
    buckets={}
    nowdt=utcnow()
    for c in containers:
        key=network_key(c)
        b=buckets.setdefault(key,{
          "port":key[0],"branch":key[1],"agent":key[2],"depot":key[3],"size_type":key[4],
          "total":0,"available":0,"reserved":0,"in_transit":0,"repair_hold":0,
          "idle_14_plus":0,"idle_30_plus":0,"detention_exposure":0.0,
          "owned":0,"leased":0,"agent_stock":0,"partner_stock":0,"soc":0
        })
        b["total"]+=1;s=norm_state(c)
        if s in AVAILABLE_STATES:b["available"]+=1
        if s in RESERVED_STATES:b["reserved"]+=1
        if s in TRANSIT_STATES:b["in_transit"]+=1
        if s in HOLD_STATES or bool(c.get("damage_hold")) or bool(c.get("inspection_hold")):b["repair_hold"]+=1
        idle=int(c.get("idle_days") or 0)
        if idle>=14:b["idle_14_plus"]+=1
        if idle>=30:b["idle_30_plus"]+=1
        try:b["detention_exposure"]+=float(detention_metrics(c,nowdt)["estimated_exposure"])
        except Exception:pass
        seg=owner_segment(c)
        if seg=="OWNED":b["owned"]+=1
        elif seg=="LEASED":b["leased"]+=1
        elif seg=="AGENT":b["agent_stock"]+=1
        elif seg=="PARTNER":b["partner_stock"]+=1
        elif seg=="SOC":b["soc"]+=1
    for key,b in buckets.items():
        matched=[t for t in targets if target_match(t,key)]
        t=matched[-1] if matched else None
        d=demand.get((b["port"],b["size_type"]),{})
        b["safety_stock"]=int(t["safety_stock"] if t else 0)
        b["target_stock"]=int(t["target_stock"] if t else 0)
        b["reorder_point"]=int(t["reorder_point"] if t else 0)
        b["forecast_demand"]=int(d.get("forecast_qty",0))
        b["usable_available"]=max(0,b["available"]-b["safety_stock"])
        b["projected_balance"]=b["available"]-b["safety_stock"]-b["forecast_demand"]
        target=max(b["target_stock"],b["reorder_point"],b["safety_stock"]+b["forecast_demand"])
        b["shortage"]=max(0,target-b["available"])
        b["surplus"]=max(0,b["available"]-target)
        b["detention_exposure"]=round(b["detention_exposure"],2)
    return sorted(buckets.values(),key=lambda x:(x["port"],x["size_type"],x["branch"],x["agent"],x["depot"]))

def recommendations(position):
    shortages=[dict(x) for x in position if x["shortage"]>0]
    surplus=[dict(x) for x in position if x["surplus"]>0]
    out=[]
    for dst in shortages:
        need=dst["shortage"]
        for src in surplus:
            if need<=0:break
            if src["size_type"]!=dst["size_type"] or src["port"]==dst["port"] or src["surplus"]<=0:continue
            q=min(need,src["surplus"]);src["surplus"]-=q;need-=q
            out.append({
              "recommendation_type":"REPOSITION","size_type":dst["size_type"],"qty":q,
              "source_port":src["port"],"source_branch":src["branch"],"source_agent":src["agent"],"source_depot":src["depot"],
              "destination_port":dst["port"],"destination_branch":dst["branch"],"destination_agent":dst["agent"],"destination_depot":dst["depot"],
              "reason":f"Move surplus {src['port']} to shortage {dst['port']}"
            })
        if need>0:
            out.append({
              "recommendation_type":"LEASE_ON_HIRE","size_type":dst["size_type"],"qty":need,
              "source_port":"","source_branch":"","source_agent":"","source_depot":"",
              "destination_port":dst["port"],"destination_branch":dst["branch"],"destination_agent":dst["agent"],"destination_depot":dst["depot"],
              "reason":"Residual shortage after internal reposition capacity"
            })
    for src in surplus:
        if src["surplus"]>0 and src["leased"]>0:
            out.append({
              "recommendation_type":"LEASE_OFF_HIRE","size_type":src["size_type"],"qty":min(src["surplus"],src["leased"]),
              "source_port":src["port"],"source_branch":src["branch"],"source_agent":src["agent"],"source_depot":src["depot"],
              "destination_port":"","destination_branch":"","destination_agent":"","destination_depot":"",
              "reason":"Leased surplus candidate for off-hire"
            })
    return out

@router.get("/policies")
def policies():
    c=connect()
    try:return {"policies":[dict(r) for r in c.execute("SELECT * FROM equipment_network_policy ORDER BY policy_key")]}
    finally:c.close()

@router.get("/targets")
def targets(x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
            x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
            x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        rows=[dict(r) for r in c.execute("SELECT * FROM equipment_network_targets ORDER BY port_code,size_type,id").fetchall()]
        if a["role"]=="AGENT":rows=[r for r in rows if not r["agent_code"] or r["agent_code"]==a["agent"]]
        if a["role"]=="DEPOT":rows=[r for r in rows if not r["depot_code"] or r["depot_code"]==a["depot"]]
        if a["branch"] and a["role"] in {"OPS","BRANCH_OPS","EQUIPMENT_CONTROLLER"}:rows=[r for r in rows if not r["branch_code"] or r["branch_code"]==a["branch"]]
        return {"actor":a,"count":len(rows),"records":rows}
    finally:c.close()

@router.post("/targets")
def create_target(b:TargetBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                  x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                  x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        if a["role"] not in STRATEGIC_ROLES:raise HTTPException(403,{"code":"NETWORK_TARGET_STRATEGIC_ROLE_REQUIRED"})
        tx(c);ref="ENT-"+uuid.uuid4().hex[:12].upper()
        c.execute("""INSERT INTO equipment_network_targets(target_ref,port_code,branch_code,agent_code,depot_code,size_type,safety_stock,target_stock,reorder_point,
                   planning_horizon_days,reposition_lead_days,idle_alert_days,critical_idle_days,status,maker_role,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?)""",
          (ref,b.port_code,b.branch_code,b.agent_code,b.depot_code,b.size_type,b.safety_stock,b.target_stock,b.reorder_point,b.planning_horizon_days,
           b.reposition_lead_days,b.idle_alert_days,b.critical_idle_days,a["role"],now(),now()))
        audit(c,a,"NETWORK_TARGET_CREATE",None,None,{},b.model_dump(),{"target_ref":ref});c.execute("COMMIT")
        return {"ok":True,"target_ref":ref,"status":"DRAFT"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/targets/{target_ref}/decision")
def target_decision(target_ref:str,b:TargetDecision,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                    x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                    x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        if a["role"] not in APPROVE_ROLES:raise HTTPException(403,{"code":"NETWORK_TARGET_APPROVAL_ROLE_REQUIRED"})
        tx(c);r=c.execute("SELECT * FROM equipment_network_targets WHERE target_ref=?",(target_ref,)).fetchone()
        if not r:raise HTTPException(404,"Unknown target")
        d=b.decision.upper()
        if d not in {"SUBMIT","APPROVE","REJECT","CANCEL"}:raise HTTPException(422,"Invalid decision")
        if d=="APPROVE" and r["maker_role"]==a["role"]:raise HTTPException(409,{"code":"MAKER_CHECKER_CONFLICT"})
        status={"SUBMIT":"SUBMITTED","APPROVE":"APPROVED","REJECT":"REJECTED","CANCEL":"CANCELLED"}[d]
        c.execute("""UPDATE equipment_network_targets SET status=?,checker_role=CASE WHEN ?='APPROVED' THEN ? ELSE checker_role END,
                     approved_at=CASE WHEN ?='APPROVED' THEN ? ELSE approved_at END,updated_at=? WHERE id=?""",
                  (status,status,a["role"],status,now(),now(),r["id"]))
        audit(c,a,"NETWORK_TARGET_"+d,None,None,dict(r),{"status":status},{"target_ref":target_ref,"note":b.note});c.execute("COMMIT")
        return {"ok":True,"target_ref":target_ref,"status":status}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.get("/position")
def position(horizon_days:int=Query(14,ge=1,le=180),x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
             x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
             x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        rows=scoped_containers(c,a);pos=aggregate_network(rows,load_targets(c),demand_forecast(c,horizon_days))
        return {"actor":a,"horizon_days":horizon_days,"count":len(pos),"records":pos}
    finally:c.close()

@router.get("/demand-forecast")
def demand(horizon_days:int=Query(14,ge=1,le=180),x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
           x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
           x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);allowed=scoped_containers(c,a)
        allowed_keys={(x.get("current_port"),x.get("size_type")) for x in allowed}
        d=demand_forecast(c,horizon_days)
        rows=[x for k,x in sorted(d.items()) if (a["role"] in {"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT","FINANCE","AUDITOR"} or k in allowed_keys)]
        return {"actor":a,"horizon_days":horizon_days,"count":len(rows),"records":rows}
    finally:c.close()

@router.get("/recommendations")
def recommendation_list(horizon_days:int=Query(14,ge=1,le=180),x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                        x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                        x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        pos=aggregate_network(scoped_containers(c,a),load_targets(c),demand_forecast(c,horizon_days))
        return {"actor":a,"horizon_days":horizon_days,"records":recommendations(pos)}
    finally:c.close()

@router.post("/recommendations/work-item")
def recommendation_to_work(b:RecommendationBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                           x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                           x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write")
        typ=b.recommendation_type.upper()
        wt={"REPOSITION":"REPOSITION","LEASE_ON_HIRE":"LEASE","LEASE_OFF_HIRE":"LEASE"}.get(typ)
        if not wt:raise HTTPException(422,{"code":"UNSUPPORTED_RECOMMENDATION_TYPE"})
        if typ in {"LEASE_ON_HIRE","LEASE_OFF_HIRE"} and a["role"] not in STRATEGIC_ROLES:
            raise HTTPException(403,{"code":"STRATEGIC_LEASE_ROLE_REQUIRED"})
        # Scope safety: the source/destination must be visible to actor through current authoritative stock scope.
        visible=scoped_containers(c,a)
        ports={x.get("current_port") for x in visible}
        if a["role"] not in {"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT","FINANCE","AUDITOR"}:
            for p in (b.source_port,b.destination_port):
                if p and p not in ports:raise HTTPException(403,{"code":"NETWORK_ACTION_OUTSIDE_CUSTODY_SCOPE","port":p})
        tx(c);ref=f"EQ-{wt[:3]}-{utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        payload=b.model_dump();payload["clx072_generated"]=True
        c.execute("""INSERT INTO equipment_work_items(work_ref,work_type,status,booking_ref,job_ref,size_type,qty,source_port,destination_port,source_agent,destination_agent,
                   source_depot,destination_depot,estimated_cost,currency,required_by,payload_json,maker_role,created_at,updated_at)
                   VALUES(?,?,'DRAFT',NULL,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (ref,wt,b.size_type,b.qty,b.source_port or None,b.destination_port or None,b.source_agent,b.destination_agent,b.source_depot,b.destination_depot,
           b.estimated_cost,b.currency,b.required_by,json.dumps(payload,sort_keys=True),a["role"],now(),now()))
        audit(c,a,"NETWORK_RECOMMENDATION_TO_WORK_ITEM",None,None,{},payload,{"work_ref":ref});c.execute("COMMIT")
        return {"ok":True,"work_ref":ref,"work_type":wt,"status":"DRAFT","execution":"EXISTING_EQUIPMENT_WORK_ITEM_QUEUE"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.get("/action-queue")
def action_queue(limit:int=Query(300,ge=1,le=1000),x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                 x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        q="SELECT * FROM equipment_work_items WHERE status IN ('DRAFT','SUBMITTED','APPROVED')";args=[]
        if a["role"]=="AGENT":
            if not a["agent"]:raise HTTPException(403,{"code":"AGENT_SCOPE_REQUIRED"})
            q+=" AND (source_agent=? OR destination_agent=?)";args.extend([a["agent"],a["agent"]])
        if a["role"]=="DEPOT":
            if not a["depot"]:raise HTTPException(403,{"code":"DEPOT_SCOPE_REQUIRED"})
            q+=" AND (source_depot=? OR destination_depot=?)";args.extend([a["depot"],a["depot"]])
        if a["branch"] and a["role"] in {"OPS","BRANCH_OPS","EQUIPMENT_CONTROLLER"}:
            q+=" AND (payload_json LIKE ? OR payload_json LIKE ?)";args.extend([f'%"source_branch": "{a["branch"]}"%',f'%"destination_branch": "{a["branch"]}"%'])
        q+=" ORDER BY CASE status WHEN 'SUBMITTED' THEN 1 WHEN 'APPROVED' THEN 2 ELSE 3 END,id DESC LIMIT ?";args.append(limit)
        rows=[dict(r) for r in c.execute(q,args).fetchall()]
        return {"actor":a,"count":len(rows),"records":rows}
    finally:c.close()

@router.get("/aging")
def aging(x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
          x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
          x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);rows=scoped_containers(c,a)
        bands={"0_7":0,"8_14":0,"15_30":0,"31_60":0,"61_plus":0};records=[]
        for x in rows:
            if norm_state(x)!="AVAILABLE":continue
            d=int(x.get("idle_days") or 0)
            band="0_7" if d<=7 else "8_14" if d<=14 else "15_30" if d<=30 else "31_60" if d<=60 else "61_plus"
            bands[band]+=1
            if d>=14:records.append({"container_no":x["container_no"],"port":x.get("current_port"),"size_type":x.get("size_type"),"idle_days":d,"owner_segment":owner_segment(x)})
        records.sort(key=lambda x:(-x["idle_days"],x["container_no"]))
        return {"actor":a,"bands":bands,"records":records}
    finally:c.close()

@router.get("/kpis")
def kpis(horizon_days:int=Query(14,ge=1,le=180),x_role:str=Header("AUDITOR"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
         x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
         x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);containers=scoped_containers(c,a)
        pos=aggregate_network(containers,load_targets(c),demand_forecast(c,horizon_days))
        total=len(containers);available=sum(1 for x in containers if norm_state(x)=="AVAILABLE")
        utilized=sum(1 for x in containers if norm_state(x) in RESERVED_STATES|TRANSIT_STATES)
        recs=recommendations(pos)
        turns=[]
        for x in containers:
            try:
                p=c.execute("SELECT actual_time FROM container_events WHERE container_id=? AND event_type='EMPTY_PICKUP' AND actual_time IS NOT NULL ORDER BY actual_time DESC LIMIT 1",(x["id"],)).fetchone()
                r=c.execute("SELECT actual_time FROM container_events WHERE container_id=? AND event_type='EMPTY_RETURN' AND actual_time IS NOT NULL ORDER BY actual_time DESC LIMIT 1",(x["id"],)).fetchone()
                if p and r:
                    pd=parse_dt(p["actual_time"]);rd=parse_dt(r["actual_time"])
                    if pd and rd and rd>=pd:turns.append((rd-pd).total_seconds()/86400)
            except Exception:pass
        return {
          "actor":a,"fleet":total,"available":available,"utilized":utilized,
          "utilization_pct":round(utilized*100/total,2) if total else 0,
          "shortage_units":sum(x["shortage"] for x in pos),"surplus_units":sum(x["surplus"] for x in pos),
          "network_nodes":len(pos),"reposition_recommendations":sum(1 for x in recs if x["recommendation_type"]=="REPOSITION"),
          "lease_on_hire_units":sum(x["qty"] for x in recs if x["recommendation_type"]=="LEASE_ON_HIRE"),
          "lease_off_hire_units":sum(x["qty"] for x in recs if x["recommendation_type"]=="LEASE_OFF_HIRE"),
          "idle_14_plus":sum(1 for x in containers if norm_state(x)=="AVAILABLE" and int(x.get("idle_days") or 0)>=14),
          "idle_30_plus":sum(1 for x in containers if norm_state(x)=="AVAILABLE" and int(x.get("idle_days") or 0)>=30),
          "detention_exposure":round(sum(x["detention_exposure"] for x in pos),2),
          "avg_turnaround_days":round(sum(turns)/len(turns),2) if turns else 0,
          "ownership":{
            "owned":sum(1 for x in containers if owner_segment(x)=="OWNED"),
            "leased":sum(1 for x in containers if owner_segment(x)=="LEASED"),
            "agent":sum(1 for x in containers if owner_segment(x)=="AGENT"),
            "partner":sum(1 for x in containers if owner_segment(x)=="PARTNER"),
            "soc":sum(1 for x in containers if owner_segment(x)=="SOC")
          }
        }
    finally:c.close()
