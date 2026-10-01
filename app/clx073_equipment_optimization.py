from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
import datetime, json, uuid

from .db import connect, tx
from .clx070_container_master_control import actor, require_action, scope_clause, audit, now
from .clx071_container_journey import parse_dt
from .clx072_equipment_network import scoped_containers, owner_segment

router=APIRouter(prefix="/api/clx073/optimization",tags=["CLX-073 Equipment Cost Optimization"])

STRATEGIC_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT"}
APPROVE_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT","FINANCE"}

class CostComponents(BaseModel):
    movement:float=Field(default=0,ge=0)
    handling:float=Field(default=0,ge=0)
    depot:float=Field(default=0,ge=0)
    load:float=Field(default=0,ge=0)
    discharge:float=Field(default=0,ge=0)
    feeder:float=Field(default=0,ge=0)
    truck:float=Field(default=0,ge=0)
    rail:float=Field(default=0,ge=0)
    lease:float=Field(default=0,ge=0)
    detention_idle_impact:float=0
    aging_impact:float=0

class OptionInput(BaseModel):
    option_type:str
    source_port:Optional[str]=None
    source_branch:Optional[str]=None
    source_agent:Optional[str]=None
    source_depot:Optional[str]=None
    distance_km:float=Field(default=0,ge=0)
    lead_days:float=Field(default=0,ge=0)
    expected_turnaround_days:float=Field(default=0,ge=0)
    source_priority:int=Field(default=999,ge=1,le=9999)
    components:CostComponents=Field(default_factory=CostComponents)

class CompareBody(BaseModel):
    destination_port:str
    size_type:str
    qty:int=Field(ge=1,le=10000)
    required_by:Optional[str]=None
    currency:str="USD"
    options:list[OptionInput]

class DecisionBody(BaseModel):
    decision:str
    option_ref:Optional[str]=None
    note:Optional[str]=None

class VarianceBody(BaseModel):
    work_ref:str
    actual_cost:Optional[float]=Field(default=None,ge=0)
    actual_lead_days:Optional[float]=Field(default=None,ge=0)
    forecast_qty:float=Field(default=0,ge=0)
    actual_shortage_avoided:float=Field(default=0,ge=0)
    utilization_delta_pct:float=0

class ExceptionDecision(BaseModel):
    action:str
    resolution_code:Optional[str]=None
    note:Optional[str]=None
    owner_role:Optional[str]=None
    owner_ref:Optional[str]=None

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)

def historical_movement_cost(conn,source_port,destination_port,size_type):
    # Reuse existing finance ledger; no parallel rate table.
    try:
        r=conn.execute("""SELECT AVG(ABS(f.amount)) avg_cost
          FROM container_financial_ledger f
          JOIN containers c ON c.id=f.container_id
          LEFT JOIN container_events e ON e.id=f.movement_event_id
          WHERE f.entry_type='COST' AND c.size_type=?
            AND f.source_type='MOVEMENT'
            AND (? IS NULL OR e.port_code=? OR c.current_port=?)
        """,(size_type,source_port,source_port,source_port)).fetchone()
        return float(r["avg_cost"] or 0) if r else 0
    except Exception:
        return 0.0

def option_total(o,historical,qty):
    c=o.components
    direct=c.movement+c.handling+c.depot+c.load+c.discharge+c.feeder+c.truck+c.rail+c.lease
    impact=c.detention_idle_impact+c.aging_impact
    history=max(0,historical)
    # Historical movement cost is fallback guidance only, not double-counted when explicit movement inputs exist.
    movement_basis=c.movement if c.movement>0 else history
    total=(movement_basis+c.handling+c.depot+c.load+c.discharge+c.feeder+c.truck+c.rail+c.lease+impact)*qty
    return round(total,2),round(movement_basis,2)

def score_option(total,lead,priority):
    # Lower is better. Cost dominates, then lead time, then configured source priority.
    return round(float(total)+(float(lead)*10)+(int(priority)*0.01),4)

