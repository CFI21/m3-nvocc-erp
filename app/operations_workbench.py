from __future__ import annotations

import datetime
import json
import uuid
from typing import Optional, Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .db import connect, tx

router=APIRouter(prefix="/api/v1/operations-workbench",tags=["CLX-026 Exception Workflow"])
READ_ROLES={"ADMIN","OPS","DOCS","FINANCE","AGENT","VIEWER","AUDITOR"}
WRITE_ROLES={"ADMIN","OPS","DOCS","FINANCE"}
PRIORITY_ORDER={"CRITICAL":0,"HIGH":1,"MEDIUM":2,"LOW":3}

class BulkAction(BaseModel):
    exception_keys:list[str]=Field(min_length=1,max_length=100)
    owner:Optional[str]=Field(default=None,max_length=120)
    team:Optional[str]=Field(default=None,max_length=120)
    note:Optional[str]=Field(default=None,max_length=500)

class LifecycleAction(BaseModel):
    action:str
    comment:Optional[str]=Field(default=None,max_length=500)
    resolution_code:Optional[str]=Field(default=None,max_length=120)

class AssignmentAction(BaseModel):
    owner:Optional[str]=Field(default=None,max_length=120)
    team:Optional[str]=Field(default=None,max_length=120)
    comment:Optional[str]=Field(default=None,max_length=500)

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
      "priority":p,"owner":st.get("owner"),"team":st.get("team"),"status":st.get("work_status","OPEN"),
      "acknowledged_by":st.get("acknowledged_by"),"acknowledged_at":st.get("acknowledged_at"),
      "resolution_code":st.get("resolution_code"),"resolved_by":st.get("resolved_by"),"resolved_at":st.get("resolved_at"),
      "escalation_state":st.get("escalation_state","NONE"),"sla_due_at":st.get("sla_due_at"),
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


def _default_team(category:str)->str:
    return {
      "DOCUMENT_GAP":"DOCS","CUTOFF_RISK":"OPS","MILESTONE_DELAY":"OPS",
      "PAYMENT_RELEASE_BLOCK":"FINANCE","FAILED_INTEGRATION":"OPS",
      "RECONCILIATION_EXCEPTION":"FINANCE"
    }.get(category,"OPS")

def _due_from_item(item:dict)->str:
    base=datetime.datetime.now(datetime.timezone.utc)
    return (base+datetime.timedelta(hours=float(item.get("sla_hours") or 24))).isoformat()

