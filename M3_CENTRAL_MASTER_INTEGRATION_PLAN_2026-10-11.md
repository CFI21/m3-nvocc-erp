# M3 Central Master Data & Principal Network Integration — Execution Contract
Date: 2026-10-11
Status: PLANNED / SECURITY GATE BLOCKED. This file is a design and acceptance contract, NOT proof of implementation.
Scope: Existing M3 NVOCC ERP only. Preserve all 196 screens, accepted UI template, current tables, PR #124 draft/unmerged, production unchanged, providers and real money OFF.

## Authoritative master architecture
- Existing `masterdata.py` domains include `common-party`, `customer`, `gl-account`, `reference-sequence`, `bank-account` and `charge-code`.
- Common Parties is the authoritative organization identity; a party can carry multiple approved roles (Principal, Agent, Investor, Customer, Vendor, Carrier, Depot, Terminal, Shipper, Consignee). Do not create duplicate organization records by role.
- Model HO, Principal, Agent and Business scopes as approved, effective-dated relationships using an existing relationship mechanism if present. Do not invent a table until current storage and permissions have been examined.
- Central Party ID is immutable after approval. Role-specific subledger/account references and GL control accounts are separate, approved financial mappings. Preserve every active reference-sequence counter; sequence allocation must be atomic and non-reusable.
- Never infer Agent ownership from untrusted request headers; derive effective scope from authenticated IAM and approved relationships.

## UI: existing screens and fields
1. PA Parties: Details / Address / Contact / A/R / A/P. Keep source reference's field names and groupings, but existing M3 grey/green compact master template. Support multiple addresses, capabilities, contacts and business-role checkboxes only when backed by approved persistence.
2. Principal Setup: authenticated linked network, Principal-wise control, View / Rectify / Approve / Hold / Release / Audit subject to IAM.
3. Investor Setup: shared Party ID and existing equipment investor / share accounting where supported.
4. GL / AR / AP: party-specific subledger and centralized control-account mappings; no independent journal or duplicate ledger.
5. Global Equipment Tower: authorized drilldown Principal > Region > Country > Agent > container journey > finance/exception, using existing equipment and journey APIs.
6. Booking, B/L, MRG, Charge, SOA: resolved Party IDs, approved ownership and finance scopes; no redundant copies.

## Verified audit observations (preview branch source)
- `web/index.html`: PA Parties reads `/api/masterdata/records/common-party`; its Details, Addresses and Contacts views are currently read-only; direct entry for A/R and A/P is not completed.
- `web/index.html`: Principal Setup and Investor Setup already link to Common Parties and existing container APIs.
- `app/masterdata.py`: `GET /records/{domain}` currently has no session requirement in the handler; this is a security blocker for scoped parties. A proper authenticated IAM and authorized record filtering design must be implemented and tested, including downstream screens relying on this endpoint.
- `app/masterdata.py`: changes use authenticated maker-checker; reference sequence next requires an authenticated maker; neither should be bypassed.
- Prior CLX078 and CLX079 test artifacts cover historical GET UI; they do not validate current Principal/Agent confidentiality.

## Ordered execution / acceptance gates
P0 Read-only cross-module inventory: map screens, APIs, tables, ownership, all related refs. Capture incompatibilities before changing code.
P1 Secure master read and lookup endpoints with authenticated role and effective ownership; verify HO, Principal, Export Agent, Import Agent, Transshipment Agent and deny unauthenticated/cross-agent requests, including exports. Avoid breaking login/bootstrap.
P2 Complete Common Party PA form with screen's exact fields; implement proposed changes via existing change requests and checker approvals. Multi-address/contact storage must be assessed for existing support before choosing encoding.
P3 Approved Principal-Agent-Investor relationship matrix and role-specific visibility; no inheritance beyond linked organizations.
P4 Atomic Party Code assignment; distinguish Party ID, customer/vendor/agent subledger ID and GL control accounts; verify idempotency and duplicate matching without resetting counters.
P5 Existing Booking/B-L/MRG/Finance/SOA integration: resolve IDs from same masters and prevent unauthorized financial adjustments.
P6 GET regional/global financial and container controls; all movements drill down to authoritative booking/container event and ledger, with audit/hold state.
P7 UI/UAT: field-level persistence, maker-checker, wrong-role denial, duplicate checks, sequence concurrency, GL posting, agent isolation, historical 196-screen regression; verify web/API commit versions on preview.
P8 Only after acceptance and explicit authorization, evaluate merge/release. Production never changes automatically.

