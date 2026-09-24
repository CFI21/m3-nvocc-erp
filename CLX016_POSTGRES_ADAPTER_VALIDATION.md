# M3 CLX-016 PostgreSQL Runtime Adapter Validation

Target database: M3-NVOCC-PROD
Supabase project ref: ozupgknqaqgvprliewxe

Safety state:
- Production traffic: OFF
- Live providers: OFF
- Real money: OFF
- ANCLINE resources: not used

Runtime changes:
- DATABASE_URL selects PostgreSQL runtime.
- psycopg 3 connection with dict rows.
- SQLite qmark placeholders translated to PostgreSQL %s.
- INSERT OR IGNORE translated to ON CONFLICT DO NOTHING.
- INSERT OR REPLACE singleton rows translated to ON CONFLICT (id) DO UPDATE.
- INSERT paths requiring lastrowid use RETURNING id.
- SQLite date(expr) translated to CAST(expr AS date).
- public table/trigger discovery uses PostgreSQL catalogs.
- SQLite-only local backup/restore endpoints are blocked on PostgreSQL instead of touching production data.
- M3_PRODUCTION_TRAFFIC and M3_LIVE_PROVIDERS remain OFF.

Regression:
- Accepted CLX regression selection: 178/178 PASS.
- PostgreSQL adapter translation checks: PASS.

Verified M3-NVOCC-PROD data:
- public tables: 126
- jobs: 5 (50001-50005)
- transaction_records: 95
- gl_records: 280
- treasury_records: 185
- integration_records: 100
- CLX-012 stage events: 150
- IAM users: 6
- IAM roles: 10
- public non-internal triggers: 10

Render configuration:
- Runtime: Python 3
- Build: pip install -r requirements.txt
- Start: uvicorn app.main:app --host 0.0.0.0 --port $PORT
- Auto deploy: OFF
