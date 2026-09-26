from __future__ import annotations
import datetime, hashlib, json
from typing import Optional
from fastapi import APIRouter, Header, HTTPException
from .db import connect, tx
from .operations_workbench import _derive, _role
from .control_tower import _job_rows, _summarize, _team_workload, _risk_jobs, _counter

router=APIRouter(prefix="/api/v1/management-kpi",tags=["CLX-028 KPI Trends + Alerts"])
WRITE_ROLES={"ADMIN","OPS","FINANCE"}

DEFAULT_RULES=[
 ("CRITICAL_WORK","critical","GTE",1,"CRITICAL","Any critical operational work item is active."),
 ("OVERDUE_WORK","overdue","GTE",1,"HIGH","One or more governed work items are overdue."),
 ("UNASSIGNED_WORK","unassigned","GTE",3,"MEDIUM","Three or more active work items are unassigned."),
 ("RELEASE_BLOCKS","release_blocked","GTE",2,"HIGH","Two or more jobs are release blocked."),
 ("PAYMENT_BLOCKS","payment_blocks","GTE",2,"HIGH","Two or more jobs have payment/credit exposure."),
]

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def today(): return datetime.datetime.now(datetime.timezone.utc).date().isoformat()

def _ensure_rules(c):
    stamp=now()
    for key,metric,op,threshold,severity,desc in DEFAULT_RULES:
        c.execute("""INSERT INTO management_alert_rules(rule_key,metric_key,operator,threshold_value,severity,enabled,description,created_at,updated_at)
        VALUES(?,?,?,?,?,1,?,?,?) ON CONFLICT(rule_key) DO NOTHING""",(key,metric,op,threshold,severity,desc,stamp,stamp))

def _match(value,op,threshold):
    return {"GT":value>threshold,"GTE":value>=threshold,"LT":value<threshold,"LTE":value<=threshold,"EQ":value==threshold}[op]

def _snapshot_payload(c,role):
    jobs=_job_rows(c,role,None,None); items=_derive(c,role,None,None)
    active=[x for x in items if x.get("status") not in ("RESOLVED","CLOSED")]
    summary=_summarize(jobs,items)
    payload={
      "summary":summary,
      "team_workload":_team_workload(items),
      "exception_categories":_counter([x.get("category") or "UNKNOWN" for x in active]),
      "priorities":_counter([x.get("priority") or "LOW" for x in active]),
      "risk_jobs":_risk_jobs(jobs,items)[:10],
    }
    payload["source_hash"]=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return payload

def _row_to_snapshot(r):
    d=dict(r)
    for k in ("team_workload_json","exception_categories_json","priority_json","risk_jobs_json"):
        d[k[:-5]]=json.loads(d.pop(k))
    return d

def _evaluate_alerts(c,snapshot_date,summary):
    _ensure_rules(c); stamp=now(); active_keys=set()
    rules=c.execute("SELECT * FROM management_alert_rules WHERE enabled=1").fetchall()
    for rr in rules:
        r=dict(rr); val=float(summary.get(r["metric_key"],0)); th=float(r["threshold_value"])
        key=f'{snapshot_date}:{r["rule_key"]}'
        if _match(val,r["operator"],th):
            active_keys.add(key)
            c.execute("""INSERT INTO management_alert_events(alert_key,snapshot_date,rule_key,metric_key,metric_value,threshold_value,severity,status,first_seen_at,last_seen_at,detail_json)
            VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?)
            ON CONFLICT(alert_key) DO UPDATE SET metric_value=excluded.metric_value,last_seen_at=excluded.last_seen_at,
            status=CASE WHEN management_alert_events.status='CLOSED' THEN 'OPEN' ELSE management_alert_events.status END,
            closed_at=NULL""",(key,snapshot_date,r["rule_key"],r["metric_key"],val,th,r["severity"],stamp,stamp,json.dumps({"description":r["description"]},sort_keys=True)))
    rows=c.execute("SELECT alert_key FROM management_alert_events WHERE snapshot_date=? AND status<>'CLOSED'",(snapshot_date,)).fetchall()
    for row in rows:
        if row["alert_key"] not in active_keys:
            c.execute("UPDATE management_alert_events SET status='CLOSED',closed_at=?,last_seen_at=? WHERE alert_key=?",(stamp,stamp,row["alert_key"]))

