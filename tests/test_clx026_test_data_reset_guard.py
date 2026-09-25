import pytest
from fastapi import HTTPException
from app import main


def test_test_data_reset_is_disabled_on_postgres(monkeypatch):
    monkeypatch.setattr(main,'backend_name',lambda:'postgres')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    assert main.test_data_reset_allowed() is False


def test_test_data_reset_is_disabled_when_production_traffic_is_on(monkeypatch):
    monkeypatch.setattr(main,'backend_name',lambda:'sqlite-test')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    assert main.test_data_reset_allowed() is False


def test_test_data_reset_is_allowed_only_in_non_production_test_runtime(monkeypatch):
    monkeypatch.setattr(main,'backend_name',lambda:'sqlite-test')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    assert main.test_data_reset_allowed() is True


def test_reset_endpoint_fails_safe_before_seed_calls(monkeypatch):
    monkeypatch.setattr(main,'backend_name',lambda:'postgres')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    called={'seed':0,'admin':0,'master':0}
    monkeypatch.setattr(main,'seed_run',lambda force: called.__setitem__('seed',called['seed']+1))
    monkeypatch.setattr(main,'admin_seed_run',lambda: called.__setitem__('admin',called['admin']+1))
    monkeypatch.setattr(main,'masterdata_seed_run',lambda: called.__setitem__('master',called['master']+1))
    with pytest.raises(HTTPException) as exc:
        main.reset('ADMIN')
    assert exc.value.status_code==403
    assert exc.value.detail=='TEST_DATA_RESET_DISABLED_IN_PRODUCTION'
    assert called=={'seed':0,'admin':0,'master':0}


def test_reset_endpoint_still_requires_admin(monkeypatch):
    monkeypatch.setattr(main,'backend_name',lambda:'sqlite-test')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    with pytest.raises(HTTPException) as exc:
        main.reset('VIEWER')
    assert exc.value.status_code==403
    assert exc.value.detail=='ADMIN only'
