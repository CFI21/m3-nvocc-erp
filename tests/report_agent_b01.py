"""Render B01 Agent Master pytest XML evidence into GitHub's job summary."""
import os
from pathlib import Path
from xml.etree import ElementTree

reports = (
    ("Master Data governance", "agent-governance.xml"),
    ("Six-tab Agent Master UI", "agent-ui.xml"),
    ("Agent/port regressions", "agent-regression.xml"),
)
lines = ["## M3 B01 — Agent Master acceptance evidence", "",
         "| Suite | Tests | Failures | Errors | Skipped | Outcome |",
         "|---|---:|---:|---:|---:|---|"]
all_passed = True
for label, filename in reports:
    path = Path(filename)
    if not path.exists():
        all_passed = False
        lines.append(f"| {label} | — | — | — | — | Missing evidence |")
        continue
    root = ElementTree.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        all_passed = False
        lines.append(f"| {label} | — | — | — | — | Invalid evidence |")
        continue
    counts = {field: sum(int(s.get(field, "0")) for s in suites)
              for field in ("tests", "failures", "errors", "skipped")}
    good = counts["tests"] > 0 and counts["failures"] == 0 and counts["errors"] == 0
    all_passed &= good
    outcome = "PASS" if good else "FAIL"
    lines.append(f"| {label} | {counts['tests']} | {counts['failures']} | "
                 f"{counts['errors']} | {counts['skipped']} | {outcome} |")
lines.extend(["", f"**Overall B01 automated evidence: {'PASS' if all_passed else 'NOT PASS'}**",
              "", "Static UI checks do not replace functional browser acceptance.",
              "Production traffic, real money, and live providers remain OFF."])
summary = "\n".join(lines) + "\n"
print(summary)
if path := os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(summary)

# Make missing or failing suite evidence a failing CI gate, not just a summary line.
if not all_passed:
    raise SystemExit(1)
