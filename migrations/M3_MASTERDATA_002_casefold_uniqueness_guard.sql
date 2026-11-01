-- M3 UAT / Master Data: enforce case-insensitive identity at the database boundary.
-- STAGED ONLY. DO NOT APPLY while historical pending duplicates are unresolved.
-- Scope: approved isolated M3 PostgreSQL; NEVER production or ANCLINE.
--
-- Preflight (read-only, before scheduling an approved maintenance window):
-- SELECT domain, lower(record_key), count(*)
-- FROM public.md_change_requests WHERE status='PENDING'
-- GROUP BY domain, lower(record_key) HAVING count(*) > 1;
-- SELECT domain, lower(record_key), count(*)
-- FROM public.md_records
-- GROUP BY domain, lower(record_key) HAVING count(*) > 1;
--
-- Requires an authorized checker to resolve duplicate pending changes via
-- /api/masterdata/changes/{change_ref}/decision (REJECT). Do not DELETE,
-- modify audit logs, or resolve those changes with raw SQL.
--
-- PostgreSQL migration: transactionally locks the source tables, refuses
-- to apply against duplicate data, then creates two unique indexes.
-- Execute using ON_ERROR_STOP=1 in a reviewed, isolated M3 migration session.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';
LOCK TABLE public.md_change_requests, public.md_records IN SHARE ROW EXCLUSIVE MODE;

DO $m3_casefold_preflight$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.md_records
        GROUP BY lower(domain), lower(record_key) HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'M3_MASTER_CASEFOLD_CONFLICT: clean duplicate master keys through governance before creating indexes';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.md_change_requests
        WHERE status = 'PENDING'
        GROUP BY lower(domain), lower(record_key) HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'M3_PENDING_CASEFOLD_CONFLICT: checker must decide duplicate pending requests before creating indexes';
    END IF;
END;
$m3_casefold_preflight$;

CREATE UNIQUE INDEX IF NOT EXISTS m3_md_records_casefold_key_uq
    ON public.md_records (lower(domain), lower(record_key));

CREATE UNIQUE INDEX IF NOT EXISTS m3_md_pending_casefold_key_uq
    ON public.md_change_requests (lower(domain), lower(record_key))
    WHERE status = 'PENDING';

COMMIT;
