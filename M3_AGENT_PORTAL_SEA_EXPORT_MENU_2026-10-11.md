# M3 Agent Portal — SEA EXPORT Menu & Field Reuse Contract
Date: 2026-10-11
Status: APPROVED MENU SPEC / INTEGRATION PENDING. Does not assert runtime functionality.
Scope: Reuse existing M3 ERP Booking, B/L, shipment, equipment, finance, documents, tasks. Do not add a second model/ledger. Exact compact M3 template and current field definitions prevail over legacy visual styling.

## Sidebar path
Portals > Agent Portal > SEA EXPORT.
Security: only authenticated agents with Export rights; Principal/HO authorized supervision. Scope each read, action, file, report and export to owned/linked Agent + related customer. Cross-agent cost leakage forbidden. Hide irrelevant entries for roles that lack privileges and deny at API regardless of UI.

## Full requested SEA EXPORT task tree — 28 entries
Each entry must resolve through existing screen/API after verification. Field descriptions below are target reuse requirements, not proven implemented screens.

01 SE Manifest — linked booking/BL, voyage, vessel, POL/POD, shipper, consignee, equipment, cargo packages, weights, status, issue date; generate from source records.
02 SE Job — existing job reference, linked booking, customer, agent, route, cargo, status, costs/revenue summaries, audit.
03 CRO — cargo release order, related BL, consignee, equipment, delivery/expiry, approvals, release status; reuse existing release workflow where applicable.
04 SE B/L — existing BL screen; BL number, booking, shipper, consignee, notify, routing, cargo/equipment, freight terms, status and approvals.
05 SE Switch B/L — reuse existing Switch BL workflow with original BL link, amendment reason, maker-checker approvals, audit; no shadow BL record.
06 Stuffing Plan — booking, container, seal, package line, weight, location, loading sequence, date, responsible user, approval, source cargo data.
07 SE Milestone — events from authoritative container/shipment journey with event time, place, agent, exceptions.
08 SE Invoice — existing AR/revenue source, invoice reference, billing party, approved charges, tax, currency, due date, posting status, related job.
09 SE Receipt — existing treasury/AR receipt and allocation; no standalone cash ledger.
10 SE Bill — existing billing document tied to source AR lines and approved documents; confirm distinction from SE Invoice.
11 SE Payment — existing treasury/AP payment approval and settlement; real-money OFF until authorized.
12 SE Agent Invoice — existing agent settlement/charge lines, source job, agent, currency, terms, approval.
13 SE Agent Receipt/Payment — existing Agent SOA/treasury links, allocations and controlled adjustments.
14 SE Query — booking/job-linked case, subject, customer, agent, priority, owner, status, resolution/audit.
15 SE Letter Template — existing document template and permissions; version, language, content variables, effective dates.
16 SE Letter Process — issue/generation pipeline from approved template, party and job, approval/status/audit.
17 SE Letters — generated letters with reference, recipients, template, delivery, document link.
18 SE Letter List — searchable issued-letter register scoped to agent; no duplicate document store.
19 SE Quotation — customer and route, cargo/equipment, approved tariff/charges, validity, currency, quote status and conversion to Booking.
20 SE Query (Customer Service) — customer-facing scoped case queue and notifications; reuse same case source if one exists.
21 Shipping Instruction — linked booking/BL party details, cargo, vessel/voyage, ports, freight terms, cutoff, amendments/audit.
22 Export Booking Request — customer/agent, origin/destination, cargo/equipment, schedule, requested dates, quoted charges, submission/approval.
23 SE Payment Requisition — AP request with source job/vendor/charge, amount/currency, approval, treasury posting link.
24 Agent Payment Requisition — same governed AP requisition; Agent relationship, agent SOA, authorizer, status.
25 SE Console Release Instruction — equipment, booking/BL, depot/terminal, release conditions, authorization, status; verify exact legacy terminology before implementation.
26 SE NOC — document/certificate workflow, linked job/party, issuing authority, validity, attachments/approvals; determine exact NOC scope before activating.
27 Fixed SalesTax — reuse existing tax codes, jurisdiction, validity, finance calculation and mapping; no hardcoded tax amount.
28 SE Loading Updation — shipment/container loading event, planned/actual status, operator, timestamp, exceptions; link journey authority.
29 SE Loading Dashboard — read-only aggregation of live source loading milestones by agent, vessel, voyage, port and exceptions. [29 entries in provided image; use 29, not 28.]

## Shared context bar and related fields
Agent (authenticated), Principal, Customer/Booking Party, Job Ref, Booking Ref, BL Ref, Voyage, Vessel, POL, POD, Cargo Type, Equipment, Status, Last Activity; each visible only if source record contains permitted values. Do not replicate input fields or create a new Booking/BL structure.
Selected task uses existing module's form layout, validations, master lookups and rights. Preserve widths, field order, labels, code generation and approval gates. Existing Related Tasks panel should route to authoritative record, not copy it.

## Build/verification gates
- Inventory exact existing screen IDs and APIs for every row; tag CONNECTED / PARTIAL / NOT CONNECTED and count supported entries.
- Never bind a legacy label to an unrelated route just to make it clickable. Unsupported entries can be shown as 'Not connected' to authorized developer/admin views, not as fake functional actions.
- Resolve ambiguous legacy terms (CRO, SE Bill vs Invoice, Console Release Instruction, NOC) against real M3 modules and user's business definitions.
- Security P1 is prerequisite for external Agent Portal activation; protect list/detail/download/export and mutations.
- Agent Portal UI must use existing 196-screen approved template and preserve standard nav; don't add unrelated new business models or duplicate counters.
- UAT: authenticated Export Agent vs Import/TS role, linked Principal audit, unrelated agent denial, field-data parity, workflow, maker-checker and 196-screen regression.
