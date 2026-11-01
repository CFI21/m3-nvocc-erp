# M3 Agent Setup — bulk acceptance gate (isolated PR #124)

## Verified source integrations
- Agent Master and Common Party are existing `md_records` domains; Agent payload uses `common_party_key`.
- IAM Agent grant uses `iam_party_access`; office membership uses `iam_office_membership`.
- Current lookup limits offices to exact `AGT-{agent_code}`; it is **single-office only**.
- `AGENT_NETWORK`, `AGENT_RELATION` and `AGENT_VOLUME_RESTRICTION` are configuration records. They are **not** IAM authorization.
- Existing Booking create/update invokes agent setup rules; per-field trusted POL/POD/TS port-grant helper exists but is not wired to authenticated grants.
- Agent UI tab/function parity has not been proved by an end-to-end acceptance run.

## Mandatory hold points
1. Multi-country: No authoritative verified mapping from one Agent to multiple IAM offices. Do not infer Agent ownership from user office membership, country alone, or Agent Network configuration; do not change the one-country master check before the mapping is confirmed.
2. Booking ports: Do not use request-submitted grant data or implicit Admin/Principal all-ports. Resolve scopes from explicit trusted IAM grants and verified Agent Port Coverage.
3. Agent screens: Compare Agent Detail, Emails, Countries, Offices, CRO Detail, B/L Template with existing screen catalog/field specs. Do not clone the reference screen or introduce parallel master tables.
4. Separate Online Agent Booking Request is **not** the operational Booking lifecycle and is outside this Agent Setup acceptance gate.

## Acceptance matrix
- Agent Code and PA Party identity resolve from one active record each.
- Cross-Agent and nonmember user office lookups are denied.
- One Agent can operate in two countries only via explicit assigned offices and authorized port coverage, with no cross-Agent leaks.
- Principal/HO authority cannot bypass port rules by role name alone.
- Agent tabs reopen with persisted existing master records; no copied party fields.
- Booking POL/POD/TS API create/update enforces the server-resolved grants; denied calls fail.
- Full targeted plus regression suite and authenticated UI/API tests pass before acceptance.
- No merge, deployment, production, provider, real-money, or ANCLINE changes.

## Status
**BLOCKED FOR FULL ACCEPTANCE** until the authoritative multi-office Agent assignment and IAM-to-port grants are defined and demonstrated. Tests committed in isolated branch are regression evidence only, not implementation of those missing relationships.
