import pytest
from fastapi import HTTPException
from app import admin


def test_sandbox_mfa_disabled_on_postgres(monkeypatch):
    monkeypatch.setattr(admin,'backend_name',lambda:'postgres')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    assert admin.sandbox_mfa_allowed() is False
    assert admin.sandbox_mfa_valid('123456') is False


def test_sandbox_mfa_disabled_when_production_traffic_on(monkeypatch):
    monkeypatch.setattr(admin,'backend_name',lambda:'sqlite-test')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    assert admin.sandbox_mfa_allowed() is False


def test_sandbox_mfa_allowed_only_in_nonprod_test_runtime(monkeypatch):
    monkeypatch.setattr(admin,'backend_name',lambda:'sqlite-test')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_SANDBOX_MFA_CODE','654321')
    assert admin.sandbox_mfa_allowed() is True
    assert admin.sandbox_mfa_valid('654321') is True
    assert admin.sandbox_mfa_valid('123456') is False


def test_invalid_sandbox_mfa_error_does_not_disclose_code(monkeypatch):
    class C:
        def execute(self,sql,args=None):
            class R:
                def fetchone(self_inner):
                    if 'FROM iam_users' in sql:
                        return {
                            'id':1,'status':'ACTIVE','password_hash':admin.phash('pw'),
                            'mfa_required':1,'user_ref':'USR-1','username':'u',
                            'office_code':'RTM'
                        }
                    return None
            return R()
        def close(self): pass
    monkeypatch.setattr(admin,'connect',lambda:C())
    monkeypatch.setattr(admin,'backend_name',lambda:'sqlite-test')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_SANDBOX_MFA_CODE','654321')
    with pytest.raises(HTTPException) as exc:
        admin.login(admin.Login(username='u',password='pw',mfa_code='bad'))
    assert exc.value.status_code==401
    assert exc.value.detail=={'code':'MFA_REQUIRED'}
    assert '654321' not in str(exc.value.detail)


def test_production_mfa_fails_closed_without_sandbox_bypass(monkeypatch):
    calls=[]
    class C:
        def execute(self,sql,args=None):
            calls.append(sql)
            class R:
                def fetchone(self_inner):
                    if 'FROM iam_users' in sql:
                        return {
                            'id':1,'status':'ACTIVE','password_hash':admin.phash('pw'),
                            'mfa_required':1,'user_ref':'USR-1','username':'u',
                            'office_code':'RTM'
                        }
                    return None
            return R()
        def close(self): pass
    monkeypatch.setattr(admin,'connect',lambda:C())
    monkeypatch.setattr(admin,'backend_name',lambda:'postgres')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    with pytest.raises(HTTPException) as exc:
        admin.login(admin.Login(username='u',password='pw',mfa_code='123456'))
    assert exc.value.status_code==503
    assert exc.value.detail=={'code':'PRODUCTION_MFA_PROVIDER_REQUIRED'}
