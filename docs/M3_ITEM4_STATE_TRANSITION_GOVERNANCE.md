# M3 TEST — Item 4 State Transition Governance

> DRAFT — HUMAN REVIEW REQUIRED

## 1. Summary
Hardens the existing authoritative CLX-071 container journey instead of creating a second state engine. Adds governed STANDARD, CORRECTION, OVERRIDE and EXCEPTION_RESOLUTION paths with immutable transition history.

## 2. Assumptions
- CLX-071 remains the authoritative container lifecycle.
- The existing transition map remains authoritative for normal transitions.
- Correction default window is 15 minutes and remains configurable.
- Human production approval boundaries remain unchanged.
- CRT remains the only post-approval change path for governed Import/Export/TS records.

## 3. Design
Modes:
- STANDARD: only an allowed existing CLX-071 transition.
- CORRECTION: recent factual correction within configured window; reason + source event + original timestamp required.
- OVERRIDE: bypass normal sequence only for manager-authorized human with mandatory reason.
- EXCEPTION_RESOLUTION: serious exception states require reason, evidence and two distinct human approvers; maker cannot be an approver.

Serious exception states:
DAMAGE_HOLD, CUSTOMS_HOLD, REPAIR, LOST, SEIZED, TOTAL_LOSS, SOLD, OFF_HIRE.

Financial-posted transitions are blocked from in-place mutation and require the existing financial reversal/correction process.

## 4. Database schema
New append-only table:
- state_transition_events

No duplicate container/event master is created.

## 5. API endpoints
No new public endpoint in Item 4. Governance is a reusable internal service intended to be called by the authoritative CLX-071 journey mutation path.

## 6. Approval gates
- STANDARD: no approval gate beyond existing RBAC.
- CORRECTION: human actor + reason/source/time.
- OVERRIDE: authorized manager human + reason.
- EXCEPTION_RESOLUTION: dual human approval + evidence; maker-checker enforced by user ID.
- Financial-posted: reversal/correction process only.

## 7. Audit events
Each attempted governed transition writes immutable history with:
- subject
- before/after state
- mode
- actor user ID/role
- reason
- evidence references
- approver user IDs
- source event
- financial-posted flag
- ALLOWED/BLOCKED outcome
- timestamp

## 8. Dashboard metrics
Potential read-only metrics:
- invalid transition attempts
- correction count
- override count
- serious exception resolutions
- financial-posted blocks
- transitions by state and branch

## 9. Tests
- standard allowed sequence
- invalid standard sequence blocked
- correction inside 15-minute window
- expired correction blocked
- override requires manager + reason
- exception resolution requires evidence + two human approvers
- maker cannot self-approve
- financial-posted in-place transition blocked
- immutable history update/delete blocked
- feature flag defaults OFF

## 10. Risks
- Existing CLX-071 has its own override flag; integration must not allow that legacy path to bypass Item 4 governance when the feature flag is enabled.
- Historical container events may not have governance rows; Item 4 applies prospectively.
- Finance/release/customs semantics are not reimplemented here.

## 11. Open questions
None blocking Item 4. A later integration pass must make the authoritative CLX-071 mutation route call this governance service before applying a state change.
