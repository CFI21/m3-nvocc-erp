# M3 TEST — Item 2 Standard vs Consolidation Job

> DRAFT — HUMAN REVIEW REQUIRED

## 1. Summary
Implements the frozen Job modes without creating a second Job model.

- STANDARD Job: default 1 Booking → 1 Job.
- Split execution: 1 Booking → N Jobs using distinct split sequences.
- CONSOLIDATION Job: N Bookings → 1 Job using one consolidation reference.

## 2. Assumptions
- Existing `jobs` remains the authoritative Job table.
- `job_booking_links` becomes the authoritative Booking↔Job relationship.
- Existing `jobs.booking_id` is retained only as a compatibility anchor for legacy code and is not commercial authority for a Consolidation Job.
- Revenue/customer ownership is not moved to Job level.
- Item 2 does not implement shared-cost allocation; that remains governed by later enforcement.

## 3. Design
STANDARD:
Booking → Job with split_sequence=1 by default.

SPLIT:
Same Booking may link to multiple Jobs using split_sequence 1..N.

CONSOLIDATION:
Multiple Bookings link to one Job through `job_booking_links`.
`consolidation_ref` is unique for the consolidated operational group.
The booking set is hashed; reusing the reference with a different set is rejected.

## 4. Database schema
- Remove the uniqueness restriction on `jobs.booking_id`.
- Add `job_mode_profiles`.
- Add `job_booking_links`.
- No second jobs table.

## 5. API endpoints
No public endpoint is introduced yet.
The module exposes internal service functions only.

## 6. Approval gates
No approval occurs in this module.
All output remains draft/test until human-reviewed deployment.

## 7. Audit events
Relationship records preserve Job, Booking, sequence, mode and consolidation identity.
Existing Job creation idempotency remains the retry/duplicate control for Standard/Split creation.

## 8. Dashboard metrics
Future metrics:
- Standard Jobs
- Split Jobs
- Consolidation Jobs
- Bookings per Consolidation
- Jobs per Booking

No dashboard change in Item 2.

## 9. Tests
- Standard default relationship
- one Booking → multiple split Jobs
- multiple Bookings → one Consolidation Job
- consolidation replay
- consolidation-reference conflict
- minimum Booking count
- feature flag defaults OFF

## 10. Risks
- Schema: uniqueness on `jobs.booking_id` is deliberately relaxed to support the frozen cardinality.
- Commercial ownership: legacy `jobs.booking_id` must never become the authoritative source for consolidated revenue/customer ownership.
- Finance/tax/customs: unchanged in Item 2.
- Production: no production migration or deployment is performed by AI.

## 11. Open questions
None for Item 2 implementation. Later modules must consume `job_booking_links` rather than infer consolidated commercial ownership from `jobs.booking_id`.
