from __future__ import annotations

import json
from typing import Any, Optional
from fastapi import APIRouter, Header, HTTPException
from .db import connect
from .clx071_container_journey import STANDARD_FLOW, NEXT
from .clx072_equipment_network import scoped_containers, norm_state, owner_segment
from .clx070_container_master_control import actor

router=APIRouter(prefix="/api/final-ho-setup",tags=["M3 Final HO Setup Gaps"])

def _row(r): return dict(r) if r else {}
def _payload(r):
    try:return json.loads(r["payload_json"] or "{}")
    except Exception:return {}

def _configs(c,kind:str):
    out=[]
    for r in c.execute("SELECT * FROM md_records WHERE domain='configuration' AND status='ACTIVE' ORDER BY record_key").fetchall():
        p=_payload(r)
        if str(p.get("config_type") or "").upper()!=kind: continue
        d=_row(r);d.pop("payload_json",None);d["payload"]=p;out.append(d)
    return out

def _active(p:dict[str,Any],day:str):
    ef=str(p.get("effective_from") or "0000-01-01")
    et=str(p.get("effective_to") or "9999-12-31")
    return ef<=day<=et

@router.get("/damage-setup")
def damage_setup(x_role:str=Header("VIEWER")):
    c=connect()
    try:
        rows=_configs(c,"DAMAGE_SETUP")
        return {
          "source":"configuration + CLX074 container_damage_items / inspections / repair workflow",
          "duplicate_mr_engine":False,
          "count":len(rows),"records":rows,
          "supported_fields":["damage_code","description","damage_category","component_code","location_code","severity","repair_required","inspection_required","default_hold","effective_from","effective_to"]
        }
    finally:c.close()

@router.get("/container-status")
def container_status(x_role:str=Header("VIEWER")):
    all_states=sorted(set(NEXT)|{x for v in NEXT.values() for x in v})
    terminal={"SOLD","SCRAPPED","OFF_HIRED","PARTNER_RETURNED","AGENT_RETURNED","SOC_RELEASED"}
    holds={"HOLD","REPAIR","INSPECTION","OFF_HIRE_DUE"}
    records=[]
    for s in all_states:
        records.append({
          "status_code":s,
          "display_name":s.replace("_"," ").title(),
          "operational_category":"STANDARD_FLOW" if s in STANDARD_FLOW else "EXCEPTION_OR_OWNERSHIP",
          "available_for_booking":s=="AVAILABLE",
          "hold_state":s in holds,
          "terminal_state":s in terminal,
          "allowed_next_status":sorted(NEXT.get(s,set())),
          "active":True,
          "source":"CLX071_NEXT"
        })
    return {"source":"CLX071 / Container Master","duplicate_status_engine":False,"state_transition_governance_changed":False,"count":len(records),"records":records}

@router.get("/local-recovery-agreements")
def local_recovery_agreements(x_role:str=Header("VIEWER")):
    c=connect()
    try:
        rows=_configs(c,"LOCAL_RECOVERY_AGREEMENT")
        return {
          "source":"configuration + existing Booking Commercial Charges + existing Finance settlement",
          "direct_finance_posting":False,
          "count":len(rows),"records":rows
        }
    finally:c.close()

def _matches_recovery(p,ctx,fields):
    pairs=[
      ("recover_from_party_code",fields.get("Customer Code") or ctx.get("customer_code")),
      ("route_code",fields.get("Route")),
      ("port_code",fields.get("POL") or ctx.get("pol")),
      ("service_code",fields.get("Service") or fields.get("Service Type")),
    ]
    for k,v in pairs:
        if p.get(k) not in (None,"") and str(p.get(k))!=str(v or ""): return False
    return True

