from __future__ import annotations

import datetime, json, uuid
from typing import Any, Optional
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .db import connect, using_postgres

router=APIRouter(prefix="/api/nvocc-principal",tags=["M3 NVOCC Principal Extensions"])

WORKSPACES={
 "branch-profile":{"table":"nvocc_branch_profiles","key":"branch_code","roles":{"ADMIN","ORG_ADMIN","OFFICE_ADMIN","MASTER_DATA_MANAGER"},"required":["branch_code","branch_type","legal_entity_code"],"title":"Branch / Office Master — Operational Profile"},
 "legal-entity-profile":{"table":"nvocc_legal_entity_profiles","key":"entity_code","roles":{"ADMIN","ORG_ADMIN","GL_MANAGER","MASTER_DATA_MANAGER"},"required":["entity_code","currency"],"title":"Legal Entity Master — Finance Profile"},
 "depot-master":{"table":"nvocc_depots","key":"depot_code","roles":{"ADMIN","OPS","MASTER_DATA_MANAGER","EQUIPMENT_MANAGER"},"required":["depot_code","depot_name","depot_type","ownership","port_code"],"title":"Depot Master"},
 "port-config":{"table":"nvocc_port_agent_depot_config","key":"config_id","roles":{"ADMIN","OPS","MASTER_DATA_MANAGER","EQUIPMENT_MANAGER"},"required":["config_id","port_code","port_role","agent_code","agent_type"],"title":"Port Agent / Depot Configuration"},
 "transfer-pricing":{"table":"nvocc_transfer_pricing","key":"rule_id","roles":{"ADMIN","FINANCE","GL_MANAGER"},"required":["rule_id","from_branch_type","to_branch_type","service_type","pricing_method","currency","effective_from"],"title":"Inter-Branch Transfer Pricing"},
 "ts-branch-ops":{"table":"nvocc_ts_branch_operations","key":"task_ref","roles":{"ADMIN","OPS","AGENT","EQUIPMENT_MANAGER"},"required":["task_ref","job_ref","container_no","ts_port","ts_branch","status"],"title":"TS Branch Operations"},
 "release-control":{"table":"nvocc_release_controls","key":"release_ref","roles":{"ADMIN","OPS","DOCS","FINANCE"},"required":["release_ref","job_ref","hbl_no"],"title":"Release Control"},
 "interbranch-settlement":{"table":"nvocc_interbranch_settlements","key":"settlement_ref","roles":{"ADMIN","FINANCE","GL_MANAGER","TREASURY_MANAGER","TREASURY"},"required":["settlement_ref","from_branch","to_branch","job_ref","currency","status"],"title":"Inter-Branch Settlement"},
}