## Release gates (must report PASS/BLOCKED, not assume)
AUTHENTICATED_SCOPE; PRINCIPAL_RELATION; CROSS_AGENT_ISOLATION; SHARED_PARTY_ID; FORM_FIELD_PERSISTENCE; APPROVAL; SEQUENCE_UNIQUENESS; GL_MAPPING; FINANCE_RECONCILIATION; GLOBAL_TOWER_DRILLDOWN; 196_SCREEN_REGRESSION; PREVIEW_SMOKE.
Historical passes do not grant a current release.

## Next engineering task
Investigate authorization of `app/masterdata.py GET /records/{domain}` and caller migration (web lookup bootstrapping and 196 screens). Implement and validate a safe scoped access policy on the isolated preview branch before exposing customer/agent records to Principal views.

## Authorized portal delivery sequence (approved)
1. Agent Portal FIRST: agent-owned customers/vendors, role-based Export/Import/Transshipment operations, own bookings/B-L, container movements, rate visibility, documents, invoices and Agent SOA. Principal can supervise only expressly linked agents; agent may not see another agent's private rates or customers.
2. Customer Portal SECOND: authenticated organization contact views own approved bookings, tracking milestones, permitted BL/release documents, invoices and payment statuses; no access to agent costs, vendor purchase rates, other customers or internal approval data.
3. Vendor Portal THIRD: authenticated vendor contact views only assigned transport/depot/service tasks, work orders, evidence, approved submitted invoices and payment status; no access to principal margins, unrelated supplier prices, agent settlement or other vendors.
Navigation entry points: use existing M3 modules and linked APIs, not new parallel booking, equipment, GL or party data stores. Portals are role-scoped experiences and must not be treated as proof of complete implementation until end-to-end tests pass.
Test each: authenticated identity association to the authoritative Common Party ID, cross-tenant 403/404 protection on list/detail/download/export, draft vs approved document redaction, audit trail, revoked permissions, and Principal vs HO visibility. Keep providers OFF, real money OFF; no new paid tiers.
Portal rollout gate: cannot activate until P1 authenticated master reads and applicable relationships, scoped API endpoints and UAT pass. No merge/deploy to production without separate authorization.

## Central Master — Related Tasks and Portal Relationship Register (approved design, not yet implementation)
All references below link to existing authoritative modules where supported. Missing fields/screens are backlog requirements and must be assessed, not auto-created as duplicate modules.

### Master record: Common Party / Organization
Identity: immutable Party ID, system sequence; organization code, legal name, display name, organization category, selected business roles (multi-role), branch, country, address, contacts, communication/language, tax identity, currency, status, effective dates, owner, Principal association, security scope, version and approval/audit.
Child panels/tabs: Details, Address (multiple addresses, capabilities, operational constraints), Contact (multiple contacts and communication preferences), A/R, A/P, Related Parties, Staff Assignments, Web Security, Rating, Config; only activate fields with authorized persistence.
Relationships: HO -> Principal -> Agent (Export/Import/Transshipment), customer and vendor organization contacts, Investor and equipment ownership, depot/carrier/transporters; allow multiple valid business relationships without duplicating Party ID.
Unique identifiers: Party ID distinct from agent/customer/vendor reference and subledger ID; GL control account from approved GL master mapping. Automatic generation must reuse existing reference sequences atomically, retain active counters and avoid collisions.
Governance: Submit / Review / Approve / Reject / Rectify / Block / Unblock / History / Duplicate Review; actions permission-gated and reason/audit recorded. No overwrite of posted financial records.

