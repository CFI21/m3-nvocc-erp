from __future__ import annotations

import datetime
import json
import os
import uuid
from typing import Any, Iterable, Optional

from fastapi import HTTPException

from .db import connect, tx


MODES={"STANDARD","CORRECTION","OVERRIDE","EXCEPTION_RESOLUTION"}
MANAGER_ROLES={"SUPER_ADMIN","ADMIN","EQUIPMENT_MANAGER","MANAGEMENT"}
SERIOUS_EXCEPTION_STATES={"DAMAGE_HOLD","CUSTOMS_HOLD","REPAIR","LOST","SEIZED","TOTAL_LOSS","SOLD","OFF_HIRE"}


def enabled() -> bool:
    return os.getenv("M3_STATE_TRANSITION_GOVERNANCE_ENABLED","false").strip().lower() in {"1","true","yes","on"}


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_dt(v: str | None) -> datetime.datetime | None:
    if not v:
        return None
    d=datetime.datetime.fromisoformat(str(v).replace("Z","+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def normalize(v: str | None) -> str:
    return (v or "").strip().upper().replace(" ","_")


def _require_human(user_id: str, actor_type: str) -> None:
    if actor_type.upper()!="HUMAN":
        raise HTTPException(403,{"code":"HUMAN_ONLY"})
    if not user_id or user_id.upper().startswith(("AI","BOT","SERVICE")):
        raise HTTPException(403,{"code":"VALID_HUMAN_USER_ID_REQUIRED"})


def _unique_humans(values: Iterable[str]) -> list[str]:
    out=[]
    for v in values:
        x=(v or "").strip()
        if x and x not in out:
            out.append(x)
    return out


def _append_event(
    conn,
    *,
    subject_type:str,
    subject_ref:str,
    from_state:str,
    to_state:str,
    mode:str,
    actor_user_id:str,
    actor_role:str,
    reason:str|None,
    evidence_refs:list[str],
    approver_user_ids:list[str],
    source_event_ref:str|None,
    financial_posted:bool,
    outcome:str,
) -> str:
    ref="STG-"+uuid.uuid4().hex.upper()
    conn.execute(
      """INSERT INTO state_transition_events(
         event_ref,subject_type,subject_ref,from_state,to_state,mode,actor_user_id,actor_role,
         reason,evidence_json,approver_user_ids_json,source_event_ref,financial_posted,outcome,created_at
         ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (
        ref,subject_type,subject_ref,from_state,to_state,mode,actor_user_id,actor_role,
        reason,json.dumps(evidence_refs,sort_keys=True),json.dumps(approver_user_ids,sort_keys=True),
        source_event_ref,int(financial_posted),outcome,now(),
      )
    )
    return ref


def authorize_container_transition(
    *,
    container_no:str,
    from_state:str,
    to_state:str,
    mode:str="STANDARD",
    actor_user_id:str,
    actor_role:str,
    actor_type:str="HUMAN",
    reason:str|None=None,
    evidence_refs:Optional[list[str]]=None,
    approver_user_ids:Optional[list[str]]=None,
    source_event_ref:str|None=None,
    original_transition_at:str|None=None,
    financial_posted:bool=False,
    correction_window_minutes:int=15,
    connection=None,
) -> dict[str,Any]:
    if not enabled():
        raise HTTPException(503,{"code":"STATE_TRANSITION_GOVERNANCE_DISABLED"})

    frm=normalize(from_state)
    to=normalize(to_state)
    mode=normalize(mode)
    if mode not in MODES:
        raise HTTPException(422,{"code":"TRANSITION_MODE_INVALID"})
    if mode in {"CORRECTION","OVERRIDE","EXCEPTION_RESOLUTION"}:
        _require_human(actor_user_id,actor_type)

    evidence=_unique_humans(evidence_refs or [])
    approvers=_unique_humans(approver_user_ids or [])

    own_conn=connection is None
    c=connection or connect()
    if own_conn: tx(c)
    try:
        allowed=False

        if financial_posted:
            _append_event(
                c,subject_type="CONTAINER",subject_ref=container_no,from_state=frm,to_state=to,mode=mode,
                actor_user_id=actor_user_id,actor_role=actor_role,reason=reason,evidence_refs=evidence,
                approver_user_ids=approvers,source_event_ref=source_event_ref,financial_posted=True,outcome="BLOCKED",
            )
            if own_conn:c.execute("COMMIT")
            raise HTTPException(409,{"code":"FINANCIAL_POSTED_REVERSAL_REQUIRED"})

        if mode=="STANDARD":
            from .clx071_container_journey import validate_transition as validate_container_transition
            allowed=validate_container_transition(frm,to)
            if not allowed:
                _append_event(
                    c,subject_type="CONTAINER",subject_ref=container_no,from_state=frm,to_state=to,mode=mode,
                    actor_user_id=actor_user_id,actor_role=actor_role,reason=reason,evidence_refs=evidence,
                    approver_user_ids=approvers,source_event_ref=source_event_ref,financial_posted=False,outcome="BLOCKED",
                )
                if own_conn:c.execute("COMMIT")
                raise HTTPException(409,{"code":"INVALID_STATE_TRANSITION","from":frm,"to":to})

        elif mode=="CORRECTION":
            if not reason or not source_event_ref or not original_transition_at:
                raise HTTPException(422,{"code":"CORRECTION_REASON_SOURCE_AND_TIME_REQUIRED"})
            original=parse_dt(original_transition_at)
            if not original:
                raise HTTPException(422,{"code":"CORRECTION_ORIGINAL_TIME_INVALID"})
            age=(datetime.datetime.now(datetime.timezone.utc)-original).total_seconds()/60
            if age<0 or age>correction_window_minutes:
                raise HTTPException(409,{"code":"CORRECTION_WINDOW_EXPIRED","window_minutes":correction_window_minutes})
            allowed=True

        elif mode=="OVERRIDE":
            if actor_role.upper() not in MANAGER_ROLES or not reason:
                raise HTTPException(403,{"code":"OVERRIDE_REQUIRES_MANAGER_AND_REASON"})
            allowed=True

        elif mode=="EXCEPTION_RESOLUTION":
            if not reason or not evidence:
                raise HTTPException(422,{"code":"EXCEPTION_RESOLUTION_REASON_EVIDENCE_REQUIRED"})
            if to not in SERIOUS_EXCEPTION_STATES and frm not in SERIOUS_EXCEPTION_STATES:
                raise HTTPException(422,{"code":"SERIOUS_EXCEPTION_STATE_REQUIRED"})
            if len(approvers)<2:
                raise HTTPException(403,{"code":"DUAL_HUMAN_APPROVAL_REQUIRED"})
            if actor_user_id in approvers:
                raise HTTPException(403,{"code":"MAKER_CHECKER_SAME_USER"})
            allowed=True

        ref=_append_event(
            c,subject_type="CONTAINER",subject_ref=container_no,from_state=frm,to_state=to,mode=mode,
            actor_user_id=actor_user_id,actor_role=actor_role,reason=reason,evidence_refs=evidence,
            approver_user_ids=approvers,source_event_ref=source_event_ref,financial_posted=False,outcome="ALLOWED",
        )
        if own_conn:c.execute("COMMIT")
        return {
            "allowed":allowed,
            "event_ref":ref,
            "mode":mode,
            "from":frm,
            "to":to,
            "immutable_history":True,
        }
    except HTTPException:
        if own_conn:
            try:c.execute("ROLLBACK")
            except Exception:pass
        raise
    finally:
        if own_conn:c.close()


def transition_history(subject_ref:str) -> list[dict[str,Any]]:
    c=connect()
    try:
        return [dict(x) for x in c.execute(
            "SELECT * FROM state_transition_events WHERE subject_ref=? ORDER BY id",
            (subject_ref,),
        )]
    finally:c.close()
