from __future__ import annotations
from fastapi import APIRouter,Header,HTTPException
from pydantic import BaseModel,Field
from typing import Optional
import json,uuid
from .db import connect,tx
from .clx070_container_master_control import actor,require_action,get_container,audit,now

router=APIRouter(prefix="/api/clx074/mr",tags=["CLX-074 Depot M&R"])
APPROVE={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT","FINANCE"}

class DecisionBody(BaseModel):
    decision:str
    note:Optional[str]=None
class RepairBody(BaseModel):
    planned_start:Optional[str]=None
    planned_complete:Optional[str]=None
class ProgressBody(BaseModel):
    actual_time:Optional[str]=None
    labour_cost:float=Field(default=0,ge=0)
    material_cost:float=Field(default=0,ge=0)
    third_party_cost:float=Field(default=0,ge=0)
    cleaning_cost:float=Field(default=0,ge=0)
class ReinspectBody(BaseModel):
    result:str
    condition_after:Optional[str]=None
    csc_result:Optional[str]=None
    safety_result:Optional[str]=None
class LiabilityBody(BaseModel):
    party_type:str
    party_code:str
    status:str="ALLOCATED"
    charge_amount:float=Field(default=0,ge=0)
    currency:str="USD"
    charge_code:str="DAMAGE_MR"

def ref(p):return p+"-"+uuid.uuid4().hex[:12].upper()

@router.post("/estimates/{estimate_ref}/decision")
def decide(estimate_ref:str,b:DecisionBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        if a["role"] not in APPROVE:raise HTTPException(403,{"code":"MR_APPROVAL_ROLE_REQUIRED"})
        tx(c);e=c.execute("SELECT e.*,i.container_id,c.container_no FROM container_repair_estimates e JOIN container_inspections i ON i.id=e.inspection_id JOIN containers c ON c.id=i.container_id WHERE e.estimate_ref=?",(estimate_ref,)).fetchone()
        if not e:raise HTTPException(404,"Unknown estimate")
        get_container(c,e["container_no"],a);d=b.decision.upper()
        if d not in {"APPROVE","REJECT"}:raise HTTPException(422,"Invalid decision")
        if d=="APPROVE" and e["maker_role"]==a["role"]:raise HTTPException(409,{"code":"MAKER_CHECKER_CONFLICT"})
        st="APPROVED" if d=="APPROVE" else "REJECTED"
        c.execute("UPDATE container_repair_estimates SET status=?,checker_role=?,decision_note=?,approved_at=CASE WHEN ?='APPROVED' THEN ? ELSE approved_at END,updated_at=? WHERE id=?",(st,a["role"],b.note,st,now(),now(),e["id"]))
        audit(c,a,"MR_ESTIMATE_"+d,e["container_id"],None,dict(e),{"status":st},{"estimate_ref":estimate_ref});c.execute("COMMIT")
        return {"ok":True,"estimate_ref":estimate_ref,"status":st}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/estimates/{estimate_ref}/repair-order")
def repair_order(estimate_ref:str,b:RepairBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        e=c.execute("SELECT e.*,i.container_id,i.depot_code,c.container_no,c.size_type,c.booking_ref FROM container_repair_estimates e JOIN container_inspections i ON i.id=e.inspection_id JOIN containers c ON c.id=i.container_id WHERE e.estimate_ref=?",(estimate_ref,)).fetchone()
        if not e:raise HTTPException(404,"Unknown estimate")
        get_container(c,e["container_no"],a)
        if e["status"]!="APPROVED":raise HTTPException(409,{"code":"REPAIR_REQUIRES_APPROVED_ESTIMATE"})
        existing=c.execute("SELECT * FROM container_repair_orders WHERE estimate_id=? AND status<>'CANCELLED' ORDER BY id DESC LIMIT 1",(e["id"],)).fetchone()
        if existing:
            c.execute("COMMIT");return {"ok":True,"work_ref":existing["work_ref"],"equipment_work_ref":existing["equipment_work_ref"],"existing":True}
        wr=ref("MRW");eq=ref("EQ-REP");payload=json.dumps({"clx074_repair_work_ref":wr,"estimate_ref":estimate_ref},sort_keys=True)
        c.execute("""INSERT INTO equipment_work_items(work_ref,work_type,status,booking_ref,job_ref,size_type,qty,source_port,destination_port,source_agent,destination_agent,source_depot,destination_depot,estimated_cost,currency,required_by,payload_json,maker_role,created_at,updated_at)
                     VALUES(?,'REPAIR','DRAFT',?,NULL,?,1,NULL,NULL,NULL,NULL,?,NULL,?,?,?,?,?,?,?)""",
                  (eq,e["booking_ref"],e["size_type"],e["depot_code"],e["total_estimate"],e["currency"],b.planned_complete,payload,a["role"],now(),now()))
        c.execute("INSERT INTO container_repair_orders(work_ref,inspection_id,estimate_id,container_id,status,planned_start,planned_complete,equipment_work_ref,created_at,updated_at) VALUES(?,?,?,?,'APPROVED',?,?,?,?,?)",
                  (wr,e["inspection_id"],e["id"],e["container_id"],b.planned_start,b.planned_complete,eq,now(),now()))
        c.execute("UPDATE containers SET equipment_status='REPAIR',journey_state='REPAIR',damage_hold=true,inspection_hold=true,updated_at=? WHERE id=?",(now(),e["container_id"]))
        c.execute("COMMIT");return {"ok":True,"work_ref":wr,"equipment_work_ref":eq,"status":"APPROVED"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/repair-orders/{work_ref}/complete")
def complete(work_ref:str,b:ProgressBody,x_role:str=Header("DEPOT"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        r=c.execute("SELECT ro.*,c.container_no FROM container_repair_orders ro JOIN containers c ON c.id=ro.container_id WHERE ro.work_ref=?",(work_ref,)).fetchone()
        if not r:raise HTTPException(404,"Unknown repair order")
        co=get_container(c,r["container_no"],a);total=round(b.labour_cost+b.material_cost+b.third_party_cost+b.cleaning_cost,2)
        c.execute("UPDATE container_repair_orders SET status='COMPLETED',actual_complete=?,actual_labour_cost=?,actual_material_cost=?,actual_third_party_cost=?,actual_cleaning_cost=?,actual_total_cost=?,updated_at=? WHERE id=?",(b.actual_time or now(),b.labour_cost,b.material_cost,b.third_party_cost,b.cleaning_cost,total,now(),r["id"]))
        c.execute("""INSERT INTO container_financial_ledger(entry_ref,container_id,job_id,booking_ref,bl_ref,movement_event_id,entry_type,charge_code,party_type,party_code,amount,currency,quantity,rate,basis,source_type,source_ref,status,created_by,created_at)
                     VALUES(?,?,?,?,NULL,NULL,'COST','M&R_REPAIR','DEPOT',?,?,'USD',1,0,'ACTUAL_REPAIR','M&R',?,'ACCRUED',?,?)""",
                  (ref("CFL"),co["id"],co["job_id"],co["booking_ref"],co["depot_code"],total,work_ref,a["user"],now()))
        c.execute("UPDATE containers SET equipment_status='INSPECTION',journey_state='INSPECTION',inspection_hold=true,damage_hold=true,updated_at=? WHERE id=?",(now(),co["id"]))
        c.execute("COMMIT");return {"ok":True,"work_ref":work_ref,"status":"COMPLETED","actual_total_cost":total}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/repair-orders/{work_ref}/reinspect")
def reinspect(work_ref:str,b:ReinspectBody,x_role:str=Header("DEPOT"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        r=c.execute("SELECT ro.*,c.container_no FROM container_repair_orders ro JOIN containers c ON c.id=ro.container_id WHERE ro.work_ref=?",(work_ref,)).fetchone()
        if not r:raise HTTPException(404,"Unknown repair order")
        get_container(c,r["container_no"],a);res=b.result.upper()
        if res not in {"PASS","FAIL"}:raise HTTPException(422,"PASS or FAIL required")
        c.execute("UPDATE container_repair_orders SET reinspected_at=?,reinspect_result=?,status=?,updated_at=? WHERE id=?",(now(),res,"CLOSED" if res=="PASS" else "REINSPECTION_FAILED",now(),r["id"]))
        if res=="PASS":
            c.execute("UPDATE containers SET condition=COALESCE(?,condition),damage_hold=false,inspection_hold=false,equipment_status='AVAILABLE',journey_state='AVAILABLE',verification_status='VERIFIED',updated_at=? WHERE id=?",(b.condition_after or "GOOD",now(),r["container_id"]))
            c.execute("UPDATE container_inspections SET status='COMPLETED',condition_after=?,csc_result=COALESCE(?,csc_result),safety_result=COALESCE(?,safety_result),completed_at=?,updated_at=? WHERE id=?",(b.condition_after,b.csc_result,b.safety_result,now(),now(),r["inspection_id"]))
        else:
            c.execute("UPDATE containers SET damage_hold=true,inspection_hold=true,equipment_status='HOLD',journey_state='HOLD',updated_at=? WHERE id=?",(now(),r["container_id"]))
            c.execute("INSERT INTO container_mr_exceptions(exception_ref,container_id,inspection_id,repair_work_ref,exception_type,severity,status,detail,created_at) VALUES(?,?,?,?, 'REINSPECTION_FAILURE','HIGH','OPEN','Post-repair reinspection failed',?)",(ref("MRX"),r["container_id"],r["inspection_id"],work_ref,now()))
        c.execute("COMMIT");return {"ok":True,"work_ref":work_ref,"result":res,"container_status":"AVAILABLE" if res=="PASS" else "HOLD"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/damage/{damage_ref}/liability")
def liability(damage_ref:str,b:LiabilityBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        d=c.execute("SELECT d.*,i.container_id,c.container_no,c.job_id,c.booking_ref FROM container_damage_items d JOIN container_inspections i ON i.id=d.inspection_id JOIN containers c ON c.id=i.container_id WHERE d.damage_ref=?",(damage_ref,)).fetchone()
        if not d:raise HTTPException(404,"Unknown damage")
        get_container(c,d["container_no"],a)
        c.execute("UPDATE container_damage_items SET responsibility_party_type=?,responsibility_party_code=?,liability_status=? WHERE id=?",(b.party_type,b.party_code,b.status.upper(),d["id"]))
        entry=None
        if b.charge_amount:
            entry=ref("CFL")
            c.execute("""INSERT INTO container_financial_ledger(entry_ref,container_id,job_id,booking_ref,bl_ref,movement_event_id,entry_type,charge_code,party_type,party_code,amount,currency,quantity,rate,basis,source_type,source_ref,status,created_by,created_at)
                         VALUES(?,?,?,?,NULL,NULL,'REVENUE',?,?,?,?,?,1,0,'DAMAGE_LIABILITY','M&R_LIABILITY',?,'ACCRUED',?,?)""",
                      (entry,d["container_id"],d["job_id"],d["booking_ref"],b.charge_code,b.party_type,b.party_code,b.charge_amount,b.currency,damage_ref,a["user"],now()))
        c.execute("COMMIT");return {"ok":True,"damage_ref":damage_ref,"liability_status":b.status.upper(),"financial_entry_ref":entry}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()
