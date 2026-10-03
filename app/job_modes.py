from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from fastapi import HTTPException

from .db import connect, tx
from .job_creation_idempotency import execute_once


def enabled() -> bool:
    return os.getenv("M3_JOB_MODES_ENABLED", "false").strip().lower() in {"1","true","yes","on"}


def normalize(v: str | None) -> str:
    return (v or "").strip().upper()


def _booking(conn, booking_ref: str):
    r=conn.execute("SELECT * FROM bookings WHERE booking_ref=?",(booking_ref,)).fetchone()
    if not r:
        raise HTTPException(404,{"code":"BOOKING_NOT_FOUND","booking_ref":booking_ref})
    return r


def _new_job(conn, *, job_ref: str, anchor_booking, status: str="TEST_DRAFT") -> dict[str,Any]:
    conn.execute(
        """INSERT INTO jobs(job_ref,booking_id,customer_id,agent_id,voyage_id,pol,pod,operational_status)
           VALUES(?,?,?,?,?,?,?,?)""",
        (job_ref,anchor_booking["id"],anchor_booking["customer_id"],anchor_booking["agent_id"],
         anchor_booking["voyage_id"],anchor_booking["pol"],anchor_booking["pod"],status),
    )
    row=conn.execute("SELECT id,job_ref FROM jobs WHERE job_ref=?",(job_ref,)).fetchone()
    return {"job_id":row["id"],"job_ref":row["job_ref"]}


def _profile(conn, job_id:int, job_type:str, consolidation_ref:str|None, split_sequence:int) -> None:
    conn.execute(
        """INSERT INTO job_mode_profiles(job_id,job_type,consolidation_ref,split_sequence,commercial_authority,version)
           VALUES(?,?,?,?,?,1)""",
        (job_id,job_type,normalize(consolidation_ref),split_sequence,"JOB_BOOKING_LINKS"),
    )


def _links(conn, job_id:int, bookings:list[Any]) -> None:
    for seq,b in enumerate(bookings,1):
        conn.execute(
            """INSERT INTO job_booking_links(job_id,booking_id,source_sequence,relationship_status)
               VALUES(?,?,?,'ACTIVE')""",
            (job_id,b["id"],seq),
        )


def create_standard_or_split_job(
    *,
    booking_ref:str,
    job_ref:str,
    split_sequence:int=1,
    actor_id:str="TEST-HUMAN",
) -> dict[str,Any]:
    if not enabled():
        raise HTTPException(503,{"code":"JOB_MODES_DISABLED"})
    payload={
        "booking_ref":normalize(booking_ref),
        "job_ref":job_ref,
        "purpose":"BOOKING_CONFIRMED",
        "split_sequence":split_sequence,
        "job_type":"STANDARD",
    }

    def _create(conn):
        b=_booking(conn,booking_ref)
        made=_new_job(conn,job_ref=job_ref,anchor_booking=b)
        _profile(conn,made["job_id"],"STANDARD",None,split_sequence)
        _links(conn,made["job_id"],[b])
        return made

    return execute_once(
        booking_ref=booking_ref,
        purpose="BOOKING_CONFIRMED",
        split_sequence=split_sequence,
        consolidation_ref=None,
        request_payload=payload,
        actor_id=actor_id,
        create_fn=_create,
    )


def create_consolidation_job(
    *,
    booking_refs:list[str],
    consolidation_ref:str,
    job_ref:str,
    actor_id:str="TEST-HUMAN",
) -> dict[str,Any]:
    if not enabled():
        raise HTTPException(503,{"code":"JOB_MODES_DISABLED"})
    refs=sorted({normalize(x) for x in booking_refs if normalize(x)})
    if len(refs)<2:
        raise HTTPException(422,{"code":"CONSOLIDATION_REQUIRES_MULTIPLE_BOOKINGS"})
    cref=normalize(consolidation_ref)
    if not cref:
        raise HTTPException(422,{"code":"CONSOLIDATION_REF_REQUIRED"})

    c=connect();tx(c)
    try:
        existing=c.execute(
            """SELECT p.job_id,j.job_ref,p.booking_set_hash
               FROM job_mode_profiles p JOIN jobs j ON j.id=p.job_id
               WHERE p.consolidation_ref=? AND p.job_type='CONSOLIDATION'""",
            (cref,),
        ).fetchone()
        set_hash=hashlib.sha256(json.dumps(refs,separators=(",",":")).encode()).hexdigest()
        if existing:
            if existing["booking_set_hash"]!=set_hash:
                c.execute("ROLLBACK")
                raise HTTPException(409,{"code":"CONSOLIDATION_REF_REUSE_CONFLICT"})
            c.execute("COMMIT")
            return {
                "status":"JOB_ALREADY_CREATED",
                "replayed":True,
                "job_id":existing["job_id"],
                "job_ref":existing["job_ref"],
                "consolidation_ref":cref,
            }

        bookings=[_booking(c,r) for r in refs]
        # jobs.booking_id remains a legacy compatibility anchor only.
        # Commercial authority for a consolidation is job_booking_links.
        anchor=bookings[0]
        made=_new_job(c,job_ref=job_ref,anchor_booking=anchor)
        c.execute(
            """INSERT INTO job_mode_profiles(
                 job_id,job_type,consolidation_ref,split_sequence,commercial_authority,booking_set_hash,version
               ) VALUES(?, 'CONSOLIDATION', ?, 1, 'JOB_BOOKING_LINKS', ?, 1)""",
            (made["job_id"],cref,set_hash),
        )
        _links(c,made["job_id"],bookings)
        c.execute("COMMIT")
        return {
            "status":"CREATED",
            "replayed":False,
            "job_id":made["job_id"],
            "job_ref":made["job_ref"],
            "consolidation_ref":cref,
            "booking_refs":refs,
        }
    except HTTPException:
        raise
    except Exception as exc:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise HTTPException(409,{"code":"JOB_CREATION_EXCEPTION"}) from exc
    finally:
        c.close()


def linked_bookings(job_ref:str) -> dict[str,Any]:
    c=connect()
    try:
        j=c.execute("SELECT id,job_ref FROM jobs WHERE job_ref=?",(job_ref,)).fetchone()
        if not j: raise HTTPException(404,{"code":"JOB_NOT_FOUND"})
        p=c.execute("SELECT * FROM job_mode_profiles WHERE job_id=?",(j["id"],)).fetchone()
        rows=[dict(x) for x in c.execute(
            """SELECT b.booking_ref,l.source_sequence,l.relationship_status
               FROM job_booking_links l JOIN bookings b ON b.id=l.booking_id
               WHERE l.job_id=? ORDER BY l.source_sequence""",(j["id"],)
        )]
        return {
            "job_ref":job_ref,
            "job_type":p["job_type"] if p else "LEGACY_STANDARD",
            "commercial_authority":"JOB_BOOKING_LINKS" if p else "LEGACY_JOB_BOOKING_ID",
            "bookings":rows,
        }
    finally:
        c.close()
