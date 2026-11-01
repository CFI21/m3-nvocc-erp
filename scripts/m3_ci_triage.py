#!/usr/bin/env python3
"""Summarize real JUnit failures without publishing captured customer data."""
import argparse
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

EXPECTED=("m3-security-auth.xml","m3-b01-b02-focused.xml",
          "m3-cross-agent-integration.xml","m3-full-regression.xml")
def summarize(folder):
    result={"status":"PASS","suites":[]}
    for name in EXPECTED:
        path=folder/name
        item={"file":name,"status":"BLOCKED","tests":0,"failures":0,
              "errors":0,"skipped":0,"failed_cases":[]}
        if not path.exists():
            item["reason"]="MISSING_REPORT"
        else:
            try:
                root=ET.parse(path).getroot()
                cases=list(root.iter("testcase"))
                item["tests"]=len(cases)
                for case in cases:
                    failures=[c for c in case if c.tag in ("failure","error")]
                    if case.find("skipped") is not None:
                        item["skipped"]+=1
                    for failure in failures:
                        item["failures" if failure.tag=="failure" else "errors"]+=1
                        item["failed_cases"].append({
                            "test_id":case.get("classname","")+"::"+case.get("name",""),
                            "error_type":re.sub(r"[^a-zA-Z0-9_.:-]","",failure.get("type",""))[:120],
                        })
                item["status"]=("PASS" if cases and not item["failures"]
                                and not item["errors"] and not item["skipped"]
                                else "BLOCKED")
                if not cases: item["reason"]="NO_EXECUTED_TESTS"
            except (ET.ParseError,OSError,ValueError):
                item["reason"]="INVALID_REPORT"
        if item["status"]!="PASS": result["status"]="BLOCKED"
        result["suites"].append(item)
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--dir",type=Path,default=Path("."))
    parser.add_argument("--output",type=Path,default=Path("m3-ci-triage.json"))
    args=parser.parse_args()
    report=summarize(args.dir)
    args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    return 0 if report["status"]=="PASS" else 1

if __name__=="__main__":
    sys.exit(main())
