# CLX-076 Live Provider Activation Runbook

1. Preserve production-active CLX-075 core and final API/web image digests.
2. Keep REAL_MONEY=OFF throughout CLX-076.
3. Enable M3_PROVIDER_SANDBOX_TESTS only while M3_LIVE_PROVIDERS=OFF.
4. Inspect only non-secret credential-presence status.
5. Run authenticated sandbox canary only for credential-complete providers.
6. Require auth/health plus inherited timeout/retry/idempotency/DLQ/circuit/callback controls.
7. For non-money provider CANARY PASS: mark provider enabled, set health LIVE_CANARY_PASS, then enable live-provider switch only after every enabled provider is ready.
8. If any activated provider fails canary: immediately disable it, open circuit, restore M3_LIVE_PROVIDERS=OFF if needed, and record evidence.
9. TrueLayer Payments may pass auth/signing readiness in CLX-076 but must remain non-money until a separate explicit REAL_MONEY authorization.
10. Never store or commit credential values in GitHub, logs, database evidence, or chat.