### Related Task routing for each party classification
HO: Master Governance, Party Administration, Global Agent Network, Reference Sequences, Finance Chart of Accounts, Master Security Roles, Audit and Exception Control, Global Equipment Tower, Management KPI.
Principal: Principal Setup, linked Agent Setup and assignment, customer/vendor oversight, region/country/port/agent network control, Booking/BL oversight, MRG tariffs, receivables/payables, Principal/Agent SOA, Global Equipment Tower, financial holds/approvals and audit.
Agent: Agent Setup, authorized customer and vendor party registration, Booking, B/L, Transport/Depot Delivery Order, container journey, MRG rate selection, document approval routing, own A/R and A/P, Agent SOA and exceptions; agent isolation enforced server-side.
Customer: Customer Registration/Contacts, authorized booking requests, booking/BL status, track & trace, cargo/container milestones, delivery and release documents, customer invoices/statements/payment status, cases and notifications. Exclude purchase rates, costs, other-party data.
Vendor: Vendor Registration/Contacts, linked service requests and jobs, quotation/rate submissions where approved, transport/depot/warehouse assignments, confirmations/evidence, vendor invoice submission, A/P status, discrepancy resolution. Exclude other vendors and Principal profits.
Investor: Investor Party Setup, existing container investor owner/sharing relationships, equipment interest, governed investor finance views and approvals subject to authority; no duplicate asset register.
Carrier/Depot/Terminal/Transporter/Warehouse: service/operating role, location, equipment and job assignment, event updates, operational documents, approved payable/recovery terms and audit, each scoped to assigned tasks only.

### Operate navigation (existing links first)
Principal: principal control tower and linked agent network operations.
Forwarding: Booking, routing, B/L, shipment events, transport instructions and documents.
Customs: customs-specific tasks and releases only if existing authorized workflows support them.
Transport: transport allocation, pickup/delivery, proof of delivery, cost evidence.
Warehouse: warehouse/depot stock, receipt/release, inspections, related charges.
These are navigation/role views, never new unrelated job models.

### Manage navigation
Tariffs: Charge Master / MRG revenue/cost / Agent Matrix / effective validity / approval history.
General Ledger: Existing GL master, posting definitions, accounting preview, exceptions/audit.
Receivables: Customer and Agent A/R, invoice/receipt allocations, SOA, disputes and approvals.
Payables: Vendor and Agent A/P, cost verification, approvals, settlements/holds and SOA.
Do not create a parallel accounting ledger.

### Portal navigation and cross-links
Principal Portal: Executive Overview; Global Equipment Tower; Regions; Countries; Agents; Customer/Vendor Network; Movements; Booking/BL; Tariffs; Revenue/Cost; SOA; Exceptions; Audit; Approvals.
Agent Portal: My Overview; My Customers; My Vendors; Bookings; BL; Equipment; Movements; Rates; Documents; My AR/AP; Agent SOA; Exceptions; Notifications.
Customer Portal: My Shipments; Booking Requests; Tracking; BL/Release Documents; Invoices/Statements; Payments Status; Support/Notifications.
Vendor Portal: My Assigned Jobs; Transport/Depot/Warehouse Tasks; Service Orders; Rates/Quotations; Evidence; Invoices; AP Status; Disputes/Notifications.
Forwarding/Customs/Transport/Warehouse portals: role-scoped workspaces over existing operational APIs with assigned task queues and document events; only expose actions and data for approved parties.
All portal menu visibility and endpoint filtering must agree; frontend-only hiding is insufficient.

### Related task panel standard for every master
Columns: Task/Module, Linked Party ID, Relationship Role, Record Reference, Owner Agent, Principal, Region, Access Scope, Status, Last Activity, Action.
Filter dimensions: party type, authorized Principal/Agent, region/country, status, task category, active/inactive.
Open record via existing authoritative screen; use same approved field styling and compact widths, no extra-wide blank controls. Do not fabricate counts, jobs, charges or relationship links.
Only display task relationships that are actually supported by source APIs; unavailable capabilities should show 'Not connected' not sample production data.

### Acceptance checks
- Cross-party duplicate and alias detection, multi-role assignment, stable Party ID, approvals.
- HO, Principal, Agent, Customer, Vendor, Investor scope for list, detail, save, file download and export.
- Country/region/agent breakdown in GET matches booking/equipment event sources, no synthetic movement.
- GL/subledger mapping reconciles actual finance records, customer/vendor portal never leaks costs/margins.
- Portal actions guarded by backend IAM; deny forbidden links with correct audit events.
- Current 196-screen regression and isolated preview smoke before release.
