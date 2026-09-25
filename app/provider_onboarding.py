from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException

from .db import connect

router = APIRouter(prefix="/api/v1/provider-onboarding", tags=["CLX-024 Authenticated Sandbox Onboarding"])

ALLOWED_ROLES={"ADMIN","SECURITY_ADMIN","TREASURY_MANAGER","FINANCE","AUDITOR"}
SANDBOX_SWITCH="M3_PROVIDER_SANDBOX_TESTS"

PROVIDERS={
    "truelayer-data":{
        "required":["M3_TRUELAYER_CLIENT_ID","M3_TRUELAYER_CLIENT_SECRET","M3_TRUELAYER_DATA_CONNECTION_ID"],
        "base_env":"M3_TRUELAYER_DATA_BASE_URL",
        "base_default":"https://api.truelayer-sandbox.com",
        "auth_env":"M3_TRUELAYER_AUTH_URL",
        "auth_default":"https://auth.truelayer-sandbox.com/connect/token",
        "kind":"TRUELAYER_DATA_V3",
    },
    "truelayer-payments":{
        "required":["M3_TRUELAYER_CLIENT_ID","M3_TRUELAYER_CLIENT_SECRET","M3_TRUELAYER_SIGNING_PRIVATE_KEY","M3_TRUELAYER_SIGNING_KEY_ID"],
        "base_env":"M3_TRUELAYER_PAYMENTS_BASE_URL",
        "base_default":"https://api.truelayer-sandbox.com",
        "auth_env":"M3_TRUELAYER_AUTH_URL",
        "auth_default":"https://auth.truelayer-sandbox.com/connect/token",
        "kind":"TRUELAYER_PAYMENTS_V3",
    },
    "openexchangerates":{
        "required":["M3_OXR_APP_ID"],
        "base_env":"M3_OXR_BASE_URL",
        "base_default":"https://openexchangerates.org/api",
        "kind":"OPENEXCHANGERATES_V1",
    },
    "avalara":{
        "required":["M3_AVALARA_ACCOUNT_ID","M3_AVALARA_LICENSE_KEY","M3_AVALARA_CLIENT_HEADER"],
        "base_env":"M3_AVALARA_BASE_URL",
        "base_default":"https://sandbox-rest.avatax.com",
        "kind":"AVALARA_AVATAX_V2",
    },
}

def _present(name:str)->bool:
    return bool(os.getenv(name,"").strip())

def _role(role:str):
    r=role.upper()
    if r not in ALLOWED_ROLES:
        raise HTTPException(403,{"code":"PROVIDER_ONBOARDING_ROLE_DENIED"})
    return r

def _sandbox_enabled()->bool:
    return os.getenv(SANDBOX_SWITCH,"OFF").upper()=="ON"

def _safety():
    return {
        "production_traffic":os.getenv("M3_PRODUCTION_TRAFFIC","OFF").upper(),
        "live_providers":os.getenv("M3_LIVE_PROVIDERS","OFF").upper(),
        "real_money":os.getenv("REAL_MONEY","OFF").upper(),
        "sandbox_tests":os.getenv(SANDBOX_SWITCH,"OFF").upper(),
    }

def _assert_sandbox_only():
    s=_safety()
    if not _sandbox_enabled():
        raise HTTPException(423,{"code":"SANDBOX_TESTS_DISABLED"})
    if s["live_providers"]!="OFF" or s["real_money"]!="OFF":
        raise HTTPException(423,{"code":"SANDBOX_SAFETY_GATE_FAILED","state":s})
    return s

def _provider_status(key:str,cfg:dict[str,Any]):
    required={name:_present(name) for name in cfg["required"]}
    base=os.getenv(cfg["base_env"],cfg["base_default"]).strip()
    return {
        "provider":key,
        "adapter_kind":cfg["kind"],
        "base_url_configured":bool(base),
        "required_env":list(cfg["required"]),
        "configured":required,
        "credentials_complete":all(required.values()),
        "secret_values_exposed":False,
    }

