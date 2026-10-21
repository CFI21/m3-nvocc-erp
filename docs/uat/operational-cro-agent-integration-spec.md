# Operational CRO ↔ Agent Master specification (isolated M3 UAT)

Status: baseline functional specification; not evidence of implementation. Scope: `m3-dev-sprint-2` isolated services only. No production, ANCLINE, or PR #124 writes.

## System boundaries
- Agent Master `agent` is authoritative for governed `bs_code`, `operator_code`, `cro_remarks`, `noc_note`. Existing independent Maker/Checker mechanism applies; CRO does not create or clone an agent.
- Operational CRO is a **separate transaction**, not a new master record. It links to an existing Booking, approved Agent, and scoped Equipment and Port/Vessel masters.
- The existing screen key `agent-tasks::cro` is the navigation target; presence of that key does not establish that transactional CRUD, API, or schema exists.
- Resolve existing booking/agent/equipment data models and APIs before introducing CRO persistence. No direct changes to authoritative master records from CRO.

## Reference screenshot field inventory and proposed source
| CRO section | Screenshot fields | Authoritative source / handling |
| --- | --- | --- |
| Header | CRO #, Ref #, Tran Date, Expiry Date, DP # | CRO-generated ID and CRO inputs; DP # from existing authoritative source if present |
| Booking | Booking selector (+), Booking #, S/O | Existing authorized Booking reference; S/O optional until source identified |
| Agents and parties | POL Agent, Clearing Agent, Freight Forwarder, Name, Customer/Shipper, Name of the Company | Approved Agent/Party Master and Booking; store references with document snapshot |
| Port routing | POL, POFD, POL Terminal, POFD Terminal | Approved Booking, Port/Terminal Master |
| Cargo | Commodity, Cargo Type | Approved Booking lines |
| Voyage | Vessel, Voyage, ETA | Booking/schedule authoritative records |
| Dates | Release Date (range shown), Cutoff Date (range shown), Full In, Date, Expiry Date | CRO-specific dates, validated against operational policy |
| Shipping/customs | EGM, CNIC/Custom Permit No., Free Days POL, Note | CRO-specific fields unless authoritative customs/tariff source exists |
| Equipment | Size, Type, Quantity, Nominate Container No, Booking #, Remarks, line numbering | Booking equipment demand + Equipment Master; multiple validated lines |
| Approval | Approval Status, Approved By, Approved On, Remarks, Approve | Server-side Maker/Checker; never browser-only authorization |

**Agent Master → CRO defaults:** use latest APPROVED ACTIVE Agent settings. BS Code and Operator Code are linked context; CRO Remarks and NOC Note are suggested defaults. Preserve Agent code and source version, plus immutable snapshot of values when CRO is approved. Display clear provenance; do not silently overwrite edited Draft fields.

## Workflow and validation
1. Draft: authorized Maker selects an approved Booking; populate eligible fields. Draft can be edited only by authorized scoped actors.
2. Submit: validate required CRO ID/ref rules, permitted Booking state, Agent ACTIVE/approved, routing, positive equipment quantities, unique equipment assignments, and chronological constraints. Transition to PENDING_APPROVAL atomically.
3. Checker: distinct authenticated and authorized actor reviews field-level change diff and submits an explicit decision comment. Cannot self-approve.
4. Approved: immutable approval snapshot (booking/agent master versions, actor, time, CRO lines); audit event emitted; equipment release authorized only from valid approved CRO subject to equipment checks.
5. Reject/withdraw: no approved release, preserve history. Post-approval edits must create versioned amendment requiring new independent approval; old approved snapshot is retained.
6. Idempotency and concurrency: repeated submit/approve cannot create duplicate CRO or double-release equipment. Use conflict handling for stale versions.
7. Scoping: enforce agent/company access in API and reads, independent of UI role selector. Production traffic and external providers remain blocked in isolated UAT.

## Field classification — provisional
- Required at draft creation: existing Booking reference and at least one CRO equipment line (size/type/positive quantity), subject to implementation validation.
- Required for submission: Agent association, POL/POFD, applicable release/expiry schedule, equipment lines, maker reason. Exact business-required customs fields vary by jurisdiction and must not be globally hardcoded without approval.
- System-controlled: CRO ID, status, approved by/on, Agent version, Booking version, audit identifiers.
- Optional pending process-owner confirmation: DP #, S/O, EGM, CNIC/Custom Permit No, Full In, carrier/terminal specific dates, free days, nomination, default remarks.

## Isolated acceptance tests
- Maker `md.maker` creates CRO from synthetic approved Booking and authorized Agent `UAT-AGENT-001`; defaults displayed without Agent duplication.
- Blank/missing mandatory fields, expired date, invalid quantity, Booking/Agent mismatch blocked.
- New Agent Master change pending approval cannot be consumed as approved CRO default.
- Checker `md.checker` separately approves with comment; Maker self-approval blocked.
- Approved CRO retains original Agent settings snapshot after a later approved Agent revision.
- Equipment allocation/release only permits approved CRO and avoids duplicate allocation, and shows traceable references.
- Reject, withdraw, amendment, race/conflict, permission isolation, audit chain and API tests all pass.
- 52/52 CI success required on any implementation commit before manual isolated deployment. Static spec/document commits do not authorize a deployment by themselves.

## Open implementation decisions
- Confirm which existing Booking API and statuses are authoritative for CRO eligibility.
- Confirm whether DP #, S/O, Full In, EGM and permit values belong to existing customs/operational masters.
- Confirm whether release date/cutoff are start/end ranges or separate timestamps (screenshot displays paired controls).
- Confirm whether multiple CROs per Booking and partial equipment releases are allowed.
- Confirm CRO numbering rules and approval matrix by company/agent.
