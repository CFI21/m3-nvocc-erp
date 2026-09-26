# M3 NVOCC ERP — Production Readiness

Governed parent baseline: **CLX-051 FINAL ACCEPTED + FROZEN**.

Current safety state:
- Production-ready baseline: **TRUE**
- Production traffic: **OFF**
- Live external providers: **OFF**
- Real money movement: **OFF**
- Database: **M3-NVOCC-PROD** / Supabase project `ozupgknqaqgvprliewxe`

Governed production-candidate runtime targets:
- Web: `srv-darbgkvlk1mc738rohng` — `m3-nvocc-web-latest.onrender.com`
- API: `srv-dar9fh17lnhs73ahlh60` — `m3-nvocc-api-latest-1.onrender.com`

The separate legacy-named `m3-nvocc-api-prod` and `m3-nvocc-web-prod` Render services are **not** CLX-052 cutover targets and must not be used for activation unless a later approved change explicitly migrates the governed runtime.

CLX-052 performs go-live control and cutover rehearsal only. Actual production traffic/provider/real-money activation requires a separate explicit authorization after CLX-052 acceptance.

ANCLINE resources must never be modified or reused for M3.
