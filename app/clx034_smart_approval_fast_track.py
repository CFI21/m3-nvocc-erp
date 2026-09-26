from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional
import json

from .db import connect
from .admin import session, audit
from .clx032_finance_controls import sod_violations
from .clx033_finance_transaction_governance import (
    Decision,
    _build_context,
    _chain_key,
    _reviews_for_chain,
    _state,
    enforce_execution as clx033_enforce_execution,
    workbench as clx033_workbench,
    decide as clx033_decide,
)

router = APIRouter(prefix='/api/clx034', tags=['CLX-034 Smart Approval Fast Track'])

MANDATORY_MANUAL_ACTIONS = {'WRITE_OFF', 'REVERSE', 'CLOSE'}
TREASURY_MANUAL_ACTIONS = {'RELEASE', 'PAY'}
POLICY_RISK_FLAGS = {
    'EXCEPTION', 'OVERRIDE', 'BANK_DETAIL_CHANGE', 'MASTER_DATA_SENSITIVE',
    'POLICY_BREACH', 'COMPLIANCE_HOLD', 'MANUAL_REVIEW'
}

class BulkDecision(BaseModel):
    review_refs: list[str] = Field(min_length=1, max_length=100)
    decision: str = Field(pattern='^(APPROVE|REJECT|RETURN_FOR_CORRECTION)$')
    comment: Optional[str] = None
    reason_code: Optional[str] = None

class ClassifyBody(BaseModel):
    resource_type: str
    module: str
    record_id: int
    action: str
    version: int = Field(ge=1)
    amount: float = 0
    currency: str = 'USD'
    transaction_type: str
    office_code: Optional[str] = None
    country_code: Optional[str] = None
    maker_ref: Optional[str] = None
    target_url: Optional[str] = None
    risk_flags: list[str] = Field(default_factory=list)

def _manual_reasons(base, risk_flags=None, existing_state=None, sod_conflicts=None):
    action = str(base.get('action') or '').upper()
    tx_type = str(base.get('transaction_type') or '').upper()
    reasons = []
    if int(base.get('required_levels') or 1) > 1:
        reasons.append('HIGH_VALUE_OR_MULTI_LEVEL_AUTHORITY')
    if action in MANDATORY_MANUAL_ACTIONS:
        reasons.append(action + '_REQUIRES_APPROVAL')
    if tx_type == 'TREASURY' and action in TREASURY_MANUAL_ACTIONS:
        reasons.append('PAYMENT_RELEASE_REQUIRES_APPROVAL')
    for flag in (risk_flags or []):
        f = str(flag).upper()
        if f in POLICY_RISK_FLAGS:
            reasons.append(f)
    if existing_state and existing_state != 'NONE':
        reasons.append('EXISTING_GOVERNED_APPROVAL_CHAIN')
    if sod_conflicts:
        reasons.append('SOD_OR_POLICY_CONFLICT')
    return list(dict.fromkeys(reasons))

def classify_context(c, s, base, risk_flags=None):
    chain_id = base['chain_id']
    existing = _reviews_for_chain(c, chain_id)
    existing_state = _state(existing)['status'] if existing else 'NONE'
    conflicts = sod_violations(c, s['user_id'])
    reasons = _manual_reasons(
        base,
        risk_flags=risk_flags,
        existing_state=existing_state,
        sod_conflicts=conflicts,
    )
    return {
        'route': 'MANUAL_APPROVAL' if reasons else 'STRAIGHT_THROUGH',
        'approval_required': bool(reasons),
        'reasons': reasons,
        'chain_id': chain_id,
        'required_levels': int(base.get('required_levels') or 1),
        'approval_source': base.get('approval_source'),
        'existing_state': existing_state,
        'sod_conflicts': [x.get('conflict_code') for x in conflicts],
    }

def enforce_execution(resource_type, module, rid, action, session_token, version, amount, currency,
                      tx_type, office_code, country_code, maker_ref, target_url, risk_flags=None):
    if not session_token:
        return {'legacy_compatibility': True, 'phase': 'CLX-034'}
    c = connect()
    try:
        s = session(c, session_token)
        base = _build_context(
            c, s, resource_type, module, rid, action, version, amount, currency,
            tx_type, office_code, country_code, maker_ref, target_url
        )
        classification = classify_context(c, s, base, risk_flags=risk_flags)
        if not classification['approval_required']:
            audit(
                c,
                s['user_ref'],
                'SMART_APPROVAL_STRAIGHT_THROUGH',
                'FINANCE_APPROVAL',
                base['chain_id'],
                None,
                {
                    'status': 'AUTO_APPROVED',
                    'action': base['action'],
                    'transaction_type': base['transaction_type'],
                    'amount': base['amount'],
                    'currency': base['currency'],
                },
                {
                    'phase': 'CLX-034',
                    'reason': 'LOW_RISK_WITHIN_ENTITLEMENT_SCOPE_LIMIT_NO_SOD',
                    'target_url': target_url,
                },
            )
            return {
                'approved': True,
                'fast_track': True,
                'auto_approved': True,
                'approval_required': False,
                'phase': 'CLX-034',
                'chain_id': base['chain_id'],
                'required_levels': 0,
                'reasons': [],
            }
    finally:
        c.close()

    try:
        return clx033_enforce_execution(
            resource_type, module, rid, action, session_token, version, amount, currency,
            tx_type, office_code, country_code, maker_ref, target_url
        )
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            detail = dict(exc.detail)
            detail['phase'] = 'CLX-034'
            detail['smart_route'] = 'MANUAL_APPROVAL'
            detail['approval_reasons'] = classification['reasons']
            raise HTTPException(exc.status_code, detail)
        raise