SCHEMAS={
 "branch-profile":[
  ["branch_code","text",True],["branch_type","select",True],["legal_entity_code","text",True],["country_code","text",False],["city","text",False],
  ["ports_served","text",False],["port_roles","text",False],["cost_center","text",False],["profit_center","text",False],["default_depot_code","text",False],
  ["default_carrier_depot_code","text",False],["contact","text",False],["sla","text",False],["active","boolean",True]],
 "legal-entity-profile":[["entity_code","text",True],["registration_no","text",False],["tax_id","text",False],["currency","text",True],["consolidation_group","text",False],["intercompany_flag","boolean",True],["active","boolean",True]],
 "depot-master":[
  ["depot_code","text",True],["depot_name","text",True],["depot_type","select",True],["ownership","select",True],["legal_entity_code","text",False],
  ["vendor_code","text",False],["carrier_code","text",False],["branch_code","text",False],["port_code","text",True],["address","textarea",True],
  ["gps_coordinates","text",False],["capacity_teu","number",False],["free_time_days","number",True],["storage_rate","number",True],["handling_rate","number",False],
  ["mr_capability","boolean",False],["reefer_plugs","number",False],["dangerous_goods","boolean",False],["customs_bonded","boolean",False],["operating_hours","text",False],["contact","text",True],["active","boolean",True]],
 "port-config":[
  ["config_id","text",True],["port_code","text",True],["port_role","select",True],["agent_code","text",True],["agent_type","select",True],["depot_code","text",False],
  ["depot_type","text",False],["empty_return_depot","text",False],["carrier_depot","text",False],["handling_instructions","textarea",False],["cost_allocation","select",True],
  ["effective_from","date",True],["effective_to","date",False],["priority","number",True],["active","boolean",True]],
 "transfer-pricing":[
  ["rule_id","text",True],["from_branch_type","select",True],["to_branch_type","select",True],["service_type","select",True],["pricing_method","select",True],
  ["markup_pct","number",False],["fixed_amount","number",False],["currency","text",True],["effective_from","date",True],["effective_to","date",False],["active","boolean",True]],
 "ts-branch-ops":[
  ["task_ref","text",True],["job_ref","text",True],["container_no","text",True],["ts_port","text",True],["ts_branch","text",True],["branch_manager","text",False],
  ["cost_center","text",False],["profit_center","text",False],["legal_entity_code","text",False],["scope","text",False],["internal_sla","text",False],["backup_branch","text",False],
  ["from_branch","text",False],["to_branch","text",False],["mbl_no","text",False],["hbl_no","text",False],["connecting_vessel","text",False],["connecting_voyage","text",False],
  ["eta_ts","datetime-local",False],["etd_ts","datetime-local",False],["free_time","number",False],["special_instructions","textarea",False],["documents_required","text",False],
  ["due_date","date",False],["priority","select",False],["acknowledged_by","text",False],["acknowledged_at","datetime-local",False],["accepted_scope","text",False],
  ["exceptions_noted","textarea",False],["estimated_internal_cost","number",False],["resource_assigned","text",False],["discharge_at","datetime-local",False],["condition","select",False],
  ["damage_details","textarea",False],["yard_location","text",False],["storage_start","datetime-local",False],["free_time_end","date",False],["storage_rate","number",False],
  ["storage_days","number",False],["storage_cost","number",False],["reload_at","datetime-local",False],["stowage_position","text",False],["departure_at","datetime-local",False],
  ["eta_final_port","datetime-local",False],["branch_reference","text",False],["documents_sent","text",False],["cost_lines_json","textarea",False],["documents_json","textarea",False],
  ["exceptions_json","textarea",False],["status","select",True]],
 "release-control":[
  ["release_ref","text",True],["job_ref","text",True],["hbl_no","text",True],["pod_agent","text",False],["pod_agent_type","select",False],["pod_depot","text",False],
  ["pod_depot_type","select",False],["empty_return_depot","text",False],["empty_return_depot_type","select",False],["release_date","date",False],["release_by","text",False],
  ["status","select",True],["dd_start","date",False],["cost_allocation","select",False]],
 "interbranch-settlement":[
  ["settlement_ref","text",True],["from_branch","text",True],["to_branch","text",True],["legal_entity_from","text",False],["legal_entity_to","text",False],["job_ref","text",True],
  ["container_no","text",False],["service_period_from","date",False],["service_period_to","date",False],["charges_json","textarea",False],["total_amount","number",False],
  ["currency","text",True],["exchange_rate","number",False],["status","select",True],["gl_posting_ref","text",False],["elimination_flag","boolean",False]]
}

