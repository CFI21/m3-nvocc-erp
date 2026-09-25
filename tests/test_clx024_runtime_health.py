import os
from app import main


def test_runtime_flags_report_production_traffic_without_enabling_external_execution(monkeypatch):
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    monkeypatch.setenv('M3_LIVE_PROVIDERS','OFF')
    monkeypatch.setenv('REAL_MONEY','OFF')
    flags=main.runtime_flags()
    assert flags['production_traffic']=='ON'
    assert flags['production_promoted'] is True
    assert flags['sandbox_only'] is False
    assert flags['live_providers']=='OFF'
    assert flags['live_credentials'] is False
    assert flags['live_bank_api'] is False
    assert flags['live_tax_api'] is False
    assert flags['live_carrier_api'] is False
    assert flags['real_money']=='OFF'
    assert flags['real_payment_execution'] is False


def test_runtime_flags_report_fully_locked_mode(monkeypatch):
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_LIVE_PROVIDERS','OFF')
    monkeypatch.setenv('REAL_MONEY','OFF')
    flags=main.runtime_flags()
    assert flags['production_promoted'] is False
    assert flags['sandbox_only'] is True
    assert flags['live_credentials'] is False
    assert flags['real_payment_execution'] is False


def test_health_uses_runtime_baseline_and_dynamic_flags(monkeypatch):
    monkeypatch.setenv('M3_RUNTIME_BASELINE','M3-TEST-RUNTIME')
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    monkeypatch.setenv('M3_LIVE_PROVIDERS','OFF')
    monkeypatch.setenv('REAL_MONEY','OFF')
    h=main.health()
    assert h['project']=='M3 NVOCC ERP'
    assert h['baseline']=='M3-TEST-RUNTIME'
    assert h['production_traffic']=='ON'
    assert h['production_promoted'] is True
    assert h['sandbox_only'] is False
    assert h['live_credentials'] is False
    assert h['real_payment_execution'] is False
