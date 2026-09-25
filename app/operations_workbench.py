from __future__ import annotations

import datetime
import json
import uuid
from typing import Optional, Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .db import connect, tx

router=APIRouter(prefix="/api/v1/operations-workbench",tags=["CLX-025 Operations Workbench"])
READ_ROLES={"ADMIN","OPS","DOCS","FINANCE","AGENT","VIEWER","AUDITOR"}
WRITE_ROLES={"ADMIN","OPS","DOCS","FINANCE"}
PRIORITY_ORDER={"CRITICAL":0,"HIGH":1,"MEDIUM":2,"LOW":3}

class BulkAction(BaseModel):
    exception_keys:list[str]=Field(min_length=1,max_length=100)
    owner:Optional[str]=Field(default=None,max_length=120)
    note:Optional[str]=Field(default=None,max_length=500)

def now()->str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _role(value:str)->str:
    r=value.upper()
    if r not in READ_ROLES: raise HTTPException(403,{"code":"WORKBENCH_ROLE_DENIED"})
    return r

def _scope(role:str,agent_scope:Optional[str],customer_scope:Optional[str]):
    if role=="AGENT":
        if not agent_scope: raise HTTPException(403,{"code":"AGENT_SCOPE_REQUIRED"})
        return " AND a.code=?", [agent_scope]
    if customer_scope:
        return " AND c.code=?", [customer_scope]
    return "",[]

def _state(c,key:str)->dict[str,Any]:
    r=c.execute("SELECT * FROM operations_work_items WHERE exception_key=?",(key,)).fetchone()
    return dict(r) if r else {}

def _priority(category:str,blocking:bool=False)->str:
    if blocking or category in {"PAYMENT_RELEASE_BLOCK","FAILED_INTEGRATION","RECONCILIATION_EXCEPTION"}: return "HIGH"
    if category in {"CUTOFF_RISK","DOCUMENT_GAP","MILESTONE_DELAY"}: return "MEDIUM"
    return "LOW"

def _item(c,key,category,title,detail,job,priority=None,module=None,transaction_id=None,target=None,age_hours=None,sla_hours=24):
    st=_state(c,key)
    p=st.get("priority_override") or priority or _priority(category)
    age=float(age_hours or 0)
    return {
      "exception_key":key,"category":category,"title":title,"detail":detail,
      "job_ref":job.get("job_ref"),"job_id":job.get("id"),"module":module,"transaction_id":transaction_id,
      "priority":p,"owner":st.get("owner"),"status":st.get("work_status","OPEN"),
      "acknowledged_by":st.get("acknowledged_by"),"acknowledged_at":st.get("acknowledged_at"),
      "age_hours":round(age,1),"sla_hours":sla_hours,"sla_breached":age>sla_hours,
      "target_url":target or (f"/api/v1/jobs/{job.get('job_ref')}" if job.get("job_ref") else None),
      "source":"DERIVED_EXISTING_APPROVED_DATA"
    }

def _age(ts:Optional[str])->float:
    if not ts:return 0
    try:
        d=datetime.datetime.fromisoformat(ts.replace("Z","+00:00"))
        if d.tzinfo is None:d=d.replace(tzinfo=datetime.timezone.utc)
        return max(0,(datetime.datetime.now(datetime.timezone.utc)-d).total_seconds()/3600)
    except Exception:return 0

def _parse_dt(value:Any):
    if not value:return None
    s=str(value).strip()
    for candidate in (s,s.replace("Z","+00:00")):
        try:
            d=datetime.datetime.fromisoformat(candidate)
            if d.tzinfo is None:d=d.replace(tzinfo=datetime.timezone.utc)
            return d
        except Exception:pass
    return None

