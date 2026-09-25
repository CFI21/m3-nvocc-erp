# M3 NVOCC ERP — Autonomous Development Baseline

Development baseline: `M3-CLX022-AUTONOMOUS-DEVELOPMENT-BASELINE`

Parent production baseline: `M3-PRODUCTION-TRAFFIC-CUTOVER-ACCEPTED-20260925-024`

## Production boundary

The approved production runtime is frozen while development continues separately.

Production:
- Production traffic: **ON**
- Live external providers: **OFF**
- Real money: **OFF**
- Release branch: `release/clx021-final-20260925`
- Release source commit: `a94d1002c7d9d41a21c6e4e9a7be8b2939179fc5`
- API image digest: `sha256:8dea48fe4fae0e821304cabffa4224e1ec77a511e2d44984b377ee5a07197c43`
- Web image digest: `sha256:cf5cf670585198275853d22077eec33b983c5cd4ce1416a2cb0482930bffa9ab`

Development branch:
- `develop/clx022-autonomous`

## Autonomous development rules

Normal development does not require intermediate approval. The development cycle is:

1. Define a controlled change request.
2. Implement only on the development branch.
3. Run compile/unit/integration/regression tests.
4. Fix verified in-scope defects.
5. Rerun tests until passing.
6. Record acceptance evidence.
7. Freeze the successful development baseline.
8. Do not promote to production automatically.

## Production protection

Development must not:
- modify the approved production Render service configuration;
- deploy development images to the production API or Web services;
- write synthetic/UAT data to the production PostgreSQL database outside an explicit transaction that is rolled back;
- change production traffic, live-provider or real-money flags;
- touch ANCLINE resources.

Image publishing workflows remain scoped to the `main` branch. The development branch uses validation-only CI.
