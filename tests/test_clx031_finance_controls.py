import sqlite3
import pytest
from fastapi import HTTPException

from app import db
from app.admin_seed import run as admin_seed_run, phash
from app.admin import Login, login
from app.clx031_finance_controls import (
    ApprovalLimitRequest, DelegationRequest, SodRequest, Decision, EvaluateRequest,
    request_limit, request_delegation, request_sod, decide, evaluate,
    revoke_limit, revoke_delegation, revoke_sod, workspace
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'clx031.db')
    admin_seed_run()
    c=db.connect()
    office=c.execute("SELECT id FROM iam_offices WHERE office_code='RTM'").fetchone()['id']
    role=c.execute("SELECT id FROM iam_roles WHERE role_code='ORG_ADMIN'").fetchone()['id']
    c.execute(
        '''INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required)
           VALUES(?,?,?,?,?,?,1)''',
        ('USR-CLX031-CHECKER','checker.admin','Independent Finance Checker','checker031@m3.test',phash('CheckerAdmin123!'),office),
    )
    uid=c.execute("SELECT id FROM iam_users WHERE username='checker.admin'").fetchone()['id']
    c.execute(
        '''INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by)
           VALUES(?,?,?,?,?,?)''',
        (uid,role,office,'2026-09-26T00:00:00+00:00','ACTIVE','seed-clx031'),
    )
    c.close()
    return db.DB_PATH


def tokens():
    maker=login(Login(username='admin',password='Admin123!',mfa_code='123456'))['session_token']
    checker=login(Login(username='checker.admin',password='CheckerAdmin123!',mfa_code='123456'))['session_token']
    return maker,checker


def approve_review(review_ref,checker):
    return decide(review_ref,Decision(decision='APPROVE',comment='Independent CLX-031 approval'),checker)


def test_multilevel_user_approval_limits_and_escalation(isolated_db):
    maker,checker=tokens()
    for level,amount in [(1,10000),(2,50000)]:
        req=request_limit(
            ApprovalLimitRequest(
                subject_type='USER',subject_code='finance.dxb',currency='USD',amount_limit=amount,
                transaction_type='JOURNAL',action='APPROVE',level=level,office_code='DXB',
                country_code='UAE',valid_from='2026-09-01T00:00:00+00:00',
                valid_to='2027-09-01T00:00:00+00:00',reason=f'Journal approval level {level}'
            ),
            maker,
        )
        assert req['status']=='PENDING_APPROVAL'
        with pytest.raises(HTTPException) as exc:
            decide(req['review_ref'],Decision(decision='APPROVE'),maker)
        assert exc.value.status_code==409
        assert exc.value.detail['code']=='FOUR_EYES_REQUIRED'
        assert approve_review(req['review_ref'],checker)['status']=='APPROVED'

    low=evaluate(EvaluateRequest(username='finance.dxb',currency='USD',amount=5000,transaction_type='JOURNAL',action='APPROVE',office_code='DXB',country_code='UAE'),checker)
    assert low['decision']=='WITHIN_LIMIT'
    assert low['required_level']==1

    mid=evaluate(EvaluateRequest(username='finance.dxb',currency='USD',amount=25000,transaction_type='JOURNAL',action='APPROVE',office_code='DXB',country_code='UAE'),checker)
    assert mid['decision']=='WITHIN_LIMIT'
    assert mid['required_level']==2

    high=evaluate(EvaluateRequest(username='finance.dxb',currency='USD',amount=75000,transaction_type='JOURNAL',action='APPROVE',office_code='DXB',country_code='UAE'),checker)
    assert high['decision']=='ESCALATE'
    assert high['reason']=='AMOUNT_EXCEEDS_AVAILABLE_LIMIT'

    wrong_scope=evaluate(EvaluateRequest(username='finance.dxb',currency='USD',amount=5000,transaction_type='JOURNAL',action='APPROVE',office_code='RTM',country_code='NL'),checker)
    assert wrong_scope['decision']=='ESCALATE'
    assert wrong_scope['reason']=='NO_APPLICABLE_APPROVAL_LIMIT'


