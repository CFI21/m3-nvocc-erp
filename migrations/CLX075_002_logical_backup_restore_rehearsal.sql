-- CLX-075 logical backup / isolated restore rehearsal for the production candidate.
-- Evidence-only schemas; no authoritative public model is duplicated or redirected.

CREATE SCHEMA IF NOT EXISTS m3_backup_clx075;
CREATE SCHEMA IF NOT EXISTS m3_restore_verify_clx075;

CREATE TABLE m3_backup_clx075.bookings AS TABLE public.bookings;
CREATE TABLE m3_backup_clx075.jobs AS TABLE public.jobs;
CREATE TABLE m3_backup_clx075.bills AS TABLE public.bills;
CREATE TABLE m3_backup_clx075.containers AS TABLE public.containers;
CREATE TABLE m3_backup_clx075.gl_records AS TABLE public.gl_records;
CREATE TABLE m3_backup_clx075.treasury_records AS TABLE public.treasury_records;
CREATE TABLE m3_backup_clx075.audit_events AS TABLE public.audit_events;
CREATE TABLE m3_backup_clx075.container_financial_ledger AS TABLE public.container_financial_ledger;
CREATE TABLE m3_backup_clx075.equipment_work_items AS TABLE public.equipment_work_items;
CREATE TABLE m3_backup_clx075.equipment_network_targets AS TABLE public.equipment_network_targets;
CREATE TABLE m3_backup_clx075.equipment_optimization_runs AS TABLE public.equipment_optimization_runs;
CREATE TABLE m3_backup_clx075.container_inspections AS TABLE public.container_inspections;
CREATE TABLE m3_backup_clx075.equipment_lease_contracts AS TABLE public.equipment_lease_contracts;

CREATE TABLE m3_restore_verify_clx075.bookings AS TABLE m3_backup_clx075.bookings;
CREATE TABLE m3_restore_verify_clx075.jobs AS TABLE m3_backup_clx075.jobs;
CREATE TABLE m3_restore_verify_clx075.bills AS TABLE m3_backup_clx075.bills;
CREATE TABLE m3_restore_verify_clx075.containers AS TABLE m3_backup_clx075.containers;
CREATE TABLE m3_restore_verify_clx075.gl_records AS TABLE m3_backup_clx075.gl_records;
CREATE TABLE m3_restore_verify_clx075.treasury_records AS TABLE m3_backup_clx075.treasury_records;
CREATE TABLE m3_restore_verify_clx075.audit_events AS TABLE m3_backup_clx075.audit_events;
CREATE TABLE m3_restore_verify_clx075.container_financial_ledger AS TABLE m3_backup_clx075.container_financial_ledger;
CREATE TABLE m3_restore_verify_clx075.equipment_work_items AS TABLE m3_backup_clx075.equipment_work_items;
CREATE TABLE m3_restore_verify_clx075.equipment_network_targets AS TABLE m3_backup_clx075.equipment_network_targets;
CREATE TABLE m3_restore_verify_clx075.equipment_optimization_runs AS TABLE m3_backup_clx075.equipment_optimization_runs;
CREATE TABLE m3_restore_verify_clx075.container_inspections AS TABLE m3_backup_clx075.container_inspections;
CREATE TABLE m3_restore_verify_clx075.equipment_lease_contracts AS TABLE m3_backup_clx075.equipment_lease_contracts;

REVOKE ALL ON SCHEMA m3_backup_clx075, m3_restore_verify_clx075 FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA m3_backup_clx075, m3_restore_verify_clx075 FROM PUBLIC, anon, authenticated;
