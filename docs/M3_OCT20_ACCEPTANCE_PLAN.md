# M3 NVOCC ERP — October 20, 2026 Acceptance Gate

Status: **BLOCKED until evidence is attached**.
Scope: Existing M3 only. Source branch `preview-pr124-booking-isolated`. PR #124 remains draft and unmerged on a distinct head branch. Production / ANCLINE / providers / real money remain untouched.

## Definition of done
"Done" means the applicable live authenticated business workflow is usable, authorized, tested and audited. A source commit or green isolated unit test alone is insufficient. Do not certify the full 196-screen ERP without a complete current acceptance matrix.

## Execution sequence
### October 17–18 — P0 security + reproducible build
- Capture branch SHA, PR head SHA, isolated web/API runtime SHA and database target (read-only).
- Run compileall, B01/B02 tests and full pytest in disposable environment. Record GitHub run URL, JUnit reports, failures, skips and artifacts.
- Audit and block unauthorized header-role / caller-provided Agent, Branch and Depot scopes (MRG / CLX070 / connected screens and booking APIs). Test anonymous and cross-Agent negative cases.
- Stop condition: missing test evidence or any unauthorized financial/party data access = BLOCKED.

### October 18–19 — B01/B02 operational core
- Test six Agent tabs: Agent Detail, Emails, Countries, Offices, CRO Detail and B/L Template for correct persistence, duplicate protection, maker/checker independence and error handling.
- Prove IAM-derived multi-office Agent assignment and effective Port Master POL/POD/TS grants; never derive Agent ownership solely from user office membership.
- Authenticated positive + negative transaction test: Booking Draft → RFA → Approved → Job → B/L. Verify references, locked approved fields, role-specific numbering, audit and idempotency.
- Confirm one user cannot access another Agent/customer's bookings, rates or documents.
- Stop condition: no trusted multi-office mapping or failed port-grant checks = BLOCKED.

### October 19–20 — integration and readiness decision
- Test container/equipment lifecycle; detention evidence rules; charge/MRG → invoice → AR/AP → GL → SOA traceability in dummy/offline mode.
- Run complete 196-screen navigation, action, field, scope and audit validation. Verify frontend/API preview deploy SHA matches audited source.
- Review Agent/Customer/Vendor portals and Principal Tower against effective-dated IAM/Party relationships. No implicit pass for partial implementations.
- Produce signed PASS/BLOCKED per-module matrix and critical/high/medium defect register, each with owner, reproducible steps and evidence.

## Evidence register
| Gate | Current status | Required proof |
| --- | --- | --- |
| CI compilation/full pytest | NOT VERIFIED | Run URL, SHA, JUnit/XML, logs, test counts |
| P0 IAM/MRG/CLX070 scopes | BLOCKED | Authenticated and anonymous negative API test results |
| Agent six tabs | NOT VERIFIED | Six-tab UAT with persisted records and audit |
| Multi-office/Port grants | BLOCKED | Authorized IAM/Port mapping with cross-Agent denial |
| Booking → Job → B/L | NOT VERIFIED | Recorded full lifecycle test with IDs and logs |
| Container/finance/SOA | NOT VERIFIED | Dummy transaction/ledger reconciliation |
| Portals and Principal Tower | NOT VERIFIED | Per-role tested matrix, safe documents/exports |
| Exactly 196 screens | NOT VERIFIED | Catalog count and per-screen automated + UI results |
| Preview runtime parity | NOT VERIFIED | Web/API deploy IDs and tested SHA |
| Production release | NOT AUTHORIZED | Separate explicit release approval after all critical gates PASS |

## Acceptance decision on October 20
Choose **CORE PASS**, **LIMITED UAT ONLY**, or **BLOCKED** based on attached evidence. **FULL ERP PASS** is permitted only if all in-scope gates pass. No merge or production release without separate approval.

## Deferred backlog if blocked
Per prioritized work packages: (1) trusted IAM + effective Party/Agent/Office/Depot scope, (2) centralized Party / GL and multi-role master persistence, (3) Agent six-tab acceptance, (4) Booking/B-L operational core, (5) equipment/finance reconciliation, (6) Agent/Customer/Vendor portals and Principal Tower, (7) 196-screen regression and release readiness.