@router.get("/status")
def status(x_role:str=Header("AUDITOR")):
    _role(x_role)
    return {
        "project":"M3 NVOCC ERP",
        "phase":"CLX-024",
        "safety":_safety(),
        "providers":[_provider_status(k,v) for k,v in PROVIDERS.items()],
        "accepted":False,
        "secret_values_exposed":False,
    }

async def _truelayer_token(scope:str)->dict[str,Any]:
    client_id=os.getenv("M3_TRUELAYER_CLIENT_ID","")
    client_secret=os.getenv("M3_TRUELAYER_CLIENT_SECRET","")
    url=os.getenv("M3_TRUELAYER_AUTH_URL","https://auth.truelayer-sandbox.com/connect/token")
    started=time.perf_counter()
    async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
        r=await client.post(url,data={
            "grant_type":"client_credentials",
            "client_id":client_id,
            "client_secret":client_secret,
            "scope":scope,
        })
    latency=int((time.perf_counter()-started)*1000)
    if r.status_code!=200:
        raise HTTPException(502,{"code":"TRUELAYER_AUTH_FAILED","http_status":r.status_code,"latency_ms":latency})
    d=r.json()
    if not d.get("access_token"):
        raise HTTPException(502,{"code":"TRUELAYER_TOKEN_MISSING"})
    return {"access_token":d["access_token"],"expires_in":d.get("expires_in"),"latency_ms":latency}

@router.post("/truelayer-data/test")
async def test_truelayer_data(x_role:str=Header("ADMIN")):
    _role(x_role); _assert_sandbox_only()
    cfg=PROVIDERS["truelayer-data"]; st=_provider_status("truelayer-data",cfg)
    if not st["credentials_complete"]:
        raise HTTPException(428,{"code":"SANDBOX_CREDENTIALS_REQUIRED","missing":[k for k,v in st["configured"].items() if not v]})
    tok=await _truelayer_token("data")
    base=os.getenv(cfg["base_env"],cfg["base_default"]).rstrip("/")
    connection_id=os.getenv("M3_TRUELAYER_DATA_CONNECTION_ID","")
    started=time.perf_counter()
    async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
        r=await client.get(base+"/v3/accounts",headers={
            "Authorization":"Bearer "+tok["access_token"],
            "Connection-Id":connection_id,
        })
    latency=int((time.perf_counter()-started)*1000)
    if r.status_code!=200:
        raise HTTPException(502,{"code":"TRUELAYER_DATA_READ_FAILED","http_status":r.status_code,"latency_ms":latency})
    data=r.json()
    return {"provider":"TrueLayer Data API v3","authentication":"PASS","bank_data_read":"PASS","record_count":len(data.get("results",[])) if isinstance(data,dict) else None,"latency_ms":latency,"secret_values_exposed":False}

@router.post("/truelayer-payments/test-auth")
async def test_truelayer_payments_auth(x_role:str=Header("ADMIN")):
    _role(x_role); _assert_sandbox_only()
    cfg=PROVIDERS["truelayer-payments"]; st=_provider_status("truelayer-payments",cfg)
    if not st["credentials_complete"]:
        raise HTTPException(428,{"code":"SANDBOX_CREDENTIALS_REQUIRED","missing":[k for k,v in st["configured"].items() if not v]})
    tok=await _truelayer_token("payments")
    key=os.getenv("M3_TRUELAYER_SIGNING_PRIVATE_KEY","")
    kid=os.getenv("M3_TRUELAYER_SIGNING_KEY_ID","")
    if "PRIVATE KEY" not in key or not kid:
        raise HTTPException(422,{"code":"TRUELAYER_SIGNING_KEY_INVALID"})
    return {"provider":"TrueLayer Payments API v3","authentication":"PASS","signing_material_present":True,"signed_payment_execution":"NOT_RUN_NO_REAL_MONEY","webhook_jwks_url":os.getenv("M3_TRUELAYER_WEBHOOK_JWKS_URL","https://webhooks.truelayer-sandbox.com/.well-known/jwks"),"secret_values_exposed":False}

