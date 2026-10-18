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


APPROVED_HOST = "m3-pr124-booking-api-preview.onrender.com"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_target(base):
    parsed = urllib.parse.urlsplit(base)
    if (parsed.scheme != "https" or parsed.hostname != APPROVED_HOST
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("APPROVED_PREVIEW_API_REQUIRED")


def request_json(base, path, token, timeout=15):
    _validate_target(base)
    url=base.rstrip("/") + path
    opener=urllib.request.build_opener(_NoRedirect())
    req=urllib.request.Request(url,headers={"X-M3-Session":token,"Accept":"application/json"})
    try:
        with opener.open(req,timeout=timeout) as response:
            if 300 <= response.status < 400:
                raise ValueError("UNEXPECTED_AUTH_REDIRECT")
            raw=response.read(262144)
            return {"status":response.status,"body":raw.decode("utf-8","replace")}
    except urllib.error.HTTPError as error:
        raw=error.read(262144)
        # Redirects must never count as an authorization denial: a reverse
        # proxy or sign-in redirect is not proof of object-level isolation.
        if 300 <= error.code < 400:
            raise ValueError("UNEXPECTED_AUTH_REDIRECT")
        return {"status":error.code,"body":raw.decode("utf-8","replace")}


def run(api_url, token_a, token_b, own_id, other_id, target_sha):
    _validate_target(api_url)
    if not token_a or not token_b or token_a==token_b:
        raise ValueError("DISTINCT_AUTHENTICATED_SESSIONS_REQUIRED")
    if (not own_id.isascii() or not other_id.isascii()
            or not own_id.isdecimal() or not other_id.isdecimal()
            or int(own_id)<=0 or int(other_id)<=0
            or int(own_id)==int(other_id)):
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
            allowed=status in expected
            # A denied cross-agent request must not return a response containing
            # another agent's data. A 403/404 with embedded records is leakage.
            if allowed and status in (403, 404):
                try:
                    denied=json.loads(result["body"])
                except (ValueError,TypeError):
                    denied=None
                sensitive={"job_ref","agent_code","customer_name","fields",
                           "records","payload_json","booking_ref","agent_id",
                           "booking_id","customer_id","container_id"}
                def contains_sensitive(value, depth=0):
                    # Unknown/deep response structures cannot establish isolation.
                    if depth > 32:
                        return True
                    if isinstance(value,dict):
                        return (bool(sensitive.intersection(
                                    str(key).lower() for key in value))
                                or any(contains_sensitive(v,depth+1)
                                       for v in value.values()))
                    if isinstance(value,list):
                        return any(contains_sensitive(v,depth+1) for v in value)
                    return False
                if contains_sensitive(denied):
                    allowed=False
            # Own-resource success must identify the requested transaction.
            # A generic 200/login page is not evidence of successful scoped access.
            if allowed and 200 <= status < 300:
                try:
                    payload=json.loads(result["body"])
                    requested_id=int(path.rsplit("/",1)[-1])
                    allowed=(isinstance(payload,dict)
                             and payload.get("id")==requested_id
                             and payload.get("module")=="booking")
                except (ValueError,TypeError):
                    allowed=False
            # Never persist customer fields or session tokens in evidence.
            results.append({"id":cid,"scenario":label,"http_status":status,
                            "status":"PASS" if allowed else "BLOCKED",
                            "response_sha256":hashlib.sha256(result["body"].encode()).hexdigest()})
        except (urllib.error.URLError,TimeoutError,OSError,ValueError) as exc:
            results.append({"id":cid,"scenario":label,"status":"BLOCKED",
                            "error":type(exc).__name__})
    return {"commit_sha":target_sha.lower(),"target":urllib.parse.urlsplit(api_url).hostname,
            "timestamp":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tests":results,"status":"PASS" if all(x["status"]=="PASS" for x in results) else "BLOCKED",
            "note":"Read-only HTTP smoke evidence; this does not satisfy all 12 security cases."}



def check_list_scope(api_url, token_a, token_b, booking_a, booking_b):
    """Read-only bulk-list IDOR validation; verify own record and exclude foreign."""
    _validate_target(api_url)
    results=[]
    for cid, token, own, foreign in (
        ("SEC-005",token_a,int(booking_a),int(booking_b)),
        ("SEC-006",token_b,int(booking_b),int(booking_a)),
    ):
        try:
            response=request_json(api_url,"/api/v1/booking",token)
            valid=False
            if response["status"]==200:
                body=json.loads(response["body"])
                if (isinstance(body,dict) and body.get("module")=="booking"
                        and isinstance(body.get("records"),list)
                        and isinstance(body.get("count"),int)
                        and body["count"]==len(body["records"])):
                    records=body["records"]
                    ids=[item.get("id") for item in records if isinstance(item,dict)]
                    valid=(len(ids)==len(records) and own in ids
                           and foreign not in ids
                           and all(item.get("module")=="booking" for item in records))
            results.append({
                "id":cid,"status":"PASS" if valid else "BLOCKED",
                "http_status":response["status"],
                "response_sha256":hashlib.sha256(response["body"].encode()).hexdigest(),
            })
        except (ValueError,TypeError,urllib.error.URLError,TimeoutError,OSError) as exc:
            results.append({"id":cid,"status":"BLOCKED","error":type(exc).__name__})
    return results



def check_unauthenticated(api_url, booking_a, booking_b):
    """SEC-007/008: missing session cannot read either agent's booking."""
    _validate_target(api_url)
    results=[]
    for sid, booking in (("SEC-007",booking_a),("SEC-008",booking_b)):
        try:
            response=request_json(api_url,"/api/v1/booking/"+booking,"")
            status=response["status"]
            valid=status==401
            results.append({"id":sid,"http_status":status,
                            "status":"PASS" if valid else "BLOCKED",
                            "response_sha256":hashlib.sha256(response["body"].encode()).hexdigest()})
        except (ValueError,TypeError,urllib.error.URLError,TimeoutError,OSError) as exc:
            results.append({"id":sid,"status":"BLOCKED","error":type(exc).__name__})
    return results


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--api-url",default=os.getenv("M3_UAT_API_URL",""))
    parser.add_argument("--commit-sha",default=os.getenv("M3_TESTED_SHA",""))
    parser.add_argument("--booking-a",default=os.getenv("M3_BOOKING_A_ID",""))
    parser.add_argument("--booking-b",default=os.getenv("M3_BOOKING_B_ID",""))
    parser.add_argument("--output",type=Path,default=Path("m3-cross-agent-smoke.json"))
    args=parser.parse_args()
    try:
        token_a=os.getenv("M3_AGENT_A_SESSION","")
        token_b=os.getenv("M3_AGENT_B_SESSION","")
        result=run(args.api_url,token_a,token_b,
                   args.booking_a,args.booking_b,args.commit_sha)
        result["tests"].extend(check_list_scope(
            args.api_url,token_a,token_b,args.booking_a,args.booking_b))
        result["tests"].extend(check_unauthenticated(
            args.api_url,args.booking_a,args.booking_b))
        result["status"]="PASS" if all(x["status"]=="PASS" for x in result["tests"]) else "BLOCKED"
    except ValueError as exc:
        result={"status":"BLOCKED","reason":str(exc)}
    args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"reason":result.get("reason"),
                      "tests":[{"id":x["id"],"status":x["status"]}
                               for x in result.get("tests",[])]}))
    return 0 if result["status"]=="PASS" else 1


if __name__=="__main__":
    sys.exit(main())