@router.post("/snapshot")
def create_snapshot(x_role:str=Header("VIEWER"),x_actor_id:str=Header("management-system",alias="X-Actor-Id")):
    role=_role(x_role)
    if role not in WRITE_ROLES: raise HTTPException(403,{"code":"SNAPSHOT_WRITE_DENIED"})
    c=connect(); tx(c)
    try:
        date=today(); old=c.execute("SELECT * FROM management_daily_snapshots WHERE snapshot_date=?",(date,)).fetchone()
        if old:
            c.execute("ROLLBACK"); return {"ok":True,"created":False,"snapshot":_row_to_snapshot(old)}
        p=_snapshot_payload(c,role); s=p["summary"]; stamp=now()
        c.execute("""INSERT INTO management_daily_snapshots(snapshot_date,created_at,created_by,scope_key,jobs_total,jobs_open,jobs_closed,release_blocked,document_gaps,payment_blocks,outstanding_total,active_work_items,critical,high,overdue,unassigned,team_workload_json,exception_categories_json,priority_json,risk_jobs_json,source_hash,immutable)
        VALUES(?,?,?,'GLOBAL',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (date,stamp,x_actor_id,s["jobs_total"],s["jobs_open"],s["jobs_closed"],s["release_blocked"],s["document_gaps"],s["payment_blocks"],s["outstanding_total"],s["active_work_items"],s["critical"],s["high"],s["overdue"],s["unassigned"],json.dumps(p["team_workload"],sort_keys=True),json.dumps(p["exception_categories"],sort_keys=True),json.dumps(p["priorities"],sort_keys=True),json.dumps(p["risk_jobs"],sort_keys=True),p["source_hash"]))
        _evaluate_alerts(c,date,s); c.execute("COMMIT")
        row=c.execute("SELECT * FROM management_daily_snapshots WHERE snapshot_date=?",(date,)).fetchone()
        return {"ok":True,"created":True,"snapshot":_row_to_snapshot(row)}
    except Exception:
        c.execute("ROLLBACK"); raise
    finally: c.close()

@router.get("/snapshot/latest")
def latest_snapshot(x_role:str=Header("VIEWER")):
    _role(x_role); c=connect()
    try:
        r=c.execute("SELECT * FROM management_daily_snapshots ORDER BY snapshot_date DESC LIMIT 1").fetchone()
        return {"phase":"CLX-028","snapshot":_row_to_snapshot(r) if r else None}
    finally:c.close()

@router.get("/trends")
def trends(days:int=30,x_role:str=Header("VIEWER")):
    _role(x_role); days=max(1,min(days,365)); c=connect()
    try:
        rows=c.execute("SELECT * FROM management_daily_snapshots ORDER BY snapshot_date DESC LIMIT ?",(days,)).fetchall()
        data=[_row_to_snapshot(r) for r in reversed(rows)]
        metrics=["jobs_open","release_blocked","document_gaps","payment_blocks","active_work_items","critical","high","overdue","unassigned","outstanding_total"]
        deltas={}
        if len(data)>=2:
            for m in metrics:deltas[m]=round(float(data[-1][m])-float(data[-2][m]),2)
        return {"phase":"CLX-028","days":days,"snapshots":data,"latest_deltas":deltas}
    finally:c.close()

@router.get("/alerts")
def alerts(status:Optional[str]="OPEN",x_role:str=Header("VIEWER")):
    _role(x_role); c=connect()
    try:
        _ensure_rules(c)
        if status:
            rows=c.execute("SELECT * FROM management_alert_events WHERE status=? ORDER BY severity,first_seen_at",(status.upper(),)).fetchall()
        else: rows=c.execute("SELECT * FROM management_alert_events ORDER BY snapshot_date DESC,first_seen_at").fetchall()
        out=[]
        for r in rows:
            d=dict(r); d["detail"]=json.loads(d.pop("detail_json") or "{}"); out.append(d)
        return {"phase":"CLX-028","alerts":out,"external_notifications":False}
    finally:c.close()

@router.post("/alerts/{alert_key}/acknowledge")
def acknowledge(alert_key:str,x_role:str=Header("VIEWER"),x_actor_id:str=Header("actor-user",alias="X-Actor-Id")):
    role=_role(x_role)
    if role not in WRITE_ROLES: raise HTTPException(403,{"code":"ALERT_ACK_DENIED"})
    c=connect();tx(c)
    try:
        r=c.execute("SELECT * FROM management_alert_events WHERE alert_key=?",(alert_key,)).fetchone()
        if not r: raise HTTPException(404,{"code":"ALERT_NOT_FOUND"})
        stamp=now();c.execute("UPDATE management_alert_events SET status='ACKNOWLEDGED',acknowledged_by=?,acknowledged_at=?,last_seen_at=? WHERE alert_key=?",(x_actor_id,stamp,stamp,alert_key))
        c.execute("COMMIT");return {"ok":True,"alert_key":alert_key,"status":"ACKNOWLEDGED"}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.get("/control-status")
def control_status(x_role:str=Header("AUDITOR")):
    _role(x_role)
    return {"phase":"CLX-028","daily_snapshot_immutable":True,"trends_read_only":True,"alerts_in_app_only":True,
      "external_notifications":False,"underlying_business_mutation":False,"screen_catalog_preserved":193,
      "role_scope_preserved":True,"maker_checker_preserved":True,"four_eyes_preserved":True,
      "live_provider_activation":False,"real_money":False}