def _audit(c,role,actor,action,metadata):
    c.execute("""INSERT INTO audit_events(event_id,ts,actor_role,actor_scope,action,module,transaction_id,job_id,before_json,after_json,metadata_json)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (str(uuid.uuid4()),now(),role,actor,action,"operations-workbench",None,None,None,None,json.dumps(metadata,sort_keys=True)))

def _derive(c,role,agent_scope,customer_scope):
    clause,args=_scope(role,agent_scope,customer_scope)
    jobs=c.execute("""SELECT j.id,j.job_ref,j.operational_status,c.code customer_code,a.code agent_code,
      w.documentation_status,w.vgm_status,w.customs_status,w.transshipment_status,w.release_status,w.closed,
      f.payment_status,f.outstanding,f.credit_hold
      FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
      JOIN workflow_states w ON w.job_id=j.id JOIN finance_states f ON f.job_id=j.id
      WHERE 1=1"""+clause+" ORDER BY j.job_ref",args).fetchall()
    items=[]
    visible_ids=set()
    for rr in jobs:
        j=dict(rr); visible_ids.add(j["id"])
        if j["closed"]: continue
        if j["documentation_status"] not in ("COMPLETE","APPROVED"):
            items.append(_item(c,f"JOB:{j['id']}:DOC", "DOCUMENT_GAP","Documentation gap",j["documentation_status"],j,
                               target=f"/api/v1/booking?job_ref={j['job_ref']}"))
        if j["vgm_status"] in ("MISSING","PENDING"):
            items.append(_item(c,f"JOB:{j['id']}:VGM","DOCUMENT_GAP","VGM action required",j["vgm_status"],j,
                               target=f"/api/v1/container-activity?job_ref={j['job_ref']}"))
        if j["customs_status"] not in ("CLEARED","N/A"):
            items.append(_item(c,f"JOB:{j['id']}:CUSTOMS","MILESTONE_DELAY","Customs milestone pending",j["customs_status"],j))
        if j["transshipment_status"]=="PENDING":
            items.append(_item(c,f"JOB:{j['id']}:TS","MILESTONE_DELAY","Transshipment confirmation pending","PENDING",j))
        if j["release_status"]=="BLOCKED" or j["payment_status"]!="CLEARED" or int(j["credit_hold"] or 0)==1 or float(j["outstanding"] or 0)>0:
            reasons=[]
            if j["release_status"]=="BLOCKED": reasons.append("release blocked")
            if j["payment_status"]!="CLEARED": reasons.append("payment "+str(j["payment_status"]))
            if int(j["credit_hold"] or 0)==1: reasons.append("credit hold")
            if float(j["outstanding"] or 0)>0: reasons.append("outstanding balance")
            items.append(_item(c,f"JOB:{j['id']}:PAYREL","PAYMENT_RELEASE_BLOCK","Payment / release block",", ".join(reasons),j,
                               priority="HIGH",target=f"/api/v1/delivery-order?job_ref={j['job_ref']}"))

    if visible_ids:
        marks=",".join("?" for _ in visible_ids)
        vals=list(visible_ids)
        for r in c.execute(f"""SELECT ir.*,j.job_ref FROM integration_records ir JOIN jobs j ON j.id=ir.job_id
                              WHERE ir.job_id IN ({marks}) AND UPPER(ir.status) IN ('FAILED','ERROR','RETRY','DEAD_LETTER','BLOCKED')""",vals):
            d=dict(r); j={"id":d["job_id"],"job_ref":d["job_ref"]}
            items.append(_item(c,f"INT:{d['id']}","FAILED_INTEGRATION","Integration action required",
                               f"{d['module']} / {d['status']}",j,priority="HIGH",module=d["module"],transaction_id=d["id"],
                               target=f"/api/v1/integrations/{d['module']}/{d['id']}",age_hours=_age(d.get("updated_at")),sla_hours=4))
        for r in c.execute(f"""SELECT e.*,j.job_ref FROM exception_events e LEFT JOIN jobs j ON j.id=e.job_id
                              WHERE e.resolved=0 AND e.job_id IN ({marks})""",vals):
            d=dict(r);j={"id":d["job_id"],"job_ref":d["job_ref"]}
            items.append(_item(c,f"EX:{d['id']}","RECONCILIATION_EXCEPTION",d["code"],d["detail"],j,
                               priority="CRITICAL" if d["severity"]=="BLOCKING" else "HIGH",module=d["module"],
                               transaction_id=d["transaction_id"],age_hours=_age(d["ts"]),sla_hours=8))
        for r in c.execute(f"""SELECT t.*,j.job_ref FROM transaction_records t JOIN jobs j ON j.id=t.job_id
                              WHERE t.job_id IN ({marks}) AND UPPER(t.status) NOT IN ('RELEASED','CLOSED','CANCELLED')""",vals):
            d=dict(r)
            try:p=json.loads(d["payload_json"] or "{}")
            except Exception:p={}
            for k,v in p.items():
                if "cutoff" not in k.lower() and "cut-off" not in k.lower(): continue
                dt=_parse_dt(v)
                if not dt: continue
                hrs=(dt-datetime.datetime.now(datetime.timezone.utc)).total_seconds()/3600
                if -4 <= hrs <= 24:
                    j={"id":d["job_id"],"job_ref":d["job_ref"]}
                    items.append(_item(c,f"CUT:{d['id']}:{k}","CUTOFF_RISK","Cut-off risk",
                                       f"{k}: {v}",j,priority="CRITICAL" if hrs<=4 else "HIGH",module=d["module"],
                                       transaction_id=d["id"],target=f"/api/v1/{d['module']}/{d['id']}",age_hours=max(0,-hrs),sla_hours=4))
    dedup={x["exception_key"]:x for x in items}
    return list(dedup.values())

@router.get("")
def list_workbench(
    q:Optional[str]=None,category:Optional[str]=None,priority:Optional[str]=None,owner:Optional[str]=None,
    status:Optional[str]=None,job_ref:Optional[str]=None,
    x_role:str=Header("VIEWER"),x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)
):
    role=_role(x_role);c=connect()
    try:items=_derive(c,role,x_agent_scope,x_customer_scope)
    finally:c.close()
    if q:
        needle=q.lower();items=[x for x in items if needle in json.dumps(x).lower()]
    if category:items=[x for x in items if x["category"]==category.upper()]
    if priority:items=[x for x in items if x["priority"]==priority.upper()]
    if owner:items=[x for x in items if (x["owner"] or "").lower()==owner.lower()]
    if status:items=[x for x in items if x["status"]==status.upper()]
    if job_ref:items=[x for x in items if x["job_ref"]==job_ref]
    items.sort(key=lambda x:(PRIORITY_ORDER.get(x["priority"],9),not x["sla_breached"],x["job_ref"] or "",x["exception_key"]))
    summary={
      "total":len(items),
      "critical":sum(x["priority"]=="CRITICAL" for x in items),
      "high":sum(x["priority"]=="HIGH" for x in items),
      "sla_breached":sum(x["sla_breached"] for x in items),
      "unassigned":sum(not x["owner"] for x in items)
    }
    return {"phase":"CLX-025","summary":summary,"items":items,"source_data_only":True,
            "maker_checker_preserved":True,"live_providers":"OFF","real_money":"OFF"}

def _bulk(body:BulkAction,action:str,role:str,actor_id:str):
    if role not in WRITE_ROLES:raise HTTPException(403,{"code":"WORKBENCH_WRITE_DENIED"})
    c=connect();tx(c)
    try:
        stamp=now();changed=0
        for key in body.exception_keys:
            old=_state(c,key)
            if action=="assign":
                if not body.owner:raise HTTPException(422,{"code":"OWNER_REQUIRED"})
                c.execute("""INSERT INTO operations_work_items(exception_key,owner,work_status,created_at,updated_at)
                             VALUES(?,?,'OPEN',?,?) ON CONFLICT(exception_key) DO UPDATE SET owner=excluded.owner,updated_at=excluded.updated_at,version=operations_work_items.version+1""",
                          (key,body.owner,stamp,stamp))
            elif action=="acknowledge":
                c.execute("""INSERT INTO operations_work_items(exception_key,work_status,acknowledged_by,acknowledged_at,note,created_at,updated_at)
                             VALUES(?,'ACKNOWLEDGED',?,?,?,?,?) ON CONFLICT(exception_key) DO UPDATE SET
                             work_status='ACKNOWLEDGED',acknowledged_by=excluded.acknowledged_by,acknowledged_at=excluded.acknowledged_at,
                             note=excluded.note,updated_at=excluded.updated_at,version=operations_work_items.version+1""",
                          (key,actor_id,stamp,body.note,stamp,stamp))
            changed+=1
        _audit(c,role,actor_id,"WORKBENCH_"+action.upper(),{"keys":body.exception_keys,"owner":body.owner,"note":body.note,"count":changed})
        c.execute("COMMIT")
        return {"ok":True,"action":action,"changed":changed,"underlying_business_records_changed":False}
    except Exception:
        c.execute("ROLLBACK");raise
    finally:c.close()

@router.post("/bulk-assign")
def bulk_assign(body:BulkAction,x_role:str=Header("VIEWER"),x_actor_id:str=Header("actor-user",alias="X-Actor-Id")):
    return _bulk(body,"assign",_role(x_role),x_actor_id)

@router.post("/bulk-acknowledge")
def bulk_ack(body:BulkAction,x_role:str=Header("VIEWER"),x_actor_id:str=Header("actor-user",alias="X-Actor-Id")):
    return _bulk(body,"acknowledge",_role(x_role),x_actor_id)

@router.get("/control-status")
def control_status(x_role:str=Header("AUDITOR")):
    _role(x_role)
    return {"phase":"CLX-025","role_scope_enforced":True,"safe_bulk_actions":["ASSIGN","ACKNOWLEDGE"],
            "underlying_financial_or_operational_mutation":False,"maker_checker_preserved":True,
            "four_eyes_preserved":True,"audit_enabled":True,"live_provider_activation":False,"real_money":False}
