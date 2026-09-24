# M3 NVOCC ERP

Authoritative M3 NVOCC ERP source repository for CLX-016.

Database target: **M3-NVOCC-PROD** (Supabase project ref `ozupgknqaqgvprliewxe`).

Deployment safety state:
- Production traffic: **OFF**
- Live providers: **OFF**
- Real money: **OFF**
- Auto deploy: **OFF**
- ANCLINE resources: **DO NOT USE**

API runtime:
- Python 3 / FastAPI
- PostgreSQL via `DATABASE_URL`
- Render start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

See `CLX016_POSTGRES_ADAPTER_VALIDATION.md` for validation evidence.
