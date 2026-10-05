from __future__ import annotations

import json
from typing import Any, Optional
from fastapi import APIRouter, Header
from .db import connect
from .clx071_container_journey import STANDARD_FLOW, NEXT

router=APIRouter(prefix="/api/ho-operational-setup",tags=["M3 HO Operational Setup"])

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

def _port_context(c,port_code:Optional[str]):
    if not port_code:return {"port":None,"country":None,"city":None,"location":None}
    p=c.execute("SELECT payload_json,display_name FROM md_records WHERE domain='port' AND record_key=? AND status='ACTIVE'",(port_code,)).fetchone()
    if not p:return {"port":port_code,"country":None,"city":None,"location":None}
    x=_payload(p)
    return {
      "port":port_code,
      "country":x.get("country") or x.get("country_code"),
      "city":x.get("city") or x.get("location") or p["display_name"],
      "location":x.get("location") or p["display_name"]
    }

def _rule_matches(p,ctx):
    for k in ("country","city","location","port","carrier_code","principal_code","agent_code","movement"):
        rv=p.get(k)
        if rv not in (None,"") and str(rv).upper()!=str(ctx.get(k) or "").upper(): return False
    return True

def tariff_applicability(c,tariff_family:str,day:str,ctx:dict[str,Any]):
    family=tariff_family.upper()
    kind="DEMURRAGE_APPLICABLE_COUNTRY" if family=="DEMURRAGE" else "DETENTION_APPLICABLE_CITY"
    rows=_configs(c,kind)
    matches=[]
    for r in rows:
        p=r["payload"]
        if not _active(p,day): continue
        if _rule_matches(p,ctx): matches.append((int(p.get("priority") or 100),r,p))
    if not matches:return {"applicable":True,"source":None,"configured":False}
    matches.sort(key=lambda x:x[0])
    _,r,p=matches[0]
    applicable=str(p.get("applicable","YES")).upper() not in {"NO","FALSE","0","N"}
    return {"applicable":applicable,"source":r["record_key"],"configured":True,"priority":p.get("priority",100)}

@router.get("/demurrage-applicable-countries")
def demurrage_countries(x_role:str=Header("VIEWER")):
    c=connect()
    try:
        rows=_configs(c,"DEMURRAGE_APPLICABLE_COUNTRY")
        return {"source":"configuration + iam_countries + port/location + existing MRG tariff engine","duplicate_demurrage_engine":False,"count":len(rows),"records":rows}
    finally:c.close()

@router.get("/detention-applicable-cities")
def detention_cities(x_role:str=Header("VIEWER")):
    c=connect()
    try:
        rows=_configs(c,"DETENTION_APPLICABLE_CITY")
        return {"source":"configuration + location/port + MRG_DETENTION","duplicate_detention_engine":False,"count":len(rows),"records":rows}
    finally:c.close()

@router.get("/activities")
def activities(x_role:str=Header("VIEWER")):
    c=connect()
    try:
        overlays={r["record_key"]:r for r in _configs(c,"ACTIVITY_METADATA")}
        rows=[]
        for idx,code in enumerate(dict.fromkeys(STANDARD_FLOW),1):
            ov=overlays.get("ACTIVITY-"+code)
            p=ov["payload"] if ov else {}
            rows.append({
              "activity_code":code,
              "activity_name":p.get("display_name") or code.replace("_"," ").title(),
              "activity_category":p.get("activity_category") or "CONTAINER_JOURNEY",
              "applies_to_module":p.get("applies_to_module") or "container-activity",
              "mandatory":p.get("mandatory",True),
              "sequence_hint":idx,
              "source":"CLX071_STANDARD_FLOW",
              "configuration_ref":ov["record_key"] if ov else None
            })
        return {"source":"CLX071 STANDARD_FLOW + optional governed configuration metadata","duplicate_activity_model":False,"count":len(rows),"records":rows}
    finally:c.close()

@router.get("/activity-sequence")
def activity_sequence(x_role:str=Header("VIEWER")):
    rows=[]
    for frm,targets in NEXT.items():
        for to in sorted(targets):
            rows.append({"process":"CONTAINER_JOURNEY","previous_activity":frm,"next_activity":to,"mandatory_predecessor":True,"server_enforced":True,"source":"CLX071_NEXT"})
    return {"source":"CLX071 NEXT transition map","server_side_enforcement":True,"container_journey_changed":False,"count":len(rows),"records":rows}

@router.get("/tariff-context/{port_code}")
def tariff_context(port_code:str,x_role:str=Header("VIEWER")):
    c=connect()
    try:return _port_context(c,port_code)
    finally:c.close()
