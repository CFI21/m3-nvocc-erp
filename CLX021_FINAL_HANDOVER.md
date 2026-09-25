# M3 NVOCC ERP — CLX-021 Final Release Candidate & Handover

Date: 2026-09-25  
Parent baseline: `M3-CLX020-OPERATIONAL-HARDENING-ACCEPTED-20260925-022`

## Release state

This is the final **non-live** release candidate. It is accepted for handover and controlled cutover preparation only.

Safety state remains mandatory:

- Production traffic: **OFF**
- Live providers: **OFF**
- Real money: **OFF**
- Final live cutover: **NOT AUTHORIZED**
- ANCLINE resources: **DO NOT USE**

## Primary runtime

### Web
- Render service: `srv-darbgkvlk1mc738rohng`
- Render name: `m3-nvocc-web:latest`
- URL: https://m3-nvocc-web-latest.onrender.com
- Image: `ghcr.io/cfi21/m3-nvocc-web:latest`
- Accepted image digest: `sha256:cf5cf670585198275853d22077eec33b983c5cd4ce1416a2cb0482930bffa9ab`
- Runtime region: Frankfurt
- Plan: Free
- Health endpoint: `/healthz`
- Web source image commit: `089b4e53f549bce8cadab32b5c471b88ab14ef68`

### API
- Render service: `srv-dar9fh17lnhs73ahlh60`
- Render name: `m3-nvocc-api:latest-1`
- URL: https://m3-nvocc-api-latest-1.onrender.com
- Image: `ghcr.io/cfi21/m3-nvocc-api:latest`
- Accepted image digest: `sha256:8dea48fe4fae0e821304cabffa4224e1ec77a511e2d44984b377ee5a07197c43`
- Runtime region: Frankfurt
- Plan: Free
- Render health check: `/api/v1/health`
- API source image commit: `23c4703a31bd86ea5164e4e24155ba6e11413122`

### Database
- Supabase project: `M3-NVOCC-PROD`
- Project ref: `ozupgknqaqgvprliewxe`
- Database: PostgreSQL
- Jobs: `50001–50005` present
- Runtime database target lock: enabled
- SQLite production runtime: not active

## Accepted screen inventory

Total: **193 screens**

- Agent Tasks: 19
- GL / Accounts: 47
- Treasury / AR-AP: 37
- Integration & Security: 20
- Administration: 28
- Master Data: 42

The catalog was reconstructed from surviving authoritative source definitions plus the accepted persisted PostgreSQL module inventory. Missing truncated tail content was not guessed.

## Control evidence

- Role/data-scope tests: PASS
- Agent scope required for AGENT role: PASS
- Customer scope filter contract: PASS
- Maker/checker SoD: 3 active
- Approval limits: 3 active
- Immutable business audit UPDATE guard: present
- Immutable business audit DELETE guard: present
- Security audit guards: present
- Idempotency uniqueness: present
- Production traffic lock: PASS
- Exact-origin CORS: PASS
- Web security headers: PASS
- Concurrent health/readiness: 30/30 PASS
- Concurrent p95: 2.341 seconds
- Restart/redeploy persistence hashes: unchanged
- Logical restore rehearsal: PASS
- Current API/WEB/PostgreSQL error scan after final hardening restart: no matching errors

## Acceptance workflow evidence

- CLX-018 Web/API Acceptance: GitHub Actions run `36172430108` — PASS
- CLX-019 Catalog/Role UAT: GitHub Actions run `36172845049` — PASS
- CLX-020 Hardening Acceptance: GitHub Actions run `36172993041` — PASS

## Non-primary M3 resources

The following are **not** part of the approved primary runtime and must not receive production traffic:

- `srv-dar2hhh7lnhs739q81tg` — duplicate older M3 API image service
- `srv-dar25irncjis73brk9n0` — source-build M3 API service
- `srv-daradtpsrm7s73c0a710` — suspended failed static-site web service

Do not touch or reuse ANCLINE resources.

## Change control

The primary Render image services currently show auto-deploy enabled. The GHCR build workflows are path-scoped, so only approved runtime source paths publish new images. Until a new approved M3 change request exists:

- do not change `app/**`, `web/**`, runtime Dockerfiles, or image workflows;
- do not change production safety flags;
- do not publish a new approved runtime image;
- do not open production traffic.

The accepted image digests above are the authoritative CLX-021 runtime reference.

## Final cutover boundary

CLX-021 does **not** authorize live cutover. A separate explicit user approval is required before any of these can change:

- `M3_PRODUCTION_TRAFFIC=ON`
- live provider connections ON
- real-money execution ON

Until then the system remains an accepted, locked production release candidate.
