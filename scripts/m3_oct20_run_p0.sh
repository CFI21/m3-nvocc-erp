#!/usr/bin/env bash
# Deterministic offline CI + P0 test execution; never touches production.
set -Eeuo pipefail
export M3_PRODUCTION_TRAFFIC=OFF
export M3_B01_STRICT_MAKER_CHECKER=true
mkdir -p acceptance-evidence
# Prevent old JUnit files or acceptance reports from being mistaken for this run.
find acceptance-evidence -maxdepth 1 -type f \( -name "*.xml" -o -name "*.log" -o -name "release-verdict.json" \) -delete
sha="$(git rev-parse HEAD)"
printf '%s\n' "$sha" > acceptance-evidence/commit-sha.txt
run_suite() {
  local label="$1"; shift
  set +e
  python -m pytest -q -ra --tb=short "$@" \
    "--junitxml=acceptance-evidence/${label}.xml" \
    >"acceptance-evidence/${label}.log" 2>&1
  local status=$?
  set -e
  cat "acceptance-evidence/${label}.log"
  return "$status"
}
failed=0
python -m compileall -q app tests || failed=1
run_suite m3-security-auth tests/test_m3_unsigned_header_authorization.py || failed=1
run_suite m3-b01-b02-focused \
  tests/test_b01_email_port_gate.py \
  tests/test_b01_office_approval_duplicate.py \
  tests/test_b01_agent_countries_iam_resolution.py \
  tests/test_b02_booking_port_authorization.py \
  tests/test_b02_agent_booking_boundary_integration.py \
  tests/test_m3_p0_lifecycle_contract.py \
  tests/test_m3_cross_agent_smoke.py \
  tests/test_unlocode_master_catalog.py \
  tests/test_unlocode_release_adapter.py || failed=1
run_suite m3-cross-agent-integration tests/test_b02_agent_booking_boundary_integration.py || failed=1
run_suite m3-full-regression tests || failed=1
python scripts/m3_oct20_acceptance.py \
  --commit-sha "$sha" \
  --evidence-dir acceptance-evidence \
  --output acceptance-evidence/release-verdict.json || echo "Release acceptance BLOCKED: UAT/preview evidence missing or invalid"
# A successful code regression gate is not final release acceptance.
# Keep the CI result independent from the deliberately fail-closed UAT verdict.
printf 'AUTOMATED_REGRESSION_STATUS=%s COMMIT=%s\n' "$([ "$failed" -eq 0 ] && echo PASS || echo BLOCKED)" "$sha"
exit "$failed"
