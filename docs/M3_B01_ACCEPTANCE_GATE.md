# M3 Agent Master B01 — Isolated Acceptance Gate

Scope: `preview-pr124-booking-isolated` only. No production/ANCLINE, no paid services, no real money/providers, no merge of PR #124, and no Booking B02 implementation until B01 acceptance.

## Automated gate

Existing workflow: `.github/workflows/m3-agent-acceptance.yml` (push to this branch).

Required suites:
1. `tests/test_clx048_admin_masterdata.py` — governed master data, maker/checker approval and persistence.
2. `tests/test_agent_master_ui_contract.py` — six tabs, data fields and UI guardrails.
3. `tests/test_final_fix_validation_cycle.py -k "agent or booking_port"` — Agent/port regression coverage.

All three suites must produce nonempty passing JUnit XML. `tests/report_agent_b01.py` rejects missing evidence and failures. Do not treat static UI contracts as live-browser acceptance.

## Protected runtime gate

Deploy to the existing isolated M3 preview API only after Render free-tier deployments recover. Confirm PostgreSQL Neon UAT target, `M3_UAT_PRESERVE_DATA=1`, non-destructive `python -m app.uat_bootstrap` startup, maker/checker separate active IAM users, correct access roles and scopes.

## Functional acceptance

Validate six Agent Master tabs (Agent Detail, Emails, Countries, Offices, CRO Detail, B/L Template); maker submit, checker approve/reject, four-eyes enforcement, save/reopen, correct country/office/port permissions, fresh IAM login/MFA, durable Neon readback and no data reset.

Status: NOT ACCEPTED until latest-commit CI evidence AND protected runtime UAT both pass.
