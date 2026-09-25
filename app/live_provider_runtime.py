from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .db import connect, backend_name

router = APIRouter(prefix="/api/v1/live-providers", tags=["Live Provider Integration Readiness"])

SAFE_ROLES = {"ADMIN", "SECURITY_ADMIN", "TREASURY_MANAGER", "FINANCE"}
PROVIDER_TYPES = {"BANK", "PAYMENT", "FX", "TAX"}
LIVE_SWITCH = "M3_LIVE_PROVIDERS"
MONEY_SWITCH = "REAL_MONEY"


class SandboxProbe(BaseModel):
    mode: str = Field(pattern=r"^(success|failure|timeout)$")
    event_type: str = Field(default="UAT_PROBE", min_length=2, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=120)


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def redact(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    lk = key.lower()
    if any(part in lk for part in ("secret", "password", "token", "credential", "signature", "authorization")):
        return "[REDACTED]"
    if any(part in lk for part in ("account", "iban", "beneficiary")):
        s = str(value)
        return "****" + s[-4:] if len(s) > 4 else "****"
    if any(part in lk for part in ("amount", "balance", "limit")):
        return "[MASKED]"
    return value


def live_switch_on() -> bool:
    return os.getenv(LIVE_SWITCH, "OFF").upper() == "ON"


def real_money_on() -> bool:
    return os.getenv(MONEY_SWITCH, "OFF").upper() == "ON"


def env_present(name: str) -> bool:
    return bool(name and os.getenv(name, "").strip())


def require_role(role: str) -> str:
    role = role.upper()
    if role not in SAFE_ROLES:
        raise HTTPException(403, {"code": "LIVE_PROVIDER_ROLE_DENIED"})
    return role


def verify_signature(secret: str, raw: bytes, supplied: str | None) -> bool:
    if not secret or not supplied:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def retry_state(attempt: int, max_attempts: int, success: bool) -> str:
    if success:
        return "DELIVERED"
    return "DEAD_LETTER" if attempt >= max_attempts else "RETRY"


def activation_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not live_switch_on():
        blockers.append("LIVE_PROVIDERS_SWITCH_OFF")
    if not int(row["enabled"]):
        blockers.append("PROVIDER_NOT_ENABLED")
    if row["circuit_state"] == "OPEN":
        blockers.append("CIRCUIT_OPEN")
    if not env_present(row["endpoint_env"]):
        blockers.append("ENDPOINT_NOT_CONFIGURED")
    if not env_present(row["credential_env"]):
        blockers.append("CREDENTIAL_NOT_CONFIGURED")
    if not env_present(row["webhook_secret_env"]):
        blockers.append("WEBHOOK_SECRET_NOT_CONFIGURED")
    if int(row["real_money_capable"]) and not real_money_on():
        blockers.append("REAL_MONEY_SWITCH_OFF")
    return blockers


def config_row(provider_key: str) -> dict[str, Any]:
    c = connect()
    try:
        row = c.execute("SELECT * FROM provider_live_configs WHERE provider_key=?", (provider_key,)).fetchone()
        if not row:
            raise HTTPException(404, {"code": "LIVE_PROVIDER_UNKNOWN"})
        return dict(row)
    finally:
        c.close()


@router.get("/readiness")
def readiness(x_role: str = Header("VIEWER")):
    role = x_role.upper()
    if role not in SAFE_ROLES | {"AUDITOR", "VIEWER"}:
        raise HTTPException(403, {"code": "LIVE_PROVIDER_ROLE_DENIED"})
    c = connect()
    try:
        providers = []
        for r in c.execute("SELECT * FROM provider_live_configs ORDER BY provider_type,provider_key"):
            d = dict(r)
            blockers = activation_blockers(d)
            providers.append({
                "provider_key": d["provider_key"],
                "provider_type": d["provider_type"],
                "display_name": d["display_name"],
                "adapter_kind": d["adapter_kind"],
                "enabled": bool(d["enabled"]),
                "real_money_capable": bool(d["real_money_capable"]),
                "timeout_ms": d["timeout_ms"],
                "max_retries": d["max_retries"],
                "circuit_state": d["circuit_state"],
                "failure_count": d["failure_count"],
                "health_status": d["health_status"],
                "endpoint_configured": env_present(d["endpoint_env"]),
                "credential_configured": env_present(d["credential_env"]),
                "webhook_secret_configured": env_present(d["webhook_secret_env"]),
                "activation_blockers": blockers,
                "activation_ready": not blockers,
            })
        return {
            "project": "M3 NVOCC ERP",
            "phase": "CLX-022",
            "database": backend_name(),
            "production_traffic": os.getenv("M3_PRODUCTION_TRAFFIC", "OFF").upper(),
            "live_providers": os.getenv(LIVE_SWITCH, "OFF").upper(),
            "real_money": os.getenv(MONEY_SWITCH, "OFF").upper(),
            "live_credentials_exposed": False,
            "providers": providers,
            "all_provider_cores_ready": len(providers) == 4,
            "external_activation_performed": False,
        }
    finally:
        c.close()


@router.get("/config")
def safe_config(x_role: str = Header("AUDITOR")):
    role = x_role.upper()
    if role not in SAFE_ROLES | {"AUDITOR"}:
        raise HTTPException(403, {"code": "LIVE_PROVIDER_ROLE_DENIED"})
    c = connect()
    try:
        return [{
            "provider_key": r["provider_key"],
            "provider_type": r["provider_type"],
            "adapter_kind": r["adapter_kind"],
            "endpoint_env": r["endpoint_env"],
            "credential_env": r["credential_env"],
            "webhook_secret_env": r["webhook_secret_env"],
            "enabled": bool(r["enabled"]),
            "timeout_ms": r["timeout_ms"],
            "max_retries": r["max_retries"],
            "circuit_failure_threshold": r["circuit_failure_threshold"],
            "circuit_state": r["circuit_state"],
            "health_status": r["health_status"],
        } for r in c.execute("SELECT * FROM provider_live_configs ORDER BY provider_type,provider_key")]
    finally:
        c.close()


@router.post("/{provider_key}/sandbox-test")
def sandbox_test(provider_key: str, body: SandboxProbe, x_role: str = Header("VIEWER")):
    require_role(x_role)
    c = connect()
    try:
        p = c.execute("SELECT * FROM provider_live_configs WHERE provider_key=?", (provider_key,)).fetchone()
        if not p:
            raise HTTPException(404, {"code": "LIVE_PROVIDER_UNKNOWN"})
        raw = json.dumps(body.payload, sort_keys=True, separators=(",", ":")).encode()
        idem = body.idempotency_key or "uat-" + uuid.uuid4().hex
        request_hash = sha_bytes(raw)
        old = c.execute(
            "SELECT * FROM provider_live_events WHERE provider_key=? AND idempotency_key=?",
            (provider_key, idem),
        ).fetchone()
        if old:
            if old["request_hash"] != request_hash:
                raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_REUSE_MISMATCH"})
            return {"duplicate": True, "event_ref": old["event_ref"], "status": old["status"]}

        success = body.mode == "success"
        status = "DELIVERED" if success else "RETRY"
        result = "SUCCESS" if success else ("TIMEOUT" if body.mode == "timeout" else "FAILURE")
        ref = "LP-" + uuid.uuid4().hex[:16].upper()
        corr = str(uuid.uuid4())
        cur = c.execute(
            """INSERT INTO provider_live_events
            (event_ref,provider_key,event_type,idempotency_key,request_hash,request_redacted,response_redacted,status,
             attempt_count,max_attempts,next_retry_at,correlation_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ref, provider_key, body.event_type, idem, request_hash,
             json.dumps(redact(body.payload), sort_keys=True), json.dumps({"mode":"SANDBOX","result":result}),
             status, 1, p["max_retries"], now() if not success else None, corr, now(), now())
        )
        event_id = cur.lastrowid
        c.execute(
            "INSERT INTO provider_live_attempts(event_id,attempt_no,result,latency_ms,detail_redacted,ts) VALUES(?,?,?,?,?,?)",
            (event_id, 1, result, min(int(p["timeout_ms"]), 250), json.dumps({"sandbox": True}), now())
        )
        return {"event_ref": ref, "status": status, "result": result, "sandbox_only": True}
    finally:
        c.close()


@router.post("/{provider_key}/sandbox-retry/{event_ref}")
def sandbox_retry(provider_key: str, event_ref: str, force_success: bool = False, x_role: str = Header("VIEWER")):
    require_role(x_role)
    c = connect()
    try:
        e = c.execute("SELECT * FROM provider_live_events WHERE provider_key=? AND event_ref=?", (provider_key,event_ref)).fetchone()
        if not e:
            raise HTTPException(404, {"code":"LIVE_EVENT_UNKNOWN"})
        if e["status"] == "DELIVERED":
            return {"event_ref": event_ref, "status": "DELIVERED", "already_delivered": True}
        attempt = int(e["attempt_count"]) + 1
        state = retry_state(attempt, int(e["max_attempts"]), force_success)
        c.execute(
            "UPDATE provider_live_events SET status=?,attempt_count=?,next_retry_at=?,updated_at=? WHERE id=?",
            (state, attempt, None if state != "RETRY" else now(), now(), e["id"])
        )
        c.execute(
            "INSERT INTO provider_live_attempts(event_id,attempt_no,result,latency_ms,detail_redacted,ts) VALUES(?,?,?,?,?,?)",
            (e["id"], attempt, "SUCCESS" if force_success else "FAILURE", 120, json.dumps({"sandbox": True}), now())
        )
        return {"event_ref": event_ref, "status": state, "attempt_count": attempt, "sandbox_only": True}
    finally:
        c.close()


@router.post("/callbacks/{provider_key}")
async def live_callback(provider_key: str, request: Request, x_provider_signature: Optional[str] = Header(None), x_provider_event_id: Optional[str] = Header(None)):
    p = config_row(provider_key)
    blockers = activation_blockers(p)
    # Callback activation requires provider gate + endpoint/credential/secret and, for money-capable providers, REAL_MONEY.
    if blockers:
        raise HTTPException(423, {"code": "LIVE_PROVIDER_CALLBACK_LOCKED", "blockers": blockers})
    raw = await request.body()
    secret = os.getenv(p["webhook_secret_env"], "")
    external_id = x_provider_event_id or sha_bytes(raw)[:24]
    valid = verify_signature(secret, raw, x_provider_signature)
    c = connect()
    try:
        prior = c.execute(
            "SELECT * FROM provider_callback_receipts WHERE provider_key=? AND external_event_id=?",
            (provider_key, external_id),
        ).fetchone()
        if prior:
            return {"duplicate": True, "status": prior["status"]}
        status = "ACCEPTED" if valid else "REJECTED"
        c.execute(
            "INSERT INTO provider_callback_receipts(provider_key,external_event_id,payload_hash,signature_valid,status,received_at) VALUES(?,?,?,?,?,?)",
            (provider_key, external_id, sha_bytes(raw), 1 if valid else 0, status, now()),
        )
    finally:
        c.close()
    if not valid:
        raise HTTPException(401, {"code": "INVALID_PROVIDER_SIGNATURE"})
    return {"accepted": True, "external_event_id": external_id}


@router.get("/{provider_key}/activation-gate")
def activation_gate(provider_key: str, x_role: str = Header("AUDITOR")):
    role = x_role.upper()
    if role not in SAFE_ROLES | {"AUDITOR"}:
        raise HTTPException(403, {"code":"LIVE_PROVIDER_ROLE_DENIED"})
    p = config_row(provider_key)
    blockers = activation_blockers(p)
    return {
        "provider_key": provider_key,
        "ready": not blockers,
        "blockers": blockers,
        "live_credentials_exposed": False,
        "external_call_performed": False,
        "real_money_movement": False,
    }
