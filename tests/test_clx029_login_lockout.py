import datetime
import pytest
from fastapi import HTTPException
from app import admin


def test_account_locked_when_locked_until_is_future():
    now=datetime.datetime(2026,9,25,20,0,tzinfo=datetime.timezone.utc)
    user={'locked_until':'2026-09-25T20:15:00+00:00'}
    assert admin.account_locked(user,now) is True


def test_account_not_locked_when_lock_expired():
    now=datetime.datetime(2026,9,25,20,20,tzinfo=datetime.timezone.utc)
    user={'locked_until':'2026-09-25T20:15:00+00:00'}
    assert admin.account_locked(user,now) is False


def test_register_failed_login_applies_configured_lockout():
    updates=[]
    class C:
        def execute(self,sql,args=None):
            if 'SELECT policy_value' in sql:
                key=args[0]
                value={'max_failed_attempts':'5','lockout_minutes':'15'}[key]
                class R:
                    def fetchone(self_inner): return {'policy_value':value}
                return R()
            updates.append((sql,args))
            class R:
                def fetchone(self_inner): return None
            return R()
    attempts,locked_until=admin.register_failed_login(
        C(),
        {'id':7,'failed_attempts':4},
        '2026-09-25T20:00:00+00:00'
    )
    assert attempts==5
    assert locked_until=='2026-09-25T20:15:00+00:00'
    assert updates[-1][1]==(5,locked_until,7)


def test_register_failed_login_does_not_lock_before_threshold():
    updates=[]
    class C:
        def execute(self,sql,args=None):
            if 'SELECT policy_value' in sql:
                class R:
                    def fetchone(self_inner): return {'policy_value':'5'}
                return R()
            updates.append((sql,args))
            class R:
                def fetchone(self_inner): return None
            return R()
    attempts,locked_until=admin.register_failed_login(
        C(),
        {'id':8,'failed_attempts':2},
        '2026-09-25T20:00:00+00:00'
    )
    assert attempts==3
    assert locked_until is None
    assert updates[-1][1]==(3,None,8)


def test_login_rejects_already_locked_account_before_password_or_mfa(monkeypatch):
    calls=[]
    class C:
        def execute(self,sql,args=None):
            calls.append((sql,args))
            class R:
                def fetchone(self_inner):
                    if 'FROM iam_users' in sql:
                        return {
                            'id':1,'status':'ACTIVE','password_hash':admin.phash('pw'),
                            'mfa_required':1,'user_ref':'USR-1','username':'u',
                            'office_code':'RTM','failed_attempts':5,
                            'locked_until':'2999-01-01T00:00:00+00:00'
                        }
                    return None
            return R()
        def close(self): pass
    monkeypatch.setattr(admin,'connect',lambda:C())
    with pytest.raises(HTTPException) as exc:
        admin.login(admin.Login(username='u',password='pw',mfa_code='123456'))
    assert exc.value.status_code==423
    assert exc.value.detail=={'code':'ACCOUNT_LOCKED'}
    assert any('ACCOUNT_LOCKED' in str(args) for sql,args in calls if 'iam_login_audit' in sql)
