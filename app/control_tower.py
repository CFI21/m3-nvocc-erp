from __future__ import annotations

import collections
from typing import Optional, Any

from fastapi import APIRouter, Header, HTTPException

from .db import connect
from .operations_workbench import _derive, _role, _scope, PRIORITY_ORDER

router=APIRouter(prefix="/api/v1/control-tower",tags=["CLX-027 Management Control Tower"])

def _job_rows(c,role:str,agent_scope:Optional[str],customer_scope:Optional[str]):
    clause,args=_scope(role,agent_scope,customer_scope)
    return [dict(r) for r in c.execute("""SELECT j.id,j.job_ref,j.operational_status,
      c.code customer_code,c.name customer_name,a.code agent_code,a.name agent_name,
      w.documentation_status,w.vgm_status,w.customs_status,w.transshipment_status,w.release_status,w.closed,
      f.payment_status,f.outstanding,f.credit_hold,f.currency
      FROM jobs j
      JOIN customers c ON c.id=j.customer_id
      JOIN agents a ON a.id=j.agent_id
      JOIN workflow_states w ON w.job_id=j.id
      JOIN finance_states f ON f.job_id=j.id
      WHERE 1=1"""+clause+" ORDER BY j.job_ref",args).fetchall()]

def _counter(values):
    c=collections.Counter(values)
    return [{"name":k,"count":c[k]} for k in sorted(c)]

def _risk_score(item:dict)->int:
    base={"CRITICAL":100,"HIGH":60,"MEDIUM":30,"LOW":10}.get(item.get("priority"),0)
    if item.get("sla_breached"):base+=25
    if not item.get("owner"):base+=10
    if item.get("escalation_state")=="OVERDUE":base+=20
    if item.get("escalation_state")=="ESCALATED":base+=30
    return base

def _summarize(jobs:list[dict],items:list[dict]):
    active=[x for x in items if x.get("status") not in ("RESOLVED","CLOSED")]
    closed_jobs=sum(int(x.get("closed") or 0)==1 for x in jobs)
    open_jobs=len(jobs)-closed_jobs
    release_blocked=sum(x.get("release_status")=="BLOCKED" for x in jobs)
    document_gaps=sum(x.get("documentation_status") not in ("COMPLETE","APPROVED") for x in jobs if not x.get("closed"))
    payment_blocks=sum(
      x.get("payment_status")!="CLEARED" or int(x.get("credit_hold") or 0)==1 or float(x.get("outstanding") or 0)>0
      for x in jobs if not x.get("closed")
    )
    outstanding=round(sum(float(x.get("outstanding") or 0) for x in jobs),2)
    critical=sum(x.get("priority")=="CRITICAL" for x in active)
    high=sum(x.get("priority")=="HIGH" for x in active)
    overdue=sum(x.get("sla_breached") or x.get("escalation_state") in ("OVERDUE","ESCALATED") for x in active)
    unassigned=sum(not x.get("owner") for x in active)
    return {
      "jobs_total":len(jobs),"jobs_open":open_jobs,"jobs_closed":closed_jobs,
      "release_blocked":release_blocked,"document_gaps":document_gaps,"payment_blocks":payment_blocks,
      "outstanding_total":outstanding,
      "active_work_items":len(active),"critical":critical,"high":high,"overdue":overdue,"unassigned":unassigned
    }

def _team_workload(items:list[dict]):
    teams={}
    for x in items:
        if x.get("status") in ("RESOLVED","CLOSED"):continue
        team=x.get("team") or "UNASSIGNED_TEAM"
        d=teams.setdefault(team,{"team":team,"total":0,"critical":0,"high":0,"overdue":0,"unassigned":0,
                                 "open":0,"acknowledged":0,"in_progress":0})
        d["total"]+=1
        if x.get("priority")=="CRITICAL":d["critical"]+=1
        if x.get("priority")=="HIGH":d["high"]+=1
        if x.get("sla_breached") or x.get("escalation_state") in ("OVERDUE","ESCALATED"):d["overdue"]+=1
        if not x.get("owner"):d["unassigned"]+=1
        st=(x.get("status") or "").lower()
        if st in d:d[st]+=1
    return sorted(teams.values(),key=lambda x:(-x["critical"],-x["overdue"],-x["total"],x["team"]))

