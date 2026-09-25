import pytest
from fastapi import HTTPException
from app import main


def test_hard_delete_disabled_on_postgres(monkeypatch):
    monkeypatch.setattr(main,'backend_name',lambda:'postgres')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    assert main.hard_delete_allowed() is False


def test_hard_delete_disabled_when_production_traffic_is_on(monkeypatch):
    monkeypatch.setattr(main,'backend_name',lambda:'sqlite-test')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    assert main.hard_delete_allowed() is False


def test_hard_delete_allowed_only_in_isolated_test_runtime(monkeypatch):
    monkeypatch.setattr(main,'backend_name',lambda:'sqlite-test')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    assert main.hard_delete_allowed() is True


def test_delete_endpoint_fails_safe_before_db_access(monkeypatch):
    monkeypatch.setattr(main,'backend_name',lambda:'postgres')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    monkeypatch.setattr(main,'require_module',lambda module: None)
    monkeypatch.setattr(main,'actor',lambda role,a,c:('ADMIN',None,None))
    called={'connect':0}
    monkeypatch.setattr(main,'connect',lambda: called.__setitem__('connect',called['connect']+1))
    with pytest.raises(HTTPException) as exc:
        main.delete_record('crt',1,1,'ADMIN',None,None)
    assert exc.value.status_code==403
    assert exc.value.detail['code']=='HARD_DELETE_DISABLED_IN_PRODUCTION'
    assert exc.value.detail['use_actions']==['cancel','reverse']
    assert called['connect']==0


def test_delete_endpoint_still_requires_admin(monkeypatch):
    monkeypatch.setattr(main,'require_module',lambda module: None)
    monkeypatch.setattr(main,'actor',lambda role,a,c:('VIEWER',None,None))
    with pytest.raises(HTTPException) as exc:
        main.delete_record('crt',1,1,'VIEWER',None,None)
    assert exc.value.status_code==403
    assert exc.value.detail=='ADMIN only delete'
