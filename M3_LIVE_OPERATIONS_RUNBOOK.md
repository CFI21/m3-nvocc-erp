# M3 Live Operations Runbook

Accepted operating baseline: M3 production-ready runtime with 196 authoritative screens, governed IAM, master data, Booking/Job/B-L, container/equipment, finance, GL and treasury.

## Operating principles

- Real production data and controlled UAT data must remain segregated.
- Job 92200, booking UAT-A220-92200, container UAT2200001 and their related UAT documents are test evidence only and must not contribute to production KPI, revenue, cost, cash or opening balances.
- Real external providers remain OFF. Real money remains OFF. Dummy Bank is the only bank-movement simulator.
- Common Party master data remains authoritative. Operational projections are derived/upserted representations, not second masters.
- Customer and Agent are independent parties and relate only through Booking/Job.
- Permanent Agent user USR-AGT-DE2D61A3B5 remains a non-blocking invite until personal password setup and MFA activation.

## Role SOP

### ADMIN
Manage platform configuration, governed user administration and exceptional operational support. Do not use Admin as a substitute for maker/checker separation. Review security/integrity gates after material releases.

### MASTER DATA
Submit new or changed masters through the governed change-request flow. Resolve duplicate/reference issues before approval. No hard delete. Do not import ambiguous or review-required rows.

### MASTER DATA MANAGER
Independently approve/reject master-data changes. Review alias/merge decisions, versions, data quality and reference integrity. Never self-approve a change created by the same actor.

### OPS
Create and maintain Booking/Job operational records, routing, planning, CRO/CRT and shipment milestones. Use POT/POT Agent only when an actual via-port/transshipment leg exists.

### DOCS
Maintain B/L, HBL/MBL, Switch/Split B/L, release/document controls and document completeness. Keep all document references on the authoritative Job context.

### AGENT
Access only the assigned Agent party and the Customer/job data reachable through authorized Bookings/Jobs. No Customer-wide or cross-Agent access.

### FINANCE
Maintain approved operational rates, AR/AP, invoice/bill/SOA and governed additional-cost controls. Additional costs after approval use the accepted controlled route only. No real-money provider movement while REAL_MONEY=OFF.

### GL
Post/review controlled accounting entries, mappings, FX, periods and reconciliations. Maintain balanced postings and separation from transactional operational edits.

### TREASURY
Manage controlled cash/reconciliation workflows using Dummy Bank only until explicit real-provider authorization. Preserve maker/checker and reversal audit controls.

### MANAGEMENT
Use Executive Home, KPI, Control Tower and exception views for production records only. Controlled UAT records are excluded from management metrics.

## First real shipment pilot

For each approved real shipment:
1. Create Booking using verified real Customer, Agent, routing and commercial data.
2. Create/confirm Job and one authoritative Job context.
3. Apply approved rate/special-rate workflow where needed.
4. Complete vessel/voyage planning and locks.
5. Create CRO and applicable CRT flow.
6. Use POT/Transshipment only if the route actually contains a via port.
7. Complete HBL/MBL and any governed document variants.
8. Link container/equipment and record actual movement/custody events.
9. Complete release and Delivery Order.
10. Record detention/storage/agent settlement only when applicable.
11. Create operational finance records, SOA, GL and Treasury control records while real money remains OFF.
12. Run integrity and exception checks after the pilot job.

## Data-quality acceptance after each real pilot

Required:
- duplicate master records = 0
- broken imported references = 0
- Booking/Job party mismatches = 0
- permanent Customer-Agent links = 0
- Agent Customer-wide access = 0
- IAM role/party-access orphans = 0
- unbalanced GL = 0
- unlinked production documents = 0
- unlinked production containers = 0
- P0/P1 defects = 0
- API/Web 5xx = 0

## Backup and recovery

M3-NVOCC-PROD is currently in a Supabase Free organization. Supabase documentation states that Free projects should regularly create logical off-site backups with Supabase CLI `db dump`/PostgreSQL dump tooling; managed daily backup retention is a paid-plan feature.

Existing in-database backup/restore rehearsal schemas prove logical recovery mechanics but are not an independent off-site disaster-recovery copy.

Before materially increasing real-business volume:
- create a regular off-site logical database dump using a controlled operator/CI environment with production DB credentials;
- keep backup credentials out of repository and logs;
- retain at least one verified recent dump outside the production database;
- periodically restore into an isolated environment and reconcile critical counts;
- document actual measured restore time after the first off-site restore drill.

Free-tier operating target until measured otherwise:
- RPO target: <= 24 hours only if a daily external dump is actually scheduled and verified;
- RTO target: not yet certified; record the measured duration of the first off-site restore drill.

Do not claim backup/recovery readiness until the off-site dump and isolated restore drill are verified.

## Live-provider controls

Keep:
- M3_PRODUCTION_TRAFFIC=ON
- M3_LIVE_PROVIDERS=OFF
- REAL_MONEY=OFF
- Dummy Bank=ON

TrueLayer, OpenExchangeRates and Avalara may be contract/sandbox tested but must not be activated without explicit authorization.
