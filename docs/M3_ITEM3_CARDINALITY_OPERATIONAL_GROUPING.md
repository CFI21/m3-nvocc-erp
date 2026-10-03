# M3 TEST — Item 3 Cardinality + Operational Grouping

> DRAFT — HUMAN REVIEW REQUIRED

## 1. Summary
Adds an additive relationship/control layer for the frozen M3 cardinalities and operational grouping rules. Existing Booking, Job, B/L, CRO, TRT and Container models remain authoritative.

## 2. Assumptions
- Item 2 remains authoritative for Booking↔Job Standard/Split/Consolidation relationships.
- Existing `bills` remains authoritative for HBL/MBL.
- Existing `transaction_records` remains authoritative for CRO/TRT.
- Existing `containers` remains authoritative for container instances.
- Operational grouping is coordination only and cannot own commercial or financial truth.

## 3. Design
Frozen cardinalities:
- Booking 1:1 Job default.
- Booking 1:N Job for split execution.
- Booking N:1 Job for consolidation.
- Job 1:N HBL.
- Job 1:N MBL.
- MBL N:M HBL where operationally justified.
- CRO 1:N Container.
- CRO 1:N TRT where operationally required.
- TRT 1:N Container.
- Job 1:N Cost Line remains owned by existing finance/GL models.

Operational groups:
- can contain multiple Jobs;
- may represent consolidation, TS, vessel, terminal, or CRO/TRT coordination;
- contain no customer, revenue, cost, margin, invoice, bill or GL authority fields;
- cannot merge records across Jobs;
- cannot replace Job P&L.

## 4. Database schema
Relationship tables only:
- operational_groups
- operational_group_jobs
- mbl_hbl_links
- cro_container_links
- cro_trt_links
- trt_container_links

No duplicate master tables.

## 5. API endpoints
No public endpoint is introduced in Item 3. Internal service functions create/query groups and relationship links. Public exposure can be added later only through the existing authoritative workspaces.

## 6. Approval gates
No approval decision is introduced in Item 3. Existing CRT governance remains the only post-approval modification path.

## 7. Audit events
Relationship tables record creator and creation time. Existing immutable audit remains authoritative for material business-state changes. Future UI/API exposure should append audit events without creating a parallel audit model.

## 8. Dashboard metrics
Potential read-only metrics:
- Jobs per operational group
- HBL/MBL links per Job
- Containers per CRO
- TRTs per CRO
- Containers per TRT
- cross-Job merge violations prevented

## 9. Tests
- operational group accepts multiple Jobs
- membership replay is idempotent
- membership conflict is rejected
- group has operational-only authority
- one Job can have multiple HBLs/MBLs
- MBL↔HBL links remain within Job
- CRO↔Container same-Job enforcement
- CRO↔TRT same-Job enforcement
- TRT↔Container same-Job enforcement
- feature flag defaults OFF

## 10. Risks
- Cross-Job grouping could be misused as a commercial merge if downstream code reads group membership as ownership. Explicit authority_scope and tests prohibit this.
- Existing legacy code may still infer one HBL in convenience queries; Item 3 does not rewrite all consumers yet.
- Finance/customs/tax behavior is unchanged.

## 11. Open questions
None blocking Item 3. Shared-cost allocation remains a later governed module and must not be inferred from operational grouping.
