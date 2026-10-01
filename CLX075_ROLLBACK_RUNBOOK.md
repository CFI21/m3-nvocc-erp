# CLX-075 Rollback Runbook

## Trigger
Rollback for failed migration, data-integrity mismatch, P0/P1 defect, IAM/data-scope failure, finance reconciliation failure, provider/canary failure or unstable production startup.

## Procedure
1. Stop new production traffic and external provider actions.
2. Put runtime into maintenance/read-only mode where available.
3. Preserve logs, audit evidence and failing transaction references.
4. Restore the pre-cutover database snapshot using the approved provider/free-tier restore procedure.
5. Restore the previous accepted application image/commit.
6. Re-verify database health, IAM, five representative jobs, GL control totals and container counts.
7. Keep providers/real-money OFF until incident review is complete.
8. Record root cause, affected records, reconciliation result and rollback evidence.

Never delete audit evidence or manually rewrite control totals to force a PASS.
