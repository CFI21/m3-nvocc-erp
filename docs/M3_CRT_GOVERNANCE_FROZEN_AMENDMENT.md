# M3 NVOCC ERP — CRT Governance / TRT Naming Amendment

> FROZEN — M3 ONLY — ANCLINE TOUCHED = NO  
> DRAFT IMPLEMENTATION — HUMAN PRODUCTION REVIEW REQUIRED

## §3.5 CRT Naming Clarification
- **CRT = Change Request Ticket** — governance workflow only.
- **TRT = Terminal Release Ticket** — operational Import / Export / Transshipment terminal-release transaction.
- The former operational names CRT / Export CRT / Import CRT / Transshipment CRT are renamed TRT / Export TRT / Import TRT / Transshipment TRT.
- Historical acceptance artifacts may retain historical terminology; current specification, code, database values, UI labels and API/module identifiers use TRT.

## §23 Frozen Principles — Principles 25–30
25. CRT is governance, never an operational release transaction.
26. TRT is the operational terminal-release transaction for Import, Export and Transshipment flows.
27. After approval, direct modification of governed Import / Export / TS operational records is prohibited. Changes must flow through an approved CRT.
28. Maker-checker is enforced by human USER ID. Requester and checker/approver cannot be the same human user. AI and service identities have no approval authority.
29. Financially posted impact is corrected only through reversal + corrected document + reposting; posted financial records are never edited in place.
30. Escalated approval requirements are cumulative: TS requires TS Branch Manager + TS Finance; closed-period impact requires Finance Manager + CFO; tax-filed impact requires CFO + external-advisor evidence.

## §26 State Machines + Abbreviation Table

### CRT — Change Request Ticket
`DRAFT → SUBMITTED → UNDER_REVIEW → APPROVED / REJECTED → APPLIED → CLOSED`

Rules:
- REJECTED is terminal unless a new CRT is raised.
- APPLIED requires all required approvals.
- CLOSED requires APPLIED.
- No AI/service account can approve.
- Approval requires MFA for internal human approvers.
- Maker ≠ checker by USER ID.

### TRT — Terminal Release Ticket
TRT retains the operational lifecycle appropriate to Import / Export / TS execution. It is not an approval-governance object and cannot be used to bypass CRT after an approved record is locked.

### Abbreviations
| Code | Meaning | Purpose |
|---|---|---|
| CRT | Change Request Ticket | Governed post-approval change |
| TRT | Terminal Release Ticket | Operational terminal-release transaction |
| CRO | Container Release Order | Container/equipment release planning |
| TS | Transshipment | Via-port operational movement |

## §27 CRT Exception Codes
- `CRT_REQUIRED_AFTER_APPROVAL`
- `CRT_INVALID_TRANSITION`
- `CRT_TARGET_RECORD_NOT_FOUND`
- `CRT_TARGET_VERSION_CHANGED`
- `CRT_TARGET_MODULE_INVALID`
- `MAKER_CHECKER_SAME_USER`
- `HUMAN_ONLY`
- `VALID_HUMAN_USER_ID_REQUIRED`
- `MFA_REQUIRED`
- `CRT_APPROVAL_ROLE_NOT_REQUIRED`
- `CRT_NOT_APPROVED`
- `TS_BRANCH_MANAGER_APPROVAL_REQUIRED`
- `TS_FINANCE_APPROVAL_REQUIRED`
- `CLOSED_PERIOD_FINANCE_MANAGER_REQUIRED`
- `CLOSED_PERIOD_CFO_REQUIRED`
- `TAX_FILED_CFO_REQUIRED`
- `EXTERNAL_ADVISOR_EVIDENCE_REQUIRED`
- `FINANCIAL_REVERSAL_CORRECTION_REPOST_REQUIRED`
- `LEGACY_OPERATIONAL_CRT_IDENTIFIER`

## §29 CRT Governance — FROZEN
1. CRT is the only governed path to modify a governed operational record after approval.
2. Scope: Import, Export and Transshipment.
3. Lifecycle: DRAFT → SUBMITTED → UNDER_REVIEW → APPROVED/REJECTED → APPLIED → CLOSED.
4. Maker-checker is by USER ID; requester cannot approve their own CRT.
5. Internal approval requires human identity and MFA.
6. AI/service identities may draft or comment but cannot approve.
7. TS changes require TS Branch Manager + TS Finance.
8. Closed-period impact requires Finance Manager + CFO.
9. Tax-filed impact requires CFO + external advisor evidence.
10. Posted financial impact requires reversal + corrected document + reposting; no in-place edit.
11. Applying a CRT records target version, before/after evidence, actor, reason and immutable audit.
12. Direct update/delete/amend of approved governed TRT records must return CRT_REQUIRED_AFTER_APPROVAL.
13. ANCLINE is outside scope and must remain untouched.