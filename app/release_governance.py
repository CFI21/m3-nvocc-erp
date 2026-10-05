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


def resolve_bl_release_authority(c,job_id,hbl_no):
    """Resolve cargo document authority from the authoritative B/L first.

    Delivery-order fields are a release-instruction projection and may be stale;
    they may evidence surrender/telex for an ORIGINAL B/L but never override
    an EXPRESS/SEA WAYBILL classification held on transaction_records:bl.
    """
    row=c.execute(
        """SELECT status,payload_json FROM transaction_records
           WHERE job_id=? AND module='bl'
           ORDER BY id DESC LIMIT 1""",(job_id,)
    ).fetchone()
    bl={}
    bl_tx_status=None
    if row:
        bl_tx_status=row["status"]
        try: bl=json.loads(row["payload_json"] or "{}")
        except Exception: bl={}
    bl_ref=str(bl.get("B/L No.") or bl.get("HBL") or "").strip()
    if bl_ref and hbl_no and bl_ref!=hbl_no:
        bl={}
    release_type=normalize(bl.get("Original / Express"))
    express=release_type in {"EXPRESS","SEA_WAYBILL","SEA_WAY_BILL","SEAWAYBILL","WAYBILL"}
    do=_latest_do_payload(c,job_id)
    original_status=normalize(do.get("Original BL Status"))
    telex=str(do.get("Telex Release","")).strip().lower() in {"yes","true","1","released","approved","authorized"}
    surrendered=original_status in {"SURRENDERED","RECEIVED"}
    not_required=original_status=="NOT_REQUIRED"
    authority_ok=express or telex or surrendered or not_required
    stale_do=bool(express and original_status not in {"","NOT_REQUIRED"})
    return {
        "bl_type":release_type or "OTHER",
        "original_bl_required":not express,
        "original_bl_surrendered":surrendered,
        "telex_release_authorized":telex,
        "express_release_authorized":express,
        "authority_ok":authority_ok,
        "source":"transaction_records:bl + delivery-order release instruction",
        "stale_do_document_fields":stale_do,
        "bl_transaction_status":bl_tx_status,
        "do_original_bl_status":original_status or None,
    }


def _container(c,container_no):
    r=c.execute("SELECT * FROM containers WHERE container_no=?",(container_no,)).fetchone()
    if not r:
        raise HTTPException(404,{"code":"CONTAINER_NOT_FOUND","container_no":container_no})
    return dict(r)


def _active_holds(c,job_id):
    return [str(x["code"]).upper() for x in c.execute(
        "SELECT code FROM workflow_holds WHERE job_id=? AND active=1 ORDER BY code",(job_id,)
    )]


def _container_state(c,con:dict) -> str:
    state=normalize(con.get("journey_state") or con.get("equipment_status"))
    if state:
        return state
    rows=c.execute(
        """SELECT event_id,event_type,status,event_time,detail_json FROM container_events
           WHERE container_id=? ORDER BY event_time DESC,id DESC""",
        (con["id"],)
    ).fetchall()
    current=datetime.datetime.now(datetime.timezone.utc)
    for row in rows:
        event_time=parse_dt(row["event_time"])
        if event_time and event_time>current:
            continue
        try:
            detail=json.loads(row["detail_json"] or "{}")
        except Exception:
            detail={}
        if detail.get("synthetic") is True:
            continue
        return normalize(row["event_type"] or row["status"])
    return "AVAILABLE"

