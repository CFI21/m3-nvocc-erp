# M3 CLX-073 Equipment Cost Optimization SOP

CLX-073 is an explainable decision layer on top of CLX-072. It does not create a rate master, container inventory or execution queue.

1. Build candidate supply options for a destination shortage.
2. Use existing container financial history as movement-cost guidance where explicit scenario movement cost is absent.
3. Add governed scenario components: handling, depot, load/discharge, feeder, truck, rail, lease, detention/idle and aging impact.
4. Compare reposition, lease on-hire, agent/partner and SOC alternatives.
5. Rank by explainable total cost, expected lead time and source priority.
6. Submit and approve the selected option under maker/checker.
7. Convert only an approved option to the existing equipment_work_items queue.
8. Journey execution remains in CLX-071.
9. Actual movement cost remains in container_financial_ledger.
10. Measure planned-vs-actual cost/lead, shortage avoidance, utilization improvement, ROI and management savings.
11. Generate cost/delay overrun exceptions when policy thresholds are exceeded.

Production traffic, live providers and real-money execution remain OFF.