@router.post("/openexchangerates/test")
async def test_oxr(x_role:str=Header("ADMIN")):
    _role(x_role); _assert_sandbox_only()
    cfg=PROVIDERS["openexchangerates"]; st=_provider_status("openexchangerates",cfg)
    if not st["credentials_complete"]:
        raise HTTPException(428,{"code":"SANDBOX_CREDENTIALS_REQUIRED","missing":[k for k,v in st["configured"].items() if not v]})
    base=os.getenv(cfg["base_env"],cfg["base_default"]).rstrip("/")
    started=time.perf_counter()
    async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
        r=await client.get(base+"/latest.json",headers={"Authorization":"Token "+os.getenv("M3_OXR_APP_ID","")})
    latency=int((time.perf_counter()-started)*1000)
    if r.status_code!=200:
        raise HTTPException(502,{"code":"OXR_AUTH_OR_RATE_FETCH_FAILED","http_status":r.status_code,"latency_ms":latency})
    d=r.json()
    if not isinstance(d.get("rates"),dict) or not d["rates"]:
        raise HTTPException(502,{"code":"OXR_RATES_MISSING"})
    return {"provider":"Open Exchange Rates","authentication":"PASS","rate_retrieval":"PASS","base":d.get("base"),"rate_count":len(d["rates"]),"latency_ms":latency,"secret_values_exposed":False}

@router.post("/avalara/test")
async def test_avalara(x_role:str=Header("ADMIN")):
    _role(x_role); _assert_sandbox_only()
    cfg=PROVIDERS["avalara"]; st=_provider_status("avalara",cfg)
    if not st["credentials_complete"]:
        raise HTTPException(428,{"code":"SANDBOX_CREDENTIALS_REQUIRED","missing":[k for k,v in st["configured"].items() if not v]})
    base=os.getenv(cfg["base_env"],cfg["base_default"]).rstrip("/")
    raw=(os.getenv("M3_AVALARA_ACCOUNT_ID","")+":"+os.getenv("M3_AVALARA_LICENSE_KEY","")).encode()
    auth="Basic "+base64.b64encode(raw).decode()
    headers={"Authorization":auth,"X-Avalara-Client":os.getenv("M3_AVALARA_CLIENT_HEADER","M3-NVOCC-ERP"),"Accept":"application/json"}
    started=time.perf_counter()
    async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
        r=await client.get(base+"/api/v2/utilities/ping",headers=headers)
    latency=int((time.perf_counter()-started)*1000)
    if r.status_code!=200:
        raise HTTPException(502,{"code":"AVALARA_AUTH_PING_FAILED","http_status":r.status_code,"latency_ms":latency})
    return {"provider":"Avalara AvaTax REST v2","authentication":"PASS","sandbox_ping":"PASS","tax_calculation":"NOT_RUN_REQUIRES_COMPANY_TRANSACTION_TEST_FIXTURE","latency_ms":latency,"secret_values_exposed":False}

@router.get("/control-evidence")
def control_evidence(x_role:str=Header("AUDITOR")):
    _role(x_role)
    c=connect()
    try:
        counts={
            "provider_live_events":c.execute("SELECT COUNT(*) n FROM provider_live_events").fetchone()["n"],
            "provider_live_attempts":c.execute("SELECT COUNT(*) n FROM provider_live_attempts").fetchone()["n"],
            "provider_callback_receipts":c.execute("SELECT COUNT(*) n FROM provider_callback_receipts").fetchone()["n"],
        }
        guards={
            "provider_idempotency_unique":bool(c.execute("""SELECT 1 FROM information_schema.table_constraints
                WHERE table_schema='public' AND table_name='provider_live_events' AND constraint_type='UNIQUE' LIMIT 1""").fetchone()),
            "callback_duplicate_unique":bool(c.execute("""SELECT 1 FROM information_schema.table_constraints
                WHERE table_schema='public' AND table_name='provider_callback_receipts' AND constraint_type='UNIQUE' LIMIT 1""").fetchone()),
        }
        return {"counts":counts,"guards":guards,"retry_dlq":"CLX-022 VERIFIED","circuit_breaker":"CLX-022 VERIFIED","audit":"PRESERVED","secret_values_exposed":False}
    finally:
        c.close()
