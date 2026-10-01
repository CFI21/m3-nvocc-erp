# CLX-054 ↔ CLX-077 Historical Comparison

## Sources
- Accepted CLX-054 baseline: `07d60f6ac8b39f7089a47f3ea16b9976232a3d80`
- Temporary preview branch head: `c6a733bd9f56ec54bb56b842019a84f3272b07ac`
- Current CLX-077 repository main: `586ed0e048f547b0c03933aaf7cebcceb38b2b71`
- Comparison run: GitHub Actions `36864783013`

The preview branch differs from the accepted CLX-054 source only by preview-isolation changes: historical banner, isolated preview API target/CORS/CSP, hard read-only write guard, and comparison workflow. It must not be merged to main.

## Environment
- Historical preview: https://m3-clx054-preview.onrender.com
- Historical isolated API: https://m3-clx054-preview-api.onrender.com
- Current live: https://m3-nvocc-web-latest.onrender.com
- Current live API: https://m3-nvocc-api-latest-1.onrender.com

## Canonical catalog comparison
| Check | CLX-054 | CLX-077 | Result |
|---|---:|---:|---|
| Canonical screens | 196 | 196 | PASS |
| Removed screen IDs | 0 | — | PASS |
| Added canonical screen IDs | — | 0 | PASS |
| Route changes | 0 | 0 | PASS |
| Name changes | 0 | 0 | PASS |
| Top-level menu order | Agent Tasks → HO Tasks → Treasury / AR-AP → Integration & Security → General / Administration → Master Data | Same | PASS |

## Key screen live-read checks
| Screen ID | CLX-054 preview | CLX-077 live |
|---|---|---|
| agent-tasks::booking | HTTP 200 | HTTP 200 |
| agent-tasks::bl | HTTP 200 | HTTP 200 |
| agent-tasks::delivery-order | HTTP 200 | HTTP 200 |
| agent-tasks::switch-bl | HTTP 200 | HTTP 200 |
| gl-accounts::invoice | HTTP 200 | HTTP 200 |
| gl-accounts::payment | HTTP 200 | HTTP 200 |
| gl-accounts::receipt | HTTP 200 | HTTP 200 |
| treasury::bank-reconciliation-exception-queue | HTTP 200 | HTTP 200 |
| administration::users | HTTP 200 | HTTP 200 |
| master-data::agents | HTTP 200 | HTTP 200 |

## Safety
- Historical preview write attempt: HTTP 423
- Guard: `CLX054_HISTORICAL_PREVIEW_READ_ONLY`
- Historical preview database: isolated SQLite/test dataset
- Production database write access from preview: NONE
- Current production services: unchanged

## Interpretation
The canonical 196-screen information architecture is preserved between CLX-054 and CLX-077. The meaningful differences to review are therefore primarily runtime/workspace enhancements, UI presentation, newer Equipment/Container controls, operations tooling, provider/readiness controls, and CLX-077 Dummy Bank review features—not loss or renaming of the accepted canonical screens.
