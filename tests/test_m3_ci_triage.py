"""CI triage must report failing tests, missing reports, and no secrets."""
import xml.etree.ElementTree as ET
from scripts.m3_ci_triage import summarize, EXPECTED

def test_missing_reports_fail_closed(tmp_path):
    report=summarize(tmp_path)
    assert report["status"]=="BLOCKED"
    assert len(report["suites"])==4
    assert all(s["reason"]=="MISSING_REPORT" for s in report["suites"])

def test_redacted_test_failure_and_clean_suites(tmp_path):
    for filename in EXPECTED:
        suite=ET.Element("testsuite")
        case=ET.SubElement(suite,"testcase",
                           classname="tests.test_bookings",
                           name="test_agent_isolation")
        if filename==EXPECTED[0]:
            failure=ET.SubElement(case,"failure",type="AssertionError",
                                  message="SECRET CUSTOMER DATA")
            failure.text="PRIVATE_BOOKING_REFERENCE_123"
        ET.ElementTree(suite).write(tmp_path/filename)
    report=summarize(tmp_path)
    assert report["status"]=="BLOCKED"
    assert report["suites"][0]["failures"]==1
    assert report["suites"][1]["status"]=="PASS"
    assert "SECRET CUSTOMER DATA" not in str(report)
    assert "PRIVATE_BOOKING_REFERENCE_123" not in str(report)

def test_incomplete_and_skipped_suites_block(tmp_path):
    for filename in EXPECTED:
        suite=ET.Element("testsuite")
        case=ET.SubElement(suite,"testcase",name="case")
        ET.SubElement(case,"skipped")
        ET.ElementTree(suite).write(tmp_path/filename)
    assert summarize(tmp_path)["status"]=="BLOCKED"
