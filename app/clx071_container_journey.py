from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Optional
import datetime, json, uuid

from .db import connect, tx
from .clx070_container_master_control import actor, require_action, get_container, audit, scope_clause, now
from .mrg import ensure as ensure_mrg

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
    governance_mode:str="STANDARD"
    governance_reason:Optional[str]=None
    governance_evidence_refs:list[str]=Field(default_factory=list)
    governance_approver_user_ids:list[str]=Field(default_factory=list)
    correction_source_event_ref:Optional[str]=None
    correction_original_transition_at:Optional[str]=None
    financial_posted:bool=False
    actor_type:str="HUMAN"
    note:Optional[str]=None

class DetentionCalculationBody(BaseModel):
    calculate_till:Optional[str]=None
    requested_stage:str="AUTO"

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


def _detention_payload(row):
    try:return json.loads(row["payload_json"] or "{}")
    except Exception:return {}

def _detention_party_code(rule,ctx):
    pt=str(rule.get("party_type") or "").upper()
    return {
      "CUSTOMER":ctx.get("customer_code"),
      "AGENT":ctx.get("agent_code"),
      "PRINCIPAL":ctx.get("principal_code") or ctx.get("owner_party_code"),
      "DEPOT":ctx.get("depot_code"),
    }.get(pt)

def _detention_rule_matches(rule,ctx,active_on):
    if str(rule.get("status") or "").upper()!="APPROVED":return False
    if str(rule.get("rate_side") or "").upper()!="REVENUE":return False
    if str(rule.get("mrg_type") or "").upper() not in {"DETENTION","DETENTION_EXPORT","SPECIAL_DETENTION"}:return False
    if rule.get("effective_from") and str(rule["effective_from"])>active_on:return False
    if rule.get("effective_to") and str(rule["effective_to"])<active_on:return False
    for rk,ck in (("job_ref","job_ref"),("booking_ref","booking_ref"),("pol","pol"),("pod","pod"),
                  ("depot_code","depot_code"),("office_code","office_code"),("branch_code","branch_code"),
                  ("organization_code","organization_code"),("size_type","size_type"),("container_type","container_type")):
        rv=rule.get(rk)
        if rv not in (None,"") and str(rv).upper()!=str(ctx.get(ck) or "").upper():return False
    pc=rule.get("party_code")
    if pc not in (None,""):
        actual=_detention_party_code(rule,ctx)
        if not actual or str(pc).upper()!=str(actual).upper():return False
    mt=str(rule.get("mrg_type") or "").upper()
    if mt=="SPECIAL_DETENTION" and not (
      str(rule.get("job_ref") or "")==str(ctx.get("job_ref") or "") or
      str(rule.get("booking_ref") or "")==str(ctx.get("booking_ref") or "")
    ):return False
    if mt=="DETENTION_EXPORT" and str(ctx.get("direction") or "").upper()!="EXPORT":return False
    return True

def _detention_rule_score(rule):
    mt=str(rule.get("mrg_type") or "").upper()
    score={"SPECIAL_DETENTION":10000,"DETENTION_EXPORT":6000,"DETENTION":5000}.get(mt,0)
    for k in ("job_ref","booking_ref","party_code","pol","pod","depot_code","office_code","branch_code","organization_code","size_type","container_type"):
        if rule.get(k) not in (None,""):score+=100
    score-=int(rule.get("priority") or 100)
    return score

def _detention_select_rule(conn,ctx,active_on):
    ensure_mrg(conn)
    rows=[dict(r) for r in conn.execute("""SELECT * FROM mrg_rules
      WHERE status='APPROVED' AND rate_side='REVENUE'
        AND mrg_type IN ('DETENTION','DETENTION_EXPORT','SPECIAL_DETENTION')
      ORDER BY priority,rule_ref""").fetchall()]
    matches=[r for r in rows if _detention_rule_matches(r,ctx,active_on)]
    if not matches:
        raise HTTPException(409,{"code":"NO_APPROVED_REVENUE_DETENTION_RULE","job_ref":ctx.get("job_ref"),
                                 "container":ctx.get("container_no"),"active_on":active_on})
    matches.sort(key=_detention_rule_score,reverse=True)
    rule=matches[0]
    rule["slabs"]=[dict(s) for s in conn.execute(
      "SELECT * FROM mrg_slabs WHERE rule_ref=? ORDER BY from_day,slab_ref",(rule["rule_ref"],)).fetchall()]
    return rule

