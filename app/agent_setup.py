from __future__ import annotations

import datetime, json
from typing import Any, Optional
from fastapi import APIRouter, Header, HTTPException
from .db import connect

router=APIRouter(prefix="/api/agent-setup",tags=["M3 Agent Setup"])

RELATION_TYPES={"PRINCIPAL","SUB_AGENT","OVERSEAS_AGENT","PORT_AGENT","BRANCH","COUNTRY","SERVICE","TRADE_LANE","OTHER_PARTY"}
LIMIT_TYPES={"BOOKING_COUNT","CONTAINER_COUNT","TEU"}
NETWORK_TYPES={"ORIGIN_AGENT","TRANSSHIPMENT_AGENT","DESTINATION_AGENT","PRINCIPAL","PARTNER","PORT_COVERAGE"}

def _row(r): return dict(r) if r else {}
def _payload(r):
    try:return json.loads(r["payload_json"] or "{}")
    except Exception:return {}
def _today(): return datetime.date.today().isoformat()

def _configuration_rows(c,config_type:str):
    rows=[]
    for r in c.execute("SELECT * FROM md_records WHERE domain='configuration' AND status='ACTIVE' ORDER BY record_key").fetchall():
        p=_payload(r)
        if str(p.get("config_type") or "").upper()!=config_type: continue
        d=_row(r);d.pop("payload_json",None);d["payload"]=p;rows.append(d)
    return rows

def _active_date(p,day:str):
    ef=str(p.get("effective_from") or "0000-01-01")
    et=str(p.get("effective_to") or "9999-12-31")
    return ef<=day<=et

def _matches(p:dict[str,Any],ctx:dict[str,Any],fields:dict[str,Any]):
    pairs=[
      ("agent_code",ctx.get("agent_code")),
      ("principal_code",fields.get("Principal")),
      ("carrier_code",fields.get("Carrier") or fields.get("Carrier / Shipping Line")),
      ("route_code",fields.get("Route")),
      ("service_code",fields.get("Service") or fields.get("Service Type")),
      ("equipment",fields.get("Equipment") or fields.get("Container Type")),
      ("pol",fields.get("POL") or ctx.get("pol")),
      ("pot",fields.get("POT (1)") or fields.get("Via Port")),
      ("pod",fields.get("POD") or ctx.get("pod")),
    ]
    for k,v in pairs:
        rule=p.get(k)
        if rule not in (None,"") and str(rule)!=str(v or ""): return False
    return True

def _qty(fields):
    for k in ("Equipment Qty","Container Qty","Quantity","Qty","Booked","Containers"):
        try:
            if fields.get(k) not in (None,""): return max(0,int(float(fields[k])))
        except Exception: pass
    return 0

def _teu(fields):
    qty=_qty(fields)
    raw=str(fields.get("Equipment") or fields.get("Container Type") or fields.get("Size Type") or "").upper()
    if "20" in raw:return qty
    if "40" in raw:return qty*2
    return 0

def _period(p,fields):
    day=str(fields.get("Booking Date") or _today())[:10]
    start=str(p.get("period_start") or p.get("effective_from") or day)
    end=str(p.get("period_end") or p.get("effective_to") or day)
    return start,end,day

def _used(c,p,ctx,exclude_booking_id=None):
    start,end,_=_period(p,{})
    rows=c.execute("""SELECT t.id,t.payload_json FROM transaction_records t
      JOIN jobs j ON j.id=t.job_id JOIN agents a ON a.id=j.agent_id
      WHERE t.module='booking' AND UPPER(t.status) NOT IN ('CANCELLED','CLOSED')
        AND a.code=?""",(p.get("agent_code") or ctx.get("agent_code"),)).fetchall()
    total=0.0
    kind=str(p.get("limit_type") or "").upper()
    for r in rows:
        if exclude_booking_id and r["id"]==exclude_booking_id: continue
        f=_payload(r);d=str(f.get("Booking Date") or "")[:10]
        if d and not (start<=d<=end): continue
        if not _matches(p,ctx,f): continue
        if kind=="BOOKING_COUNT": total+=1
        elif kind=="CONTAINER_COUNT": total+=_qty(f)
        elif kind=="TEU": total+=_teu(f)
    return total

