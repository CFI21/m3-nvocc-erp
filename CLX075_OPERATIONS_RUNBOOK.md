# CLX-075 Operations Runbook

Use health/readiness endpoints, structured request/audit logs, operations workbench, control tower and exception queues as the first-line operational view.

For an incident: identify request/correlation ID → isolate affected job/booking/B-L/container → inspect audit/exception trail → place affected flow on hold → reconcile finance/container state → apply controlled fix with maker-checker where required → retest → close exception with resolution evidence.

Large lists/imports must use pagination, dry-run/validation and duplicate prevention. External integrations remain retry/idempotency controlled and must use sandbox/provider-OFF mode until explicit activation.