def test_limit_revoke_removes_authority(isolated_db):
    maker,checker=tokens()
    req=request_limit(
        ApprovalLimitRequest(subject_type='ROLE',subject_code='FINANCE',currency='USD',amount_limit=20000,
            transaction_type='AR',action='WRITE_OFF',level=1,reason='AR write-off control'),
        maker,
    )
    out=approve_review(req['review_ref'],checker)
    lid=out['approval_limit']['id']
    assert revoke_limit(lid,checker)['status']=='REVOKED'
    view=workspace(x_m3_session=checker)
    row=next(x for x in view['approval_limits'] if x['id']==lid)
    assert row['status']=='INACTIVE'


def test_delegation_four_eyes_activation_and_revoke(isolated_db):
    maker,checker=tokens()
    req=request_delegation(
        DelegationRequest(
            from_username='admin',to_username='finance.dxb',permission_code='GL_VIEW',
            transaction_type='GL',office_code='DXB',country_code='UAE',
            valid_from='2026-09-26T00:00:00+00:00',valid_to='2026-10-15T00:00:00+00:00',
            reason='Temporary month-end cover'
        ),
        maker,
    )
    assert req['status']=='PENDING_APPROVAL'
    out=approve_review(req['review_ref'],checker)
    ref=out['delegation']['delegation_ref']
    assert out['delegation']['status']=='ACTIVE'
    assert revoke_delegation(ref,checker)['status']=='REVOKED'
    view=workspace(q=ref,x_m3_session=checker)
    assert next(x for x in view['delegations'] if x['delegation_ref']==ref)['status']=='REVOKED'


def test_sod_blocks_journal_maker_plus_checker_delegation(isolated_db):
    maker,checker=tokens()
    c=db.connect()
    uid=c.execute("SELECT id FROM iam_users WHERE username='finance.dxb'").fetchone()['id']
    c.execute('''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
      VALUES(?,?,?,?,?,?,?,?)''',('FENT-SOD-MAKER',uid,'JOURNAL_CREATE','2026-09-01T00:00:00+00:00','2027-09-01T00:00:00+00:00','test','ACTIVE','test'))
    c.close()
    with pytest.raises(HTTPException) as exc:
        request_delegation(
            DelegationRequest(
                from_username='admin',to_username='finance.dxb',permission_code='JOURNAL_APPROVE',
                valid_from='2026-09-26T00:00:00+00:00',valid_to='2026-10-15T00:00:00+00:00',
                reason='Should be blocked by SoD'
            ),
            maker,
        )
    assert exc.value.status_code==409
    assert exc.value.detail['code']=='SOD_CONFLICT'
    assert 'SOD-JOURNAL-MAKER-CHECKER' in exc.value.detail['conflicts']


def test_custom_sod_rule_requires_four_eyes_and_can_be_revoked(isolated_db):
    maker,checker=tokens()
    req=request_sod(
        SodRequest(
            conflict_code='SOD-TEST-AR-AP',
            capability_a='PERM:GL_CREATE',capability_b='PERM:GL_DISABLE',
            reason='Test incompatible finance capabilities',severity='HIGH'
        ),
        maker,
    )
    assert req['status']=='PENDING_APPROVAL'
    with pytest.raises(HTTPException) as exc:
        decide(req['review_ref'],Decision(decision='APPROVE'),maker)
    assert exc.value.detail['code']=='FOUR_EYES_REQUIRED'
    out=approve_review(req['review_ref'],checker)
    assert out['sod_conflict']['status']=='ACTIVE'
    assert revoke_sod('SOD-TEST-AR-AP',checker)['status']=='REVOKED'


def test_existing_required_sod_rules_are_seeded(isolated_db):
    maker,checker=tokens()
    view=workspace(x_m3_session=checker)
    codes={x['conflict_code'] for x in view['sod_conflicts']}
    assert {'SOD-JOURNAL-MAKER-CHECKER','SOD-POSTER-APPROVER','SOD-PAYMENT-MAKER-RELEASER'} <= codes


def test_finance_control_audit_is_immutable(isolated_db):
    maker,checker=tokens()
    req=request_limit(
        ApprovalLimitRequest(subject_type='ROLE',subject_code='FINANCE',currency='EUR',amount_limit=1000,
            transaction_type='AP',action='APPROVE',level=1,reason='Audit proof'),
        maker,
    )
    approve_review(req['review_ref'],checker)
    c=db.connect()
    row=c.execute("SELECT id FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT 1").fetchone()
    assert row
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE iam_audit_events SET action='TAMPERED' WHERE id=?",(row['id'],))
    c.close()
