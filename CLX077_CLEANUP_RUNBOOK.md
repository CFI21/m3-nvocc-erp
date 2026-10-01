# CLX-077 Test Data Cleanup / Archive Runbook

Do not delete audit history.

1. Confirm REAL_MONEY=OFF, M3_LIVE_PROVIDERS=OFF and real providers disabled.
2. Export the CLX077 scenario register and reconciliation snapshot.
3. Use the CLX-077 archive action to mark scenario records ARCHIVED and treasury test records TestArchived.
4. Retain dummy treasury accounts for training unless explicitly retired.
5. Exclude records with source_type=DUMMY_BANK, marker=CLX077_TEST, or CLX077-prefixed references from ordinary management reporting where required.
6. Never delete or rewrite audit_events, GL vouchers, provider events, bank-import evidence or reconciliation evidence.
7. A future purge, if ever required, must be a separately approved accounting-safe archival process.