@router.get("/local-recovery/booking/{job_ref}/applicable")
def local_recovery_booking(job_ref:str,x_role:str=Header("VIEWER")):
    c=connect()
    try:
        r=c.execute("""SELECT j.id job_id,j.job_ref,j.pol,j.pod,b.booking_ref,c.code customer_code,
          t.payload_json FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN customers c ON c.id=j.customer_id
          LEFT JOIN transaction_records t ON t.job_id=j.id AND t.module='booking'
          WHERE j.job_ref=? ORDER BY t.id LIMIT 1""",(job_ref,)).fetchone()
        if not r:raise HTTPException(404,{"code":"JOB_NOT_FOUND"})
        ctx=_row(r);fields=_payload(r);day=str(fields.get("Booking Date") or "9999-12-31")
        charges=list(fields.get("Commercial Charges") or [])
        out=[]
        for rec in _configs(c,"LOCAL_RECOVERY_AGREEMENT"):
            p=rec["payload"]
            if not _active(p,day) or not _matches_recovery(p,ctx,fields): continue
            code=str(p.get("charge_code") or "")
            base=sum(float(x.get("applied_rate") or 0) for x in charges if not code or x.get("charge_code")==code)
            basis=str(p.get("recovery_basis") or "PERCENT").upper()
            rate=float(p.get("recovery_rate") or 0)
            amount=rate if basis=="FIXED" else round(base*rate/100,2)
            out.append({"agreement_ref":rec["record_key"],"charge_code":code,"recovery_basis":basis,"recovery_rate":rate,
                        "base_amount":base,"expected_recovery":amount,"recover_from_party_code":p.get("recover_from_party_code"),
                        "recover_to_party_code":p.get("recover_to_party_code"),"currency":p.get("currency"),"priority":p.get("priority",100)})
        out.sort(key=lambda x:int(x.get("priority") or 100))
        return {"job_ref":job_ref,"booking_ref":ctx["booking_ref"],"expected_only":True,"direct_finance_posting":False,"count":len(out),"records":out}
    finally:c.close()

def _aging_rule(rows,crow):
    matches=[]
    for rec in rows:
        p=rec["payload"]
        checks=[
          ("port_code",crow.get("current_port")),("depot_code",crow.get("depot_code")),
          ("branch_code",crow.get("branch_code")),("agent_code",crow.get("agent_code")),
          ("ownership_type",owner_segment(crow)),("equipment_type",crow.get("size_type"))
        ]
        ok=True
        for k,v in checks:
            if p.get(k) not in (None,"") and str(p.get(k)).upper()!=str(v or "").upper():ok=False;break
        if ok:matches.append((int(p.get("priority") or 100),rec,p))
    if not matches:return None
    matches.sort(key=lambda x:x[0]);return matches[0]

@router.get("/long-ageing")
def long_ageing(x_role:str=Header("AUDITOR"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),
               x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),
               x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope)
        rows=scoped_containers(c,a);rules=_configs(c,"LONG_AGEING_CONFIGURATION")
        records=[];summary={"NORMAL":0,"WARNING":0,"CRITICAL":0}
        for x in rows:
            if norm_state(x)!="AVAILABLE":continue
            idle=int(x.get("idle_days") or 0);m=_aging_rule(rules,x)
            if m:
                _,rec,p=m
                warning=int(p.get("warning_threshold") or p.get("ageing_threshold_days") or 14)
                critical=int(p.get("critical_threshold") or 30)
                source=rec["record_key"]
                escalation=p.get("escalation_role");action=p.get("action_required")
            else:
                warning=14;critical=30;source="DEFAULT_EXISTING_14_30";escalation=None;action=None
            cls="CRITICAL" if idle>=critical else "WARNING" if idle>=warning else "NORMAL"
            summary[cls]+=1
            records.append({"container_no":x["container_no"],"idle_days":idle,"classification":cls,"warning_threshold":warning,
                            "critical_threshold":critical,"source_rule":source,"port":x.get("current_port"),"depot":x.get("depot_code"),
                            "branch":x.get("branch_code"),"agent":x.get("agent_code"),"ownership_type":owner_segment(x),
                            "escalation_role":escalation,"action_required":action})
        records.sort(key=lambda x:(0 if x["classification"]=="CRITICAL" else 1 if x["classification"]=="WARNING" else 2,-x["idle_days"]))
        return {"source":"existing containers.idle_days + CLX072 Equipment Network + governed configuration",
                "equipment_network_changed":False,"duplicate_ageing_engine":False,"server_side_evaluation":True,
                "summary":summary,"count":len(records),"records":records}
    finally:c.close()
