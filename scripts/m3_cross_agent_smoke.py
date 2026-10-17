#!/usr/bin/env python3
"""Authenticated, read-only P0 cross-Agent and preview smoke probe.

Runs against an isolated UAT API only. Never writes, creates transactions,
changes permissions or sends secrets to stdout. Exit nonzero on missing inputs.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request


def request_json(base, path, token, timeout=15):
    url=base.rstrip("/") + path
    req=urllib.request.Request(url,headers={"X-M3-Session":token,"Accept":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            raw=response.read(262144)
            return {"status":response.status,"body":raw.decode("utf-8","replace")}
    except urllib.error.HTTPError as error:
        raw=error.read(262144)
        return {"status":error.code,"body":raw.decode("utf-8","replace")}


def run(api_url, token_a, token_b, own_id, other_id, target_sha):
    if not api_url.startswith("https://"):
        raise ValueError("HTTPS_API_REQUIRED")
    if not token_a or not token_b or token_a==token_b:
        raise ValueError("DISTINCT_AUTHENTICATED_SESSIONS_REQUIRED")
    if not own_id.isdigit() or not other_id.isdigit() or own_id==other_id:
        raise ValueError("DISTINCT_NUMERIC_BOOKING_IDS_REQUIRED")
    if len(target_sha)!=40 or any(x not in "0123456789abcdef" for x in target_sha.lower()):
        raise ValueError("FULL_COMMIT_SHA_REQUIRED")
    cases=[
        ("SEC-001","Agent A booking A","/api/v1/booking/"+own_id,token_a,range(200,300)),
        ("SEC-002","Agent A booking B","/api/v1/booking/"+other_id,token_a,{403,404}),
        ("SEC-003","Agent B booking A","/api/v1/booking/"+own_id,token_b,{403,404}),
        ("SEC-004","Agent B booking B","/api/v1/booking/"+other_id,token_b,range(200,300)),
    ]
    results=[]
    for cid,label,path,token,expected in cases:
        try:
            result=request_json(api_url,path,token)
            status=result["status"]
            # Never store response payloads: customer data may be sensitive.
            results.append({"id":cid,"scenario":label,"http_status":status,
                            "status":"PASS" if status in expected else "BLOCKED",
                            "response_sha256":hashlib.sha256(result["body"].encode()).hexdigest()})
        except (urllib.error.URLError,TimeoutError,OSError) as exc:
            results.append({"id":cid,"scenario":label,"status":"BLOCKED",
                            "error":type(exc).__name__})
    return {"commit_sha":target_sha.lower(),"target":urllib.parse.urlsplit(api_url).hostname,
            "timestamp":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tests":results,"status":"PASS" if all(x["status"]=="PASS" for x in results) else "BLOCKED",
            "note":"Read-only HTTP smoke evidence; this does not satisfy all 12 security cases."}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--api-url",default=os.getenv("M3_UAT_API_URL",""))
    parser.add_argument("--commit-sha",default=os.getenv("M3_TESTED_SHA",""))
    parser.add_argument("--booking-a",default=os.getenv("M3_BOOKING_A_ID",""))
    parser.add_argument("--booking-b",default=os.getenv("M3_BOOKING_B_ID",""))
    parser.add_argument("--output",type=Path,default=Path("m3-cross-agent-smoke.json"))
    args=parser.parse_args()
    try:
        result=run(args.api_url,os.getenv("M3_AGENT_A_SESSION",""),
                   os.getenv("M3_AGENT_B_SESSION",""),
                   args.booking_a,args.booking_b,args.commit_sha)
    except ValueError as exc:
        result={"status":"BLOCKED","reason":str(exc)}
    args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"reason":result.get("reason"),
                      "tests":[{"id":x["id"],"status":x["status"]}
                               for x in result.get("tests",[])]}))
    return 0 if result["status"]=="PASS" else 1


if __name__=="__main__":
    sys.exit(main())
