#!/usr/bin/env python3
"""Fail-closed public probe of M3 isolated UI/API/DB readiness; no credentials."""
import json
import urllib.request
import urllib.error
import sys

API="https://m3-pr124-booking-api-preview.onrender.com"
UI="https://m3-pr124-booking-ui-preview.onrender.com"
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args): return None

def fetch(url):
    with urllib.request.build_opener(NoRedirect()).open(
        urllib.request.Request(url,headers={"Accept":"application/json, text/html"}),
        timeout=25
    ) as response:
        if response.status!=200: raise ValueError("UNEXPECTED_HTTP_STATUS")
        return response.read(131072).decode("utf-8","replace")

def validate_health(data):
    return (isinstance(data,dict) and data.get("project")=="M3 NVOCC ERP"
            and data.get("sandbox_only") is True
            and data.get("production_promoted") is False
            and data.get("real_payment_execution") is False)

def validate_readiness(data):
    return (isinstance(data,dict) and data.get("ready") is True
            and data.get("database_health")=="ok"
            and data.get("database_target_approved") is True
            and data.get("production_traffic")=="OFF"
            and data.get("live_providers")=="OFF"
            and data.get("real_money")=="OFF")

def main():
    results={}
    for name,url,check in (
        ("api_health",API+"/api/v1/health",validate_health),
        ("database_readiness",API+"/api/clx016/readiness",validate_readiness),
        ("frontend",UI,lambda raw: "<html" in raw.lower() and "m3" in raw.lower()),
    ):
        try:
            raw=fetch(url)
            results[name]="PASS" if check(raw if name=="frontend" else json.loads(raw)) else "BLOCKED"
        except (ValueError,UnicodeError,TimeoutError,OSError,urllib.error.URLError):
            results[name]="BLOCKED"
    result={"status":"PASS" if all(s=="PASS" for s in results.values()) else "BLOCKED","checks":results}
    print(json.dumps(result,sort_keys=True))
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__": sys.exit(main())
