from __future__ import annotations

import datetime
import json
import os
import uuid
from typing import Any, Optional

from fastapi import HTTPException

from .db import connect, tx


RELEASE_STATES={
    "BLOCKED","PENDING","CONDITIONAL","PARTIALLY_RELEASED","RELEASED",
    "RELEASED_WITH_POST_DELIVERY_HOLD","REVOKED","EXPIRED"
}
CONDITION_TYPES={
    "BG","LOI","CREDIT_OVERRIDE","ORIGINAL_WAIVER",
    "MANAGEMENT_APPROVAL","CUSTOMS_CONDITIONAL"
}
TERMINAL_INELIGIBLE_STATES={
    "LOST","SEIZED","TOTAL_LOSS","SOLD","OFF_HIRE","OFF_HIRED","SCRAPPED",
    "PARTNER_RETURNED","AGENT_RETURNED","SOC_RELEASED"
}
PRE_DELIVERY_STATES={
    "AVAILABLE","RESERVED","RELEASED","EMPTY_PICKUP","STUFFED","GATE_IN",
    "LOADED","IN_TRANSIT","TRANSSHIPMENT","DISCHARGED","GATE_OUT_FULL"
}
POST_DELIVERY_STATES={"DELIVERED","EMPTY_RETURN","INSPECTION","REPAIR"}


