# M3 CLX-071 Container Journey SOP

## Purpose
Operate each physical container through one authoritative M3 Container Master record, linked to the same Booking/Job/B-L, custody, finance and audit context.

## Standard lifecycle
AVAILABLE → RESERVED → RELEASED → EMPTY_PICKUP → STUFFED → GATE_IN → LOADED → IN_TRANSIT → TRANSSHIPMENT (when applicable) → DISCHARGED → GATE_OUT_FULL → EMPTY_RETURN → INSPECTION → AVAILABLE.

## Planning and actuals
1. Planned events may be recorded with planned time, port, branch, agent, depot, vessel and voyage.
2. The actual movement updates the same event when a matching planned event exists.
3. Planned-but-missing events generate an operations exception after their due time.

## Custody
Every actual movement may update current Port / Branch / Agent / Depot on the same Container Master record. Economic ownership does not change merely because custody changes.

## Detention / free days
GATE_OUT_FULL starts the free-time clock. Due date = actual gate-out-full + configured free days. An outstanding container beyond due date creates an EMPTY_RETURN_OVERDUE exception and estimated detention exposure using the container detention rate.

## Damage / inspection
Damage or inspection hold prevents the unit becoming AVAILABLE. REPAIR turns damage hold on. INSPECTION may hold the unit until verification/repair clearance.

## Movement cost
A movement event may create a COST accrual in the existing container financial ledger. The entry links container, job/booking, movement event, party, charge code and source reference.

## Sequence exceptions
Events outside the approved lifecycle are blocked. The system creates a SEQUENCE_EXCEPTION. Only an authorized global role can override and only with a recorded reason. The override is audited and remains visible as an exception.

## Variant endings
- Leasing: EMPTY_RETURN → OFF_HIRE_DUE → OFF_HIRED
- Overseas Partner: EMPTY_RETURN → RETURN_TO_PARTNER → PARTNER_RETURNED
- Agent Supplied: EMPTY_RETURN → RETURN_TO_AGENT → AGENT_RETURNED
- SOC: EMPTY_RETURN → SOC_RELEASED
- Purchase: PURCHASE_RECEIVED → INSPECTION → AVAILABLE
- Sale: AVAILABLE → ALLOCATED_FOR_SALE → SOLD

## Exception queue
The exception queue covers missing events, overdue empty returns, damage holds, inspection holds, invalid sequence and authorized overrides. Operators acknowledge/in-progress/resolve with owner and resolution code.

## KPI
Minimum journey KPIs:
- open journey exceptions
- high/critical exceptions
- missing events
- overdue empty returns
- detention exposure
- damage/inspection holds
- average turnaround days
- event mix
