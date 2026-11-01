# M3 NVOCC ERP — Central Master, Portal, Finance and Tower Gap Audit
Date: 2026-10-11
Branch audited: preview-pr124-booking-isolated
Method: read-only repository source review, historic acceptance artifacts, latest Render web deploy metadata. No runtime authenticated UAT, database inspection or current full regression performed. Results below distinguish source-observed from unverified. No production change.

## Severity A — Security / must block portal activation
A1 CRITICAL: app/masterdata.py GET /records/{domain} does not request X-M3-Session or check actor/scope. All common-party, account master and other reference data returned for requested domain without per-actor authorization in handler. Fix authenticated and authorized list access, explicit safe public/bootstrap exemptions if truly needed, and query-level scope. Verify all existing callers and forbid cross-tenant enumeration.
A2 CRITICAL: app/mrg.py actor(c,token,x_role) falls back to role provided via X-Role without a session. For protected rule/charge/financial routes, deny unauthenticated or implement explicit tightly-scoped UAT-only bypass not reachable on live public preview.
A3 CRITICAL: app/clx070_container_master_control.py actor(...) permits header-provided role/scope fallback without session; even session actors use x_agent_scope provided by caller. Derive permitted agent/depot/branch scope from IAM and approved relationships. Test negative cases list/detail/export/update; review app/screen_integration.py and clx049 booking/BL similarly.
A4 HIGH: Principal-Agent relationships and Investor rights across APIs not proved; lack of consistent server-side principal-to-agent policy means Global Tower view cannot be called confidentiality-safe.
Acceptance: no anonymous protected reads, no spoofed X-Role/X-Agent-Scope escalation, record- and field-level finance confidentiality, audit denied privileged actions.

## Existing/partial UI and master
B1 PARTIAL: Common Party master, PA Parties listing and Details/Addresses/Contacts read-only preview panel exist. A/R and A/P party-detail tabs, editing/persistence for address and contact, multi-role checkbox authorization, and exact user-provided reference field comparison remain unfinished.
B2 PARTIAL: Principal Setup and Investor Setup link to existing Common Party and container master; approved effective-dated Principal/Agent/Investor relationships and associated permissions not verified.
B3 EXISTING: masterdata.py maker/checker change workflow authenticated, pending change protection, and reference sequence next endpoint with maker-role check. This alone does NOT establish a complete automatic Party Code ID / subledger mapping or concurrency-safe end-to-end onboarding.
B4 PARTIAL: existing 'gl-account' master and finance screens exist; unique Party ID vs AR/AP subledger vs GL control mapping not proven. Customer/Agent/Vendor finance data should not create independent parallel ledger.
B5 PARTIAL: source UI references HO Setup, Business Master, Common Parties, Agent Setup. Unified related-tasks panel and each authorized Party -> Booking/BL/MRG/Equipment/Finance drilldown not yet demonstrated.

## Portals / tower / operations
C1 NOT VERIFIED / PENDING: standalone usable Agent Portal, Customer Portal, Vendor Portal. No evidence of end-to-end authenticated portal route+workflow+API coverage in inspected web/index.html (literal 'portal' absent). Need inventory other files before claiming truly nonexistent.
C2 PARTIAL: Principal tower and GET UI exist with historical CLX078/079 PASS artifacts; Principal > Region > Country > Agent drilldown / financial scope and API permission tests not completed as of this audit.
C3 PARTIAL: booking and finance use existing masters in several places, but end-to-end source ID, GL link, agent rate confidentiality, SOA auto-reconciliation and exception hold require tests.
C4 UNVERIFIED: Operate tabs Principal/Forwarding/Customs/Transport/Warehouse and Manage Tariffs/GL/Receivables/Payables as integrated role-filtered navigation. Do not add screens above the 196 baseline without approval.

## Prioritized repair work packages
WP-0 Read-only comprehensive per-endpoint IAM/caller map and user-role testing matrix; baseline and backup.
WP-1 AUTH: secure master read endpoints, MRG and container control, booking/screen integration; remove header-trust in any network-exposed protected route. Add authenticated per-party, per-agent, financial-row and document/export tests. Block acceptance until PASS.
WP-2 PARTY: complete PA Party fields and durable Contact/Address/AR/AP backed by current existing master approval and audit. Verify duplicates, multi-roles, Principal/Agent/Investor relationships.
WP-3 CODE/GL: atomic approved Party ID, no counter reset; approved AR/AP subledgers and GL control account mapping with duplicate detection.
WP-4 AGENT: scoped agent portal over existing tasks and customer/vendor links.
WP-5 CUSTOMER: scoped booking/tracking/doc/invoice portal.
WP-6 VENDOR: assigned order/service/invoice portal.
WP-7 GET/PRINCIPAL: region/country/agent movement, holds, Finance/SOA with real authoritative source drilldown.
WP-8 acceptance: negative security tests, identity mapping, finance UAT, 196 screens baseline, preview deployment and smoke, then separately authorized release.

## Latest verified web preview deploy
Render web service srv-db40nnad0e5s73fcht00 returned LIVE for commit d506502a2800a80d5a7f5884806e4e2ae83052be (UI Principal checkbox). Subsequent markdown-only plan commits did NOT deploy web implementation.
Production unchanged; PR #124 remains draft/unmerged according to prior status, not reverified by PR API in this audit.

## Acceptance reporting
For each work package report: existing API+screen; missing fields; exact reuse links; backend RBAC result; tests run; preview commit/deploy ID; PASS/BLOCKED. Do not use historical passing test evidence as current runtime verification. No fabricated business records, real money OFF, providers OFF, free tier only.