def enabled() -> bool:
    return os.getenv("M3_RELEASE_GOVERNANCE_ENABLED","false").strip().lower() in {"1","true","yes","on"}


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_dt(v: str | None) -> datetime.datetime | None:
    if not v:
        return None
    d=datetime.datetime.fromisoformat(str(v).replace("Z","+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def normalize(v: str | None) -> str:
    return (v or "").strip().upper().replace(" ","_")


def require_human(user_id:str,actor_type:str="HUMAN") -> None:
    if actor_type.upper()!="HUMAN":
        raise HTTPException(403,{"code":"HUMAN_ONLY"})
    if not user_id or user_id.upper().startswith(("AI","BOT","SERVICE")):
        raise HTTPException(403,{"code":"VALID_HUMAN_USER_ID_REQUIRED"})


def _event(c,release_ref,container_no,from_state,to_state,action,actor_user_id,reason=None,detail=None):
    ref="REL-EVT-"+uuid.uuid4().hex.upper()
    c.execute(
        """INSERT INTO nvocc_release_events(
           event_ref,release_ref,container_no,from_state,to_state,action,actor_user_id,
           reason,detail_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (ref,release_ref,container_no,from_state,to_state,action,actor_user_id,reason,
         json.dumps(detail or {},sort_keys=True,separators=(",",":")),now())
    )
    return ref


def _header(c,release_ref):
    row=c.execute("SELECT * FROM nvocc_release_controls WHERE release_ref=?",(release_ref,)).fetchone()
    if not row:
        raise HTTPException(404,{"code":"RELEASE_CONTROL_NOT_FOUND"})
    return dict(row)


def _latest_do_payload(c,job_id):
    row=c.execute(
        """SELECT payload_json FROM transaction_records
           WHERE job_id=? AND module='delivery-order'
           ORDER BY id DESC LIMIT 1""",(job_id,)
    ).fetchone()
    if not row:
        return {}
    try:return json.loads(row["payload_json"] or "{}")
    except Exception:return {}


def _container(c,container_no):
    r=c.execute("SELECT * FROM containers WHERE container_no=?",(container_no,)).fetchone()
    if not r:
        raise HTTPException(404,{"code":"CONTAINER_NOT_FOUND","container_no":container_no})
    return dict(r)


def _active_holds(c,job_id):
    return [str(x["code"]).upper() for x in c.execute(
        "SELECT code FROM workflow_holds WHERE job_id=? AND active=1 ORDER BY code",(job_id,)
    )]


def evaluate_prerequisites(
    *,
    job_ref:str,
    hbl_no:str,
    container_no:str,
    condition_type:str|None=None,
    condition_valid_until:str|None=None,
) -> dict[str,Any]:
    c=connect()
    try:
        j=c.execute(
            """SELECT j.id,w.documentation_status,w.customs_status,w.release_status,
                      f.payment_status,f.outstanding,f.credit_hold
               FROM jobs j
               JOIN workflow_states w ON w.job_id=j.id
               JOIN finance_states f ON f.job_id=j.id
               WHERE j.job_ref=?""",(job_ref,)
        ).fetchone()
        if not j: raise HTTPException(404,{"code":"JOB_NOT_FOUND"})
        j=dict(j)
        h=c.execute("SELECT * FROM bills WHERE bill_no=? AND kind='HBL'",(hbl_no,)).fetchone()
        if not h or h["job_id"]!=j["id"]:
            raise HTTPException(409,{"code":"HBL_JOB_SCOPE_MISMATCH"})
        con=_container(c,container_no)
        if con["job_id"]!=j["id"]:
            raise HTTPException(409,{"code":"CONTAINER_JOB_SCOPE_MISMATCH"})

        condition=normalize(condition_type)
        valid_condition=False
        if condition:
            if condition not in CONDITION_TYPES:
                raise HTTPException(422,{"code":"RELEASE_CONDITION_TYPE_INVALID"})
            expiry=parse_dt(condition_valid_until)
            valid_condition=bool(expiry and expiry>=datetime.datetime.now(datetime.timezone.utc))

        do=_latest_do_payload(c,j["id"])
        holds=_active_holds(c,j["id"])
        bl_ok=normalize(h["status"]) in {"ISSUED","RELEASED","APPROVED"}
        docs_ok=normalize(j["documentation_status"]) not in {"PENDING","BLOCKED","MISSING","SI_PENDING","VGM_MISSING"}
        customs_raw=normalize(j["customs_status"]) in {"CLEARED","PASS","APPROVED","NOT_REQUIRED"}
        customs_ok=customs_raw or (condition=="CUSTOMS_CONDITIONAL" and valid_condition)
        payment_ok=normalize(j["payment_status"]) in {"CLEARED","PAID","APPROVED"} and float(j["outstanding"] or 0)<=0
        credit_ok=(not int(j["credit_hold"] or 0)) or (condition=="CREDIT_OVERRIDE" and valid_condition)

        original=normalize(do.get("Original BL Status"))
        telex=str(do.get("Telex Release","")).strip().lower() in {"yes","true","1","released","approved"}
        surrender_raw=telex or original in {"SURRENDERED","RECEIVED","NOT_REQUIRED"}
        surrender_ok=surrender_raw or (condition=="ORIGINAL_WAIVER" and valid_condition)

        blocked_hold_codes=[
            x for x in holds
            if x.startswith(("LEGAL","COMPLIANCE","DOCUMENT","CUSTOMS","RELEASE","FRAUD","SANCTION"))
        ]
        state=normalize(con.get("journey_state") or con.get("equipment_status") or "AVAILABLE")
        container_ok=state not in TERMINAL_INELIGIBLE_STATES and not bool(con.get("damage_hold",0)) and not bool(con.get("inspection_hold",0))

        checks={
            "hbl_issued":bl_ok,
            "document_authority":docs_ok,
            "customs_cleared":customs_ok,
            "payment_cleared":payment_ok,
            "credit_clear":credit_ok,
            "surrender_or_telex":surrender_ok,
            "no_legal_compliance_document_hold":not blocked_hold_codes,
            "container_eligible":container_ok,
        }
        return {
            "job_ref":job_ref,
            "hbl_no":hbl_no,
            "container_no":container_no,
            "container_state":state,
            "checks":checks,
            "blocking_reasons":[k for k,v in checks.items() if not v],
            "ready":all(checks.values()),
            "condition_type":condition or None,
            "condition_valid":valid_condition,
            "active_holds":holds,
            "negative_margin_blocks_release":False,
        }
    finally:c.close()


def _summary_state(c,release_ref):
    rows=[dict(x) for x in c.execute(
        "SELECT * FROM nvocc_release_container_control WHERE release_ref=? ORDER BY container_no",
        (release_ref,)
    )]
    states={x["status"] for x in rows}
    if not rows:return "PENDING"
    if "RELEASED_WITH_POST_DELIVERY_HOLD" in states:return "RELEASED_WITH_POST_DELIVERY_HOLD"
    released=sum(x["status"] in {"RELEASED","CONDITIONAL","RELEASED_WITH_POST_DELIVERY_HOLD"} for x in rows)
    if released==len(rows):return "RELEASED" if "CONDITIONAL" not in states else "CONDITIONAL"
    if released>0:return "PARTIALLY_RELEASED"
    if "BLOCKED" in states:return "BLOCKED"
    if "REVOKED" in states:return "REVOKED"
    if "EXPIRED" in states:return "EXPIRED"
    return "PENDING"


def upsert_release_container(
    *,
    release_ref:str,
    container_no:str,
    actor_user_id:str,
    actor_type:str="HUMAN",
    action:str="EVALUATE",
    condition_type:str|None=None,
    condition_reason:str|None=None,
    condition_valid_until:str|None=None,
    approver_user_id:str|None=None,
) -> dict[str,Any]:
    if not enabled():
        raise HTTPException(503,{"code":"RELEASE_GOVERNANCE_DISABLED"})
    require_human(actor_user_id,actor_type)
    action=normalize(action)
    if action not in {"EVALUATE","RELEASE","REVOKE","EXPIRE","HOLD"}:
        raise HTTPException(422,{"code":"RELEASE_ACTION_INVALID"})

    c=connect();tx(c)
    try:
        header=_header(c,release_ref)
        con=_container(c,container_no)
        j=c.execute("SELECT id FROM jobs WHERE job_ref=?",(header["job_ref"],)).fetchone()
        if not j or con["job_id"]!=j["id"]:
            raise HTTPException(409,{"code":"CONTAINER_RELEASE_SCOPE_MISMATCH"})
        existing=c.execute(
            "SELECT * FROM nvocc_release_container_control WHERE release_ref=? AND container_no=?",
            (release_ref,container_no)
        ).fetchone()
        before_state=existing["status"] if existing else "PENDING"

        condition=normalize(condition_type)
        if condition:
            if condition not in CONDITION_TYPES:
                raise HTTPException(422,{"code":"RELEASE_CONDITION_TYPE_INVALID"})
            require_human(approver_user_id or "",actor_type)
            if approver_user_id==actor_user_id:
                raise HTTPException(403,{"code":"MAKER_CHECKER_SAME_USER"})
            if not condition_reason or not condition_valid_until:
                raise HTTPException(422,{"code":"CONDITIONAL_RELEASE_REASON_VALIDITY_REQUIRED"})

        pre=evaluate_prerequisites(
            job_ref=header["job_ref"],hbl_no=header["hbl_no"],container_no=container_no,
            condition_type=condition or None,condition_valid_until=condition_valid_until,
        )

        state=normalize(con.get("journey_state") or con.get("equipment_status") or "AVAILABLE")
        delivered=state in POST_DELIVERY_STATES
        if action=="RELEASE":
            if not pre["ready"]:
                new_state="BLOCKED"
            elif condition:
                new_state="CONDITIONAL"
            else:
                new_state="RELEASED"
        elif action=="REVOKE":
            new_state="RELEASED_WITH_POST_DELIVERY_HOLD" if delivered else "REVOKED"
        elif action=="HOLD":
            new_state="RELEASED_WITH_POST_DELIVERY_HOLD" if delivered else "REVOKED"
        elif action=="EXPIRE":
            new_state="EXPIRED"
        else:
            new_state="PENDING" if pre["ready"] else "BLOCKED"

        if existing:
            c.execute(
                """UPDATE nvocc_release_container_control SET
                   status=?,condition_type=?,condition_reason=?,condition_valid_until=?,
                   condition_approver_user_id=?,blocking_reasons_json=?,delivered_at_action=?,
                   updated_at=?,version=version+1
                   WHERE release_ref=? AND container_no=?""",
                (
                    new_state,condition or None,condition_reason,condition_valid_until,approver_user_id,
                    json.dumps(pre["blocking_reasons"],sort_keys=True),int(delivered),now(),release_ref,container_no
                )
            )
        else:
            c.execute(
                """INSERT INTO nvocc_release_container_control(
                   release_ref,container_no,status,condition_type,condition_reason,condition_valid_until,
                   condition_approver_user_id,blocking_reasons_json,delivered_at_action,created_at,updated_at,version
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    release_ref,container_no,new_state,condition or None,condition_reason,condition_valid_until,
                    approver_user_id,json.dumps(pre["blocking_reasons"],sort_keys=True),int(delivered),now(),now()
                )
            )

        _event(c,release_ref,container_no,before_state,new_state,action,actor_user_id,condition_reason,{
            "prerequisites":pre["checks"],"condition_type":condition or None,
            "post_delivery_hold":new_state=="RELEASED_WITH_POST_DELIVERY_HOLD",
        })
        summary=_summary_state(c,release_ref)
        c.execute(
            "UPDATE nvocc_release_controls SET status=?,release_by=?,updated_at=?,version=version+1 WHERE release_ref=?",
            (summary,actor_user_id,now(),release_ref)
        )
        if j:
            c.execute(
                "UPDATE workflow_states SET release_status=?,version=version+1 WHERE job_id=?",
                (summary,j["id"])
            )
        c.execute("COMMIT")
        return {
            "release_ref":release_ref,"container_no":container_no,"status":new_state,
            "summary_status":summary,"prerequisites":pre,
        }
    except HTTPException:
        c.execute("ROLLBACK");raise
    finally:c.close()


def delivery_order_eligible(*,job_ref:str,hbl_no:str,container_no:str) -> dict[str,Any]:
    if not enabled():
        return {"eligible":True,"governance_enabled":False}
    c=connect()
    try:
        rows=[dict(x) for x in c.execute(
            """SELECT rc.release_ref,cc.status
               FROM nvocc_release_controls rc
               JOIN nvocc_release_container_control cc ON cc.release_ref=rc.release_ref
               WHERE rc.job_ref=? AND rc.hbl_no=? AND cc.container_no=?
               ORDER BY cc.id DESC""",
            (job_ref,hbl_no,container_no)
        )]
        eligible=bool(rows and rows[0]["status"] in {"RELEASED","CONDITIONAL","RELEASED_WITH_POST_DELIVERY_HOLD"})
        return {
            "eligible":eligible,
            "governance_enabled":True,
            "release_ref":rows[0]["release_ref"] if rows else None,
            "release_status":rows[0]["status"] if rows else "NONE",
        }
    finally:c.close()


def assert_delivery_order_eligible(*,job_ref:str,hbl_no:str,container_no:str) -> None:
    r=delivery_order_eligible(job_ref=job_ref,hbl_no=hbl_no,container_no=container_no)
    if r["governance_enabled"] and not r["eligible"]:
        raise HTTPException(409,{"code":"DELIVERY_ORDER_CONTAINER_NOT_RELEASED","detail":r})