DDL="""
CREATE TABLE IF NOT EXISTS nvocc_branch_profiles(
 branch_code TEXT PRIMARY KEY,branch_type TEXT NOT NULL,legal_entity_code TEXT NOT NULL,country_code TEXT,city TEXT,ports_served TEXT,port_roles TEXT,
 cost_center TEXT,profit_center TEXT,default_depot_code TEXT,default_carrier_depot_code TEXT,contact TEXT,sla TEXT,active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_legal_entity_profiles(
 entity_code TEXT PRIMARY KEY,registration_no TEXT,tax_id TEXT,currency TEXT NOT NULL,consolidation_group TEXT,intercompany_flag INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_depots(
 depot_code TEXT PRIMARY KEY,depot_name TEXT NOT NULL,depot_type TEXT NOT NULL,ownership TEXT NOT NULL,legal_entity_code TEXT,vendor_code TEXT,carrier_code TEXT,branch_code TEXT,
 port_code TEXT NOT NULL,address TEXT NOT NULL,gps_coordinates TEXT,capacity_teu REAL,free_time_days REAL NOT NULL DEFAULT 0,storage_rate REAL NOT NULL DEFAULT 0,handling_rate REAL,
 mr_capability INTEGER NOT NULL DEFAULT 0,reefer_plugs INTEGER,dangerous_goods INTEGER NOT NULL DEFAULT 0,customs_bonded INTEGER NOT NULL DEFAULT 0,operating_hours TEXT,contact TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_port_agent_depot_config(
 config_id TEXT PRIMARY KEY,port_code TEXT NOT NULL,port_role TEXT NOT NULL,agent_code TEXT NOT NULL,agent_type TEXT NOT NULL,depot_code TEXT,depot_type TEXT,
 empty_return_depot TEXT,carrier_depot TEXT,handling_instructions TEXT,cost_allocation TEXT NOT NULL,effective_from TEXT NOT NULL,effective_to TEXT,priority INTEGER NOT NULL DEFAULT 1,
 active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_transfer_pricing(
 rule_id TEXT PRIMARY KEY,from_branch_type TEXT NOT NULL,to_branch_type TEXT NOT NULL,service_type TEXT NOT NULL,pricing_method TEXT NOT NULL,markup_pct REAL,fixed_amount REAL,
 currency TEXT NOT NULL,effective_from TEXT NOT NULL,effective_to TEXT,active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_ts_branch_operations(
 task_ref TEXT PRIMARY KEY,job_ref TEXT NOT NULL,container_no TEXT NOT NULL,ts_port TEXT NOT NULL,ts_branch TEXT NOT NULL,branch_manager TEXT,cost_center TEXT,profit_center TEXT,
 legal_entity_code TEXT,scope TEXT,internal_sla TEXT,backup_branch TEXT,from_branch TEXT,to_branch TEXT,mbl_no TEXT,hbl_no TEXT,connecting_vessel TEXT,connecting_voyage TEXT,
 eta_ts TEXT,etd_ts TEXT,free_time REAL,special_instructions TEXT,documents_required TEXT,due_date TEXT,priority TEXT,acknowledged_by TEXT,acknowledged_at TEXT,accepted_scope TEXT,
 exceptions_noted TEXT,estimated_internal_cost REAL,resource_assigned TEXT,discharge_at TEXT,condition TEXT,damage_details TEXT,yard_location TEXT,storage_start TEXT,free_time_end TEXT,
 storage_rate REAL,storage_days REAL,storage_cost REAL,reload_at TEXT,stowage_position TEXT,departure_at TEXT,eta_final_port TEXT,branch_reference TEXT,documents_sent TEXT,
 cost_lines_json TEXT,documents_json TEXT,exceptions_json TEXT,status TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_release_controls(
 release_ref TEXT PRIMARY KEY,job_ref TEXT NOT NULL,hbl_no TEXT NOT NULL,pod_agent TEXT,pod_agent_type TEXT,pod_depot TEXT,pod_depot_type TEXT,empty_return_depot TEXT,
 empty_return_depot_type TEXT,release_date TEXT,release_by TEXT,status TEXT NOT NULL DEFAULT 'Pending',dd_start TEXT,cost_allocation TEXT,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_interbranch_settlements(
 settlement_ref TEXT PRIMARY KEY,from_branch TEXT NOT NULL,to_branch TEXT NOT NULL,legal_entity_from TEXT,legal_entity_to TEXT,job_ref TEXT NOT NULL,container_no TEXT,
 service_period_from TEXT,service_period_to TEXT,charges_json TEXT,total_amount REAL NOT NULL DEFAULT 0,currency TEXT NOT NULL,exchange_rate REAL,status TEXT NOT NULL,
 gl_posting_ref TEXT,elimination_flag INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_extension_audit(
 audit_ref TEXT PRIMARY KEY,ts TEXT NOT NULL,actor_role TEXT NOT NULL,branch_scope TEXT,workspace_key TEXT NOT NULL,record_ref TEXT NOT NULL,action TEXT NOT NULL,
 before_json TEXT,after_json TEXT);
"""

