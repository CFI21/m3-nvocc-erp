"""Offline M3 isolated health and readiness middleware safety regression.

No network, Render, Neon or actual PostgreSQL connection is used.
"""
from fastapi.testclient import TestClient

import app.main as main
from app import db


def _unexpected_database_access(*args, **kwargs):
    raise AssertionError("public health path must not access or write to the database")


def test_health_is_database_independent_and_write_free(monkeypatch):
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC", "OFF")
    monkeypatch.setattr(db, "DATABASE_URL", "")
    monkeypatch.setattr(main, "get_runtime_mode_value", _unexpected_database_access)
    monkeypatch.setattr(main, "log_request", _unexpected_database_access)
    monkeypatch.setattr(main, "connect", _unexpected_database_access)

    with TestClient(main.app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200, response.text
    assert response.json()["project"] == "M3 NVOCC ERP"
    assert response.json()["sandbox_only"] is True
    assert response.json()["production_promoted"] is False
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


def test_health_path_other_methods_do_not_touch_database(monkeypatch):
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC", "OFF")
    monkeypatch.setattr(main, "get_runtime_mode_value", _unexpected_database_access)
    monkeypatch.setattr(main, "log_request", _unexpected_database_access)
    monkeypatch.setattr(main, "connect", _unexpected_database_access)

    with TestClient(main.app) as client:
        response = client.post("/api/v1/health")

    assert response.status_code == 405, response.text


def test_readiness_still_uses_database_and_fails_closed(monkeypatch):
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC", "OFF")
    monkeypatch.setattr(main, "get_runtime_mode_value", lambda: "NORMAL")
    monkeypatch.setattr(main, "log_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "connect", _unexpected_database_access)

    with TestClient(main.app) as client:
        response = client.get("/api/clx016/readiness")

    assert response.status_code == 200, response.text
    assert response.json()["database_health"] == "fail"
    assert response.json()["ready"] is False
    assert response.json()["error_class"] == "AssertionError"


def test_other_routes_retain_traffic_lock_and_runtime_mode_lookup(monkeypatch):
    calls = []
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC", "OFF")
    monkeypatch.delenv("M3_UAT_TOKEN", raising=False)
    monkeypatch.setattr(main, "get_runtime_mode_value", lambda: calls.append("runtime") or "NORMAL")
    monkeypatch.setattr(main, "log_request", lambda *args, **kwargs: calls.append("audit"))

    with TestClient(main.app) as client:
        response = client.get("/")

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "PRODUCTION_TRAFFIC_LOCKED"
    assert calls == ["runtime", "audit"]