def check_scope_option(conn,a,o,destination_port,size_type):
    if a["role"] in {"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT","FINANCE","AUDITOR"}:
        return
    visible=scoped_containers(conn,a)
    ports={x.get("current_port") for x in visible if x.get("size_type")==size_type}
    for p in (o.source_port,destination_port):
        if p and p not in ports:
            raise HTTPException(403,{"code":"OPTIMIZATION_OPTION_OUTSIDE_CUSTODY_SCOPE","port":p})

def create_exception(conn,kind,severity,detail,work_ref=None,run_ref=None,option_ref=None,threshold=None,actual=None):
    ex="EOX-"+uuid.uuid4().hex[:12].upper()
    conn.execute("""INSERT INTO equipment_optimization_exceptions(
      exception_ref,work_ref,run_ref,option_ref,exception_type,severity,status,threshold_value,actual_value,detail,created_at
    ) VALUES(?,?,?,?,?,?,'OPEN',?,?,?,?,?)""",
      (ex,work_ref,run_ref,option_ref,kind,severity,threshold,actual,detail,now()))
    return ex

@router.get("/policies")
def policies():
    c=connect()
    try:return {"policies":[dict(r) for r in c.execute("SELECT * FROM equipment_optimization_policy ORDER BY policy_key")]}
    finally:c.close()

