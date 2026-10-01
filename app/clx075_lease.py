from __future__ import annotations
from fastapi import APIRouter,Header,HTTPException,Query
from pydantic import BaseModel,Field
from typing import Optional
import datetime,uuid,json
from .db import connect,tx
from .clx070_container_master_control import actor,require_action,get_container,audit,now
from .clx071_container_journey import parse_dt

router=APIRouter(prefix="/api/clx075/lease",tags=["CLX-075 Equipment Lease Commercial"])
APPROVE={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT","FINANCE"}

class ContractBody(BaseModel):
    provider_type:str
    provider_code:str
    contract_type:str="LEASE"
    currency:str="USD"
    start_date:str
    end_date:Optional[str]=None
    minimum_hire_days:int=Field(default=0,ge=0)
    free_days:int=Field(default=0,ge=0)
    per_diem_rate:float=Field(default=0,ge=0)
    handling_rate:float=Field(default=0,ge=0)
    depot_rate:float=Field(default=0,ge=0)
    lift_on_rate:float=Field(default=0,ge=0)
    lift_off_rate:float=Field(default=0,ge=0)
    drop_off_rate:float=Field(default=0,ge=0)
    pick_up_rate:float=Field(default=0,ge=0)
    damage_responsibility:Optional[str]=None
    repair_responsibility:Optional[str]=None
    redelivery_terms:Optional[str]=None
    offhire_inspection_required:bool=True

class DecisionBody(BaseModel):
    decision:str
    note:Optional[str]=None

class AllocationBody(BaseModel):
    container_no:str
    on_hire_date:str
    off_hire_due_date:Optional[str]=None
    on_hire_port:Optional[str]=None
    off_hire_port:Optional[str]=None

class ExtensionBody(BaseModel):
    extension_until:str
    reason:str

class OffhireBody(BaseModel):
    actual_off_hire_date:Optional[str]=None
    off_hire_port:Optional[str]=None
    inspection_passed:bool=False
    actual_amount:Optional[float]=Field(default=None,ge=0)

class SettlementBody(BaseModel):
    amount:float=Field(ge=0)
    note:Optional[str]=None

class ExceptionDecision(BaseModel):
    action:str
    resolution_code:Optional[str]=None
    note:Optional[str]=None

def ref(p): return p+"-"+uuid.uuid4().hex[:12].upper()
def utcnow(): return datetime.datetime.now(datetime.timezone.utc)

def days_between(a,b):
    x=parse_dt(a);y=parse_dt(b)
    if not x or not y:return 0
    return max(0,int((y-x).total_seconds()//86400)+1)

def contract(c,contract_ref):
    r=c.execute("SELECT * FROM equipment_lease_contracts WHERE contract_ref=?",(contract_ref,)).fetchone()
    if not r:raise HTTPException(404,"Unknown lease contract")
    return r

def allocation(c,a,allocation_ref):
    r=c.execute("""SELECT la.*,lc.contract_ref,lc.provider_type,lc.provider_code,lc.currency,lc.minimum_hire_days,lc.free_days,lc.per_diem_rate,
                   lc.handling_rate,lc.depot_rate,lc.lift_on_rate,lc.lift_off_rate,lc.drop_off_rate,lc.pick_up_rate,lc.offhire_inspection_required,
                   co.container_no,co.job_id,co.booking_ref
                   FROM equipment_lease_allocations la JOIN equipment_lease_contracts lc ON lc.id=la.contract_id
                   JOIN containers co ON co.id=la.container_id WHERE la.allocation_ref=?""",(allocation_ref,)).fetchone()
    if not r:raise HTTPException(404,"Unknown lease allocation")
    get_container(c,r["container_no"],a)
    return r

def post_cost(c,a,r,amount,code,source_ref):
    if amount<=0:return None
    er=ref("CFL")
    c.execute("""INSERT INTO container_financial_ledger(entry_ref,container_id,job_id,booking_ref,bl_ref,movement_event_id,entry_type,charge_code,
                 party_type,party_code,amount,currency,quantity,rate,basis,source_type,source_ref,status,created_by,created_at)
                 VALUES(?,?,?,?,NULL,NULL,'COST',?,?,?,?,?,1,0,'LEASE','LEASE',?,'ACCRUED',?,?)""",
              (er,r["container_id"],r["job_id"],r["booking_ref"],code,r["provider_type"],r["provider_code"],amount,r["currency"],source_ref,a["user"],now()))
    return er

def accrued(r,asof=None):
    end=asof or now()
    d=days_between(r["on_hire_date"],end)
    bill=max(int(r["minimum_hire_days"] or 0),max(0,d-int(r["free_days"] or 0)))
    recurring=bill*float(r["per_diem_rate"] or 0)
    fixed=sum(float(r[k] or 0) for k in ["handling_rate","depot_rate","lift_on_rate","pick_up_rate"])
    return round(recurring+fixed,2),bill,d

@router.get("/contracts")
def contracts(status:Optional[str]=None,limit:int=Query(200,ge=1,le=1000)):
    c=connect()
    try:
        q="SELECT * FROM equipment_lease_contracts WHERE 1=1";args=[]
        if status:q+=" AND status=?";args.append(status)
        q+=" ORDER BY id DESC LIMIT ?";args.append(limit)
        rows=[dict(r) for r in c.execute(q,args)]
        return {"count":len(rows),"records":rows}
    finally:c.close()

@router.post("/contracts")
def create_contract(b:ContractBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        if a["role"] not in APPROVE:raise HTTPException(403,{"code":"LEASE_COMMERCIAL_ROLE_REQUIRED"})
        tx(c);cr=ref("LCT")
        c.execute("""INSERT INTO equipment_lease_contracts(contract_ref,provider_type,provider_code,contract_type,currency,start_date,end_date,minimum_hire_days,
                    free_days,per_diem_rate,handling_rate,depot_rate,lift_on_rate,lift_off_rate,drop_off_rate,pick_up_rate,damage_responsibility,
                    repair_responsibility,redelivery_terms,offhire_inspection_required,status,maker_role,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?)""",
                 (cr,b.provider_type.upper(),b.provider_code,b.contract_type.upper(),b.currency,b.start_date,b.end_date,b.minimum_hire_days,b.free_days,
                  b.per_diem_rate,b.handling_rate,b.depot_rate,b.lift_on_rate,b.lift_off_rate,b.drop_off_rate,b.pick_up_rate,b.damage_responsibility,
                  b.repair_responsibility,b.redelivery_terms,b.offhire_inspection_required,a["role"],now(),now()))
        audit(c,a,"LEASE_CONTRACT_CREATE",None,None,{},b.model_dump(),{"contract_ref":cr});c.execute("COMMIT")
        return {"ok":True,"contract_ref":cr,"status":"DRAFT"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/contracts/{contract_ref}/decision")
def decide(contract_ref:str,b:DecisionBody,x_role:str=Header("FINANCE"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        if a["role"] not in APPROVE:raise HTTPException(403,{"code":"LEASE_APPROVAL_ROLE_REQUIRED"})
        tx(c);r=contract(c,contract_ref);d=b.decision.upper()
        if d not in {"SUBMIT","APPROVE","REJECT","CANCEL"}:raise HTTPException(422,"Invalid decision")
        if d=="APPROVE" and r["maker_role"]==a["role"]:raise HTTPException(409,{"code":"MAKER_CHECKER_CONFLICT"})
        st={"SUBMIT":"SUBMITTED","APPROVE":"APPROVED","REJECT":"REJECTED","CANCEL":"CANCELLED"}[d]
        c.execute("UPDATE equipment_lease_contracts SET status=?,checker_role=?,approved_at=CASE WHEN ?='APPROVED' THEN ? ELSE approved_at END,updated_at=? WHERE id=?",
                  (st,a["role"],st,now(),now(),r["id"]))
        audit(c,a,"LEASE_CONTRACT_"+d,None,None,dict(r),{"status":st},{"contract_ref":contract_ref,"note":b.note});c.execute("COMMIT")
        return {"ok":True,"contract_ref":contract_ref,"status":st}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/contracts/{contract_ref}/on-hire")
def on_hire(contract_ref:str,b:AllocationBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        lc=contract(c,contract_ref)
        if lc["status"]!="APPROVED":raise HTTPException(409,{"code":"LEASE_CONTRACT_NOT_APPROVED"})
        co=get_container(c,b.container_no,a);ar=ref("LAL")
        c.execute("""INSERT INTO equipment_lease_allocations(allocation_ref,contract_id,container_id,on_hire_date,off_hire_due_date,on_hire_port,off_hire_port,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,'ON_HIRE',?,?)""",(ar,lc["id"],co["id"],b.on_hire_date,b.off_hire_due_date,b.on_hire_port,b.off_hire_port,now(),now()))
        c.execute("""UPDATE containers SET ownership='LEASED',owner_party_type='LEASING_COMPANY',owner_party_code=?,lease_contract_ref=?,supplier_or_lessor=?,
                    equipment_status='AVAILABLE',journey_state='AVAILABLE',updated_at=? WHERE id=?""",(lc["provider_code"],contract_ref,lc["provider_code"],now(),co["id"]))
        audit(c,a,"LEASE_ON_HIRE",co["id"],co["job_id"],dict(co),{"allocation_ref":ar},{"contract_ref":contract_ref});c.execute("COMMIT")
        return {"ok":True,"allocation_ref":ar,"container_no":b.container_no,"status":"ON_HIRE"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/allocations/{allocation_ref}/accrue")
def accrue(allocation_ref:str,as_of:Optional[str]=None,x_role:str=Header("FINANCE"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"finance");tx(c);r=allocation(c,a,allocation_ref)
        amount,bill_days,elapsed=accrued(r,as_of)
        prior=float(r["accrued_amount"] or 0);delta=max(0,round(amount-prior,2));entry=post_cost(c,a,r,delta,"LEASE_ACCRUAL",allocation_ref)
        c.execute("UPDATE equipment_lease_allocations SET accrued_amount=?,updated_at=? WHERE id=?",(amount,now(),r["id"]))
        audit(c,a,"LEASE_ACCRUE",r["container_id"],r["job_id"],{"accrued":prior},{"accrued":amount},{"entry_ref":entry});c.execute("COMMIT")
        return {"ok":True,"allocation_ref":allocation_ref,"elapsed_days":elapsed,"billable_days":bill_days,"accrued_amount":amount,"posted_delta":delta,"entry_ref":entry}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/allocations/{allocation_ref}/extend")
def extend(allocation_ref:str,b:ExtensionBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        if a["role"] not in APPROVE:raise HTTPException(403,{"code":"LEASE_EXTENSION_ROLE_REQUIRED"})
        tx(c);r=allocation(c,a,allocation_ref)
        c.execute("UPDATE equipment_lease_allocations SET extension_until=?,off_hire_due_date=?,updated_at=? WHERE id=?",(b.extension_until,b.extension_until,now(),r["id"]))
        audit(c,a,"LEASE_EXTEND",r["container_id"],r["job_id"],dict(r),{"extension_until":b.extension_until},{"reason":b.reason});c.execute("COMMIT")
        return {"ok":True,"allocation_ref":allocation_ref,"off_hire_due_date":b.extension_until}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/allocations/{allocation_ref}/off-hire")
def off_hire(allocation_ref:str,b:OffhireBody,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c);r=allocation(c,a,allocation_ref)
        if r["offhire_inspection_required"] and not b.inspection_passed:raise HTTPException(409,{"code":"OFFHIRE_INSPECTION_REQUIRED"})
        dt=b.actual_off_hire_date or now();expected,_,_=accrued(r,dt);actual=b.actual_amount if b.actual_amount is not None else expected
        end_cost=float(r["lift_off_rate"] or 0)+float(r["drop_off_rate"] or 0)
        if end_cost:post_cost(c,a,r,end_cost,"LEASE_OFFHIRE_CHARGES",allocation_ref)
        c.execute("""UPDATE equipment_lease_allocations SET actual_off_hire_date=?,off_hire_port=COALESCE(?,off_hire_port),status='OFF_HIRED',
                    accrued_amount=?,updated_at=? WHERE id=?""",(dt,b.off_hire_port,actual,now(),r["id"]))
        c.execute("UPDATE containers SET equipment_status='INSPECTION',journey_state='INSPECTION',inspection_hold=true,updated_at=? WHERE id=?",(now(),r["container_id"]))
        audit(c,a,"LEASE_OFF_HIRE",r["container_id"],r["job_id"],dict(r),{"actual_off_hire_date":dt,"actual_amount":actual},{"allocation_ref":allocation_ref});c.execute("COMMIT")
        return {"ok":True,"allocation_ref":allocation_ref,"status":"OFF_HIRED","actual_amount":actual}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/allocations/{allocation_ref}/settle")
def settle(allocation_ref:str,b:SettlementBody,x_role:str=Header("FINANCE"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"finance");tx(c);r=allocation(c,a,allocation_ref)
        new=round(float(r["settled_amount"] or 0)+b.amount,2);st="SETTLED" if new>=float(r["accrued_amount"] or 0) else "PARTIAL"
        c.execute("UPDATE equipment_lease_allocations SET settled_amount=?,settlement_status=?,updated_at=? WHERE id=?",(new,st,now(),r["id"]))
        audit(c,a,"LEASE_SETTLEMENT",r["container_id"],r["job_id"],{"settled":r["settled_amount"]},{"settled":new,"status":st},{"note":b.note});c.execute("COMMIT")
        return {"ok":True,"allocation_ref":allocation_ref,"settled_amount":new,"settlement_status":st}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/scan-exceptions")
def scan(x_role:str=Header("EQUIPMENT_MANAGER")):
    c=connect()
    try:
        a=actor(c,None,x_role,None,None,None);require_action(a,"write");tx(c);created=[]
        rows=c.execute("""SELECT la.*,lc.end_date,co.container_no FROM equipment_lease_allocations la JOIN equipment_lease_contracts lc ON lc.id=la.contract_id
                          JOIN containers co ON co.id=la.container_id WHERE la.status='ON_HIRE'""").fetchall()
        t=utcnow()
        for r in rows:
            for typ,due in [("OFFHIRE_OVERDUE",r["off_hire_due_date"]),("CONTRACT_EXPIRED",r["end_date"])]:
                d=parse_dt(due)
                if d and t>d:
                    ex=c.execute("SELECT exception_ref FROM equipment_lease_exceptions WHERE allocation_id=? AND exception_type=? AND status<>'RESOLVED'",(r["id"],typ)).fetchone()
                    if not ex:
                        er=ref("LEX");c.execute("""INSERT INTO equipment_lease_exceptions(exception_ref,allocation_id,container_id,exception_type,severity,status,detail,due_date,created_at)
                          VALUES(?,?,?,?, 'HIGH','OPEN',?,?,?)""",(er,r["id"],r["container_id"],typ,f"{typ} for {r['container_no']}",due,now()));created.append(er)
        c.execute("COMMIT");return {"ok":True,"count":len(created),"created":created}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.get("/exceptions")
def exceptions(status:Optional[str]="OPEN",limit:int=Query(300,ge=1,le=1000),x_role:str=Header("VIEWER"),
 x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),
 x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        q="""SELECT e.*,co.container_no FROM equipment_lease_exceptions e LEFT JOIN containers co ON co.id=e.container_id WHERE 1=1""";args=[]
        if status:q+=" AND e.status=?";args.append(status)
        q+=" ORDER BY CASE e.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END,e.id DESC LIMIT ?";args.append(limit)
        out=[]
        for r in c.execute(q,args):
            if r["container_no"]:
                try:get_container(c,r["container_no"],a)
                except HTTPException:continue
            out.append(dict(r))
        return {"actor":a,"count":len(out),"records":out}
    finally:c.close()

@router.post("/exceptions/{exception_ref}/decision")
def exception_decision(exception_ref:str,b:ExceptionDecision,x_role:str=Header("EQUIPMENT_MANAGER"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
 x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c)
        e=c.execute("""SELECT e.*,co.container_no FROM equipment_lease_exceptions e LEFT JOIN containers co ON co.id=e.container_id WHERE e.exception_ref=?""",(exception_ref,)).fetchone()
        if not e:raise HTTPException(404,"Unknown lease exception")
        if e["container_no"]:get_container(c,e["container_no"],a)
        d=b.action.upper()
        if d=="ACKNOWLEDGE":
            c.execute("UPDATE equipment_lease_exceptions SET status='ACKNOWLEDGED',acknowledged_at=?,owner_role=?,owner_ref=? WHERE id=?",
                      (now(),a["role"],a["user"],e["id"]))
        elif d=="RESOLVE":
            if not b.resolution_code:raise HTTPException(422,{"code":"RESOLUTION_CODE_REQUIRED"})
            c.execute("""UPDATE equipment_lease_exceptions SET status='RESOLVED',resolution_code=?,resolution_note=?,resolved_at=?,
                         owner_role=COALESCE(owner_role,?),owner_ref=COALESCE(owner_ref,?) WHERE id=?""",
                      (b.resolution_code,b.note,now(),a["role"],a["user"],e["id"]))
        else:raise HTTPException(422,"Invalid action")
        audit(c,a,"LEASE_EXCEPTION_"+d,e["container_id"],None,dict(e),b.model_dump(),{"exception_ref":exception_ref});c.execute("COMMIT")
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
        contracts=int(c.execute("SELECT COUNT(*) n FROM equipment_lease_contracts WHERE status='APPROVED'").fetchone()["n"] or 0)
        active=int(c.execute("SELECT COUNT(*) n FROM equipment_lease_allocations WHERE status='ON_HIRE'").fetchone()["n"] or 0)
        acc=float(c.execute("SELECT COALESCE(SUM(accrued_amount),0) n FROM equipment_lease_allocations").fetchone()["n"] or 0)
        settled=float(c.execute("SELECT COALESCE(SUM(settled_amount),0) n FROM equipment_lease_allocations").fetchone()["n"] or 0)
        overdue=int(c.execute("SELECT COUNT(*) n FROM equipment_lease_exceptions WHERE status<>'RESOLVED'").fetchone()["n"] or 0)
        return {"approved_contracts":contracts,"active_on_hire":active,"accrued":round(acc,2),"settled":round(settled,2),"outstanding":round(acc-settled,2),"open_exceptions":overdue}
    finally:c.close()
