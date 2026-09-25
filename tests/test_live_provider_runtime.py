import hashlib
import hmac
import os

from app.live_provider_runtime import activation_blockers, redact, retry_state, verify_signature


BASE = {
    "enabled": 0,
    "circuit_state": "CLOSED",
    "endpoint_env": "M3_TEST_ENDPOINT",
    "credential_env": "M3_TEST_TOKEN",
    "webhook_secret_env": "M3_TEST_WEBHOOK",
    "real_money_capable": 0,
}


def test_redaction_masks_sensitive_values():
    d = redact({"token":"abc","iban":"NL00BANK1234567890","amount":99,"name":"ok"})
    assert d["token"] == "[REDACTED]"
    assert d["iban"].endswith("7890")
    assert d["amount"] == "[MASKED]"
    assert d["name"] == "ok"


def test_webhook_signature_validation():
    raw=b'{"event":"ok"}'
    sig=hmac.new(b"secret",raw,hashlib.sha256).hexdigest()
    assert verify_signature("secret",raw,sig) is True
    assert verify_signature("secret",raw,"bad") is False
    assert verify_signature("",raw,sig) is False


def test_retry_to_dlq():
    assert retry_state(1,3,False) == "RETRY"
    assert retry_state(3,3,False) == "DEAD_LETTER"
    assert retry_state(2,3,True) == "DELIVERED"


def test_activation_stays_blocked_without_live_configuration(monkeypatch):
    monkeypatch.setenv("M3_LIVE_PROVIDERS","OFF")
    monkeypatch.setenv("REAL_MONEY","OFF")
    row=dict(BASE)
    blockers=activation_blockers(row)
    assert "LIVE_PROVIDERS_SWITCH_OFF" in blockers
    assert "PROVIDER_NOT_ENABLED" in blockers
    assert "ENDPOINT_NOT_CONFIGURED" in blockers
    assert "CREDENTIAL_NOT_CONFIGURED" in blockers
    assert "WEBHOOK_SECRET_NOT_CONFIGURED" in blockers


def test_money_provider_needs_real_money_switch(monkeypatch):
    monkeypatch.setenv("M3_LIVE_PROVIDERS","ON")
    monkeypatch.setenv("REAL_MONEY","OFF")
    monkeypatch.setenv("M3_TEST_ENDPOINT","https://example.invalid")
    monkeypatch.setenv("M3_TEST_TOKEN","x")
    monkeypatch.setenv("M3_TEST_WEBHOOK","y")
    row=dict(BASE,enabled=1,real_money_capable=1)
    assert activation_blockers(row) == ["REAL_MONEY_SWITCH_OFF"]


def test_non_money_provider_can_be_core_ready(monkeypatch):
    monkeypatch.setenv("M3_LIVE_PROVIDERS","ON")
    monkeypatch.setenv("REAL_MONEY","OFF")
    monkeypatch.setenv("M3_TEST_ENDPOINT","https://example.invalid")
    monkeypatch.setenv("M3_TEST_TOKEN","x")
    monkeypatch.setenv("M3_TEST_WEBHOOK","y")
    row=dict(BASE,enabled=1,real_money_capable=0)
    assert activation_blockers(row) == []
