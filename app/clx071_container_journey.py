from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Optional
import datetime, json, uuid

from .db import connect, tx
from .clx070_container_master_control import actor, require_action, get_container, audit, scope_clause, now

router=APIRouter(prefix="/api/clx071/journey",tags=["CLX-071 Full Container Journey"])

GLOBAL_OVERRIDE_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT"}
WRITE_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","EQUIPMENT_CONTROLLER","OPS","BRANCH_OPS","AGENT","DEPOT"}

STANDARD_FLOW=[
 "AVAILABLE","RESERVED","RELEASED","EMPTY_PICKUP","STUFFED","GATE_IN","LOADED",
 "IN_TRANSIT","TRANSSHIPMENT","DISCHARGED","GATE_OUT_FULL","EMPTY_RETURN","INSPECTION","AVAILABLE"
]
NEXT={
 "AVAILABLE":{"RESERVED","INSPECTION","ON_HIRE","PURCHASE_RECEIVED","AGENT_STOCK_RECEIVED","PARTNER_STOCK_RECEIVED","SOC_ACCEPTED","ALLOCATED_FOR_SALE"},
 "RESERVED":{"RELEASED","AVAILABLE"},
 "RELEASED":{"EMPTY_PICKUP","AVAILABLE"},
 "EMPTY_PICKUP":{"STUFFED","EMPTY_RETURN"},
 "STUFFED":{"GATE_IN","EMPTY_RETURN"},
 "GATE_IN":{"LOADED","STUFFED"},
 "LOADED":{"IN_TRANSIT","DISCHARGED"},
 "IN_TRANSIT":{"TRANSSHIPMENT","DISCHARGED"},
 "TRANSSHIPMENT":{"IN_TRANSIT","LOADED","DISCHARGED"},
 "DISCHARGED":{"GATE_OUT_FULL","IN_TRANSIT"},
 "GATE_OUT_FULL":{"EMPTY_RETURN"},
 "EMPTY_RETURN":{"INSPECTION","AVAILABLE","OFF_HIRE_DUE","RETURN_TO_PARTNER","RETURN_TO_AGENT","SOC_RELEASED"},
 "INSPECTION":{"AVAILABLE","REPAIR","HOLD","OFF_HIRED","SOLD","SCRAPPED"},
 "REPAIR":{"INSPECTION","AVAILABLE","SCRAPPED"},
 "HOLD":{"INSPECTION","REPAIR","AVAILABLE"},
 "ON_HIRE":{"AVAILABLE"},
 "OFF_HIRE_DUE":{"OFF_HIRED","AVAILABLE"},
 "PURCHASE_RECEIVED":{"INSPECTION"},
 "AGENT_STOCK_RECEIVED":{"AVAILABLE"},
 "PARTNER_STOCK_RECEIVED":{"AVAILABLE"},
 "SOC_ACCEPTED":{"AVAILABLE"},
 "ALLOCATED_FOR_SALE":{"SOLD","AVAILABLE"},
 "RETURN_TO_PARTNER":{"PARTNER_RETURNED"},
 "RETURN_TO_AGENT":{"AGENT_RETURNED"}
}

class PlanBody(BaseModel):
    event_type:str
    planned_time:str
    port_code:Optional[str]=None
    branch_code:Optional[str]=None
    agent_code:Optional[str]=None
    depot_code:Optional[str]=None
    voyage_ref:Optional[str]=None
    vessel_name:Optional[str]=None
    source_work_ref:Optional[str]=None
    note:Optional[str]=None

class EventBody(BaseModel):
    event_type:str
    actual_time:Optional[str]=None
    port_code:Optional[str]=None
    branch_code:Optional[str]=None
    agent_code:Optional[str]=None
    depot_code:Optional[str]=None
    voyage_ref:Optional[str]=None
    vessel_name:Optional[str]=None
    source_work_ref:Optional[str]=None
    booking_ref:Optional[str]=None
    job_ref:Optional[str]=None
    condition:Optional[str]=None
    damage_hold:Optional[bool]=None
    inspection_hold:Optional[bool]=None
    free_days:Optional[int]=Field(default=None,ge=0,le=365)
    detention_rate:Optional[float]=Field(default=None,ge=0)
    expected_empty_return:Optional[str]=None
    cost_amount:float=0
    currency:str="USD"
    charge_code:Optional[str]=None
    party_type:Optional[str]=None
    party_code:Optional[str]=None
    source_ref:Optional[str]=None
    override:bool=False
    override_reason:Optional[str]=None
    note:Optional[str]=None

