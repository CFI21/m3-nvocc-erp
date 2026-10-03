from __future__ import annotations

import datetime
import hashlib
import json
import os
from typing import Any, Callable

from fastapi import HTTPException

from .db import connect, tx


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def enabled() -> bool:
    return os.getenv("M3_JOB_IDEMPOTENCY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def normalize(value: str | None) -> str:
    return (value or "").strip().upper()


def business_key(
    booking_ref: str,
    purpose: str,
    split_sequence: int,
    consolidation_ref: str | None = None,
) -> str:
    if split_sequence < 1:
        raise HTTPException(422, {"code": "INVALID_SPLIT_SEQUENCE"})
    raw = "|".join(
        [
            normalize(booking_ref),
            normalize(purpose),
            str(split_sequence),
            normalize(consolidation_ref),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def request_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def execute_once(
    *,
    booking_ref: str,
    purpose: str,
    split_sequence: int,
    consolidation_ref: str | None,
    request_payload: dict[str, Any],
    actor_id: str,
    create_fn: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """
    Execute one Job-creation callback for one permanent business identity.

    This function does not approve anything and does not define Booking/Job
    cardinality. It is an idempotency guard only. The callback is supplied by
    the caller so Item 2 can own actual Job creation behavior.
    """
    if not enabled():
        raise HTTPException(503, {"code": "JOB_IDEMPOTENCY_DISABLED"})

    key = business_key(booking_ref, purpose, split_sequence, consolidation_ref)
    rh = request_hash(request_payload)
    c = connect()
    tx(c)
    try:
        row = c.execute(
            "SELECT * FROM job_creation_idempotency WHERE business_key=?",
            (key,),
        ).fetchone()

        if row:
            row = dict(row)
            if row["request_hash"] != rh:
                c.execute("ROLLBACK")
                raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_REUSE_CONFLICT"})

            if row["status"] == "CREATED":
                c.execute("COMMIT")
                return {
                    "status": "JOB_ALREADY_CREATED",
                    "replayed": True,
                    "business_key": key,
                    "job_id": row["job_id"],
                    "job_ref": row["job_ref"],
                    "attempts": row["attempts"],
                }

            # Same business intent may retry after a failed attempt. The
            # identity itself never expires.
            c.execute(
                """UPDATE job_creation_idempotency
                   SET status='IN_PROGRESS', attempts=attempts+1,
                       last_error=NULL, updated_at=?, created_by=?
                   WHERE business_key=?""",
                (now(), actor_id, key),
            )
        else:
            c.execute(
                """INSERT INTO job_creation_idempotency(
                     business_key,booking_ref,purpose,split_sequence,consolidation_ref,
                     request_hash,status,job_id,job_ref,attempts,last_error,
                     created_at,updated_at,created_by
                   ) VALUES(?,?,?,?,?,?,'IN_PROGRESS',NULL,NULL,1,NULL,?,?,?)""",
                (
                    key,
                    normalize(booking_ref),
                    normalize(purpose),
                    split_sequence,
                    normalize(consolidation_ref),
                    rh,
                    now(),
                    now(),
                    actor_id,
                ),
            )

        try:
            created = create_fn(c)
            job_id = created.get("job_id")
            job_ref = created.get("job_ref")
            if not job_id or not job_ref:
                raise RuntimeError("JOB_CREATION_CALLBACK_MISSING_IDENTITY")
            c.execute(
                """UPDATE job_creation_idempotency
                   SET status='CREATED', job_id=?, job_ref=?, updated_at=?
                   WHERE business_key=?""",
                (job_id, str(job_ref), now(), key),
            )
            c.execute("COMMIT")
            return {
                "status": "CREATED",
                "replayed": False,
                "business_key": key,
                "job_id": job_id,
                "job_ref": str(job_ref),
            }
        except Exception as exc:
            c.execute(
                """UPDATE job_creation_idempotency
                   SET status='FAILED', last_error=?, updated_at=?
                   WHERE business_key=?""",
                (type(exc).__name__, now(), key),
            )
            c.execute("COMMIT")
            raise HTTPException(
                409,
                {"code": "JOB_CREATION_EXCEPTION", "business_key": key},
            ) from exc
    finally:
        c.close()
