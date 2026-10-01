# CLX-075 Final Cutover Runbook

This runbook operates only from the frozen accepted CLX-075 production-candidate commit.

## Hard stop
Production traffic, live providers and real-money execution remain OFF until the owner issues the exact explicit production-activation instruction.

## Rehearsal / cutover sequence
1. Freeze accepted commit and record SHA.
2. Confirm database target and environment separation.
3. Take approved backup / provider snapshot.
4. Run all migrations in order; stop on any destructive or failed migration.
5. Load or reconcile master data, users/roles, open bookings/jobs/B-L, open AR/AP, opening GL balances, active containers and active leases.
6. Verify record counts, duplicate controls, foreign-key/data integrity and financial/container control totals.
7. Start API/web with live-provider switches OFF.
8. Verify login/session/logout, role/scope, branch/agent/depot isolation, maker-checker and audit.
9. Smoke Booking → Job → B/L → container journey → finance; include release/DO, reposition, M&R and lease paths.
10. Verify GL/Treasury reporting and management KPIs.
11. Verify health/readiness, logs, exception queues and rollback availability.
12. Re-run reconciliation after transaction smoke.
13. Keep production traffic OFF and record rehearsal evidence.
14. Only after explicit production activation: configure live credentials/providers, verify one controlled canary, then open traffic according to activation plan.

Any P0/P1 security, integrity, finance-control, IAM-scope, migration or reconciliation failure is STOP/ROLLBACK.