def enforce_gl_action(module, rid, action, session_token, version, reason=None):
    if not session_token:
        return {'legacy_compatibility': True, 'phase': 'CLX-034'}
    from .clx033_finance_transaction_governance import _json, _country_for_office, _gl_tx_type
    c = connect()
    try:
        s = session(c, session_token)
        r = c.execute(
            '''SELECT g.*,j.job_ref FROM gl_records g LEFT JOIN jobs j ON j.id=g.job_id
               WHERE g.module=? AND g.id=?''', (module, rid)
        ).fetchone()
        if not r:
            raise HTTPException(404, 'GL record not found')
        p = _json(r['payload_json'])
        amount = float(p.get('Amount') or p.get('Total Amount') or 0)
        currency = str(p.get('Currency') or 'USD').upper()
        maker = None
        v = c.execute('SELECT * FROM gl_vouchers WHERE gl_record_id=?', (rid,)).fetchone()
        if v:
            amount = float(v['total_debit'] or amount)
            currency = v['currency'] or currency
            maker = v['maker_role']
        country = _country_for_office(c, s['office_code'])
        risk_flags = []
        if reason and any(k in reason.upper() for k in ('OVERRIDE', 'EXCEPTION', 'POLICY', 'BANK DETAIL', 'MASTER DATA')):
            risk_flags.append('EXCEPTION')
    finally:
        c.close()
    return enforce_execution(
        'GL', module, rid, action, session_token, version, amount, currency,
        _gl_tx_type(module), s['office_code'], country, maker,
        f'/api/v1/gl/{module}/{rid}/actions/{action.lower()}', risk_flags=risk_flags
    )

def enforce_treasury_action(module, rid, action, session_token, version):
    if not session_token:
        return {'legacy_compatibility': True, 'phase': 'CLX-034'}
    from .clx033_finance_transaction_governance import _country_for_office, _treasury_tx_type
    c = connect()
    try:
        s = session(c, session_token)
        r = c.execute('SELECT * FROM treasury_records WHERE module=? AND id=?', (module, rid)).fetchone()
        if not r:
            raise HTTPException(404, 'Treasury record not found')
        country = _country_for_office(c, s['office_code'])
        maker = r['maker_id']
        amount = float(r['amount'] or 0)
        currency = r['currency'] or 'USD'
        flags = []
        module_upper = module.upper()
        if 'BANK' in module_upper and ('DETAIL' in module_upper or 'ACCOUNT' in module_upper):
            flags.append('BANK_DETAIL_CHANGE')
    finally:
        c.close()
    return enforce_execution(
        'TREASURY', module, rid, action, session_token, version, amount, currency,
        _treasury_tx_type(module), s['office_code'], country, maker,
        f'/api/v1/treasury/{module}/{rid}/actions/{action.lower()}', risk_flags=flags
    )

@router.post('/classify')
def classify(body: ClassifyBody, x_m3_session: Optional[str] = Header(None, alias='X-M3-Session')):
    c = connect()
    try:
        s = session(c, x_m3_session)
        base = _build_context(
            c, s, body.resource_type, body.module, body.record_id, body.action,
            body.version, body.amount, body.currency, body.transaction_type,
            body.office_code, body.country_code, body.maker_ref, body.target_url or ''
        )
        out = classify_context(c, s, base, risk_flags=body.risk_flags)
        return {'phase': 'CLX-034', **out}
    finally:
        c.close()

@router.get('/workbench')
def workbench(status: Optional[str] = None, q: Optional[str] = None,
              x_m3_session: Optional[str] = Header(None, alias='X-M3-Session')):
    data = clx033_workbench(status=status, q=q, x_m3_session=x_m3_session)
    items = []
    for item in data['items']:
        reasons = _manual_reasons(item, existing_state=item.get('state'))
        items.append({
            **item,
            'smart_route': 'MANUAL_APPROVAL',
            'approval_reasons': reasons or ['EXISTING_GOVERNED_APPROVAL_CHAIN'],
        })
    return {'phase': 'CLX-034', 'actor': data['actor'], 'count': len(items), 'items': items}

@router.post('/bulk-decision')
def bulk_decision(body: BulkDecision,
                  x_m3_session: Optional[str] = Header(None, alias='X-M3-Session')):
    results = []
    for ref in body.review_refs:
        try:
            out = clx033_decide(
                ref,
                Decision(decision=body.decision, comment=body.comment, reason_code=body.reason_code),
                x_m3_session,
            )
            results.append({'review_ref': ref, 'ok': True, 'result': out})
        except HTTPException as exc:
            results.append({'review_ref': ref, 'ok': False, 'status_code': exc.status_code, 'detail': exc.detail})
    return {
        'phase': 'CLX-034',
        'decision': body.decision,
        'requested': len(body.review_refs),
        'succeeded': sum(1 for x in results if x['ok']),
        'failed': sum(1 for x in results if not x['ok']),
        'results': results,
    }

@router.get('/policy')
def policy():
    return {
        'phase': 'CLX-034',
        'mode': 'EXCEPTION_BASED_GOVERNANCE',
        'straight_through_when': [
            'ENTITLED', 'IN_SCOPE', 'WITHIN_APPROVED_LIMIT',
            'VALID_DATA', 'NO_SOD_CONFLICT', 'NO_RISK_FLAG'
        ],
        'manual_approval_when': sorted(MANDATORY_MANUAL_ACTIONS),
        'treasury_manual_actions': sorted(TREASURY_MANUAL_ACTIONS),
        'risk_flags': sorted(POLICY_RISK_FLAGS),
        'live_providers': False,
        'real_money': False,
    }
