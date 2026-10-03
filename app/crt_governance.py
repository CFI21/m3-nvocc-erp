from __future__ import annotations

import datetime
import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .db import connect, tx

router=APIRouter(prefix="/api/crt",tags=["crt-governance"])

STATES={"DRAFT","SUBMITTED","UNDER_REVIEW","APPROVED","REJECTED","APPLIED","CLOSED"}
TARGET_MODULES={"trt","export-trt","import-trt","transshipment-trt"}
POST_APPROVAL_STATUSES={"APPROVED","RELEASED","ISSUED","COMPLETED","CLOSED"}

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def j(x): return json.dumps(x,sort_keys=True,separators=(",",":"))

def human(user_id:Optional[str],actor_type:str,mfa:str|bool=False):
    if actor_type.upper()!="HUMAN": raise HTTPException(403,{"code":"HUMAN_ONLY"})
    if not user_id or user_id.upper().startswith(("AI","SERVICE","BOT")):
        raise HTTPException(403,{"code":"VALID_HUMAN_USER_ID_REQUIRED"})
    return user_id

def require_mfa(v:str|bool):
    if str(v).lower() not in {"1","true","yes","verified"}:
        raise HTTPException(403,{"code":"MFA_REQUIRED"})

def audit(c,ticket_ref,actor_user_id,action,before=None,after=None,reason=None):
    c.execute(
      """INSERT INTO crt_audit(event_ref,ticket_ref,ts,actor_user_id,action,before_json,after_json,reason)
         VALUES(?,?,?,?,?,?,?,?)""",
      ("CRTA-"+uuid.uuid4().hex.upper(),ticket_ref,now(),actor_user_id,action,j(before) if before is not None else None,j(after) if after is not None else None,reason)
    )

def ticket(c,ref):
    r=c.execute("SELECT * FROM change_request_tickets WHERE ticket_ref=?",(ref,)).fetchone()
    if not r: raise HTTPException(404,{"code":"CRT_NOT_FOUND"})
    return dict(r)

def required_approval_roles(t:dict)->list[str]:
    roles=["AUTHORIZED_APPROVER"]
    if t["target_module"]=="transshipment-trt":
        roles += ["TS_BRANCH_MANAGER","TS_FINANCE"]
    if t["closed_period"]:
        roles += ["FINANCE_MANAGER","CFO"]
    if t["tax_filed"]:
        roles += ["CFO","EXTERNAL_ADVISOR"]
    return list(dict.fromkeys(roles))

def approvals(c,ticket_ref):
    return [dict(x) for x in c.execute(
        "SELECT * FROM crt_approvals WHERE ticket_ref=? ORDER BY id",(ticket_ref,)
    )]

def approvals_complete(c,t:dict)->bool:
    approved={x["approval_role"] for x in approvals(c,t["ticket_ref"]) if x["decision"]=="APPROVED"}
    return set(required_approval_roles(t)) <= approved

class CreateTicket(BaseModel):
    target_module:str
    target_record_id:int
    change_payload:dict[str,Any]=Field(default_factory=dict)
    reason:str=Field(min_length=3,max_length=1000)
    financial_posted:bool=False
    closed_period:bool=False
    tax_filed:bool=False

class Decision(BaseModel):
    role:str
    decision:str
    comment:Optional[str]=None
    external_advisor_ref:Optional[str]=None
    evidence_ref:Optional[str]=None

class ApplyBody(BaseModel):
    application_mode:str="STANDARD"
    reversal_ref:Optional[str]=None
    corrected_document_ref:Optional[str]=None
    repost_ref:Optional[str]=None

@router.post("",status_code=201)
def create_ticket(
    body:CreateTicket,
    x_user_id:Optional[str]=Header(None,alias="X-User-Id"),
    x_actor_type:str=Header("HUMAN",alias="X-Actor-Type"),
):
    maker=human(x_user_id,x_actor_type)
    module=body.target_module.strip().lower()
    if module not in TARGET_MODULES: raise HTTPException(422,{"code":"CRT_TARGET_MODULE_INVALID","allowed":sorted(TARGET_MODULES)})
    c=connect();tx(c)
    try:
        target=c.execute("SELECT id,status,version,payload_json FROM transaction_records WHERE id=? AND module=?",(body.target_record_id,module)).fetchone()
        if not target: raise HTTPException(404,{"code":"CRT_TARGET_RECORD_NOT_FOUND"})
        ref="CRT-"+uuid.uuid4().hex[:12].upper()
        c.execute(
          """INSERT INTO change_request_tickets(
             ticket_ref,target_module,target_record_id,maker_user_id,state,change_payload_json,reason,
             financial_posted,closed_period,tax_filed,target_version,created_at,updated_at,version
          ) VALUES(?,?,?,?, 'DRAFT',?,?,?,?,?,?,?, ?,1)""",
          (ref,module,body.target_record_id,maker,j(body.change_payload),body.reason,
           int(body.financial_posted),int(body.closed_period),int(body.tax_filed),target["version"],now(),now())
        )
        out=ticket(c,ref);audit(c,ref,maker,"CREATE",None,out,body.reason);c.execute("COMMIT");return out
    except HTTPException:
        c.execute("ROLLBACK");raise
    finally:c.close()

