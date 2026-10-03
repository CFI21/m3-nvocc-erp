# M3 TEST — Item 5 Release Governance

> DRAFT — HUMAN REVIEW REQUIRED

## 1. Summary
Extends the existing `nvocc_release_controls` header to HBL + container-level release authority. No second release master is created.

## 2. Assumptions
- Existing `nvocc_release_controls` remains the authoritative release header.
- Release authority is HBL + container level; Job is summary only.
- Existing HBL, workflow/customs, finance, hold and Delivery Order data remain authoritative prerequisite sources.
- Negative margin does not block release by default.
- AI/service identities never approve conditional release.

## 3. Design
States:
`BLOCKED | PENDING | CONDITIONAL | PARTIALLY_RELEASED | RELEASED | RELEASED_WITH_POST_DELIVERY_HOLD | REVOKED | EXPIRED`

Prerequisites:
- valid issued HBL/document authority
- customs clearance
- payment clearance
- no credit hold unless a valid CREDIT_OVERRIDE exists
- surrender/telex/original authority unless a valid ORIGINAL_WAIVER exists
- no legal/compliance/document hold
- container eligible

Conditional types:
BG / LOI / CREDIT_OVERRIDE / ORIGINAL_WAIVER / MANAGEMENT_APPROVAL / CUSTOMS_CONDITIONAL.

Conditional authority is narrow:
- CREDIT_OVERRIDE bypasses only the credit-hold gate, not payment/outstanding or other gates.
- ORIGINAL_WAIVER bypasses only surrender/original requirement.
- CUSTOMS_CONDITIONAL bypasses only customs gate during validity.
- All conditions require human checker, reason and validity.

Partial release is derived when only part of the release header's containers are released.

Pre-delivery HOLD/REVOKE → REVOKED.
Post-delivery HOLD/REVOKE → RELEASED_WITH_POST_DELIVERY_HOLD and physical history is not rewritten.

Delivery Order is eligible only for HBL + containers with a governed released/conditional status.

## 4. Database schema
Existing master retained:
- nvocc_release_controls

Added child/control tables:
- nvocc_release_container_control
- nvocc_release_events (append-only)

## 5. API endpoints
No new public endpoint in Item 5. Internal service functions enforce release and Delivery Order eligibility. Existing release workspace remains the UI/API authority.

## 6. Approval gates
Conditional release requires:
- human actor
- distinct human approver
- reason
- validity
- condition type

No AI approval is permitted.

## 7. Audit events
Every container release action logs:
- release ref
- container
- previous/new release state
- action
- actor user ID
- reason
- prerequisite snapshot
- condition type
- post-delivery-hold marker
- timestamp

Release events are immutable.

## 8. Dashboard metrics
Potential read-only metrics:
- blocked releases by reason
- partial releases
- conditional releases by type
- revoked releases
- post-delivery holds
- expired conditional releases
- DO eligibility blocks

## 9. Tests
- HBL + container scoping
- prerequisite blocking
- full release
- partial release
- conditional credit override does not bypass payment
- original waiver/customs conditional are narrow
- maker/checker separation
- pre-delivery revoke
- post-delivery hold
- DO blocks unreleased container
- immutable release events
- feature flag defaults OFF

## 10. Risks
RISK: HUMAN REVIEW REQUIRED
- Cargo release is operationally binding in production. Item 5 remains test/draft only.
- Existing Job-level workflow release_status becomes summary only and must not be treated as container authority.
- Legacy release actions must call this governance layer when the Item 5 flag is enabled.

## 11. Open questions
None blocking Item 5. Release UI refinement can occur later without changing the frozen authority model.
