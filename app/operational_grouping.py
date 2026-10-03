from __future__ import annotations

import datetime
import json
import os
import uuid
from typing import Any

from fastapi import HTTPException

from .db import connect, tx


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def enabled() -> bool:
    return os.getenv("M3_OPERATIONAL_GROUPING_ENABLED","false").strip().lower() in {"1","true","yes","on"}


def normalize(v: str | None) -> str:
    return (v or "").strip().upper()


def _job(conn, job_ref: str):
    row=conn.execute("SELECT id,job_ref FROM jobs WHERE job_ref=?",(job_ref,)).fetchone()
    if not row:
        raise HTTPException(404,{"code":"JOB_NOT_FOUND","job_ref":job_ref})
    return row


def create_operational_group(*,group_ref:str,group_type:str,job_refs:list[str],actor_id:str) -> dict[str,Any]:
    if not enabled():
        raise HTTPException(503,{"code":"OPERATIONAL_GROUPING_DISABLED"})
    ref=normalize(group_ref)
    gtype=normalize(group_type)
    if gtype not in {"CONSOLIDATION","TS","VESSEL","TERMINAL","CRO_TRT"}:
        raise HTTPException(422,{"code":"OPERATIONAL_GROUP_TYPE_INVALID"})
    refs=sorted({x.strip() for x in job_refs if x and x.strip()})
    if len(refs)<2:
        raise HTTPException(422,{"code":"OPERATIONAL_GROUP_REQUIRES_MULTIPLE_JOBS"})

    c=connect();tx(c)
    try:
        existing=c.execute("SELECT * FROM operational_groups WHERE group_ref=?",(ref,)).fetchone()
        if existing:
            existing=dict(existing)
            members=[r["job_ref"] for r in c.execute(
                """SELECT j.job_ref FROM operational_group_jobs g
                   JOIN jobs j ON j.id=g.job_id WHERE g.group_id=? ORDER BY j.job_ref""",
                (existing["id"],)
            )]
            if members!=refs:
                c.execute("ROLLBACK")
                raise HTTPException(409,{"code":"OPERATIONAL_GROUP_MEMBERSHIP_CONFLICT"})
            c.execute("COMMIT")
            return {"status":"EXISTS","group_ref":ref,"group_type":existing["group_type"],"job_refs":members}

        cur=c.execute(
            """INSERT INTO operational_groups(
               group_ref,group_type,authority_scope,created_by,created_at,updated_at
               ) VALUES(?,?,'OPERATIONAL_COORDINATION_ONLY',?,?,?)""",
            (ref,gtype,actor_id,now(),now())
        )
        gid=cur.lastrowid
        for jr in refs:
            j=_job(c,jr)
            c.execute(
                """INSERT INTO operational_group_jobs(group_id,job_id,relationship_status,created_at)
                   VALUES(?,?,'ACTIVE',?)""",
                (gid,j["id"],now())
            )
        c.execute("COMMIT")
        return {"status":"CREATED","group_ref":ref,"group_type":gtype,"job_refs":refs}
    except HTTPException:
        raise
    except Exception as exc:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise HTTPException(409,{"code":"OPERATIONAL_GROUP_CREATE_FAILED"}) from exc
    finally:c.close()


def group_context(group_ref:str) -> dict[str,Any]:
    c=connect()
    try:
        g=c.execute("SELECT * FROM operational_groups WHERE group_ref=?",(normalize(group_ref),)).fetchone()
        if not g: raise HTTPException(404,{"code":"OPERATIONAL_GROUP_NOT_FOUND"})
        jobs=[dict(x) for x in c.execute(
            """SELECT j.job_ref,m.relationship_status FROM operational_group_jobs m
               JOIN jobs j ON j.id=m.job_id WHERE m.group_id=? ORDER BY j.job_ref""",
            (g["id"],)
        )]
        return {
            "group_ref":g["group_ref"],
            "group_type":g["group_type"],
            "authority_scope":g["authority_scope"],
            "job_refs":[x["job_ref"] for x in jobs],
            "data_merge_allowed":False,
            "pnl_authority":"JOB_ONLY",
            "commercial_authority":"BOOKING_HBL_CUSTOMER_LEVEL",
        }
    finally:c.close()


