import sqlite3
import pytest
from fastapi import HTTPException

from app import db
from app.admin_seed import run as admin_seed_run, phash
from app.admin import Login, login, permission_code
from app.clx030_finance_entitlements import (
    FINANCE_CODES,
    GrantRequest,
    RoleAssignmentRequest,
    Decision,
    request_grant,
    request_role_assignment,
    decide,
    revoke_direct,
    revoke_role_assignment,
    matrix,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'clx030.db')
    admin_seed_run()
    c=db.connect()
    office=c.execute("SELECT id FROM iam_offices WHERE office_code='RTM'").fetchone()['id']
    role=c.execute("SELECT id FROM iam_roles WHERE role_code='ORG_ADMIN'").fetchone()['id']
    c.execute(
        '''INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required)
           VALUES(?,?,?,?,?,?,1)''',
        ('USR-CLX030-CHECKER','checker.admin','Independent Finance Checker','checker.admin@m3.test',phash('CheckerAdmin123!'),office),
    )
    uid=c.execute("SELECT id FROM iam_users WHERE username='checker.admin'").fetchone()['id']
    c.execute(
        '''INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by)
           VALUES(?,?,?,?,?,?)''',
        (uid,role,office,'2026-09-26T00:00:00+00:00','ACTIVE','seed-clx030'),
    )
    c.close()
    return db.DB_PATH


def tokens():
    maker=login(Login(username='admin',password='Admin123!',mfa_code='123456'))['session_token']
    checker=login(Login(username='checker.admin',password='CheckerAdmin123!',mfa_code='123456'))['session_token']
    return maker,checker


def test_all_granular_finance_entitlements_seed_without_unique_collisions(isolated_db):
    c=db.connect()
    rows={r['permission_code'] for r in c.execute("SELECT permission_code FROM iam_permissions WHERE module='finance-entitlement'")}
    assert FINANCE_CODES <= rows
    gl_accountant={r['permission_code'] for r in c.execute('''SELECT p.permission_code FROM iam_role_permissions rp
        JOIN iam_roles r ON r.id=rp.role_id JOIN iam_permissions p ON p.id=rp.permission_id
        WHERE r.role_code='GL_ACCOUNTANT' AND rp.effect='ALLOW' ''')}
    assert {'GL_VIEW','GL_CREATE','GL_EDIT','JOURNAL_CREATE'} <= gl_accountant
    assert 'JOURNAL_APPROVE' not in gl_accountant
    c.close()


def test_sensitive_direct_user_grant_requires_independent_checker_and_can_be_revoked(isolated_db):
    maker,checker=tokens()
    req=request_grant(
        GrantRequest(target_type='USER',username='finance.dxb',permission_code='GL_POST',reason='Month-end posting duty'),
        maker,
    )
    assert req['status']=='PENDING_APPROVAL'
    with pytest.raises(HTTPException) as exc:
        decide(req['review_ref'],Decision(decision='APPROVE'),maker)
    assert exc.value.status_code==409
    assert exc.value.detail['code']=='FOUR_EYES_REQUIRED'

    approved=decide(req['review_ref'],Decision(decision='APPROVE',comment='Independent approval'),checker)
    assert approved['status']=='APPROVED'
    temp_ref=approved['grant']['ref']

    c=db.connect()
    uid=c.execute("SELECT id FROM iam_users WHERE username='finance.dxb'").fetchone()['id']
    assert permission_code(c,uid,'GL_POST') is True
    c.close()

    revoked=revoke_direct(temp_ref,checker)
    assert revoked['status']=='REVOKED'
    c=db.connect()
    assert permission_code(c,uid,'GL_POST') is False
    c.close()


def test_gl_role_assignment_is_scoped_four_eyes_and_revocable(isolated_db):
    maker,checker=tokens()
    req=request_role_assignment(
        RoleAssignmentRequest(
            username='finance.dxb',role_code='GL_ACCOUNTANT',office_code='DXB',
            valid_from='2026-09-26T00:00:00+00:00',valid_to='2027-09-26T00:00:00+00:00',
            reason='Assigned accounting maker responsibility',
        ),
        maker,
    )
    assert req['status']=='PENDING_APPROVAL'
    approved=decide(req['review_ref'],Decision(decision='APPROVE'),checker)
    a=approved['assignment']
    assert a['role_code']=='GL_ACCOUNTANT'
    assert a['office_code']=='DXB'
    assert a['status']=='ACTIVE'

    view=matrix(q='finance.dxb',x_m3_session=checker)
    row=next(x for x in view['assignments'] if x['assignment_id']==a['assignment_id'])
    assert row['status']=='ACTIVE'

    out=revoke_role_assignment(a['assignment_id'],checker)
    assert out['status']=='REVOKED'
    view=matrix(q='finance.dxb',x_m3_session=checker)
    row=next(x for x in view['assignments'] if x['assignment_id']==a['assignment_id'])
    assert row['status']=='INACTIVE'


def test_nonsensitive_direct_view_grant_is_immediate(isolated_db):
    maker,_=tokens()
    out=request_grant(
        GrantRequest(target_type='USER',username='finance.dxb',permission_code='GL_VIEW',reason='Read-only month-end review'),
        maker,
    )
    assert out['status']=='ACTIVE'
    c=db.connect()
    uid=c.execute("SELECT id FROM iam_users WHERE username='finance.dxb'").fetchone()['id']
    assert permission_code(c,uid,'GL_VIEW') is True
    c.close()


def test_sensitive_role_permission_uses_four_eyes(isolated_db):
    maker,checker=tokens()
    req=request_grant(
        GrantRequest(target_type='ROLE',role_code='GL_ACCOUNTANT',permission_code='GL_POST',reason='Controlled posting extension'),
        maker,
    )
    assert req['status']=='PENDING_APPROVAL'
    approved=decide(req['review_ref'],Decision(decision='APPROVE'),checker)
    assert approved['grant']['role']=='GL_ACCOUNTANT'
    c=db.connect()
    effect=c.execute('''SELECT rp.effect FROM iam_role_permissions rp JOIN iam_roles r ON r.id=rp.role_id
        JOIN iam_permissions p ON p.id=rp.permission_id WHERE r.role_code='GL_ACCOUNTANT' AND p.permission_code='GL_POST' ''').fetchone()
    assert effect and effect['effect']=='ALLOW'
    c.close()


def test_finance_entitlement_audit_is_immutable(isolated_db):
    maker,_=tokens()
    request_grant(
        GrantRequest(target_type='USER',username='finance.dxb',permission_code='GL_VIEW',reason='Audit immutability proof'),
        maker,
    )
    c=db.connect()
    row=c.execute("SELECT id FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT 1").fetchone()
    assert row
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE iam_audit_events SET action='TAMPERED' WHERE id=?",(row['id'],))
    c.close()