class WorkspaceWrite(BaseModel):
    data:dict[str,Any]=Field(default_factory=dict)

def _now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def _role(workspace:str,role:str):
    r=(role or "VIEWER").upper()
    if r=="SUPER_ADMIN": r="ADMIN"
    if r not in WORKSPACES[workspace]["roles"]: raise HTTPException(403,{"code":"ROLE_NOT_ALLOWED","workspace":workspace})
    return r
def _ensure(conn):
    if not using_postgres(): conn.executescript(DDL)
def _row(r): return dict(r) if r else None
def _clean(workspace:str,data:dict[str,Any]):
    allowed={x[0] for x in SCHEMAS[workspace]}
    out={k:v for k,v in data.items() if k in allowed}
    missing=[k for k in WORKSPACES[workspace]["required"] if out.get(k) in (None,"")]
    if missing: raise HTTPException(422,{"code":"REQUIRED_FIELDS","fields":missing})
    if workspace=="transfer-pricing":
        method=str(out.get("pricing_method","")).upper()
        if "COST" in method and out.get("markup_pct") in (None,""): raise HTTPException(422,{"code":"MARKUP_REQUIRED"})
        if "FIXED" in method and out.get("fixed_amount") in (None,""): raise HTTPException(422,{"code":"FIXED_AMOUNT_REQUIRED"})
    for k,v in list(out.items()):
        typ=next(x[1] for x in SCHEMAS[workspace] if x[0]==k)
        if typ=="boolean": out[k]=1 if str(v).lower() in {"1","true","yes","on"} else 0
        elif typ=="number" and v not in ("",None): out[k]=float(v)
    return out
def _audit(c,role,branch,workspace,ref,action,before,after):
    c.execute("INSERT INTO nvocc_extension_audit(audit_ref,ts,actor_role,branch_scope,workspace_key,record_ref,action,before_json,after_json) VALUES(?,?,?,?,?,?,?,?,?)",
      (str(uuid.uuid4()),_now(),role,branch,workspace,ref,action,json.dumps(before,sort_keys=True) if before else None,json.dumps(after,sort_keys=True) if after else None))

@router.get("/meta")
def meta():
    return {"project":"M3 NVOCC ERP","screen_baseline":196,"screen_count_changed":False,"extension_workspaces":len(WORKSPACES),
      "workspaces":{k:{"title":v["title"],"schema":SCHEMAS[k],"roles":sorted(v["roles"])} for k,v in WORKSPACES.items()},
      "real_money":False,"providers_active":False,"ancline_touched":False}

