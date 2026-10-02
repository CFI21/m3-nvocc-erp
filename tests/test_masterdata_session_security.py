import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin import Login, login
from app.masterdata import Change, Decision, create_change, decide

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'masterdata-session-security.db')
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH

def token(username,password):
    return login(Login(username=username,password=password,mfa_code='123456'))['session_token']

def body(key,name):
    return Change(domain='carrier',record_key=key,operation='CREATE',payload={'name':name},reason='session-security-test')

def test_no_session_is_401(isolated):
    with pytest.raises(HTTPException) as e:
        create_change(body('SEC-NO-SESSION','No Session'),x_m3_session=None)
    assert e.value.status_code==401
    assert e.value.detail['code']=='SESSION_REQUIRED'

def test_invalid_session_is_401(isolated):
    with pytest.raises(HTTPException) as e:
        create_change(body('SEC-BAD-SESSION','Bad Session'),x_m3_session='not-a-session')
    assert e.value.status_code==401
    assert e.value.detail['code']=='SESSION_INVALID'

def test_synthetic_role_headers_are_not_authorization_inputs(isolated):
    import inspect
    params=inspect.signature(create_change).parameters
    assert 'x_role' not in params
    assert 'x_user' not in params
    with pytest.raises(HTTPException) as e:
        create_change(body('SEC-FAKE-ROLE','Fake Header'),x_m3_session=None)
    assert e.value.status_code==401

def test_master_data_maker_create_allowed_but_approve_denied(isolated):
    maker=token('md.maker','Maker123!')
    cr=create_change(body('SEC-MAKER','Maker Record'),x_m3_session=maker)
    assert cr['status']=='PENDING'
    with pytest.raises(HTTPException) as e:
        decide(cr['change_ref'],Decision(decision='APPROVE'),x_m3_session=maker)
    assert e.value.status_code==403
    assert e.value.detail['code']=='CHECKER_PERMISSION_DENIED'

def test_master_data_manager_checker_approve_allowed(isolated):
    maker=token('md.maker','Maker123!')
    checker=token('md.checker','Checker123!')
    cr=create_change(body('SEC-CHECKER','Checker Record'),x_m3_session=maker)
    out=decide(cr['change_ref'],Decision(decision='APPROVE',comment='independent checker'),x_m3_session=checker)
    assert out['status']=='APPROVED'
    assert out['version']==1

def test_same_super_admin_cannot_make_and_self_approve(isolated):
    admin=token('admin','Admin123!')
    cr=create_change(body('SEC-FOUR-EYES','Four Eyes'),x_m3_session=admin)
    with pytest.raises(HTTPException) as e:
        decide(cr['change_ref'],Decision(decision='APPROVE'),x_m3_session=admin)
    assert e.value.status_code==409
    assert e.value.detail['code']=='FOUR_EYES_VIOLATION'