def link_mbl_hbl(*,mbl_no:str,hbl_no:str,job_ref:str,actor_id:str) -> dict[str,Any]:
    if not enabled(): raise HTTPException(503,{"code":"OPERATIONAL_GROUPING_DISABLED"})
    c=connect();tx(c)
    try:
        j=_job(c,job_ref)
        m=c.execute("SELECT id,job_id FROM bills WHERE bill_no=? AND kind='MBL'",(mbl_no,)).fetchone()
        h=c.execute("SELECT id,job_id FROM bills WHERE bill_no=? AND kind='HBL'",(hbl_no,)).fetchone()
        if not m or not h: raise HTTPException(404,{"code":"BL_NOT_FOUND"})
        if m["job_id"]!=j["id"] or h["job_id"]!=j["id"]:
            raise HTTPException(409,{"code":"BL_JOB_SCOPE_MISMATCH"})
        c.execute(
            """INSERT INTO mbl_hbl_links(mbl_bill_id,hbl_bill_id,job_id,link_status,created_by,created_at)
               VALUES(?,?,?,'ACTIVE',?,?)
               ON CONFLICT(mbl_bill_id,hbl_bill_id) DO NOTHING""",
            (m["id"],h["id"],j["id"],actor_id,now())
        )
        c.execute("COMMIT")
        return {"status":"LINKED","job_ref":job_ref,"mbl_no":mbl_no,"hbl_no":hbl_no}
    except HTTPException:
        c.execute("ROLLBACK");raise
    finally:c.close()


def link_cro_container(*,cro_id:int,container_id:int,actor_id:str) -> dict[str,Any]:
    if not enabled(): raise HTTPException(503,{"code":"OPERATIONAL_GROUPING_DISABLED"})
    c=connect();tx(c)
    try:
        cro=c.execute("SELECT id,job_id FROM transaction_records WHERE id=? AND module='cro'",(cro_id,)).fetchone()
        con=c.execute("SELECT id,job_id FROM containers WHERE id=?",(container_id,)).fetchone()
        if not cro or not con: raise HTTPException(404,{"code":"CRO_OR_CONTAINER_NOT_FOUND"})
        if cro["job_id"]!=con["job_id"]: raise HTTPException(409,{"code":"CRO_CONTAINER_CROSS_JOB_DATA_MERGE_FORBIDDEN"})
        c.execute(
          """INSERT INTO cro_container_links(cro_transaction_id,container_id,relationship_status,created_by,created_at)
             VALUES(?,?,'ACTIVE',?,?) ON CONFLICT(cro_transaction_id,container_id) DO NOTHING""",
          (cro_id,container_id,actor_id,now())
        )
        c.execute("COMMIT");return {"status":"LINKED","cro_id":cro_id,"container_id":container_id}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()


def link_cro_trt(*,cro_id:int,trt_id:int,actor_id:str) -> dict[str,Any]:
    if not enabled(): raise HTTPException(503,{"code":"OPERATIONAL_GROUPING_DISABLED"})
    c=connect();tx(c)
    try:
        cro=c.execute("SELECT id,job_id FROM transaction_records WHERE id=? AND module='cro'",(cro_id,)).fetchone()
        trt=c.execute("SELECT id,job_id,module FROM transaction_records WHERE id=? AND module IN ('trt','export-trt','import-trt','transshipment-trt')",(trt_id,)).fetchone()
        if not cro or not trt: raise HTTPException(404,{"code":"CRO_OR_TRT_NOT_FOUND"})
        if cro["job_id"]!=trt["job_id"]: raise HTTPException(409,{"code":"CRO_TRT_CROSS_JOB_DATA_MERGE_FORBIDDEN"})
        c.execute(
          """INSERT INTO cro_trt_links(cro_transaction_id,trt_transaction_id,relationship_status,created_by,created_at)
             VALUES(?,?,'ACTIVE',?,?) ON CONFLICT(cro_transaction_id,trt_transaction_id) DO NOTHING""",
          (cro_id,trt_id,actor_id,now())
        )
        c.execute("COMMIT");return {"status":"LINKED","cro_id":cro_id,"trt_id":trt_id}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()


def link_trt_container(*,trt_id:int,container_id:int,actor_id:str) -> dict[str,Any]:
    if not enabled(): raise HTTPException(503,{"code":"OPERATIONAL_GROUPING_DISABLED"})
    c=connect();tx(c)
    try:
        trt=c.execute("SELECT id,job_id,module FROM transaction_records WHERE id=? AND module IN ('trt','export-trt','import-trt','transshipment-trt')",(trt_id,)).fetchone()
        con=c.execute("SELECT id,job_id FROM containers WHERE id=?",(container_id,)).fetchone()
        if not trt or not con: raise HTTPException(404,{"code":"TRT_OR_CONTAINER_NOT_FOUND"})
        if trt["job_id"]!=con["job_id"]: raise HTTPException(409,{"code":"TRT_CONTAINER_CROSS_JOB_DATA_MERGE_FORBIDDEN"})
        c.execute(
          """INSERT INTO trt_container_links(trt_transaction_id,container_id,relationship_status,created_by,created_at)
             VALUES(?,?,'ACTIVE',?,?) ON CONFLICT(trt_transaction_id,container_id) DO NOTHING""",
          (trt_id,container_id,actor_id,now())
        )
        c.execute("COMMIT");return {"status":"LINKED","trt_id":trt_id,"container_id":container_id}
    except HTTPException:c.execute("ROLLBACK");raise
    finally:c.close()
