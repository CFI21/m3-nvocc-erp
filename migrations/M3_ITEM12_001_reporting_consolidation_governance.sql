-- M3 Item 12 Financial Reporting + Consolidation Governance
-- Additive governance fields on the existing interbranch settlement model only.
ALTER TABLE public.nvocc_interbranch_settlements ADD COLUMN IF NOT EXISTS elimination_status text NOT NULL DEFAULT 'NONE';
ALTER TABLE public.nvocc_interbranch_settlements ADD COLUMN IF NOT EXISTS elimination_requested_by text;
ALTER TABLE public.nvocc_interbranch_settlements ADD COLUMN IF NOT EXISTS elimination_requested_at text;
ALTER TABLE public.nvocc_interbranch_settlements ADD COLUMN IF NOT EXISTS elimination_reason text;
ALTER TABLE public.nvocc_interbranch_settlements ADD COLUMN IF NOT EXISTS elimination_approved_by text;
ALTER TABLE public.nvocc_interbranch_settlements ADD COLUMN IF NOT EXISTS elimination_approved_at text;
