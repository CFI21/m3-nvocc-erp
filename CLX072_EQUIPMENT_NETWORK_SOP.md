# M3 CLX-072 Equipment Network SOP

CLX-072 is a planning and control layer inside the existing M3 NVOCC ERP. It does not create a second fleet, booking, job, B/L, finance or action model.

## Authoritative data
1. Physical stock always comes from the existing `public.containers` Container Master.
2. Movement and turnaround always come from CLX-071 `container_events`.
3. Booking demand is derived from existing Booking/Job transaction context and open equipment work items.
4. Reposition, lease, shortage and related execution remains in the existing `equipment_work_items` queue.
5. Container economics remain in the existing container financial ledger.

## Network planning cycle
1. Review global position by port / branch / agent / depot / size-type.
2. Apply only APPROVED network targets for safety stock, target stock and reorder point.
3. Compare available stock against safety stock plus forecast demand.
4. Identify shortages and surpluses.
5. Prefer internal reposition from matching surplus size-type before external on-hire.
6. For remaining shortage, recommend lease on-hire or existing approved supply paths.
7. For leased surplus, identify off-hire candidates.
8. Convert an approved recommendation into an existing Equipment Work Item; never create a parallel execution record.
9. Existing maker/checker approval controls govern execution.
10. Track idle/aging, utilization, turnaround, detention exposure and open work items.

## Safety
Production traffic, live providers and real-money execution remain OFF. CLX-072 must not promote production or create a new ERP shell.
