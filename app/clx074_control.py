from __future__ import annotations
from fastapi import APIRouter,Header,HTTPException,Query
from pydantic import BaseModel
from typing import Optional
import datetime,uuid
from .db import connect,tx
from .clx070_container_master_control import actor,require_action,get_container,audit,now
from .clx071_container_journey import parse_dt

router=APIRouter(prefix="/api/clx074/mr",tags=["CLX-074 Depot M&R"])

class ExceptionDecision(BaseModel):
    action:str
    resolution_code:Optional[str]=None
    note:Optional[str]=None
    owner_role:Optional[str]=None
    owner_ref:Optional[str]=None

def ref(p):return p+"-"+uuid.uuid4().hex[:12].upper()
def utcnow():return datetime.datetime.now(datetime.timezone.utc)

@router.get("/policies")
def policies():
    c=connect()
    try:return {"policies":[dict(r) for r in c.execute("SELECT * FROM container_mr_policy ORDER BY policy_key")]}
    finally:c.close()

@router.post("/scan-exceptions")
def scan(x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        pol={r["policy_key"]:r["policy_value"] for r in c.execute("SELECT * FROM container_mr_policy")}
        grace=float(pol.get("repair_overdue_days","2"));threshold=float(pol.get("estimate_overrun_pct","15"));created=[]
        rows=c.execute("SELECT ro.*,c.container_no,e.total_estimate FROM container_repair_orders ro JOIN containers c ON c.id=ro.container_id LEFT JOIN container_repair_estimates e ON e.id=ro.estimate_id").fetchall()
        for r in rows:
            try:get_container(c,r["container_no"],a)
            except HTTPException:continue
            due=parse_dt(r["planned_complete"])
            if r["status"] in ("APPROVED","IN_PROGRESS") and due and utcnow()>due+datetime.timedelta(days=grace):
                er=ref("MRX");days=(utcnow()-due).total_seconds()/86400
                c.execute("INSERT INTO container_mr_exceptions(exception_ref,container_id,inspection_id,repair_work_ref,exception_type,severity,status,threshold_value,actual_value,detail,created_at) VALUES(?,?,?,?, 'REPAIR_OVERDUE','HIGH','OPEN',?,?,?,?)",
                          (er,r["container_id"],r["inspection_id"],r["work_ref"],grace,days,f"Repair overdue by {round(days,2)} day(s)",now()));created.append(er)
            est=float(r["total_estimate"] or 0);act=float(r["actual_total_cost"] or 0)
            pct=((act-est)/est*100) if est>0 else 0
            if act>0 and pct>threshold:
                exists=c.execute("SELECT 1 FROM container_mr_exceptions WHERE repair_work_ref=? AND exception_type='ESTIMATE_OVERRUN' AND status<>'RESOLVED'",(r["work_ref"],)).fetchone()
                if not exists:
                    er=ref("MRX");c.execute("INSERT INTO container_mr_exceptions(exception_ref,container_id,inspection_id,repair_work_ref,exception_type,severity,status,threshold_value,actual_value,detail,created_at) VALUES(?,?,?,?, 'ESTIMATE_OVERRUN','HIGH','OPEN',?,?,?,?)",
                          (er,r["container_id"],r["inspection_id"],r["work_ref"],threshold,pct,f"Actual repair cost exceeds estimate by {round(pct,2)}%",now()));created.append(er)
        c.execute("COMMIT");return {"ok":True,"count":len(created),"created":created}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.get("/exceptions")
def exceptions(status:Optional[str]="OPEN",limit:int=Query(300,ge=1,le=1000),x_role:str=Header("VIEWER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        q="SELECT e.*,c.container_no FROM container_mr_exceptions e JOIN containers c ON c.id=e.container_id WHERE 1=1";args=[]
        if status:q+=" AND e.status=?";args.append(status)
        q+=" ORDER BY CASE e.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END,e.id DESC LIMIT ?";args.append(limit)
        out=[]
        for r in c.execute(q,args):
            try:get_container(c,r["container_no"],a);out.append(dict(r))
            except HTTPException:pass
        return {"actor":a,"count":len(out),"records":out}
    finally:c.close()

@router.post("/exceptions/{exception_ref}/decision")
def exception_decision(exception_ref:str,b:ExceptionDecision,x_role:str=Header("OPS"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        e=c.execute("SELECT e.*,c.container_no FROM container_mr_exceptions e JOIN containers c ON c.id=e.container_id WHERE e.exception_ref=?",(exception_ref,)).fetchone()
        if not e:raise HTTPException(404,"Unknown exception")
        get_container(c,e["container_no"],a);d=b.action.upper()
        if d=="ACKNOWLEDGE":
            c.execute("UPDATE container_mr_exceptions SET status='ACKNOWLEDGED',acknowledged_at=?,owner_role=?,owner_ref=? WHERE id=?",(now(),b.owner_role or a["role"],b.owner_ref or a["user"],e["id"]))
        elif d=="RESOLVE":
            if not b.resolution_code:raise HTTPException(422,{"code":"RESOLUTION_CODE_REQUIRED"})
            c.execute("UPDATE container_mr_exceptions SET status='RESOLVED',resolution_code=?,resolution_note=?,resolved_at=?,owner_role=COALESCE(owner_role,?),owner_ref=COALESCE(owner_ref,?) WHERE id=?",
                      (b.resolution_code,b.note,now(),a["role"],a["user"],e["id"]))
        else:raise HTTPException(422,"Invalid action")
        audit(c,a,"MR_EXCEPTION_"+d,e["container_id"],None,dict(e),b.model_dump(),{"exception_ref":exception_ref});c.execute("COMMIT")
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
        ins=int(c.execute("SELECT COUNT(*) n FROM container_inspections").fetchone()["n"] or 0)
        dmg=int(c.execute("SELECT COUNT(*) n FROM container_damage_items").fetchone()["n"] or 0)
        reps=[dict(r) for r in c.execute("SELECT * FROM container_repair_orders")]
        closed=[r for r in reps if r["status"]=="CLOSED"];turn=[]
        for r in closed:
            a=parse_dt(r["actual_start"]);b=parse_dt(r["actual_complete"])
            if a and b and b>=a:turn.append((b-a).total_seconds()/86400)
        est=sum(float(r["total_estimate"] or 0) for r in c.execute("SELECT total_estimate FROM container_repair_estimates"))
        act=sum(float(r["actual_total_cost"] or 0) for r in reps)
        exc=int(c.execute("SELECT COUNT(*) n FROM container_mr_exceptions WHERE status<>'RESOLVED'").fetchone()["n"] or 0)
        depot=[dict(r) for r in c.execute("SELECT depot_code,COUNT(*) inspections FROM container_inspections GROUP BY depot_code ORDER BY inspections DESC")]
        return {"inspections":ins,"damage_items":dmg,"repair_orders":len(reps),"closed_repairs":len(closed),"avg_repair_turnaround_days":round(sum(turn)/len(turn),2) if turn else 0,
                "estimated_mr_cost":round(est,2),"actual_mr_cost":round(act,2),"estimate_variance":round(act-est,2),"damage_frequency_pct":round(dmg*100/ins,2) if ins else 0,
                "open_exceptions":exc,"depot_performance":depot}
    finally:c.close()