def _detention_amount(rule,charge_days,size_type,container_type):
    if charge_days<=0:return 0.0,0.0
    if int(rule.get("slab_wise") or 0):
        total=0.0
        for day in range(1,charge_days+1):
            candidates=[]
            for s in rule.get("slabs") or []:
                if s.get("size_type") not in (None,"") and str(s["size_type"]).upper()!=str(size_type or "").upper():continue
                if s.get("container_type") not in (None,"") and str(s["container_type"]).upper()!=str(container_type or "").upper():continue
                lo=int(s.get("from_day") or 0); hi=s.get("till_day")
                if day<lo or (hi is not None and day>int(hi)):continue
                candidates.append(s)
            if not candidates:
                raise HTTPException(409,{"code":"MRG_DETENTION_SLAB_GAP","rule_ref":rule["rule_ref"],"day":day})
            candidates.sort(key=lambda s:(0 if s.get("size_type") else 1,0 if s.get("container_type") else 1,int(s.get("from_day") or 0)))
            total+=float(candidates[0].get("rate") or 0)
        avg=total/charge_days if charge_days else 0.0
        amount=total
        rate=avg
    else:
        basis=str(rule.get("rate_basis") or "PER_DAY").upper()
        unit=float(rule.get("unit_rate") or 0)
        if basis in {"PER_DAY","DAY","DAILY"}:
            amount=unit*charge_days;rate=unit
        elif basis in {"FLAT","FIXED"}:
            amount=unit;rate=unit
        else:
            raise HTTPException(409,{"code":"UNSUPPORTED_DETENTION_RATE_BASIS","rule_ref":rule["rule_ref"],"rate_basis":basis})
    if rule.get("minimum_rate") is not None:amount=max(amount,float(rule["minimum_rate"]))
    if rule.get("maximum_rate") is not None:amount=min(amount,float(rule["maximum_rate"]))
    return round(amount,2),round(rate,6)

def _detention_context(conn,record_id):
    txr=conn.execute("""SELECT t.*,j.job_ref,j.pol,j.pod,j.office_code,j.branch_code,j.organization_code,
      b.booking_ref,c.code customer_code,a.code agent_code
      FROM transaction_records t
      JOIN jobs j ON j.id=t.job_id JOIN bookings b ON b.id=t.booking_id
      JOIN customers c ON c.id=t.customer_id JOIN agents a ON a.id=t.agent_id
      WHERE t.id=? AND t.module='detention-collection'""",(record_id,)).fetchone()
    if not txr:raise HTTPException(404,{"code":"DETENTION_RECORD_NOT_FOUND"})
    d=dict(txr);payload=_detention_payload(txr)
    container=None
    if d.get("container_id"):
        container=conn.execute("SELECT * FROM containers WHERE id=?",(d["container_id"],)).fetchone()
    if not container:
        no=payload.get("Container") or payload.get("Container No") or payload.get("Container #")
        if no:container=conn.execute("SELECT * FROM containers WHERE container_no=?",(no,)).fetchone()
    if not container:raise HTTPException(409,{"code":"DETENTION_CONTAINER_REQUIRED","record_id":record_id})
    c=dict(container)
    direction=str(payload.get("Direction") or payload.get("Import / Export") or payload.get("Movement Type") or "").upper()
    ctx={
      "transaction_id":d["id"],"job_id":d["job_id"],"job_ref":d["job_ref"],"booking_ref":d["booking_ref"],
      "pol":d["pol"],"pod":d["pod"],"office_code":d.get("office_code"),"branch_code":d.get("branch_code"),
      "organization_code":d.get("organization_code"),"customer_code":d.get("customer_code"),
      "agent_code":d.get("agent_code"),"container_id":c["id"],"container_no":c.get("container_no"),
      "size_type":c.get("size_type"),"container_type":payload.get("Container Category"),
      "depot_code":c.get("depot_code"),"owner_party_code":c.get("owner_party_code"),
      "principal_code":c.get("principal_code"),"custody_agent_code":c.get("agent_code"),
      "payload":payload,"direction":direction
    }
    return txr,ctx


