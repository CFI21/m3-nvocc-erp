# M3 CLX-052 Authoritative Production Cutover Runbook

Parent: M3-CLX051-PRODUCTION-READINESS-ACCEPTED-20260926

## Governed runtime targets
- Web service: srv-darbgkvlk1mc738rohng / https://m3-nvocc-web-latest.onrender.com
- API service: srv-dar9fh17lnhs73ahlh60 / https://m3-nvocc-api-latest-1.onrender.com
- Database: Supabase M3-NVOCC-PROD / ozupgknqaqgvprliewxe
- CLX-051 Web digest: sha256:069beaca63368729f10a5ab5be57824963b40ff25331a8a4f8574cf9a59aa00b
- CLX-051 API digest: sha256:bbc746f1317f5eec88addfb040acb94f2d7e33760c57dcd965cd3b013d050c31

The legacy-named m3-nvocc-api-prod and m3-nvocc-web-prod services are NOT cutover targets for CLX-052. They must not be used unless a later explicitly approved migration changes the governed runtime target.

## Cutover sequence

| # | Step | Owner | Required check | Evidence | Failure action |
|---|---|---|---|---|---|
| 1 | Pre-cutover lock | Release Manager | CLX-052 accepted candidate + explicit activation authorization present | Acceptance manifest + authorization record | STOP |
| 2 | Database health | DBA | Approved project ref only; connectivity healthy | health output + project ref | ROLLBACK/STOP |
| 3 | Backup confirmation | DBA | Latest isolated restore evidence PASS | backup/restore evidence | STOP |
| 4 | Image confirmation | Release Manager | Exact approved immutable Web/API digests | Render deploy image SHAs | STOP |
| 5 | Security check | Security | RLS deny-by-default; CSP/HSTS; IAM controls | security gate evidence | STOP |
| 6 | Role/data-scope | Security + Business Owner | Global/HO/Agent/Finance/Treasury/Accounting/Auditor/Viewer boundaries PASS | role UAT | STOP |
| 7 | Provider lock | Integration Owner | All live provider configs disabled | provider_live_configs | STOP |
| 8 | Traffic lock | Release Manager | M3_PRODUCTION_TRAFFIC remains OFF before authorization | readiness/health evidence | STOP |
| 9 | Release authorization | Business Owner + Release Manager | Separate explicit live-activation command | authorization evidence | STOP |
| 10 | Deploy/activate | Release Manager | Activate only approved digests/services | deploy IDs | ROLLBACK |
| 11 | Health check | SRE | Web live; API health 200; DB healthy | Render + health logs | ROLLBACK |
| 12 | Business smoke | Operations | Jobs 50001-50005 governed smoke PASS | UAT evidence | ROLLBACK |
| 13 | Finance control | Finance Controller | VC/LC balance + subledger/Treasury-to-GL traceability PASS | finance UAT | ROLLBACK |
| 14 | Audit check | Auditor | audit history/immutability/request correlation available | audit evidence | ROLLBACK |
| 15 | Confirm or rollback | Business Owner + Release Manager | all watch-window gates remain green | watch-window log | rollback on any trigger |

## Go-live watch window
Minimum governed watch window: first 60 minutes after any future explicit production activation, with checks at activation, +5m, +15m, +30m and +60m.

Immediate rollback triggers:
- API or Web cannot reach healthy/live state after deployment.
- Database health fails or approved project-ref lock fails.
- Any P0/P1 data-integrity defect, orphan, duplicate, or broken financial traceability appears.
- Unauthorized cross-office/customer/agent data visibility is observed.
- Maker-checker, four-eyes or SoD bypass is observed.
- Audit immutability or request/correlation tracing is unavailable for a critical mutation.
- Production traffic is enabled without the explicit activation authorization.
- Any live provider becomes enabled unexpectedly.
- Any real-money path becomes available while REAL_MONEY is OFF.
- Repeated critical 5xx errors affect a governed business flow.

## Current CLX-052 safety state
GO_LIVE_READY is not asserted until final CLX-052 acceptance.
PRODUCTION_TRAFFIC=OFF
LIVE_PROVIDERS=OFF
REAL_MONEY=OFF
