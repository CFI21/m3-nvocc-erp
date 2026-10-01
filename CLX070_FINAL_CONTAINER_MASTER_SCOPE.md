# M3 CLX-070 Final Container Master / Equipment Governance Scope

## Permanent architecture rule
There is one M3 NVOCC ERP. Equipment Planning, Container Master, Allocation, Shortage, Reposition, Lease, Purchase, Partner/Investor/Agent/SOC stock, Container Journey and container economics are native M3 functions. They use the existing Booking/Job/B-L, IAM, audit, finance and management-control architecture.

## Comparison against supplied legacy Container Coding screen and approved M3 requirements

| Area | Legacy / requested requirement | Current M3 before this correction | Final build treatment |
|---|---|---|---|
| Container identity | Container #, Size, Type, Kind, Status | Container #, size/type, status | Preserve and extend same authoritative containers row |
| Technical | CSC validity/plate/safety approval, manufacturing #, max gross, tare, classification, grade/payload, capacity, stacking, ISO, test load, model, machinery, inner dimensions | condition + size/type only | Add governed technical fields |
| Inspection | Periodic test, last/due date, tank kind, stacking capability, verification | partial condition only | Add inspection/verification fields and KPI exceptions |
| Images/docs | Container picture + attachments | absent in Equipment runtime | Add container_documents linkage |
| Property / owner | Company/Principal, Overseas Partner, Leasing Company | OWNED/LEASED/AGENT/SOC + principal_code | Explicit owner_party_type + owner_party_code and related-party links |
| Investor | Investor linkage | absent | Add INVESTOR owner/provider model and party links |
| Agent supplied | Agent/provider boxes | generic AGENT ownership | Explicit AGENT_SUPPLIED + custody separated from economic ownership |
| Purchase | Supplier, invoice/date, purchase ref, bulk upload | acquisition refs + one register endpoint | Add supplier/invoice refs + bulk registration + inspection flow |
| Lease | Lessor, contract, on/off-hire | lease contract/basic supplier_or_lessor | Preserve + owner/provider link + finance/share ledger |
| Sale | Allocate for sale, customer, invoice/date | absent | Add allocate_for_sale + sale refs; operational allocation policy can block when enabled |
| Afghan transit | Flag | absent | Add afghan_transit |
| Principal update | Update Principal / explicit principal per box | principal_code exists but defaults | Preserve; explicit owner party required |
| Custody | Branch, Agent, Depot | port/agent/depot | Add branch/office + custodian type/code; keep owner separate |
| Global control | HO sees global; branch/agent custody scope | partial role/agent filtering | Add role/data-scope policy profile; native M3 shell |
| Booking link | Equipment requirement, allocated real containers | allocation accepts booking ref manually | Add Booking Equipment tab + authoritative booking-context endpoint |
| B-L/release link | Same containers follow Booking → release → B-L → journey | job/container records exist but UI link incomplete | Booking Equipment exposes same Container Master rows; existing job/B-L/journey path preserved |
| Movement | Full container journey + reposition etc. | container_events + movement API | Preserve same event ledger; Container detail exposes journey |
| Cost/revenue | Financials per container | unit_cost only | Add container_financial_ledger |
| Commission/sharing | Agent/partner/investor shares | no container-level model | Add party commission/share fields + container_share_rules + ledger entry types |
| Container profitability | Revenue - cost - commission - share | absent | Add per-container financial summary + KPI |
| KPI | Fleet, availability, utilization, idle, ownership, financial margin, exceptions | basic fleet KPIs only | Add governed KPI endpoint/native M3 KPI view |
| SOP | Operational SOPs/policies | no equipment SOP catalogue | Add 10 core SOPs + policy endpoint/document |
| System policy | Role + scope + workflow + action | IAM exists; Equipment runtime used x-role headers | Integrate M3 session where available + role/custody scope + central equipment policies |
| Audit | Before/after, action, user/scope | existing audit_events | Reuse same audit table for container changes/financial postings |
| Duplicate ERP/data | Must not create separate ERP/inventory | standalone runtime was removed in latest baseline | Explicitly preserved: one M3 shell + one containers table |

## Owner/provider model
Allowed economic owner/provider types:
- PRINCIPAL
- OVERSEAS_PARTNER
- LEASING_COMPANY
- INVESTOR
- AGENT_SUPPLIED
- SOC

Ownership/provider is distinct from physical custody. Custody uses branch/office/port/agent/depot and can change through movements without changing economic ownership.

## Container-level finance
Every financial entry can carry Container, Job, Booking, B-L, movement event, charge code, party, source document, entry type, amount and currency.

Entry types:
- REVENUE
- COST
- COMMISSION
- SHARE

Container margin = Revenue - Cost - Commission - Share.

## Access model
User -> Role -> Data Scope -> System Policy -> Workflow State -> Allowed Action.

HO/global roles receive network visibility. Branch/Agent/Depot roles operate only their governed custody scope. Finance may see/post permitted economics without receiving unrestricted operational edit rights. Audit is read-only.

## Production control
This scope is an additive M3 integration. Production traffic, live providers and real-money execution remain OFF until the existing controlled cutover gate is separately authorized.