def _detention_num(v):
    try:return float(v or 0)
    except Exception:return 0.0

def _detention_finance_summary(conn,detention_ref,job_id):
    invoices=[dict(r) for r in conn.execute("""SELECT DISTINCT g.*
      FROM gl_records g
      WHERE g.job_id=? AND g.module='invoice'
        AND UPPER(COALESCE(g.status,'')) NOT IN ('CANCELLED','CANCELED','REVERSED','VOID')
        AND (
          (UPPER(COALESCE(g.source_type,''))='DETENTION_COLLECTION' AND g.source_ref=?)
          OR EXISTS(
            SELECT 1 FROM gl_source_links l
            WHERE l.gl_record_id=g.id AND UPPER(l.source_type)='DETENTION_COLLECTION' AND l.source_ref=?
          )
        )
      ORDER BY g.id""",(job_id,detention_ref,detention_ref)).fetchall()]
    committed_status={"APPROVED","POSTED","ISSUED","OPEN","PARTIALLY PAID","PARTIALLY_PAID","PAID","SETTLED"}
    invoice_refs=[];total_invoiced=0.0;advance_invoiced=0.0;final_invoiced=0.0;draft_amount=0.0
    due_dates=[];currencies=set()
    for inv in invoices:
        p=_detention_payload(inv);ref=inv["external_ref"];invoice_refs.append(ref)
        amount=_detention_num(p.get("Invoice Amount") or p.get("Amount") or p.get("Net Amount"))
        curr=str(p.get("Currency") or "").strip()
        if curr:currencies.add(curr)
        st=str(inv.get("status") or p.get("Status") or "").upper()
        stage=str(p.get("Detention Stage") or p.get("Calculation Stage") or "").upper()
        if p.get("Due Date"):due_dates.append(str(p["Due Date"]))
        if st=="DRAFT":
            draft_amount+=amount
            continue
        if st not in committed_status:
            continue
        total_invoiced+=amount
        if stage in {"ACTUAL","FINAL","ADDITIONAL","FINAL_ADDITIONAL"}:final_invoiced+=amount
        else:advance_invoiced+=amount

    credit_amount=0.0
    if invoice_refs:
        marks=",".join("?" for _ in invoice_refs)
        credits=[dict(r) for r in conn.execute(f"""SELECT * FROM gl_records
          WHERE module='credit-note' AND source_type='invoice' AND source_ref IN ({marks})
            AND UPPER(COALESCE(status,'')) NOT IN ('DRAFT','REJECTED','CANCELLED','CANCELED','REVERSED','VOID')""",invoice_refs).fetchall()]
        credit_amount=sum(_detention_num(_detention_payload(x).get("Amount")) for x in credits)

        allocs=[dict(r) for r in conn.execute(f"""SELECT a.*,t.status,t.updated_at,t.payload_json
          FROM treasury_allocations a JOIN treasury_records t ON t.id=a.treasury_record_id
          WHERE UPPER(a.source_type)='INVOICE' AND a.source_ref IN ({marks})
            AND UPPER(COALESCE(t.status,'')) NOT IN ('REVERSED','CANCELLED','CANCELED','VOID')""",invoice_refs).fetchall()]
    else:
        allocs=[]
    paid_amount=round(sum(_detention_num(x.get("allocated_amount")) for x in allocs),2)
    net_invoiced=round(max(0.0,total_invoiced-credit_amount),2)
    outstanding=round(max(0.0,net_invoiced-paid_amount),2)
    last_payment=max((str(x.get("updated_at") or "") for x in allocs),default="") or None
    today=utcnow().date().isoformat()
    overdue=bool(outstanding>0 and any(d and d<today for d in due_dates))
    if net_invoiced<=0:collection_status="NOT INVOICED"
    elif outstanding<=0:collection_status="PAID"
    elif paid_amount>0:collection_status="PARTIALLY PAID"
    elif overdue:collection_status="OVERDUE"
    else:collection_status="INVOICED"
    return {
      "invoice_refs":invoice_refs,"currency":next(iter(currencies),None),
      "draft_invoice_amount":round(draft_amount,2),"advance_invoiced":round(advance_invoiced,2),
      "final_invoiced":round(final_invoiced,2),"total_invoiced":round(total_invoiced,2),
      "credit_adjustment":round(credit_amount,2),"net_invoiced":net_invoiced,
      "paid_amount":paid_amount,"outstanding_balance":outstanding,
      "last_payment_date":last_payment,"collection_status":collection_status
    }