def _history(c,key,role,actor,action,from_status=None,to_status=None,owner=None,team=None,resolution_code=None,comment=None,metadata=None):
    c.execute("""INSERT INTO operations_work_history(exception_key,ts,actor_role,actor_id,action,from_status,to_status,owner,team,resolution_code,comment,metadata_json)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (key,now(),role,actor,action,from_status,to_status,owner,team,resolution_code,comment,json.dumps(metadata or {},sort_keys=True)))

def _sync_items(c,items:list[dict],role:str,actor:str):
    stamp=now(); active={x["exception_key"] for x in items}
    for item in items:
        old=_state(c,item["exception_key"])
        team=old.get("team") or _default_team(item["category"])
        due=old.get("sla_due_at") or _due_from_item(item)
        escalation="NONE"
        dt=_parse_dt(due)
        if dt:
            hrs=(dt-datetime.datetime.now(datetime.timezone.utc)).total_seconds()/3600
            escalation="OVERDUE" if hrs < 0 else ("DUE_SOON" if hrs <= 4 else "NONE")
        if old:
            c.execute("""UPDATE operations_work_items SET team=?,category=?,job_id=?,job_ref=?,sla_due_at=?,escalation_state=?,
                         last_seen_at=?,source_active=1,updated_at=?,version=version+1
                         WHERE exception_key=?""",
                      (team,item["category"],item.get("job_id"),item.get("job_ref"),due,escalation,stamp,stamp,item["exception_key"]))
            if old.get("source_active")==0 or old.get("work_status")=="CLOSED":
                c.execute("""UPDATE operations_work_items SET work_status='OPEN',resolution_code=NULL,resolved_by=NULL,resolved_at=NULL
                             WHERE exception_key=?""",(item["exception_key"],))
                _history(c,item["exception_key"],role,actor,"AUTO_REOPEN",old.get("work_status"),"OPEN",old.get("owner"),team,None,
                         "Underlying condition became active again")
        else:
            c.execute("""INSERT INTO operations_work_items(exception_key,team,category,job_id,job_ref,work_status,sla_due_at,escalation_state,
                         source_active,last_seen_at,created_at,updated_at)
                         VALUES(?,?,?,?,?,'OPEN',?,?,1,?,?,?)""",
                      (item["exception_key"],team,item["category"],item.get("job_id"),item.get("job_ref"),due,escalation,stamp,stamp,stamp))
            _history(c,item["exception_key"],role,actor,"AUTO_CREATE",None,"OPEN",None,team,None,"Derived from approved source data")
    rows=c.execute("SELECT * FROM operations_work_items WHERE source_active=1 AND work_status<>'CLOSED'").fetchall()
    for rr in rows:
        d=dict(rr)
        if d["exception_key"] not in active:
            c.execute("""UPDATE operations_work_items SET source_active=0,work_status='CLOSED',resolution_code='AUTO_SOURCE_CLEARED',
                         resolved_by='SYSTEM',resolved_at=?,updated_at=?,version=version+1 WHERE exception_key=?""",
                      (stamp,stamp,d["exception_key"]))
            _history(c,d["exception_key"],role,actor,"AUTO_CLOSE",d["work_status"],"CLOSED",d.get("owner"),d.get("team"),
                     "AUTO_SOURCE_CLEARED","Underlying condition verified cleared")

def _view_filter(items:list[dict],view:Optional[str],actor_id:Optional[str],team:Optional[str]):
    v=(view or "").lower()
    if v=="my":
        return [x for x in items if actor_id and x.get("owner")==actor_id]
    if v=="team":
        return [x for x in items if team and x.get("team")==team]
    if v=="overdue":
        return [x for x in items if x.get("escalation_state") in ("OVERDUE","ESCALATED") and x.get("status") not in ("RESOLVED","CLOSED")]
    if v=="critical":
        return [x for x in items if x.get("priority")=="CRITICAL" and x.get("status") not in ("RESOLVED","CLOSED")]
    return items

@router.get("")
def list_workbench(
    q:Optional[str]=None,category:Optional[str]=None,priority:Optional[str]=None,owner:Optional[str]=None,
    status:Optional[str]=None,job_ref:Optional[str]=None,view:Optional[str]=None,
    x_role:str=Header("VIEWER"),x_actor_id:Optional[str]=Header(None,alias="X-Actor-Id"),x_team:Optional[str]=Header(None,alias="X-Team"),
    x_agent_scope:Optional[str]=Header(None),x_customer_scope:Optional[str]=Header(None)
):
    role=_role(x_role);c=connect()
    try:
        items=_derive(c,role,x_agent_scope,x_customer_scope)
        if role in WRITE_ROLES and not x_agent_scope and not x_customer_scope:
            tx(c);_sync_items(c,items,role,x_actor_id or "SYSTEM");c.execute("COMMIT")
            items=_derive(c,role,x_agent_scope,x_customer_scope)
    finally:c.close()
    if q:
        needle=q.lower();items=[x for x in items if needle in json.dumps(x).lower()]
    if category:items=[x for x in items if x["category"]==category.upper()]
    if priority:items=[x for x in items if x["priority"]==priority.upper()]
    if owner:items=[x for x in items if (x["owner"] or "").lower()==owner.lower()]
    if status:items=[x for x in items if x["status"]==status.upper()]
    if job_ref:items=[x for x in items if x["job_ref"]==job_ref]
    items=_view_filter(items,view,x_actor_id,x_team)
    items.sort(key=lambda x:(PRIORITY_ORDER.get(x["priority"],9),not x["sla_breached"],x["job_ref"] or "",x["exception_key"]))
    summary={
      "total":len(items),
      "critical":sum(x["priority"]=="CRITICAL" for x in items),
      "high":sum(x["priority"]=="HIGH" for x in items),
      "sla_breached":sum(x["sla_breached"] for x in items),
      "unassigned":sum(not x["owner"] for x in items)
    }
    return {"phase":"CLX-026","summary":summary,"items":items,"source_data_only":True,
            "views":["my","team","overdue","critical"],"lifecycle":["OPEN","ACKNOWLEDGED","IN_PROGRESS","RESOLVED","CLOSED"],
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
                c.execute("""INSERT INTO operations_work_items(exception_key,owner,team,work_status,created_at,updated_at)
                             VALUES(?,?,?,'OPEN',?,?) ON CONFLICT(exception_key) DO UPDATE SET owner=excluded.owner,
                             team=COALESCE(excluded.team,operations_work_items.team),updated_at=excluded.updated_at,version=operations_work_items.version+1""",
                          (key,body.owner,body.team,stamp,stamp))
                _history(c,key,role,actor_id,"ASSIGN",old.get("work_status"),old.get("work_status") or "OPEN",body.owner,body.team or old.get("team"),None,body.note)
            elif action=="acknowledge":
                c.execute("""INSERT INTO operations_work_items(exception_key,work_status,acknowledged_by,acknowledged_at,note,created_at,updated_at)
                             VALUES(?,'ACKNOWLEDGED',?,?,?,?,?) ON CONFLICT(exception_key) DO UPDATE SET
                             work_status='ACKNOWLEDGED',acknowledged_by=excluded.acknowledged_by,acknowledged_at=excluded.acknowledged_at,
                             note=excluded.note,updated_at=excluded.updated_at,version=operations_work_items.version+1""",
                          (key,actor_id,stamp,body.note,stamp,stamp))
                _history(c,key,role,actor_id,"ACKNOWLEDGE",old.get("work_status") or "OPEN","ACKNOWLEDGED",old.get("owner"),old.get("team"),None,body.note)
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


@router.post("/{exception_key}/assign")
def assign_item(exception_key:str,body:AssignmentAction,x_role:str=Header("VIEWER"),x_actor_id:str=Header("actor-user",alias="X-Actor-Id")):
    role=_role(x_role)
    if role not in WRITE_ROLES:raise HTTPException(403,{"code":"WORKBENCH_WRITE_DENIED"})
    c=connect();tx(c)
    try:
        old=_state(c,exception_key)
        if not old:raise HTTPException(404,{"code":"WORK_ITEM_NOT_FOUND"})
        c.execute("UPDATE operations_work_items SET owner=?,team=COALESCE(?,team),updated_at=?,version=version+1 WHERE exception_key=?",
                  (body.owner,body.team,now(),exception_key))
        _history(c,exception_key,role,x_actor_id,"ASSIGN",old.get("work_status"),old.get("work_status"),body.owner,body.team or old.get("team"),None,body.comment)
        _audit(c,role,x_actor_id,"WORKBENCH_ASSIGN",{"key":exception_key,"owner":body.owner,"team":body.team})
        c.execute("COMMIT");return {"ok":True,"exception_key":exception_key}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.post("/{exception_key}/lifecycle")
def lifecycle_action(exception_key:str,body:LifecycleAction,x_role:str=Header("VIEWER"),x_actor_id:str=Header("actor-user",alias="X-Actor-Id")):
    role=_role(x_role)
    if role not in WRITE_ROLES:raise HTTPException(403,{"code":"WORKBENCH_WRITE_DENIED"})
    transitions={"OPEN":"ACKNOWLEDGED","ACKNOWLEDGED":"IN_PROGRESS","IN_PROGRESS":"RESOLVED","RESOLVED":"CLOSED"}
    requested=body.action.upper()
    aliases={"ACKNOWLEDGE":"ACKNOWLEDGED","START":"IN_PROGRESS","RESOLVE":"RESOLVED","CLOSE":"CLOSED"}
    target=aliases.get(requested,requested)
    c=connect();tx(c)
    try:
        old=_state(c,exception_key)
        if not old:raise HTTPException(404,{"code":"WORK_ITEM_NOT_FOUND"})
        current=old.get("work_status","OPEN")
        if transitions.get(current)!=target:
            raise HTTPException(422,{"code":"INVALID_LIFECYCLE_TRANSITION","current":current,"allowed_next":transitions.get(current)})
        if target=="RESOLVED" and not body.resolution_code:
            raise HTTPException(422,{"code":"RESOLUTION_CODE_REQUIRED"})
        stamp=now()
        ack_by=x_actor_id if target=="ACKNOWLEDGED" else old.get("acknowledged_by")
        ack_at=stamp if target=="ACKNOWLEDGED" else old.get("acknowledged_at")
        res_by=x_actor_id if target=="RESOLVED" else old.get("resolved_by")
        res_at=stamp if target=="RESOLVED" else old.get("resolved_at")
        res_code=body.resolution_code if target=="RESOLVED" else old.get("resolution_code")
        c.execute("""UPDATE operations_work_items SET work_status=?,acknowledged_by=?,acknowledged_at=?,resolution_code=?,
                     resolved_by=?,resolved_at=?,note=COALESCE(?,note),updated_at=?,version=version+1 WHERE exception_key=?""",
                  (target,ack_by,ack_at,res_code,res_by,res_at,body.comment,stamp,exception_key))
        _history(c,exception_key,role,x_actor_id,"LIFECYCLE",current,target,old.get("owner"),old.get("team"),res_code,body.comment)
        _audit(c,role,x_actor_id,"WORKBENCH_LIFECYCLE",{"key":exception_key,"from":current,"to":target,"resolution_code":res_code})
        c.execute("COMMIT")
        return {"ok":True,"exception_key":exception_key,"from":current,"to":target}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.get("/{exception_key}/history")
def item_history(exception_key:str,x_role:str=Header("VIEWER")):
    _role(x_role);c=connect()
    try:return {"exception_key":exception_key,"history":[dict(r) for r in c.execute("SELECT * FROM operations_work_history WHERE exception_key=? ORDER BY id",(exception_key,)).fetchall()]}
    finally:c.close()

@router.post("/refresh")
def refresh_workbench(x_role:str=Header("VIEWER"),x_actor_id:str=Header("SYSTEM",alias="X-Actor-Id")):
    role=_role(x_role)
    if role not in WRITE_ROLES:raise HTTPException(403,{"code":"WORKBENCH_WRITE_DENIED"})
    c=connect();tx(c)
    try:
        items=_derive(c,role,None,None)
        before=c.execute("SELECT COUNT(*) n FROM operations_work_items WHERE source_active=1").fetchone()["n"]
        _sync_items(c,items,role,x_actor_id)
        after=c.execute("SELECT COUNT(*) n FROM operations_work_items WHERE source_active=1").fetchone()["n"]
        closed=c.execute("SELECT COUNT(*) n FROM operations_work_items WHERE source_active=0 AND resolution_code='AUTO_SOURCE_CLEARED'").fetchone()["n"]
        _audit(c,role,x_actor_id,"WORKBENCH_REFRESH",{"derived":len(items),"active_before":before,"active_after":after,"auto_closed_total":closed})
        c.execute("COMMIT")
        return {"ok":True,"phase":"CLX-026","derived":len(items),"active_before":before,"active_after":after,"auto_closed_total":closed}
    except Exception:
        c.execute("ROLLBACK");raise
    finally:c.close()

@router.get("/control-status")
def control_status(x_role:str=Header("AUDITOR")):
    _role(x_role)
    return {"phase":"CLX-026","role_scope_enforced":True,"safe_bulk_actions":["ASSIGN","ACKNOWLEDGE"],
            "lifecycle":["OPEN","ACKNOWLEDGED","IN_PROGRESS","RESOLVED","CLOSED"],
            "auto_refresh":True,"auto_close_when_source_cleared":True,
            "views":["MY_WORK","TEAM_WORK","OVERDUE","CRITICAL"],
            "underlying_financial_or_operational_mutation":False,"maker_checker_preserved":True,
            "four_eyes_preserved":True,"audit_enabled":True,"live_provider_activation":False,"real_money":False}
