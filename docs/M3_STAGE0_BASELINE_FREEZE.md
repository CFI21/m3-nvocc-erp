# M3 NVOCC ERP — Stage 0 Baseline Freeze

> DRAFT — HUMAN REVIEW REQUIRED

## Summary
Stage 0 freezes the current M3 application structure before any new enforcement work. It adds no business behavior, no approval behavior, no production write path, and no production deployment.

## Scope
- M3 NVOCC ERP only.
- Current authoritative screen catalog baseline: 196 screens.
- 196 is a governed baseline, not a permanent ceiling.
- No ANCLINE changes.
- No real-money capability.
- No provider activation.
- No production data access.
- No real business data in tests.

## Baseline controls
The regression harness must prove:
1. Screen count is at least 196.
2. Existing screen IDs are unique.
3. Existing routes are unique within the authoritative screen catalog.
4. Business-navigation aliases resolve to existing screen IDs.
5. The catalog continues to declare duplicate models forbidden.
6. The catalog continues to declare placeholder screens forbidden.
7. REAL_MONEY remains false.
8. TrueLayer, OXR and Avalara remain disabled.
9. ANCLINE remains untouched.
10. The complete Python regression suite remains green.

## Governance
A legitimate move from 196 to 197+ screens is allowed only through a governed change that:
- documents the business purpose;
- proves the workspace is genuinely distinct;
- includes role, audit, navigation and regression coverage;
- does not duplicate an existing authoritative model.

Unauthorized removal, duplication or ID/route drift is a governance breach.

## AI boundary
AI-generated changes are drafts only. AI cannot approve a PR, merge a PR, deploy, operate production, alter production data, or create production credentials. Human review and testing are required before any merge or deployment.

## Stage 0 exit criteria
- Focused Stage 0 tests pass.
- Full M3 pytest regression passes.
- Python compile passes.
- Baseline snapshot artifact is generated for human review.
- No production/deploy step exists in this workflow.
- PR remains subject to human review and merge.