def _detention_history(payload):
    h=payload.get("Advance History")
    return list(h) if isinstance(h,list) else []

def _detention_history_state(history):
    valid=[x for x in history if isinstance(x,dict)]
    if not valid:return {"total_days":0,"cumulative_amount":0.0,"covered_till":None}
    total_days=max(int(_detention_num(x.get("cumulative_days"))) for x in valid)
    cumulative_amount=max(_detention_num(x.get("cumulative_amount")) for x in valid)
    parsed=[(parse_dt(x.get("covered_till")),x.get("covered_till")) for x in valid if x.get("covered_till")]
    parsed=[x for x in parsed if x[0]]
    covered=max(parsed,key=lambda x:x[0])[1] if parsed else None
    return {"total_days":total_days,"cumulative_amount":round(cumulative_amount,2),"covered_till":covered}


def _detention_event_time(conn,container_id,event_type):
    r=conn.execute("""SELECT COALESCE(actual_time,event_time) t FROM container_events
      WHERE container_id=? AND event_type=? AND COALESCE(actual_time,event_time) IS NOT NULL
      ORDER BY COALESCE(actual_time,event_time) DESC,id DESC LIMIT 1""",(container_id,event_type)).fetchone()
    return r["t"] if r else None

@router.get("/detention/{record_id}/summary")
def detention_summary(record_id:int,x_role:str=Header("VIEWER"),
                      x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                      x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),
                      x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                      x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    conn=connect()
    try:
        a=actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        txr,ctx=_detention_context(conn,record_id)
        c=get_container(conn,ctx["container_no"],a)
        if int(c["id"])!=int(ctx["container_id"]):raise HTTPException(409,{"code":"DETENTION_CONTAINER_SCOPE_MISMATCH"})
        payload=_detention_payload(txr);history=_detention_history(payload);hs=_detention_history_state(history)
        finance=_detention_finance_summary(conn,txr["external_ref"],ctx["job_id"])
        empty_return=_detention_event_time(conn,c["id"],"EMPTY_RETURN")
        process_status=str(payload.get("Process Status") or ("COMPLETE" if empty_return and str(payload.get("Calculation Stage") or "").upper()=="ACTUAL" else "RUNNING")).upper()
        stage=str(payload.get("Calculation Stage") or ("ACTUAL_PENDING" if empty_return else ("ONGOING" if history else "ADVANCE"))).upper()
        return {"record_id":record_id,"detention_ref":txr["external_ref"],"process_status":process_status,
                "calculation_stage":stage,"empty_return":empty_return,"history":history,
                "covered_till":hs["covered_till"],"covered_days":hs["total_days"],
                "finance":finance,"screen_count":196}
    finally:conn.close()