@router.get("/{ticket_ref}")
def get_ticket(ticket_ref:str):
    c=connect()
    try:
        t=ticket(c,ticket_ref)
        return {"ticket":t,"required_approvals":required_approval_roles(t),"approvals":approvals(c,ticket_ref)}
    finally:c.close()

@router.post("/{ticket_ref}/submit")
def submit(
    ticket_ref:str,
    x_user_id:Optional[str]=Header(None,alias="X-User-Id"),
    x_actor_type:str=Header("HUMAN",alias="X-Actor-Type"),
):
    uid=human(x_user_id,x_actor_type);c=connect();tx(c)
    try:
        t=ticket(c,ticket_ref)
        if t["maker_user_id"]!=uid: raise HTTPException(403,{"code":"MAKER_ONLY_SUBMIT"})
        if t["state"]!="DRAFT": raise HTTPException(409,{"code":"CRT_INVALID_TRANSITION"})
        c.execute("UPDATE change_request_tickets SET state='SUBMITTED',updated_at=?,version=version+1 WHERE ticket_ref=?",(now(),ticket_ref))
        after=ticket(c,ticket_ref);audit(c,ticket_ref,uid,"SUBMIT",t,after,t["reason"]);c.execute("COMMIT");return after
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.post("/{ticket_ref}/start-review")
def start_review(
    ticket_ref:str,
    x_user_id:Optional[str]=Header(None,alias="X-User-Id"),
    x_actor_type:str=Header("HUMAN",alias="X-Actor-Type"),
):
    uid=human(x_user_id,x_actor_type);c=connect();tx(c)
    try:
        t=ticket(c,ticket_ref)
        if uid==t["maker_user_id"]: raise HTTPException(403,{"code":"MAKER_CHECKER_SAME_USER"})
        if t["state"]!="SUBMITTED": raise HTTPException(409,{"code":"CRT_INVALID_TRANSITION"})
        c.execute("UPDATE change_request_tickets SET state='UNDER_REVIEW',updated_at=?,version=version+1 WHERE ticket_ref=?",(now(),ticket_ref))
        after=ticket(c,ticket_ref);audit(c,ticket_ref,uid,"START_REVIEW",t,after);c.execute("COMMIT");return after
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.post("/{ticket_ref}/decision")
def decision(
    ticket_ref:str,
    body:Decision,
    x_user_id:Optional[str]=Header(None,alias="X-User-Id"),
    x_actor_type:str=Header("HUMAN",alias="X-Actor-Type"),
    x_mfa_verified:str=Header("false",alias="X-MFA-Verified"),
):
    uid=human(x_user_id,x_actor_type);require_mfa(x_mfa_verified)
    role=body.role.strip().upper();dec=body.decision.strip().upper()
    if dec not in {"APPROVED","REJECTED"}: raise HTTPException(422,{"code":"CRT_DECISION_INVALID"})
    c=connect();tx(c)
    try:
        t=ticket(c,ticket_ref)
        if t["state"]!="UNDER_REVIEW": raise HTTPException(409,{"code":"CRT_INVALID_TRANSITION"})
        if uid==t["maker_user_id"]: raise HTTPException(403,{"code":"MAKER_CHECKER_SAME_USER"})
        if role not in required_approval_roles(t): raise HTTPException(403,{"code":"CRT_APPROVAL_ROLE_NOT_REQUIRED"})
        if role=="EXTERNAL_ADVISOR":
            if not body.external_advisor_ref or not body.evidence_ref:
                raise HTTPException(422,{"code":"EXTERNAL_ADVISOR_EVIDENCE_REQUIRED"})
        c.execute(
          """INSERT INTO crt_approvals(ticket_ref,approval_role,approver_user_id,approver_type,external_advisor_ref,decision,mfa_verified,comment,evidence_ref,decided_at)
             VALUES(?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(ticket_ref,approval_role) DO UPDATE SET
               approver_user_id=excluded.approver_user_id,approver_type=excluded.approver_type,
               external_advisor_ref=excluded.external_advisor_ref,decision=excluded.decision,
               mfa_verified=excluded.mfa_verified,comment=excluded.comment,evidence_ref=excluded.evidence_ref,decided_at=excluded.decided_at""",
          (ticket_ref,role,None if role=="EXTERNAL_ADVISOR" else uid,
           "EXTERNAL_ADVISOR" if role=="EXTERNAL_ADVISOR" else "HUMAN_USER",
           body.external_advisor_ref,dec,1,body.comment,body.evidence_ref,now())
        )
        if dec=="REJECTED":
            c.execute("UPDATE change_request_tickets SET state='REJECTED',updated_at=?,version=version+1 WHERE ticket_ref=?",(now(),ticket_ref))
        else:
            t2=ticket(c,ticket_ref)
            if approvals_complete(c,t2):
                c.execute("UPDATE change_request_tickets SET state='APPROVED',updated_at=?,version=version+1 WHERE ticket_ref=?",(now(),ticket_ref))
        after=ticket(c,ticket_ref);audit(c,ticket_ref,uid,"DECISION",t,after,body.comment);c.execute("COMMIT")
        return {"ticket":after,"required_approvals":required_approval_roles(after),"approvals":approvals(c,ticket_ref)}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.post("/{ticket_ref}/apply")