def _risk_jobs(jobs:list[dict],items:list[dict]):
    by_job={}
    job_map={str(x["job_ref"]):x for x in jobs}
    for item in items:
        jr=str(item.get("job_ref") or "")
        if not jr or jr not in job_map or item.get("status") in ("RESOLVED","CLOSED"):continue
        d=by_job.setdefault(jr,{"job_ref":jr,"risk_score":0,"active_exceptions":0,"critical":0,"high":0,
                               "overdue":0,"unassigned":0,"categories":set()})
        d["risk_score"]+=_risk_score(item);d["active_exceptions"]+=1
        if item.get("priority")=="CRITICAL":d["critical"]+=1
        if item.get("priority")=="HIGH":d["high"]+=1
        if item.get("sla_breached") or item.get("escalation_state") in ("OVERDUE","ESCALATED"):d["overdue"]+=1
        if not item.get("owner"):d["unassigned"]+=1
        d["categories"].add(item.get("category"))
    out=[]
    for jr,d in by_job.items():
        j=job_map[jr]
        d["categories"]=sorted(x for x in d["categories"] if x)
        d["customer_code"]=j.get("customer_code");d["agent_code"]=j.get("agent_code")
        d["operational_status"]=j.get("operational_status");d["release_status"]=j.get("release_status")
        d["workbench_url"]=f"/api/v1/operations-workbench?job_ref={jr}"
        out.append(d)
    return sorted(out,key=lambda x:(-x["risk_score"],x["job_ref"]))

@router.get("")
def control_tower(
    x_role:str=Header("VIEWER"),
    x_agent_scope:Optional[str]=Header(None),
    x_customer_scope:Optional[str]=Header(None)
):
    role=_role(x_role);c=connect()
    try:
        jobs=_job_rows(c,role,x_agent_scope,x_customer_scope)
        items=_derive(c,role,x_agent_scope,x_customer_scope)
    finally:c.close()
    active=[x for x in items if x.get("status") not in ("RESOLVED","CLOSED")]
    return {
      "phase":"CLX-027",
      "mode":"READ_ONLY_MANAGEMENT_CONTROL_TOWER",
      "summary":_summarize(jobs,items),
      "job_statuses":_counter([x.get("operational_status") or "UNKNOWN" for x in jobs]),
      "release_statuses":_counter([x.get("release_status") or "UNKNOWN" for x in jobs]),
      "exception_categories":_counter([x.get("category") or "UNKNOWN" for x in active]),
      "lifecycle_statuses":_counter([x.get("status") or "OPEN" for x in active]),
      "priorities":_counter([x.get("priority") or "LOW" for x in active]),
      "team_workload":_team_workload(items),
      "risk_jobs":_risk_jobs(jobs,items)[:10],
      "controls":{
        "source_data_only":True,"underlying_business_mutation":False,"screen_catalog_preserved":193,
        "role_scope_enforced":True,"maker_checker_preserved":True,"four_eyes_preserved":True,"audit_preserved":True,
        "live_providers":"OFF","real_money":"OFF"
      }
    }

@router.get("/risk-jobs")
def risk_jobs(
    x_role:str=Header("VIEWER"),
    x_agent_scope:Optional[str]=Header(None),
    x_customer_scope:Optional[str]=Header(None)
):
    role=_role(x_role);c=connect()
    try:
        jobs=_job_rows(c,role,x_agent_scope,x_customer_scope)
        items=_derive(c,role,x_agent_scope,x_customer_scope)
    finally:c.close()
    return {"phase":"CLX-027","items":_risk_jobs(jobs,items)}

@router.get("/teams")
def teams(
    x_role:str=Header("VIEWER"),
    x_agent_scope:Optional[str]=Header(None),
    x_customer_scope:Optional[str]=Header(None)
):
    role=_role(x_role);c=connect()
    try:items=_derive(c,role,x_agent_scope,x_customer_scope)
    finally:c.close()
    return {"phase":"CLX-027","teams":_team_workload(items)}

@router.get("/control-status")
def control_status(x_role:str=Header("AUDITOR")):
    _role(x_role)
    return {
      "phase":"CLX-027","read_only":True,"source_data_only":True,"screen_catalog_preserved":193,
      "role_scope_enforced":True,"maker_checker_preserved":True,"four_eyes_preserved":True,"audit_preserved":True,
      "live_provider_activation":False,"real_money":False
    }
