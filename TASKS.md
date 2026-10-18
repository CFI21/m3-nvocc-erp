# M3 NVOCC ERP — P0 completion tracker
Deadline: 2026-10-20
Repository: CFI21/m3-nvocc-erp
Work branch: preview-pr124-booking-isolated
Safety: M3 only; PR #124 draft/unmerged; ANCLINE and production untouched; free tier only.

## Verified baseline (historical; recheck before relying on it)
- [x] BASE-01: 196 screens reported.
- [x] TEST-BASE: 709 tests reportedly passed on 2026-10-12.
- [x] B01-STRUCTURE: Agent Master six tabs established.
- [x] MAKER-CHECKER-FOUNDATION: Initial maker/checker controls.
- [x] ISOLATED-BRANCH: preview-pr124-booking-isolated exists.
- [x] CI-SCAFFOLD: .github/workflows/m3-isolated-ci.yml committed at 87583a33ebbf13fc08aabf982ee2de2eaea032ac.

## Open P0 workstreams (11)
- [ ] P0-CICD: identify actual workflow runs/failures; regression green (workflow file exists, no runs returned for new commit).
- [ ] P0-RENDER: verify isolated web/API/database connectivity (do not deploy production).
- [ ] P0-IAM: resolve 401; verify MFA, maker/checker permissions and navigation.
- [ ] P0-B01: six-tab CRUD, Countries from canonical IAM, Offices, approval, durable form state, duplicate protection.
- [ ] P0-BOOK: Draft -> RFA -> Approved, edit lock, withdrawal, validations.
- [ ] P0-JOB: approved Booking -> Job consistency.
- [ ] P0-BL: HBL/MBL, templates, propagation.
- [ ] P0-EQUIP: allocation, depots, movements.
- [ ] P0-FIN: charges, invoices, authorization, reconciliation.
- [ ] P0-SEC: cross-agent isolation, audit, server-side enforcement.
- [ ] P0-UAT: E2E flows, full regression, acceptance evidence.

## Acceptance
Close a workstream only with passing test/output evidence. Maintain DONE/LEFT/NEXT. No secrets in logs. Do not merge PR #124 or touch production/ANCLINE.