def apply(
    ticket_ref:str,
    body:ApplyBody,
    x_user_id:Optional[str]=Header(None,alias="X-User-Id"),
    x_actor_type:str=Header("HUMAN",alias="X-Actor-Type"),
):
    uid=human(x_user_id,x_actor_type);c=connect();tx(c)
    try:
        t=ticket(c,ticket_ref)
        if t["state"]!="APPROVED": raise HTTPException(409,{"code":"CRT_NOT_APPROVED"})
        target=c.execute("SELECT * FROM transaction_records WHERE id=? AND module=?",(t["target_record_id"],t["target_module"])).fetchone()
        if not target: raise HTTPException(404,{"code":"CRT_TARGET_RECORD_NOT_FOUND"})
        if target["version"]!=t["target_version"]: raise HTTPException(409,{"code":"CRT_TARGET_VERSION_CHANGED"})
        if t["financial_posted"]:
            if body.application_mode!="REVERSAL_CORRECTED_REPOST" or not all([body.reversal_ref,body.corrected_document_ref,body.repost_ref]):
                raise HTTPException(422,{"code":"FINANCIAL_REVERSAL_CORRECTION_REPOST_REQUIRED"})
        payload=json.loads(target["payload_json"]);change=json.loads(t["change_payload_json"]);payload.update(change)
        c.execute("UPDATE transaction_records SET payload_json=?,version=version+1,updated_at=? WHERE id=? AND version=?",(j(payload),now(),target["id"],target["version"]))
        c.execute(
          """UPDATE change_request_tickets SET state='APPLIED',applied_by_user_id=?,application_mode=?,
             reversal_ref=?,corrected_document_ref=?,repost_ref=?,updated_at=?,version=version+1 WHERE ticket_ref=?""",
          (uid,body.application_mode,body.reversal_ref,body.corrected_document_ref,body.repost_ref,now(),ticket_ref)
        )
        after=ticket(c,ticket_ref);audit(c,ticket_ref,uid,"APPLY",t,after,t["reason"]);c.execute("COMMIT");return after
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()

@router.post("/{ticket_ref}/close")
def close(
    ticket_ref:str,
    x_user_id:Optional[str]=Header(None,alias="X-User-Id"),
    x_actor_type:str=Header("HUMAN",alias="X-Actor-Type"),
):
    uid=human(x_user_id,x_actor_type);c=connect();tx(c)
    try:
        t=ticket(c,ticket_ref)
        if t["state"]!="APPLIED": raise HTTPException(409,{"code":"CRT_INVALID_TRANSITION"})
        c.execute("UPDATE change_request_tickets SET state='CLOSED',updated_at=?,version=version+1 WHERE ticket_ref=?",(now(),ticket_ref))
        after=ticket(c,ticket_ref);audit(c,ticket_ref,uid,"CLOSE",t,after);c.execute("COMMIT");return after
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()
