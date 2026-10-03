# M3 TEST — Item 1 Job Creation Idempotency

> DRAFT — HUMAN REVIEW REQUIRED

## 1. Summary
Adds a permanent business-idempotency guard for Job creation. The guard ensures one business creation identity executes its Job-creation callback once and replays the original Job on later identical attempts.

## 2. Assumptions
- Item 1 does not change Booking↔Job cardinality.
- Existing `jobs.booking_id UNIQUE` remains untouched until Item 2.
- Actual Standard vs Consolidation Job behavior belongs to Item 2.
- Synthetic data only is used in tests.
- `M3_JOB_IDEMPOTENCY_ENABLED` defaults OFF until a human enables it in an approved non-production/runtime configuration.

## 3. Design
Permanent identity:

`BOOKING_REF + JOB_CREATION_PURPOSE + SPLIT_SEQUENCE + CONSOLIDATION_REF`

The identity has no TTL.

States:
`IN_PROGRESS → CREATED`
or
`IN_PROGRESS → FAILED → IN_PROGRESS` on a deliberate retry.

A completed identity replays the original Job and returns `JOB_ALREADY_CREATED`.

A reused identity with a different request hash is rejected as `IDEMPOTENCY_KEY_REUSE_CONFLICT`.

## 4. Database schema
New additive table:
`job_creation_idempotency`

No existing table/column is dropped or altered.

## 5. API endpoints
No public endpoint is introduced in Item 1. This is a reusable enforcement primitive that Item 2 will call from the authoritative Job-creation service.

## 6. Approval gates
No approval action exists in this module.
AI cannot approve or create an APPROVED state.
Human approval remains required for PR merge and any runtime enablement.

## 7. Audit events
The table records:
- permanent business key
- booking reference
- purpose
- split sequence
- consolidation reference
- request hash
- status
- Job identity
- attempts
- last failure type
- timestamps
- actor ID

## 8. Dashboard metrics
Future dashboard metrics may derive:
- replay count
- failed/retried creation count
- idempotency conflicts

No dashboard change is made in Item 1.

## 9. Tests
- deterministic permanent business key
- same intent creates exactly one synthetic Job
- repeat returns original Job
- changed payload under same key is rejected
- failed attempt may retry without key expiry
- feature flag defaults OFF

## 10. Risks
- Money: none; no financial posting logic changed.
- Customs: none.
- Tax: none.
- Compliance: auditability improved.
- Security: no credentials or secrets added.
- Data model: cardinality intentionally unchanged until Item 2.

## 11. Open questions
None for Item 1. Standard vs Consolidation Job cardinality is explicitly deferred to Item 2.
