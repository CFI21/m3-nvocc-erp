# CLX-077 User Live Review Checklist

Use the production web session with the **Dummy Bank Review** screen. All CLX-077 records are marked `CLX077_TEST` / `DUMMY_BANK`.

Review these paths without enabling real providers or real money:
- Executive Home → Booking → Job → B/L → Container
- Commercial → invoice/cost context → AR/AP
- Treasury → Dummy Bank Review → bank import/matching → payment simulation
- GL → voucher → trial balance → P&L / Balance Sheet / Cash
- Customer/Agent/Carrier statements
- Audit and management KPI context
- Booking Info → Release Instruction → Delivery Order → Lock Info → Authorization
- Switch B/L remains separate and linked

Acceptance: 15/15 scenarios PASS, unmatched bank items 0, unbalanced vouchers 0, unexplained reconciliation variance 0.

**Red safety banner must remain visible:** TEST BANK — SIMULATION ONLY — NO REAL MONEY.