def real_container_release_event(c,container_no:str) -> dict[str,Any]:
    con=_container(c,container_no)
    rows=c.execute(
        """SELECT event_id,event_type,status,event_time,source_module,detail_json
           FROM container_events
           WHERE container_id=? AND (upper(event_type)='RELEASED' OR upper(status)='RELEASED')
           ORDER BY event_time DESC,id DESC""",
        (con["id"],)
    ).fetchall()
    current=datetime.datetime.now(datetime.timezone.utc)
    for row in rows:
        event_time=parse_dt(row["event_time"])
        if not event_time or event_time>current:
            continue
        try:
            detail=json.loads(row["detail_json"] or "{}")
        except Exception:
            detail={}
        if detail.get("synthetic") is True:
            continue
        actor=str(detail.get("actor_user_id") or detail.get("actor") or "").strip()
        if not actor or actor.upper().startswith(("AI","BOT","SERVICE")):
            continue
        return {
            "found":True,
            "event_ref":row["event_id"],
            "event_time":row["event_time"],
            "actor_user_id":actor,
            "source_module":row["source_module"],
            "detail":detail,
        }
    return {"found":False,"event_ref":None,"event_time":None,"actor_user_id":None,"source_module":None,"detail":{}}


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
        release_instruction=str(do.get("Release Instruction") or do.get("Release Type") or "").strip()
        release_valid_until=parse_dt(do.get("Valid Until"))
        release_instruction_ok=bool(release_instruction) and (
            release_valid_until is None or release_valid_until>=datetime.datetime.now(datetime.timezone.utc)
        )
        bl_ok=normalize(h["status"]) in {"ISSUED","RELEASED","APPROVED"}
        docs_ok=normalize(j["documentation_status"]) not in {"PENDING","BLOCKED","MISSING","SI_PENDING","VGM_MISSING"}
        customs_raw=normalize(j["customs_status"]) in {"CLEARED","PASS","APPROVED","NOT_REQUIRED"}
        customs_ok=customs_raw or (condition=="CUSTOMS_CONDITIONAL" and valid_condition)
        payment_ok=normalize(j["payment_status"]) in {"CLEARED","PAID","APPROVED"} and float(j["outstanding"] or 0)<=0
        credit_ok=(not int(j["credit_hold"] or 0)) or (condition=="CREDIT_OVERRIDE" and valid_condition)

        bl_authority=resolve_bl_release_authority(c,j["id"],hbl_no)
        surrender_raw=bool(bl_authority["authority_ok"])
        surrender_ok=surrender_raw or (condition=="ORIGINAL_WAIVER" and valid_condition)

        # Any active workflow hold is release-blocking. Specific finance/credit and
        # equipment holds are also evaluated below from their authoritative stores.
        blocked_hold_codes=list(holds)
        state=_container_state(c,con)
        container_ok=state not in TERMINAL_INELIGIBLE_STATES and not bool(con.get("damage_hold",0)) and not bool(con.get("inspection_hold",0))

        checks={
            "hbl_issued":bl_ok,
            "document_authority":docs_ok,
            "customs_cleared":customs_ok,
            "payment_cleared":payment_ok,
            "credit_clear":credit_ok,
            "surrender_or_telex":surrender_ok,
            "release_instruction_valid":release_instruction_ok,
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
            "bl_release_authority":bl_authority,
            "document_source_of_truth":bl_authority["source"],
            "release_instruction":{
                "value":release_instruction or None,
                "valid_until":do.get("Valid Until"),
                "valid":release_instruction_ok,
                "source":"transaction_records:delivery-order",
            },
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
    reissue_ref:str|None=None,
    actor_role:str|None=None,
    office_scope:str|None=None,
    branch_scope:str|None=None,
    country_scope:str|None=None,
    organization_scope:str|None=None,
    crt_ref:str|None=None,
) -> dict[str,Any]:
    if not enabled():
        raise HTTPException(503,{"code":"RELEASE_GOVERNANCE_DISABLED"})
    require_human(actor_user_id,actor_type)
    action=normalize(action)
    if action not in {"EVALUATE","RELEASE","REVOKE","EXPIRE","HOLD","REISSUE"}:
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

        if action=="RELEASE" and before_state in {"REVOKED","EXPIRED"}:
            raise HTTPException(409,{
                "code":"RELEASE_REISSUE_REQUIRED",
                "from_state":before_state,
            })
        if action=="REISSUE":
            if before_state not in {"REVOKED","EXPIRED"}:
                raise HTTPException(409,{
                    "code":"REISSUE_NOT_ALLOWED_FROM_STATE",
                    "from_state":before_state,
                })
            if not reissue_ref or not condition_reason:
                raise HTTPException(422,{"code":"REISSUE_REFERENCE_REASON_REQUIRED"})

        condition=normalize(condition_type)
        if condition:
            if condition not in CONDITION_TYPES:
                raise HTTPException(422,{"code":"RELEASE_CONDITION_TYPE_INVALID"})
            require_human(approver_user_id or "",actor_type)
            if approver_user_id==actor_user_id:
                raise HTTPException(403,{"code":"MAKER_CHECKER_SAME_USER"})
            if not condition_reason or not condition_valid_until:
                raise HTTPException(422,{"code":"CONDITIONAL_RELEASE_REASON_VALIDITY_REQUIRED"})
            expiry=parse_dt(condition_valid_until)
            if not expiry or expiry<datetime.datetime.now(datetime.timezone.utc):
                raise HTTPException(422,{"code":"CONDITIONAL_RELEASE_EXPIRED_OR_INVALID"})

        pre=evaluate_prerequisites(
            job_ref=header["job_ref"],hbl_no=header["hbl_no"],container_no=container_no,
            condition_type=condition or None,condition_valid_until=condition_valid_until,
        )

        state=_container_state(c,con)
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
        elif action=="REISSUE":
            new_state="PENDING" if pre["ready"] else "BLOCKED"
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
            "prerequisites":pre["checks"],
            "condition_type":condition or None,
            "post_delivery_hold":new_state=="RELEASED_WITH_POST_DELIVERY_HOLD",
            "approver_user_id":approver_user_id,
            "actor_role":actor_role,
            "office_scope":office_scope,
            "branch_scope":branch_scope,
            "country_scope":country_scope,
            "organization_scope":organization_scope,
            "job_ref":header["job_ref"],
            "hbl_no":header["hbl_no"],
            "crt_ref":crt_ref,
            "reissue_ref":reissue_ref,
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
        legacy=c.execute(
            """SELECT w.release_status,w.closed,
                      (SELECT status FROM transaction_records
                       WHERE job_id=j.id AND module='delivery-order'
                       ORDER BY id DESC LIMIT 1) do_tx_status,
                      (SELECT payload_json FROM transaction_records
                       WHERE job_id=j.id AND module='delivery-order'
                       ORDER BY id DESC LIMIT 1) do_payload,
                      (SELECT status FROM transaction_records
                       WHERE job_id=j.id AND module='bl'
                       ORDER BY id DESC LIMIT 1) bl_tx_status
               FROM jobs j JOIN workflow_states w ON w.job_id=j.id
               WHERE j.job_ref=?""",
            (job_ref,)
        ).fetchone()
        legacy_presentation={}
        if legacy:
            try:
                do_payload=json.loads(legacy["do_payload"] or "{}")
            except Exception:
                do_payload={}
            legacy_presentation={
                "classification":"LEGACY_PRESENTATION_STATE",
                "workflow_release_status":legacy["release_status"],
                "workflow_closed":bool(legacy["closed"]),
                "delivery_order_transaction_status":legacy["do_tx_status"],
                "delivery_order_release_status":do_payload.get("Release Status"),
                "delivery_order_status":do_payload.get("Status"),
                "bl_transaction_status":legacy["bl_tx_status"],
                "authoritative_for_current_release":False,
            }

        rows=[dict(x) for x in c.execute(
            """SELECT rc.release_ref,rc.status release_header_status,cc.status
               FROM nvocc_release_controls rc
               JOIN nvocc_release_container_control cc ON cc.release_ref=rc.release_ref
               WHERE rc.job_ref=? AND rc.hbl_no=? AND cc.container_no=?
               ORDER BY cc.id DESC""",
            (job_ref,hbl_no,container_no)
        )]
        authorized_states={"RELEASED","CONDITIONAL","RELEASED_WITH_POST_DELIVERY_HOLD"}
        cargo_authorized=bool(
            rows
            and rows[0]["release_header_status"] in authorized_states
            and rows[0]["status"] in authorized_states
        )
        physical=real_container_release_event(c,container_no)
        eligible=bool(cargo_authorized and physical["found"])
        return {
            "eligible":eligible,
            "governance_enabled":True,
            "authority_source":"nvocc_release_controls + nvocc_release_container_control + real container RELEASED event",
            "legacy_presentation_state":legacy_presentation,
            "release_ref":rows[0]["release_ref"] if rows else None,
            "release_status":rows[0]["status"] if rows else "NONE",
            "release_header_status":rows[0]["release_header_status"] if rows else "NONE",
            "cargo_release_authorized":cargo_authorized,
            "real_container_release_event_found":bool(physical["found"]),
            "real_container_release_event_ref":physical["event_ref"],
            "real_container_release_actor":physical["actor_user_id"],
            "real_container_release_timestamp":physical["event_time"],
        }
    finally:c.close()


def assert_delivery_order_eligible(*,job_ref:str,hbl_no:str,container_no:str) -> None:
    r=delivery_order_eligible(job_ref=job_ref,hbl_no=hbl_no,container_no=container_no)
    if r["governance_enabled"] and not r["eligible"]:
        raise HTTPException(409,{"code":"DELIVERY_ORDER_CONTAINER_NOT_RELEASED","detail":r})