@router.post("/detention/{record_id}/calculate")
def calculate_detention(record_id:int,b:DetentionCalculationBody,x_role:str=Header("OPS"),
                        x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
                        x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),
                        x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
                        x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    conn=connect()
    try:
        a=actor(conn,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(conn)
        txr,ctx=_detention_context(conn,record_id)
        c=get_container(conn,ctx["container_no"],a)
        if int(c["id"])!=int(ctx["container_id"]):raise HTTPException(409,{"code":"DETENTION_CONTAINER_SCOPE_MISMATCH"})
        payload=_detention_payload(txr);history=_detention_history(payload);hs=_detention_history_state(history)
        requested_stage=str(b.requested_stage or "AUTO").upper()
        if requested_stage not in {"AUTO","ADVANCE","ONGOING","ACTUAL"}:
            raise HTTPException(422,{"code":"INVALID_DETENTION_STAGE","requested_stage":requested_stage})

        gate_out=_detention_event_time(conn,c["id"],"GATE_OUT_FULL") or c["free_time_start"]
        if not gate_out:raise HTTPException(409,{"code":"GATE_OUT_FULL_REQUIRED","container":c["container_no"]})
        start=parse_dt(gate_out)
        if not start:raise HTTPException(409,{"code":"INVALID_GATE_OUT_FULL_TIME","container":c["container_no"]})
        active_on=start.date().isoformat()
        ctx["free_time_start"]=gate_out
        rule=_detention_select_rule(conn,ctx,active_on)
        if history and any(str(x.get("rule_ref") or "") not in {"",rule["rule_ref"]} for x in history if isinstance(x,dict)):
            raise HTTPException(409,{"code":"MRG_RULE_CHANGED_DURING_ACTIVE_DETENTION","current_rule":rule["rule_ref"]})
        free_days=int(rule["free_days"] if rule.get("free_days") is not None else (c["free_days"] or 0))
        due=start+datetime.timedelta(days=free_days)
        actual_return_raw=_detention_event_time(conn,c["id"],"EMPTY_RETURN")
        actual_return=parse_dt(actual_return_raw)

        if requested_stage=="ACTUAL" and not actual_return:
            raise HTTPException(409,{"code":"EMPTY_RETURN_REQUIRED_FOR_ACTUAL","container":c["container_no"]})
        if requested_stage=="ADVANCE" and history:
            raise HTTPException(409,{"code":"ADVANCE_ALREADY_STARTED_USE_ONGOING","covered_till":hs["covered_till"]})
        if requested_stage=="ONGOING" and not history:
            raise HTTPException(409,{"code":"NO_PRIOR_ADVANCE_USE_ADVANCE"})

        requested=parse_dt(b.calculate_till) if b.calculate_till else None
        if b.calculate_till and not requested:raise HTTPException(422,{"code":"INVALID_CALCULATE_TILL"})
        if actual_return:
            calc_until=actual_return
        else:
            calc_until=requested or utcnow()
        if calc_until<start:raise HTTPException(422,{"code":"CALCULATE_TILL_BEFORE_GATE_OUT"})

        delta=(calc_until-due).total_seconds()
        total_days=max(0,int((delta+86399)//86400))
        cumulative_amount,rate=_detention_amount(rule,total_days,c.get("size_type"),payload.get("Container Category"))
        finance=_detention_finance_summary(conn,txr["external_ref"],ctx["job_id"])
        is_actual=actual_return is not None

        if not is_actual:
            covered=parse_dt(hs["covered_till"]) if hs["covered_till"] else None
            if covered and calc_until<=covered:
                raise HTTPException(409,{"code":"DETENTION_PERIOD_ALREADY_COVERED","covered_till":hs["covered_till"],"requested_till":dtiso(calc_until)})
            if total_days<hs["total_days"]:
                raise HTTPException(409,{"code":"DETENTION_DAY_REGRESSION","covered_days":hs["total_days"],"calculated_days":total_days})
            segment_days=total_days-hs["total_days"]
            segment_amount=round(max(0.0,cumulative_amount-hs["cumulative_amount"]),2)
            stage="ADVANCE" if not history else "ONGOING"
            prior_till=parse_dt(hs["covered_till"]) if hs["covered_till"] else None
            segment_from=due if not history else ((prior_till+datetime.timedelta(days=1)) if prior_till else due)
            entry={
              "sequence":len(history)+1,"stage":stage,"rule_ref":rule["rule_ref"],
              "rate_group":rule["mrg_type"],"rate":rate,"currency":str(rule.get("currency") or payload.get("Currency") or "USD"),
              "covered_from":dtiso(segment_from),"covered_till":dtiso(calc_until),
              "segment_days":segment_days,"segment_amount":segment_amount,
              "cumulative_days":total_days,"cumulative_amount":cumulative_amount,"calculated_at":now()
            }
            history.append(entry)
            process_status="RUNNING"
            calculation_stage=stage
            advance_total=cumulative_amount
            actual_amount=_detention_num(payload.get("Actual Detention") or payload.get("Actual Amount"))
            difference=0.0
            final_due=0.0
            credit_required=0.0
        else:
            process_status="COMPLETE"
            calculation_stage="ACTUAL"
            segment_days=max(0,total_days-hs["total_days"])
            segment_amount=round(max(0.0,cumulative_amount-hs["cumulative_amount"]),2)
            advance_total=hs["cumulative_amount"]
            actual_amount=cumulative_amount
            prior_invoiced=max(0.0,round(finance["advance_invoiced"]-finance["credit_adjustment"],2))
            difference=round(actual_amount-prior_invoiced,2)
            final_due=round(max(0.0,difference),2)
            credit_required=round(max(0.0,-difference),2)

        currency=str(rule.get("currency") or payload.get("Currency") or "USD")
        fields={
          "Reference":txr["external_ref"],"Job Ref":ctx["job_ref"],"Container":c["container_no"],
          "Process Status":process_status,"Calculation Stage":calculation_stage,
          "Gate-out":gate_out,"Detention Start":dtiso(due),"Empty Return":actual_return_raw or "",
          "Free Days":free_days,"Previous Advance Till Date":hs["covered_till"] or "",
          "Next Charge Start Date":dtiso(due if not hs["covered_till"] else ((parse_dt(hs["covered_till"])+datetime.timedelta(days=1)) if parse_dt(hs["covered_till"]) else due)),
          "Advance Till Date":dtiso(calc_until) if not is_actual else (hs["covered_till"] or ""),
          "Calculate Till Date":dtiso(calc_until),"Previous Advance Days":hs["total_days"],
          "Advance Days":segment_days if not is_actual else hs["total_days"],
          "Ongoing Days":segment_days if calculation_stage=="ONGOING" else 0,
          "Actual Chargeable Days":total_days if is_actual else 0,"Total Chargeable Days":total_days,
          "Tariff":rule["rule_ref"],"Tariff Code":rule["charge_code"],"Rate Group":rule["mrg_type"],"Rate":rate,
          "Currency":currency,"Current Advance Amount":segment_amount if not is_actual else 0,
          "Advance Detention":round(advance_total,2),"Ongoing Detention":segment_amount if calculation_stage=="ONGOING" else 0,
          "Actual Detention":round(actual_amount,2),"Difference":difference,
          "Final / Additional Due":final_due,"Credit / Adjustment Required":credit_required,
          "Draft Invoice Amount":finance["draft_invoice_amount"],"Advance Invoiced":finance["advance_invoiced"],
          "Additional / Final Invoiced":finance["final_invoiced"],"Total Invoiced":finance["total_invoiced"],
          "Paid Amount":finance["paid_amount"],"Credit / Adjustment":finance["credit_adjustment"],
          "Outstanding Balance":finance["outstanding_balance"],"Last Payment Date":finance["last_payment_date"] or "",
          "Collection Status":finance["collection_status"],"Advance History":history,
          "Status":process_status
        }
        before={**payload};payload.update(fields)
        cur=conn.execute("""UPDATE transaction_records SET payload_json=?,status=?,version=version+1,updated_at=?
          WHERE id=? AND module='detention-collection' AND version=?""",
          (json.dumps(payload,sort_keys=True),process_status,now(),record_id,txr["version"]))
        if cur.rowcount!=1:raise HTTPException(409,{"code":"OPTIMISTIC_LOCK_CONFLICT"})
        audit(conn,a,"DETENTION_CALCULATE",c["id"],ctx["job_id"],before,payload,{
          "record_id":record_id,"rule_ref":rule["rule_ref"],"mrg_type":rule["mrg_type"],
          "rate_side":"REVENUE","calculation_stage":calculation_stage,"process_status":process_status,
          "segment_days":segment_days,"total_chargeable_days":total_days,"segment_amount":segment_amount,
          "actual_amount":actual_amount,"advance_invoiced":finance["advance_invoiced"],
          "difference":difference,"currency":currency,"gl_posted":False,"payment_posted":False
        })
        conn.execute("COMMIT")
        return {"ok":True,"record_id":record_id,"detention_ref":txr["external_ref"],
                "process_status":process_status,"stage":calculation_stage,"rule_ref":rule["rule_ref"],
                "mrg_type":rule["mrg_type"],"free_days":free_days,"gate_out":gate_out,
                "detention_start":dtiso(due),"previous_advance_till":hs["covered_till"],
                "calculate_till":dtiso(calc_until),"empty_return":actual_return_raw,
                "advance_days":segment_days if not is_actual else hs["total_days"],
                "ongoing_days":segment_days if calculation_stage=="ONGOING" else 0,
                "actual_chargeable_days":total_days if is_actual else 0,"total_chargeable_days":total_days,
                "rate":rate,"currency":currency,"segment_amount":segment_amount,
                "advance":round(advance_total,2),"actual":round(actual_amount,2),
                "difference":difference,"final_due":final_due,"credit_required":credit_required,
                "finance":finance,"gl_posted":False,"payment_posted":False,"screen_count":196}
    except HTTPException:
        try:conn.execute("ROLLBACK")
        except Exception:pass
        raise
    except Exception:
        try:conn.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:conn.close()

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
        governance_active=False
        try:
            from .state_transition_governance import enabled as state_governance_enabled, authorize_container_transition
            governance_active=state_governance_enabled()
        except Exception:
            governance_active=False
        if governance_active:
            mode=(b.governance_mode or ("OVERRIDE" if b.override else "STANDARD")).upper()
            reason=b.governance_reason or b.override_reason
            authorize_container_transition(
                container_no=container_no,
                from_state=frm,
                to_state=to,
                mode=mode,
                actor_user_id=a["user"],
                actor_role=a["role"],
                actor_type=b.actor_type,
                reason=reason,
                evidence_refs=b.governance_evidence_refs,
                approver_user_ids=b.governance_approver_user_ids,
                source_event_ref=b.correction_source_event_ref,
                original_transition_at=b.correction_original_transition_at,
                financial_posted=b.financial_posted,
                connection=conn,
            )
            valid=True
        elif not valid:
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
        if b.override and not governance_active:
            create_exception(conn,updated,"AUTHORIZED_OVERRIDE","MEDIUM",f"Authorized override {frm} -> {to}: {b.override_reason}",created_by=a["user"])
        if governance_active and (b.governance_mode or "").upper()=="OVERRIDE":
            create_exception(conn,updated,"AUTHORIZED_OVERRIDE","MEDIUM",f"Governed override {frm} -> {to}: {b.governance_reason or b.override_reason}",created_by=a["user"])
        audit(conn,a,"JOURNEY_EVENT",c["id"],job["id"] if job else c["job_id"],before,updated,
              {"event_id":event_id,"from":frm,"to":to,"override":b.override,"override_reason":b.override_reason,
               "governance_mode":b.governance_mode if governance_active else None,"cost_amount":b.cost_amount})
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
        # Enforce the same branch/agent/depot custody scope used by all container journey reads/writes.
        # Knowing an exception_ref must never allow a scoped actor to mutate another custodian's container.
        get_container(conn,e["container_no"],a)
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