def booking_rule_check(c,ctx:dict[str,Any],fields:dict[str,Any],exclude_booking_id=None):
    day=str(fields.get("Booking Date") or _today())[:10]
    out={"errors":[],"warnings":[],"matched_relations":[],"matched_network":[],"volume":[]}

    relations=_configuration_rows(c,"AGENT_RELATION")
    network=_configuration_rows(c,"AGENT_NETWORK")
    restrictions=_configuration_rows(c,"AGENT_VOLUME_RESTRICTION")

    # Existing booking/job agent stays authoritative. Optional configured relations only constrain
    # explicitly selected operational agents; they never create a second agent master or auto-change a booking.
    selected=[
      fields.get("Origin / Sending Agent"),fields.get("POL Agent"),
      fields.get("POT Agent"),fields.get("TS Agent (1)"),fields.get("TS Agent (2)"),
      fields.get("Destination / Receiving Agent"),fields.get("POD Agent")
    ]
    selected={str(x) for x in selected if x not in (None,"")}
    if selected and relations:
        allowed=set()
        for r in relations:
            p=r["payload"]
            if _active_date(p,day) and _matches(p,ctx,fields):
                allowed.add(str(p.get("related_party_code") or p.get("agent_code") or ""))
                out["matched_relations"].append(r["record_key"])
        blocked=sorted(x for x in selected if x not in allowed and x!=str(ctx.get("agent_code") or ""))
        if blocked: out["errors"].append("AGENT_RELATION_NOT_AUTHORIZED:"+",".join(blocked))

    if selected and network:
        covered=set()
        for r in network:
            p=r["payload"]
            if _active_date(p,day) and _matches(p,ctx,fields):
                covered.add(str(p.get("agent_code") or p.get("related_party_code") or ""))
                out["matched_network"].append(r["record_key"])
        blocked=sorted(x for x in selected if x not in covered and x!=str(ctx.get("agent_code") or ""))
        if blocked: out["errors"].append("AGENT_NETWORK_NOT_AUTHORIZED:"+",".join(blocked))

    for r in restrictions:
        p=r["payload"];kind=str(p.get("limit_type") or "").upper()
        if kind not in LIMIT_TYPES or not _active_date(p,day) or not _matches(p,ctx,fields): continue
        try:limit=float(p.get("allowed_volume"))
        except Exception:continue
        used=_used(c,p,ctx,exclude_booking_id)
        requested=1 if kind=="BOOKING_COUNT" else (_qty(fields) if kind=="CONTAINER_COUNT" else _teu(fields))
        remaining=max(0,limit-used)
        projected=used+requested
        detail={"record_key":r["record_key"],"limit_type":kind,"allowed":limit,"used":used,"requested":requested,"remaining_before":remaining,"projected":projected,"enforcement":str(p.get("enforcement") or "WARN").upper()}
        out["volume"].append(detail)
        if projected>limit:
            code=f"AGENT_VOLUME_{kind}_EXCEEDED"
            if detail["enforcement"]=="BLOCK":out["errors"].append(code)
            else:out["warnings"].append(code)
    return out

def assert_booking_rules(c,ctx,fields,exclude_booking_id=None):
    result=booking_rule_check(c,ctx,fields,exclude_booking_id)
    if result["errors"]:
        raise HTTPException(422,{"code":"AGENT_SETUP_RULE_BLOCK","errors":result["errors"],"warnings":result["warnings"],"volume":result["volume"]})
    return result

@router.get("/relations")
def relations(x_role:str=Header("VIEWER")):
    c=connect()
    try:
        rows=_configuration_rows(c,"AGENT_RELATION")
        return {"source":"md_records:configuration","new_agent_model":False,"allowed_relation_types":sorted(RELATION_TYPES),"count":len(rows),"records":rows}
    finally:c.close()

@router.get("/volume-restrictions")
def volume_restrictions(x_role:str=Header("VIEWER")):
    c=connect()
    try:
        rows=_configuration_rows(c,"AGENT_VOLUME_RESTRICTION")
        return {"source":"md_records:configuration","server_side_enforcement":True,"limit_types":sorted(LIMIT_TYPES),"count":len(rows),"records":rows}
    finally:c.close()

@router.get("/network")
def network(x_role:str=Header("VIEWER")):
    c=connect()
    try:
        rows=_configuration_rows(c,"AGENT_NETWORK")
        return {"source":"agents+common-party+location+configuration","equipment_network_changed":False,"network_types":sorted(NETWORK_TYPES),"count":len(rows),"records":rows}
    finally:c.close()

@router.get("/booking/{job_ref}/eligibility")
def booking_eligibility(job_ref:str,x_role:str=Header("VIEWER")):
    c=connect()
    try:
        j=c.execute("""SELECT j.id,j.job_ref,j.pol,j.pod,a.code agent_code,b.booking_ref,t.id booking_tx_id,t.payload_json
          FROM jobs j JOIN agents a ON a.id=j.agent_id JOIN bookings b ON b.id=j.booking_id
          LEFT JOIN transaction_records t ON t.job_id=j.id AND t.module='booking'
          WHERE j.job_ref=? ORDER BY t.id LIMIT 1""",(job_ref,)).fetchone()
        if not j:raise HTTPException(404,{"code":"JOB_NOT_FOUND"})
        ctx=_row(j);fields=_payload(j)
        result=booking_rule_check(c,ctx,fields,ctx.get("booking_tx_id"))
        return {"job_ref":job_ref,"booking_ref":ctx["booking_ref"],"eligible":not result["errors"],**result}
    finally:c.close()