@router.post("/compare")
def compare(b:CompareBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
            x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
            x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    if not b.options:raise HTTPException(422,{"code":"AT_LEAST_ONE_OPTION_REQUIRED"})
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write")
        tx(c);run_ref="EOR-"+uuid.uuid4().hex[:12].upper()
        c.execute("""INSERT INTO equipment_optimization_runs(run_ref,destination_port,size_type,qty,required_by,currency,status,maker_role,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,'DRAFT',?,?,?)""",
                  (run_ref,b.destination_port,b.size_type,b.qty,b.required_by,b.currency,a["role"],now(),now()))
        rid=c.execute("SELECT id FROM equipment_optimization_runs WHERE run_ref=?",(run_ref,)).fetchone()["id"]
        out=[]
        for o in b.options:
            check_scope_option(c,a,o,b.destination_port,b.size_type)
            hist=historical_movement_cost(c,o.source_port,b.destination_port,b.size_type)
            total,movement_basis=option_total(o,hist,b.qty)
            score=score_option(total,o.lead_days,o.source_priority)
            option_ref="EOP-"+uuid.uuid4().hex[:12].upper()
            explanation={
              "explicit_components":o.components.model_dump(),
              "historical_movement_basis":hist,
              "movement_basis_used":movement_basis,
              "qty":b.qty,
              "lead_days":o.lead_days,
              "source_priority":o.source_priority,
              "formula":"estimated_total_cost + lead_days*10 + source_priority*0.01"
            }
            c.execute("""INSERT INTO equipment_optimization_options(
              run_id,option_ref,option_type,source_port,source_branch,source_agent,source_depot,distance_km,lead_days,
              movement_cost,handling_cost,depot_cost,load_cost,discharge_cost,feeder_cost,truck_cost,rail_cost,lease_cost,
              detention_idle_impact,aging_impact,estimated_total_cost,expected_turnaround_days,historical_cost_basis,source_priority,score,explanation_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (rid,option_ref,o.option_type.upper(),o.source_port,o.source_branch,o.source_agent,o.source_depot,o.distance_km,o.lead_days,
               movement_basis,o.components.handling,o.components.depot,o.components.load,o.components.discharge,o.components.feeder,o.components.truck,
               o.components.rail,o.components.lease,o.components.detention_idle_impact,o.components.aging_impact,total,o.expected_turnaround_days,
               hist,o.source_priority,score,json.dumps(explanation,sort_keys=True),now()))
            out.append({"option_ref":option_ref,"option_type":o.option_type.upper(),"source_port":o.source_port,"source_depot":o.source_depot,
                        "source_agent":o.source_agent,"estimated_total_cost":total,"lead_days":o.lead_days,"score":score,"explanation":explanation})
        out.sort(key=lambda x:(x["score"],x["estimated_total_cost"],x["lead_days"]))
        best=out[0]
        c.execute("""UPDATE equipment_optimization_runs SET selected_option_type=?,selected_source_port=?,selected_source_depot=?,selected_source_agent=?,
                     selected_estimated_cost=?,selected_estimated_lead_days=?,selected_score=?,selected_reason=?,updated_at=? WHERE id=?""",
                  (best["option_type"],best["source_port"],best["source_depot"],best["source_agent"],best["estimated_total_cost"],best["lead_days"],
                   best["score"],"Lowest explainable score from cost, lead time and source priority",now(),rid))
        audit(c,a,"OPTIMIZATION_COMPARE",None,None,{},{"run_ref":run_ref,"recommended":best},{"option_count":len(out)});c.execute("COMMIT")
        return {"ok":True,"run_ref":run_ref,"recommended":best,"options":out,"currency":b.currency}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.get("/runs/{run_ref}")
def run_detail(run_ref:str,x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
               x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
               x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        r=c.execute("SELECT * FROM equipment_optimization_runs WHERE run_ref=?",(run_ref,)).fetchone()
        if not r:raise HTTPException(404,"Unknown optimization run")
        opts=[dict(x) for x in c.execute("SELECT * FROM equipment_optimization_options WHERE run_id=? ORDER BY score,estimated_total_cost",(r["id"],)).fetchall()]
        return {"actor":a,"run":dict(r),"options":opts}
    finally:c.close()

@router.post("/runs/{run_ref}/decision")
def run_decision(run_ref:str,b:DecisionBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                 x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        if a["role"] not in APPROVE_ROLES:raise HTTPException(403,{"code":"OPTIMIZATION_APPROVAL_ROLE_REQUIRED"})
        tx(c);r=c.execute("SELECT * FROM equipment_optimization_runs WHERE run_ref=?",(run_ref,)).fetchone()
        if not r:raise HTTPException(404,"Unknown optimization run")
        d=b.decision.upper()
        if d not in {"SUBMIT","APPROVE","REJECT","CANCEL"}:raise HTTPException(422,"Invalid decision")
        if d=="APPROVE" and r["maker_role"]==a["role"]:raise HTTPException(409,{"code":"MAKER_CHECKER_CONFLICT"})
        opt=None
        if b.option_ref:
            opt=c.execute("SELECT * FROM equipment_optimization_options WHERE run_id=? AND option_ref=?",(r["id"],b.option_ref)).fetchone()
            if not opt:raise HTTPException(404,"Option not in optimization run")
        elif d=="APPROVE":
            opt=c.execute("SELECT * FROM equipment_optimization_options WHERE run_id=? ORDER BY score,estimated_total_cost LIMIT 1",(r["id"],)).fetchone()
        status={"SUBMIT":"SUBMITTED","APPROVE":"APPROVED","REJECT":"REJECTED","CANCEL":"CANCELLED"}[d]
        if opt:
            c.execute("""UPDATE equipment_optimization_runs SET status=?,selected_option_type=?,selected_source_port=?,selected_source_depot=?,selected_source_agent=?,
                         selected_estimated_cost=?,selected_estimated_lead_days=?,selected_score=?,selected_reason=?,checker_role=?,
                         approved_at=CASE WHEN ?='APPROVED' THEN ? ELSE approved_at END,updated_at=? WHERE id=?""",
                      (status,opt["option_type"],opt["source_port"],opt["source_depot"],opt["source_agent"],opt["estimated_total_cost"],opt["lead_days"],
                       opt["score"],b.note or "Approved selected optimization option",a["role"],status,now(),now(),r["id"]))
        else:
            c.execute("UPDATE equipment_optimization_runs SET status=?,updated_at=? WHERE id=?",(status,now(),r["id"]))
        audit(c,a,"OPTIMIZATION_"+d,None,None,dict(r),{"status":status,"option_ref":b.option_ref},{"run_ref":run_ref,"note":b.note});c.execute("COMMIT")
        return {"ok":True,"run_ref":run_ref,"status":status}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/runs/{run_ref}/create-work-item")
def create_work_item(run_ref:str,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                     x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                     x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        r=c.execute("SELECT * FROM equipment_optimization_runs WHERE run_ref=?",(run_ref,)).fetchone()
        if not r:raise HTTPException(404,"Unknown optimization run")
        if r["status"]!="APPROVED":raise HTTPException(409,{"code":"OPTIMIZATION_RUN_NOT_APPROVED"})
        if r["linked_work_ref"]:
            c.execute("COMMIT");return {"ok":True,"work_ref":r["linked_work_ref"],"existing":True}
        opt=c.execute("""SELECT * FROM equipment_optimization_options WHERE run_id=? AND option_type=? AND
                        COALESCE(source_port,'')=COALESCE(?, '') ORDER BY score LIMIT 1""",
                      (r["id"],r["selected_option_type"],r["selected_source_port"])).fetchone()
        if not opt:raise HTTPException(409,{"code":"SELECTED_OPTION_NOT_FOUND"})
        typ=opt["option_type"]
        wt="REPOSITION" if typ=="REPOSITION" else "LEASE" if typ in {"LEASE_ON_HIRE","LEASE_OFF_HIRE"} else "AGENT_SUPPLY" if typ=="AGENT_SUPPLY" else "SOC" if typ=="SOC_SUPPLY" else "REPOSITION"
        ref=f"EQ-{wt[:3]}-{utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        payload={"clx073_run_ref":run_ref,"clx073_option_ref":opt["option_ref"],"option_type":typ,"planned_cost":float(opt["estimated_total_cost"] or 0),
                 "planned_lead_days":float(opt["lead_days"] or 0),"source_branch":opt["source_branch"],"source_agent":opt["source_agent"],"source_depot":opt["source_depot"]}
        c.execute("""INSERT INTO equipment_work_items(work_ref,work_type,status,booking_ref,job_ref,size_type,qty,source_port,destination_port,source_agent,destination_agent,
                   source_depot,destination_depot,estimated_cost,currency,required_by,payload_json,maker_role,created_at,updated_at)
                   VALUES(?,?,'DRAFT',NULL,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (ref,wt,r["size_type"],r["qty"],opt["source_port"],r["destination_port"],opt["source_agent"],None,opt["source_depot"],None,
           opt["estimated_total_cost"],r["currency"],r["required_by"],json.dumps(payload,sort_keys=True),a["role"],now(),now()))
        c.execute("UPDATE equipment_optimization_runs SET linked_work_ref=?,updated_at=? WHERE id=?",(ref,now(),r["id"]))
        audit(c,a,"OPTIMIZATION_TO_WORK_ITEM",None,None,dict(r),{"work_ref":ref},{"run_ref":run_ref,"option_ref":opt["option_ref"]});c.execute("COMMIT")
        return {"ok":True,"work_ref":ref,"status":"DRAFT","execution":"EXISTING_EQUIPMENT_WORK_ITEMS"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

def derive_actual_cost(conn,work_ref):
    total=0.0
    try:
        rows=conn.execute("""SELECT f.amount FROM container_financial_ledger f
          WHERE f.source_ref=? OR f.source_ref IN (
            SELECT event_id FROM container_events WHERE source_work_ref=?
          )""",(work_ref,work_ref)).fetchall()
        total=sum(abs(float(x["amount"] or 0)) for x in rows)
    except Exception:pass
    return round(total,2)

def derive_actual_lead(conn,work_ref):
    try:
        rows=conn.execute("""SELECT actual_time FROM container_events WHERE source_work_ref=? AND actual_time IS NOT NULL ORDER BY actual_time""",(work_ref,)).fetchall()
        if len(rows)>=2:
            a=parse_dt(rows[0]["actual_time"]);b=parse_dt(rows[-1]["actual_time"])
            if a and b and b>=a:return round((b-a).total_seconds()/86400,2)
    except Exception:pass
    return 0.0

@router.post("/variance")
def variance(b:VarianceBody,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
             x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
             x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        w=c.execute("SELECT * FROM equipment_work_items WHERE work_ref=?",(b.work_ref,)).fetchone()
        if not w:raise HTTPException(404,"Unknown work item")
        p=json.loads(w["payload_json"] or "{}")
        run_ref=p.get("clx073_run_ref");option_ref=p.get("clx073_option_ref")
        planned_cost=float(p.get("planned_cost") or w["estimated_cost"] or 0);planned_lead=float(p.get("planned_lead_days") or 0)
        actual_cost=float(b.actual_cost) if b.actual_cost is not None else derive_actual_cost(c,b.work_ref)
        actual_lead=float(b.actual_lead_days) if b.actual_lead_days is not None else derive_actual_lead(c,b.work_ref)
        cost_var=round(actual_cost-planned_cost,2);lead_var=round(actual_lead-planned_lead,2)
        avoided=float(b.actual_shortage_avoided);roi=round(((avoided*planned_cost)-actual_cost)/actual_cost,4) if actual_cost>0 else 0
        c.execute("""INSERT INTO equipment_execution_variance(work_ref,run_ref,option_ref,planned_cost,actual_cost,planned_lead_days,actual_lead_days,cost_variance,
                     lead_variance_days,forecast_qty,actual_shortage_avoided,utilization_delta_pct,reposition_roi,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(work_ref) DO UPDATE SET actual_cost=EXCLUDED.actual_cost,actual_lead_days=EXCLUDED.actual_lead_days,
                     cost_variance=EXCLUDED.cost_variance,lead_variance_days=EXCLUDED.lead_variance_days,forecast_qty=EXCLUDED.forecast_qty,
                     actual_shortage_avoided=EXCLUDED.actual_shortage_avoided,utilization_delta_pct=EXCLUDED.utilization_delta_pct,
                     reposition_roi=EXCLUDED.reposition_roi,updated_at=EXCLUDED.updated_at""",
                  (b.work_ref,run_ref,option_ref,planned_cost,actual_cost,planned_lead,actual_lead,cost_var,lead_var,b.forecast_qty,avoided,
                   b.utilization_delta_pct,roi,now()))
        pol={x["policy_key"]:x["policy_value"] for x in c.execute("SELECT * FROM equipment_optimization_policy").fetchall()}
        cost_pct=(cost_var/planned_cost*100) if planned_cost>0 else 0
        created=[]
        if cost_pct>float(pol.get("cost_overrun_threshold_pct","15")):
            created.append(create_exception(c,"COST_OVERRUN","HIGH",f"Actual cost exceeds planned by {round(cost_pct,2)}%",b.work_ref,run_ref,option_ref,float(pol.get("cost_overrun_threshold_pct","15")),cost_pct))
        if lead_var>float(pol.get("delay_overrun_threshold_days","2")):
            created.append(create_exception(c,"DELAY_OVERRUN","HIGH",f"Actual lead exceeds planned by {lead_var} day(s)",b.work_ref,run_ref,option_ref,float(pol.get("delay_overrun_threshold_days","2")),lead_var))
        audit(c,a,"OPTIMIZATION_VARIANCE",None,None,{},b.model_dump(),{"work_ref":b.work_ref,"exceptions":created});c.execute("COMMIT")
        return {"ok":True,"work_ref":b.work_ref,"planned_cost":planned_cost,"actual_cost":actual_cost,"cost_variance":cost_var,
                "planned_lead_days":planned_lead,"actual_lead_days":actual_lead,"lead_variance_days":lead_var,"reposition_roi":roi,"exceptions":created}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.get("/exceptions")
def exceptions(status:Optional[str]="OPEN",limit:int=Query(300,ge=1,le=1000)):
    c=connect()
    try:
        q="SELECT * FROM equipment_optimization_exceptions WHERE 1=1";args=[]
        if status:q+=" AND status=?";args.append(status)
        q+=" ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END,id DESC LIMIT ?";args.append(limit)
        return {"count":0 if not args else None,"records":[dict(r) for r in c.execute(q,args).fetchall()]}
    finally:c.close()

@router.post("/exceptions/{exception_ref}/decision")
def exception_decision(exception_ref:str,b:ExceptionDecision,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                       x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                       x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        e=c.execute("SELECT * FROM equipment_optimization_exceptions WHERE exception_ref=?",(exception_ref,)).fetchone()
        if not e:raise HTTPException(404,"Unknown optimization exception")
        d=b.action.upper()
        if d=="ACKNOWLEDGE":
            c.execute("UPDATE equipment_optimization_exceptions SET status='ACKNOWLEDGED',acknowledged_at=?,owner_role=?,owner_ref=? WHERE id=?",(now(),b.owner_role or a["role"],b.owner_ref or a["user"],e["id"]))
        elif d=="RESOLVE":
            if not b.resolution_code:raise HTTPException(422,{"code":"RESOLUTION_CODE_REQUIRED"})
            c.execute("UPDATE equipment_optimization_exceptions SET status='RESOLVED',resolution_code=?,resolution_note=?,resolved_at=?,owner_role=COALESCE(owner_role,?),owner_ref=COALESCE(owner_ref,?) WHERE id=?",
                      (b.resolution_code,b.note,now(),a["role"],a["user"],e["id"]))
        else:raise HTTPException(422,"Invalid action")
        audit(c,a,"OPTIMIZATION_EXCEPTION_"+d,None,None,dict(e),b.model_dump(),{"exception_ref":exception_ref});c.execute("COMMIT")
        return {"ok":True,"exception_ref":exception_ref,"status":"ACKNOWLEDGED" if d=="ACKNOWLEDGE" else "RESOLVED"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.get("/kpis")
def kpis():
    c=connect()
    try:
        v=[dict(r) for r in c.execute("SELECT * FROM equipment_execution_variance").fetchall()]
        runs=[dict(r) for r in c.execute("SELECT * FROM equipment_optimization_runs").fetchall()]
        approved=[r for r in runs if r["status"]=="APPROVED"]
        forecast=sum(float(x["forecast_qty"] or 0) for x in v);avoided=sum(float(x["actual_shortage_avoided"] or 0) for x in v)
        accuracy=round(min(100,(avoided/forecast*100)),2) if forecast>0 else 0
        planned=sum(float(x["planned_cost"] or 0) for x in v);actual=sum(float(x["actual_cost"] or 0) for x in v)
        savings=round(planned-actual,2)
        roi=[float(x["reposition_roi"] or 0) for x in v]
        open_exc=c.execute("SELECT COUNT(*) n FROM equipment_optimization_exceptions WHERE status<>'RESOLVED'").fetchone()["n"]
        return {"optimization_runs":len(runs),"approved_runs":len(approved),"executions_measured":len(v),
                "planned_cost":round(planned,2),"actual_cost":round(actual,2),"management_savings":savings,
                "forecast_accuracy_pct":accuracy,"shortage_avoided_units":round(avoided,2),
                "avg_utilization_delta_pct":round(sum(float(x["utilization_delta_pct"] or 0) for x in v)/len(v),2) if v else 0,
                "avg_reposition_roi":round(sum(roi)/len(roi),4) if roi else 0,"open_cost_delay_exceptions":int(open_exc or 0)}
    finally:c.close()
