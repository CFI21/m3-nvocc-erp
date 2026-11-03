#!/usr/bin/env python3
"""Offline-only request-contract fixture for the pinned B-Atomic endpoint.

NO HTTP transport, NO database connection, NO credentials and NO submissions.
Creates one unique, disposable two-row customer payload and checks its shape.
"""
import argparse
import json
import re
import uuid
from pathlib import Path

PINNED_APP_SHA = "79968230bd5601142d9239c739853b64ed9d2f82"
ENDPOINT = "/api/masterdata/bulk/submit-atomic"
ALLOWED_DOMAINS = frozenset(("agent","customer","equipment-type","container-type"))
ALLOWED_OPERATIONS = frozenset(("CREATE","UPDATE"))

def make_fixture(nonce=None):
    nonce = nonce or uuid.uuid4().hex[:16]
    if not re.fullmatch(r"[a-f0-9]{16}",nonce):
        raise ValueError("nonce must be 16 lowercase hex chars")
    key = "m3-uat-b-atomic-" + nonce
    rows = [
        {"record_key":"M3-BAT-" + nonce.upper() + "-01",
         "operation":"CREATE","payload":{"name":"M3 Atomic Test " + nonce + " A"},
         "reason":"Isolated disposable atomic rollback UAT"},
        {"record_key":"M3-BAT-" + nonce.upper() + "-02",
         "operation":"CREATE","payload":{"name":"M3 Atomic Test " + nonce + " B"},
         "reason":"Isolated disposable atomic rollback UAT"},
    ]
    return {
        "approved_app_sha":PINNED_APP_SHA,
        "endpoint":ENDPOINT,
        "headers":{"Idempotency-Key":key,"X-M3-Session":"<RUNTIME_ONLY>"},
        "body":{"domain":"customer","rows":rows},
        "submission_enabled":False,
    }

def validate_fixture(obj):
    if obj.get("approved_app_sha") != PINNED_APP_SHA or obj.get("endpoint") != ENDPOINT:
        raise ValueError("wrong immutable application or endpoint")
    if obj.get("submission_enabled") is not False:
        raise ValueError("offline-only fixture must disable submission")
    headers=obj.get("headers",{})
    if set(headers)!={"Idempotency-Key","X-M3-Session"}:
        raise ValueError("invalid header contract")
    if not (8<=len(headers["Idempotency-Key"])<=128):
        raise ValueError("invalid idempotency key length")
    if headers["X-M3-Session"]!="<RUNTIME_ONLY>":
        raise ValueError("session tokens must never be embedded in fixtures")
    body=obj.get("body",{})
    if set(body)!={"domain","rows"} or body["domain"] not in ALLOWED_DOMAINS:
        raise ValueError("invalid request body")
    rows=body["rows"]
    if len(rows)!=2:
        raise ValueError("exactly two disposable test rows required")
    keys=set()
    for row in rows:
        if set(row)!={"record_key","operation","payload","reason"}:
            raise ValueError("BulkRow schema mismatch")
        rk=row["record_key"].strip()
        if not rk or len(rk)>120 or rk.casefold() in keys:
            raise ValueError("duplicate or invalid record key")
        keys.add(rk.casefold())
        if row["operation"] not in ALLOWED_OPERATIONS:
            raise ValueError("invalid operation")
        if not isinstance(row["payload"],dict) or not row["payload"].get("name"):
            raise ValueError("missing customer name")
        if not isinstance(row["reason"],str) or not 3<=len(row["reason"])<=500:
            raise ValueError("reason absent or out of bounds")
    return True

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nonce",help="Optional 16-character lowercase hex fixture nonce")
    parser.add_argument("--output",help="Optional output JSON path; no credentials included")
    args=parser.parse_args()
    doc=make_fixture(args.nonce)
    validate_fixture(doc)
    blob=json.dumps(doc,indent=2,sort_keys=True)+"\n"
    if args.output:
        p=Path(args.output)
        p.write_text(blob)
        print("OFFLINE_CONTRACT_PASS saved="+str(p))
    else:
        print(blob,end="")
    print("NO_HTTP_NO_DB_NO_SUBMISSION_PASS",flush=True)

if __name__=="__main__":
    main()
