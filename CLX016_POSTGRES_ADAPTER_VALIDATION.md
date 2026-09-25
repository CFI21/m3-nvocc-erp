# M3 CLX-016 PostgreSQL Runtime Adapter Validation

Target database: M3-NVOCC-PROD  
Supabase project ref: `ozupgknqaqgvprliewxe`

## Safety state

- Production traffic: OFF
- Live providers: OFF
- Real money: OFF
- DATABASE_URL is rejected at runtime unless it contains the approved M3 project ref.
- ANCLINE project ref `wgxbvbcttxkdqmtdngmq` is not accepted by the adapter.

## Runtime changes

- `DATABASE_URL` selects PostgreSQL runtime.
- psycopg 3 connection with dict rows.
- SQLite qmark placeholders translate to PostgreSQL `%s`.
- `INSERT OR IGNORE` translates to `ON CONFLICT DO NOTHING`.
- singleton `INSERT OR REPLACE` translates to `ON CONFLICT (id) DO UPDATE`.
- last-row-id insert paths use `RETURNING id`, including `treasury_payment_batches`.
- SQLite `date(...)` calls translate to `CAST(... AS date)`.
- SQLite `julianday(...)-julianday(...)` aging expressions translate to PostgreSQL date subtraction.
- SQLite `json_set` GL voucher write-through translates to PostgreSQL `jsonb_set` and stores back as TEXT.
- public table/trigger discovery uses PostgreSQL catalogs.
- local SQLite backup/restore paths are blocked when PostgreSQL is active.

## Validation performed for CLX-016

- Local accepted-runtime regression excluding the obsolete CLX-010 historical assertion: **161/161 PASS**.
- Focused CLX-011 through CLX-014 regression: **83/83 PASS**.
- PostgreSQL SQL translation checks: **PASS**.
- Direct M3-NVOCC-PROD transactional insert/identity test: **PASS**.
- Direct test transaction was rolled back; persisted UAT rows after rollback: **0**.
- Jobs 50001–50005 present: **5/5**.
- Each job has 19 Agent Tasks, 30 GL records, 37 Treasury records and 20 Integration records.
- IAM: 6 users, 10 roles, 157 role-permissions, 7 scope rules, 3 SoD conflicts, 3 approval limits.
- Supabase security advisor: no high-severity finding was introduced; 126 INFO findings remain for intentional RLS-enabled/no-policy deny-by-default tables.

## Render configuration

- Runtime: Python 3
- Branch: main
- Region: Frankfurt
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/api/v1/health`
- Auto deploy: OFF
- `M3_SUPABASE_PROJECT_REF=ozupgknqaqgvprliewxe`
- `M3_PRODUCTION_TRAFFIC=OFF`
- `M3_LIVE_PROVIDERS=OFF`
- `REAL_MONEY=OFF`

## Current gate

**READY FOR CONTROLLED RENDER API DEPLOYMENT / UAT.**

This is not a production cutover approval. Production traffic, live providers and real-money execution remain OFF.