@router.get("/workspaces/{workspace}")
def list_workspace(workspace:str,x_role:str=Header("VIEWER"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope")):
    if workspace not in WORKSPACES: raise HTTPException(404,"Unknown workspace")
    role=_role(workspace,x_role)
    c=connect();_ensure(c);w=WORKSPACES[workspace]
    sql=f"SELECT * FROM {w['table']}"
    args=[]
    if workspace=="ts-branch-ops" and x_branch_scope and role not in {"ADMIN"}:
        sql+=" WHERE ts_branch=?";args=[x_branch_scope]
    elif workspace=="interbranch-settlement" and x_branch_scope and role not in {"ADMIN","GL_MANAGER","TREASURY_MANAGER"}:
        sql+=" WHERE from_branch=? OR to_branch=?";args=[x_branch_scope,x_branch_scope]
    sql+=" ORDER BY updated_at DESC"
    rows=[dict(x) for x in c.execute(sql,args).fetchall()];c.close()
    return {"workspace":workspace,"title":w["title"],"schema":SCHEMAS[workspace],"rows":rows,"editable":True}

@router.post("/workspaces/{workspace}")
def upsert_workspace(workspace:str,b:WorkspaceWrite,x_role:str=Header("VIEWER"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope")):
    if workspace not in WORKSPACES: raise HTTPException(404,"Unknown workspace")
    role=_role(workspace,x_role); data=_clean(workspace,b.data);w=WORKSPACES[workspace];key=w["key"];ref=str(data[key])
    if workspace=="ts-branch-ops" and x_branch_scope and role!="ADMIN" and data.get("ts_branch")!=x_branch_scope:
        raise HTTPException(403,{"code":"BRANCH_SCOPE_MISMATCH"})
    if workspace=="interbranch-settlement" and x_branch_scope and role not in {"ADMIN","GL_MANAGER","TREASURY_MANAGER"} and x_branch_scope not in {data.get("from_branch"),data.get("to_branch")}:
        raise HTTPException(403,{"code":"BRANCH_SCOPE_MISMATCH"})
    c=connect();_ensure(c);old=_row(c.execute(f"SELECT * FROM {w['table']} WHERE {key}=?",(ref,)).fetchone())
    data["updated_at"]=_now()
    cols=list(data)
    if old:
        sets=",".join(f"{k}=?" for k in cols if k!=key)
        vals=[data[k] for k in cols if k!=key]+[ref]
        c.execute(f"UPDATE {w['table']} SET {sets},version=version+1 WHERE {key}=?",vals);action="UPDATE"
    else:
        cols2=cols+([] if "version" in cols else ["version"])
        vals=[data[k] for k in cols]+([1] if "version" not in cols else [])
        c.execute(f"INSERT INTO {w['table']}({','.join(cols2)}) VALUES({','.join('?' for _ in cols2)})",vals);action="CREATE"
    current=_row(c.execute(f"SELECT * FROM {w['table']} WHERE {key}=?",(ref,)).fetchone())
    _audit(c,role,x_branch_scope,workspace,ref,action,old,current);c.close()
    return {"ok":True,"workspace":workspace,"record":current}

@router.get("/release-prerequisites/{job_ref}")
def release_prerequisites(job_ref:str,x_role:str=Header("VIEWER")):
    if x_role.upper() not in {"ADMIN","SUPER_ADMIN","OPS","DOCS","FINANCE","AUDITOR","VIEWER"}: raise HTTPException(403,"Role not allowed")
    c=connect();_ensure(c)
    row=c.execute("""SELECT j.id,w.documentation_status,w.customs_status,w.release_status,f.payment_status,f.outstanding,f.credit_hold,
      (SELECT bill_no FROM bills WHERE job_id=j.id AND kind='HBL' ORDER BY id LIMIT 1) hbl_no,
      (SELECT payload_json FROM transaction_records WHERE job_id=j.id AND module='delivery-order' ORDER BY id DESC LIMIT 1) do_payload
      FROM jobs j JOIN workflow_states w ON w.job_id=j.id JOIN finance_states f ON f.job_id=j.id WHERE j.job_ref=?""",(job_ref,)).fetchone()
    if not row: c.close(); raise HTTPException(404,"Job not found")
    r=dict(row);p={}
    try:p=json.loads(r.get("do_payload") or "{}")
    except Exception:pass
    bl_ok=bool(r.get("hbl_no")) and str(r.get("documentation_status","")).upper() not in {"PENDING","BLOCKED","MISSING"}
    customs_ok=str(r.get("customs_status","")).upper() in {"CLEARED","PASS","APPROVED","NOT_REQUIRED"}
    finance_ok=(str(r.get("payment_status","")).upper() in {"CLEARED","PAID","APPROVED"} and float(r.get("outstanding") or 0)<=0 and not int(r.get("credit_hold") or 0))
    original=str(p.get("Original BL Status","")).upper()
    telex=str(p.get("Telex Release","")).lower() in {"yes","true","1","released","approved"}
    surrender_ok=telex or original in {"SURRENDERED","RECEIVED","NOT_REQUIRED"}
    c.close()
    checks={"hbl_issued":bl_ok,"customs_cleared":customs_ok,"finance_cleared":finance_ok,"surrender_or_telex":surrender_ok}
    return {"job_ref":job_ref,"hbl_no":r.get("hbl_no"),"checks":checks,"release_ready":all(checks.values()),"authoritative_sources":["bills","workflow_states","finance_states","delivery-order transaction"]}

@router.get("/bl-linkage/{job_ref}")
def bl_linkage(job_ref:str,x_role:str=Header("VIEWER")):
    if x_role.upper() not in {"ADMIN","SUPER_ADMIN","OPS","DOCS","FINANCE","AUDITOR","VIEWER","AGENT"}: raise HTTPException(403,"Role not allowed")
    c=connect();_ensure(c)
    j=c.execute("SELECT id FROM jobs WHERE job_ref=?",(job_ref,)).fetchone()
    if not j:c.close();raise HTTPException(404,"Job not found")
    bills=[dict(x) for x in c.execute("SELECT bill_no,kind,status FROM bills WHERE job_id=? ORDER BY kind,bill_no",(j["id"],)).fetchall()]
    tx=[dict(x) for x in c.execute("SELECT module,external_ref,status,payload_json FROM transaction_records WHERE job_id=? AND module IN ('booking','bl','import-bl') ORDER BY id",(j["id"],)).fetchall()]
    gl=[dict(x) for x in c.execute("SELECT module,external_ref,status,source_type,source_ref,payload_json FROM gl_records WHERE job_id=? AND module IN ('invoice','bills') ORDER BY id",(j["id"],)).fetchall()]
    c.close();return {"job_ref":job_ref,"bills":bills,"documents":tx,"carrier_customer_finance":gl,"parallel_tracks":True}

@router.get("/branch-pnl")
def branch_pnl(branch_code:Optional[str]=None,x_role:str=Header("VIEWER")):
    if x_role.upper() not in {"ADMIN","SUPER_ADMIN","FINANCE","GL_MANAGER","GL_ACCOUNTANT","AUDITOR","VIEWER"}: raise HTTPException(403,"Role not allowed")
    c=connect();_ensure(c)
    where=" WHERE status IN ('APPROVED','SETTLED','POSTED')" + (" AND (from_branch=? OR to_branch=?)" if branch_code else "")
    args=[branch_code,branch_code] if branch_code else []
    rows=[dict(x) for x in c.execute("SELECT * FROM nvocc_interbranch_settlements"+where,args).fetchall()]
    agg={}
    for r in rows:
        amt=float(r.get("total_amount") or 0)
        for br,rev,cost in ((r["from_branch"],amt,0),(r["to_branch"],0,amt)):
            a=agg.setdefault(br,{"branch":br,"internal_revenue":0.0,"internal_cost":0.0,"elimination":0.0})
            a["internal_revenue"]+=rev;a["internal_cost"]+=cost
            if int(r.get("elimination_flag") or 0):a["elimination"]+=amt
    for a in agg.values():
        a["branch_margin"]=round(a["internal_revenue"]-a["internal_cost"],2)
        a["group_effect_after_elimination"]=round(a["branch_margin"]-(a["elimination"] if a["branch_margin"]>0 else -a["elimination"]),2)
    c.close()
    return {"scope":"INTER_BRANCH_COMPONENT","branch":branch_code,"rows":list(agg.values()),"note":"External job revenue/cost continues to come from authoritative GL/job profitability; this view adds the missing internal/inter-company component and elimination control."}