class ExceptionDecision(BaseModel):
    action:str
    resolution_code:Optional[str]=None
    note:Optional[str]=None
    owner_role:Optional[str]=None
    owner_ref:Optional[str]=None

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)

def parse_dt(v):
    if not v:return None
    try:
        d=datetime.datetime.fromisoformat(str(v).replace("Z","+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None

def dtiso(d):
    return d.astimezone(datetime.timezone.utc).isoformat() if d else None

def event_state(e):
    e=e.upper().strip().replace(" ","_").replace("-","_")
    aliases={"STUFFED/FULL":"STUFFED","FULL":"STUFFED","GATEOUT_FULL":"GATE_OUT_FULL","EMPTYRETURN":"EMPTY_RETURN"}
    return aliases.get(e,e)

def current_state(c):
    return event_state(c["journey_state"] or c["equipment_status"] or "AVAILABLE")

def validate_transition(frm,to):
    if frm==to:return True
    return to in NEXT.get(frm,set())

def resolve_job(conn,c,job_ref,booking_ref):
    if job_ref:
        r=conn.execute("""SELECT j.id,j.job_ref,b.booking_ref,j.voyage_id
                          FROM jobs j JOIN bookings b ON b.id=j.booking_id WHERE j.job_ref=?""",(job_ref,)).fetchone()
        if not r:raise HTTPException(404,{"code":"UNKNOWN_JOB"})
        if booking_ref and booking_ref!=r["booking_ref"]:raise HTTPException(409,{"code":"BOOKING_JOB_MISMATCH"})
        return r
    if c["job_id"]:
        return conn.execute("""SELECT j.id,j.job_ref,b.booking_ref,j.voyage_id
                              FROM jobs j JOIN bookings b ON b.id=j.booking_id WHERE j.id=?""",(c["job_id"],)).fetchone()
    if booking_ref:
        r=conn.execute("""SELECT j.id,j.job_ref,b.booking_ref,j.voyage_id
                          FROM jobs j JOIN bookings b ON b.id=j.booking_id WHERE b.booking_ref=?""",(booking_ref,)).fetchone()
        if r:return r
    return None

def create_exception(conn,c,kind,severity,detail,expected_event=None,due_time=None,created_by="SYSTEM"):
    existing=conn.execute("""SELECT id,exception_ref FROM container_journey_exceptions
                             WHERE container_id=? AND exception_type=? AND status IN ('OPEN','ACKNOWLEDGED','IN_PROGRESS')
                             ORDER BY id DESC LIMIT 1""",(c["id"],kind)).fetchone()
    if existing:return existing["exception_ref"]
    ref="CJX-"+uuid.uuid4().hex[:12].upper()
    conn.execute("""INSERT INTO container_journey_exceptions(
      exception_ref,container_id,job_id,exception_type,severity,status,expected_event,actual_state,due_time,detail,created_at,created_by
    ) VALUES(?,?,?,?,?,'OPEN',?,?,?,?,?,?)""",
    (ref,c["id"],c["job_id"],kind,severity,expected_event,current_state(c),due_time,detail,now(),created_by))
    return ref

def detention_metrics(c,asof=None):
    start=parse_dt(c["free_time_start"])
    due=parse_dt(c["detention_due_date"])
    expected=parse_dt(c["expected_empty_return"])
    asof=asof or utcnow()
    overdue_days=0
    exposure=0.0
    if due and current_state(c) not in {"EMPTY_RETURN","INSPECTION","AVAILABLE","OFF_HIRED","PARTNER_RETURNED","AGENT_RETURNED","SOC_RELEASED","SOLD","SCRAPPED"}:
        delta=(asof-due).total_seconds()
        overdue_days=max(0,int((delta+86399)//86400))
        exposure=overdue_days*float(c["detention_rate"] or 0)
    return {
      "free_time_start":c["free_time_start"],"free_days":int(c["free_days"] or 0),
      "due_date":c["detention_due_date"],"expected_empty_return":c["expected_empty_return"],
      "overdue_days":overdue_days,"detention_rate":float(c["detention_rate"] or 0),
      "estimated_exposure":round(exposure,2)
    }

def scope_container_query(a):
    sc,args=scope_clause(a,"c")
    return sc,args

def close_recovered_exceptions(conn,c,event_type):
    if event_type=="EMPTY_RETURN":
        conn.execute("""UPDATE container_journey_exceptions SET status='RESOLVED',resolution_code='EMPTY_RETURN_RECEIVED',
                        resolution_note='Auto-resolved by empty return event',resolved_at=?
                        WHERE container_id=? AND exception_type='EMPTY_RETURN_OVERDUE' AND status<>'RESOLVED'""",(now(),c["id"]))
    if event_type in {"INSPECTION","AVAILABLE"}:
        conn.execute("""UPDATE container_journey_exceptions SET status='RESOLVED',resolution_code='INSPECTION_PROGRESS',
                        resolution_note='Auto-resolved by inspection/availability event',resolved_at=?
                        WHERE container_id=? AND exception_type IN ('INSPECTION_OVERDUE','DAMAGE_HOLD') AND status<>'RESOLVED'""",(now(),c["id"]))

@router.get("/policies")
def policies():
    conn=connect()
    try:return {"flow":STANDARD_FLOW,"transitions":{k:sorted(v) for k,v in NEXT.items()},"policies":[dict(r) for r in conn.execute("SELECT * FROM container_journey_policy ORDER BY policy_key")]}
    finally:conn.close()

@router.post("/containers/{container_no}/plan")
def plan_event(container_no:str,b:PlanBody,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
               x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
               x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    conn=connect()
    try:
        a=actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(conn)
        c=get_container(conn,container_no,a);job=resolve_job(conn,c,None,c["booking_ref"])
        if not job:raise HTTPException(409,{"code":"JOURNEY_REQUIRES_BOOKING_JOB"})
        et=event_state(b.event_type)
        ref=str(uuid.uuid4())
        loc=" / ".join(x for x in [b.port_code or c["current_port"],b.agent_code or c["agent_code"],b.depot_code or c["depot_code"]] if x)
        conn.execute("""INSERT INTO container_events(event_id,job_id,container_id,event_type,event_time,location,status,source_module,detail_json,
                     planned_time,actual_time,event_phase,port_code,branch_code,agent_code,depot_code,voyage_id,voyage_ref,vessel_name,source_work_ref,actor_role,actor_ref)
                     VALUES(?,?,?,?,?,?,?,?,?,?,NULL,'PLAN',?,?,?,?,?,?,?,?,?,?)""",
          (ref,job["id"],c["id"],et,b.planned_time,loc,current_state(c),"clx071-journey",json.dumps({"note":b.note},sort_keys=True),
           b.planned_time,b.port_code,b.branch_code,b.agent_code,b.depot_code,job["voyage_id"],b.voyage_ref,b.vessel_name,b.source_work_ref,a["role"],a["user"]))
        audit(conn,a,"JOURNEY_PLAN",c["id"],job["id"],{},b.model_dump(),{"event_id":ref})
        conn.execute("COMMIT");return {"ok":True,"event_id":ref,"event_type":et,"planned_time":b.planned_time}
    except Exception:
        try:conn.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:conn.close()

@router.post("/containers/{container_no}/events")
def post_event(container_no:str,b:EventBody,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
               x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
               x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    conn=connect()
    try:
        a=actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(conn)
        c=get_container(conn,container_no,a);before=dict(c);frm=current_state(c);to=event_state(b.event_type)
        valid=validate_transition(frm,to)
        if not valid:
            if not b.override:
                ref=create_exception(conn,c,"SEQUENCE_EXCEPTION","HIGH",f"Invalid journey transition {frm} -> {to}",expected_event=" / ".join(sorted(NEXT.get(frm,set()))),created_by=a["user"])
                conn.execute("COMMIT")
                raise HTTPException(409,{"code":"INVALID_JOURNEY_SEQUENCE","from":frm,"to":to,"allowed":sorted(NEXT.get(frm,set())),"exception_ref":ref})
            if a["role"] not in GLOBAL_OVERRIDE_ROLES or not (b.override_reason or "").strip():
                raise HTTPException(403,{"code":"OVERRIDE_REQUIRES_AUTHORIZED_ROLE_AND_REASON"})
        if to=="AVAILABLE" and (bool(c["damage_hold"]) or bool(c["inspection_hold"])):
            if not (b.override and a["role"] in GLOBAL_OVERRIDE_ROLES and (b.override_reason or "").strip()):
                raise HTTPException(409,{"code":"HOLD_BLOCKS_AVAILABLE","damage_hold":bool(c["damage_hold"]),"inspection_hold":bool(c["inspection_hold"])})
        job=resolve_job(conn,c,b.job_ref,b.booking_ref)
        if to not in {"AVAILABLE","INSPECTION","PURCHASE_RECEIVED","AGENT_STOCK_RECEIVED","PARTNER_STOCK_RECEIVED","SOC_ACCEPTED","ALLOCATED_FOR_SALE"} and not job:
            raise HTTPException(409,{"code":"JOURNEY_EVENT_REQUIRES_BOOKING_JOB"})
        actual=b.actual_time or now()
        loc=" / ".join(x for x in [b.port_code or c["current_port"],b.agent_code or c["agent_code"],b.depot_code or c["depot_code"]] if x)
        planned=conn.execute("""SELECT * FROM container_events WHERE container_id=? AND event_type=? AND event_phase='PLAN' AND actual_time IS NULL
                                ORDER BY planned_time,id LIMIT 1""",(c["id"],to)).fetchone()
        event_id=planned["event_id"] if planned else str(uuid.uuid4())
        detail={"note":b.note,"override":b.override,"override_reason":b.override_reason}
        if planned:
            conn.execute("""UPDATE container_events SET actual_time=?,event_time=?,event_phase='ACTUAL',location=?,status=?,detail_json=?,
                         port_code=COALESCE(?,port_code),branch_code=COALESCE(?,branch_code),agent_code=COALESCE(?,agent_code),depot_code=COALESCE(?,depot_code),
                         voyage_ref=COALESCE(?,voyage_ref),vessel_name=COALESCE(?,vessel_name),source_work_ref=COALESCE(?,source_work_ref),
                         override_used=?,override_reason=?,actor_role=?,actor_ref=? WHERE id=?""",
              (actual,actual,loc,to,json.dumps(detail,sort_keys=True),b.port_code,b.branch_code,b.agent_code,b.depot_code,b.voyage_ref,b.vessel_name,b.source_work_ref,
               b.override,b.override_reason,a["role"],a["user"],planned["id"]))
            event_db_id=planned["id"]
        else:
            cur=conn.execute("""INSERT INTO container_events(event_id,job_id,container_id,event_type,event_time,location,status,source_module,detail_json,
                         planned_time,actual_time,event_phase,port_code,branch_code,agent_code,depot_code,voyage_id,voyage_ref,vessel_name,source_work_ref,
                         override_used,override_reason,actor_role,actor_ref)
                         VALUES(?,?,?,?,?,?,?,?,?,NULL,?,'ACTUAL',?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
                (event_id,job["id"] if job else c["job_id"],c["id"],to,actual,loc,to,"clx071-journey",json.dumps(detail,sort_keys=True),actual,
                 b.port_code,b.branch_code,b.agent_code,b.depot_code,job["voyage_id"] if job else None,b.voyage_ref,b.vessel_name,b.source_work_ref,
                 b.override,b.override_reason,a["role"],a["user"]))
            event_db_id=cur.fetchone()["id"]

        free_days=int(b.free_days if b.free_days is not None else (c["free_days"] or 0))
        rate=float(b.detention_rate if b.detention_rate is not None else (c["detention_rate"] or 0))
        free_start=c["free_time_start"];due=c["detention_due_date"];expected=b.expected_empty_return or c["expected_empty_return"]
        if to=="GATE_OUT_FULL":
            free_start=actual
            ad=parse_dt(actual);due=dtiso(ad+datetime.timedelta(days=free_days)) if ad else None
        if to=="EMPTY_RETURN":
            expected=actual
        condition=b.condition or c["condition"]
        damage_hold=bool(c["damage_hold"]) if b.damage_hold is None else bool(b.damage_hold)
        inspection_hold=bool(c["inspection_hold"]) if b.inspection_hold is None else bool(b.inspection_hold)
        if to=="REPAIR": damage_hold=True
        if to=="INSPECTION": inspection_hold=True
        if to=="AVAILABLE" and valid:
            damage_hold=False;inspection_hold=False
        new_booking=(job["booking_ref"] if job else (b.booking_ref or c["booking_ref"]))
        new_job=(job["id"] if job else c["job_id"])
        if to in {"AVAILABLE","OFF_HIRED","PARTNER_RETURNED","AGENT_RETURNED","SOC_RELEASED","SOLD","SCRAPPED"}:
            new_booking=None;new_job=None
        conn.execute("""UPDATE containers SET journey_state=?,equipment_status=?,last_event_type=?,last_event_time=?,journey_updated_at=?,
                     current_port=COALESCE(?,current_port),branch_code=COALESCE(?,branch_code),agent_code=COALESCE(?,agent_code),depot_code=COALESCE(?,depot_code),
                     booking_ref=?,job_id=?,condition=?,damage_hold=?,inspection_hold=?,free_days=?,free_time_start=?,detention_due_date=?,detention_rate=?,
                     expected_empty_return=?,idle_days=0,updated_at=? WHERE id=?""",
          (to,to,to,actual,now(),b.port_code,b.branch_code,b.agent_code,b.depot_code,new_booking,new_job,condition,damage_hold,inspection_hold,free_days,
           free_start,due,rate,expected,now(),c["id"]))

        if b.cost_amount:
            ref="CFL-"+uuid.uuid4().hex[:12].upper()
            conn.execute("""INSERT INTO container_financial_ledger(entry_ref,container_id,job_id,booking_ref,bl_ref,movement_event_id,entry_type,charge_code,
                         party_type,party_code,amount,currency,quantity,rate,basis,source_type,source_ref,status,created_by,created_at)
                         VALUES(?,?,?,?,NULL,?,'COST',?,?,?,?,?,1,0,?,'MOVEMENT',?,'ACCRUED',?,?)""",
              (ref,c["id"],new_job or (job["id"] if job else c["job_id"]),new_booking or (job["booking_ref"] if job else c["booking_ref"]),event_db_id,
               b.charge_code or "MOVE_"+to,b.party_type,b.party_code,float(b.cost_amount),b.currency,to,b.source_ref or event_id,a["user"],now()))
        updated=dict(conn.execute("SELECT * FROM containers WHERE id=?",(c["id"],)).fetchone())
        close_recovered_exceptions(conn,updated,to)
        if b.override:
            create_exception(conn,updated,"AUTHORIZED_OVERRIDE","MEDIUM",f"Authorized override {frm} -> {to}: {b.override_reason}",created_by=a["user"])
        audit(conn,a,"JOURNEY_EVENT",c["id"],job["id"] if job else c["job_id"],before,updated,
              {"event_id":event_id,"from":frm,"to":to,"override":b.override,"override_reason":b.override_reason,"cost_amount":b.cost_amount})
        conn.execute("COMMIT")
        return {"ok":True,"event_id":event_id,"from":frm,"to":to,"record":updated,"detention":detention_metrics(updated)}
    except HTTPException as e:
        if e.status_code!=409 or not isinstance(e.detail,dict) or e.detail.get("code")!="INVALID_JOURNEY_SEQUENCE":
            try:conn.execute("ROLLBACK")
            except Exception:pass
        raise
    except Exception:
        try:conn.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:conn.close()

@router.get("/containers/{container_no}")
def journey(container_no:str,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
            x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
            x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    conn=connect()
    try:
        a=actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);c=get_container(conn,container_no,a)
        ev=[dict(r) for r in conn.execute("""SELECT * FROM container_events WHERE container_id=?
              ORDER BY COALESCE(actual_time,planned_time,event_time),id""",(c["id"],)).fetchall()]
        ex=[dict(r) for r in conn.execute("SELECT * FROM container_journey_exceptions WHERE container_id=? ORDER BY id DESC",(c["id"],)).fetchall()]
        frm=current_state(c);allowed=sorted(NEXT.get(frm,set()))
        return {"actor":a,"container":dict(c),"state":frm,"allowed_next":allowed,"timeline":ev,"detention":detention_metrics(c),"exceptions":ex}
    finally:conn.close()

@router.post("/scan-exceptions")
def scan_exceptions(x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                    x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                    x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    conn=connect();created=[]
    try:
        a=actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(conn)
        sc,args=scope_container_query(a)
        rows=[dict(r) for r in conn.execute("SELECT c.* FROM containers c WHERE 1=1"+sc,args).fetchall()]
        nowdt=utcnow()
        for c in rows:
            dm=detention_metrics(c,nowdt)
            if dm["overdue_days"]>0:
                created.append(create_exception(conn,c,"EMPTY_RETURN_OVERDUE","HIGH",
                  f"Empty return overdue by {dm['overdue_days']} day(s); estimated detention exposure {dm['estimated_exposure']} {c['currency'] or 'USD'}",
                  expected_event="EMPTY_RETURN",due_time=c["detention_due_date"],created_by=a["user"]))
            if bool(c["damage_hold"]):
                created.append(create_exception(conn,c,"DAMAGE_HOLD","HIGH","Container is on damage hold",expected_event="INSPECTION/REPAIR",created_by=a["user"]))
            if bool(c["inspection_hold"]):
                created.append(create_exception(conn,c,"INSPECTION_OVERDUE","MEDIUM","Container awaits inspection clearance",expected_event="INSPECTION/AVAILABLE",created_by=a["user"]))
            plans=conn.execute("""SELECT * FROM container_events WHERE container_id=? AND event_phase='PLAN' AND actual_time IS NULL
                                  AND planned_time IS NOT NULL ORDER BY planned_time""",(c["id"],)).fetchall()
            for p in plans:
                pd=parse_dt(p["planned_time"])
                if pd and pd<nowdt:
                    created.append(create_exception(conn,c,"MISSING_EVENT","HIGH",f"Planned event {p['event_type']} was not posted by {p['planned_time']}",
                      expected_event=p["event_type"],due_time=p["planned_time"],created_by=a["user"]))
        conn.execute("COMMIT")
        return {"ok":True,"scanned":len(rows),"exceptions_touched":len(set(created))}
    except Exception:
        try:conn.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:conn.close()

@router.get("/exceptions")
def exceptions(status:Optional[str]="OPEN",severity:Optional[str]=None,limit:int=Query(300,ge=1,le=1000),
               x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
               x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
               x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    conn=connect()
    try:
        a=actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        sc,args=scope_container_query(a)
        q="""SELECT e.*,c.container_no,c.size_type,c.current_port,c.agent_code,c.depot_code,c.owner_party_type,c.owner_party_code
             FROM container_journey_exceptions e JOIN containers c ON c.id=e.container_id WHERE 1=1"""
        if status:q+=" AND e.status=?";args.append(status)
        if severity:q+=" AND e.severity=?";args.append(severity)
        q+=sc+" ORDER BY CASE e.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END,e.id DESC LIMIT ?"
        args.append(limit)
        rows=[dict(r) for r in conn.execute(q,args).fetchall()]
        return {"actor":a,"count":len(rows),"records":rows}
    finally:conn.close()

@router.post("/exceptions/{exception_ref}/decision")
def exception_decision(exception_ref:str,b:ExceptionDecision,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                       x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                       x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    conn=connect()
    try:
        a=actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(conn)
        e=conn.execute("""SELECT e.*,c.container_no FROM container_journey_exceptions e
                          JOIN containers c ON c.id=e.container_id WHERE e.exception_ref=?""",(exception_ref,)).fetchone()
        if not e:raise HTTPException(404,"Unknown exception")
        action=b.action.upper()
        if action=="ACKNOWLEDGE":
            conn.execute("UPDATE container_journey_exceptions SET status='ACKNOWLEDGED',acknowledged_at=?,owner_role=?,owner_ref=? WHERE id=?",
                         (now(),b.owner_role or a["role"],b.owner_ref or a["user"],e["id"]))
        elif action=="IN_PROGRESS":
            conn.execute("UPDATE container_journey_exceptions SET status='IN_PROGRESS',owner_role=?,owner_ref=? WHERE id=?",
                         (b.owner_role or a["role"],b.owner_ref or a["user"],e["id"]))
        elif action=="RESOLVE":
            if not b.resolution_code:raise HTTPException(422,{"code":"RESOLUTION_CODE_REQUIRED"})
            conn.execute("""UPDATE container_journey_exceptions SET status='RESOLVED',resolution_code=?,resolution_note=?,resolved_at=?,
                         owner_role=COALESCE(owner_role,?),owner_ref=COALESCE(owner_ref,?) WHERE id=?""",
                         (b.resolution_code,b.note,now(),a["role"],a["user"],e["id"]))
        else:raise HTTPException(422,"Invalid action")
        audit(conn,a,"JOURNEY_EXCEPTION_"+action,e["container_id"],e["job_id"],dict(e),b.model_dump(),{"exception_ref":exception_ref})
        conn.execute("COMMIT");return {"ok":True,"exception_ref":exception_ref,"status":{"ACKNOWLEDGE":"ACKNOWLEDGED","IN_PROGRESS":"IN_PROGRESS","RESOLVE":"RESOLVED"}[action]}
    except Exception:
        try:conn.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:conn.close()

@router.get("/kpis")
def kpis(x_role:str=Header("AUDITOR"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
         x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
         x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    conn=connect()
    try:
        a=actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);sc,args=scope_container_query(a)
        rows=[dict(r) for r in conn.execute("SELECT c.* FROM containers c WHERE 1=1"+sc,args).fetchall()]
        ids=[r["id"] for r in rows];today=utcnow()
        exposure=0.0;overdue=0;holds=0
        for c in rows:
            d=detention_metrics(c,today);exposure+=d["estimated_exposure"];overdue+=1 if d["overdue_days"]>0 else 0
            holds+=1 if c["damage_hold"] or c["inspection_hold"] else 0
        open_exc=0;high_exc=0;missing=0
        if ids:
            ph=",".join("?" for _ in ids)
            er=conn.execute(f"""SELECT COUNT(*) total,
                     COALESCE(SUM(CASE WHEN severity IN ('HIGH','CRITICAL') THEN 1 ELSE 0 END),0) high_count,
                     COALESCE(SUM(CASE WHEN exception_type='MISSING_EVENT' THEN 1 ELSE 0 END),0) missing_count
                     FROM container_journey_exceptions WHERE status<>'RESOLVED' AND container_id IN ({ph})""",ids).fetchone()
            open_exc=int(er["total"] or 0);high_exc=int(er["high_count"] or 0);missing=int(er["missing_count"] or 0)
        events=[]
        if ids:
            ph=",".join("?" for _ in ids)
            events=[dict(r) for r in conn.execute(f"""SELECT event_type,COUNT(*) n FROM container_events
                    WHERE event_phase='ACTUAL' AND container_id IN ({ph}) GROUP BY event_type ORDER BY n DESC""",ids).fetchall()]
        # Turnaround from EMPTY_PICKUP to EMPTY_RETURN per container, based on latest pair.
        turns=[]
        for c in rows:
            pick=conn.execute("SELECT actual_time FROM container_events WHERE container_id=? AND event_type='EMPTY_PICKUP' AND actual_time IS NOT NULL ORDER BY actual_time DESC LIMIT 1",(c["id"],)).fetchone()
            ret=conn.execute("SELECT actual_time FROM container_events WHERE container_id=? AND event_type='EMPTY_RETURN' AND actual_time IS NOT NULL ORDER BY actual_time DESC LIMIT 1",(c["id"],)).fetchone()
            if pick and ret:
                p=parse_dt(pick["actual_time"]);r=parse_dt(ret["actual_time"])
                if p and r and r>=p:turns.append((r-p).total_seconds()/86400)
        return {"actor":a,"containers":len(rows),"open_exceptions":open_exc,"high_exceptions":high_exc,"missing_events":missing,
                "empty_return_overdue":overdue,"detention_exposure":round(exposure,2),"damage_or_inspection_holds":holds,
                "avg_turnaround_days":round(sum(turns)/len(turns),2) if turns else 0,"event_mix":events}
    finally:conn.close()
