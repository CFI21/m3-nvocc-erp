import json
from pathlib import Path

from app import db
from app.screen_catalog import build_catalog
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin import Login, login, logout, session
from fastapi import HTTPException
import pytest

ROOT=Path(__file__).resolve().parents[1]
SERVER_ONLY_RLS_TABLES={
    "provider_live_configs","provider_live_events","provider_live_attempts","provider_callback_receipts",
    "operations_work_items","operations_work_history",
    "management_daily_snapshots","management_alert_rules","management_alert_events",
}

def test_clx050_is_frozen_parent_and_196_screens_preserved():
    m=json.loads((ROOT/"CLX050_ACCEPTANCE_MANIFEST.json").read_text())
    assert m["status"]=="ACCEPTED" and m["freeze"] is True
    assert m["scope"]["screenBaseline"]==196
    assert build_catalog()["screen_count"]==196

def test_only_approved_m3_supabase_target_is_accepted(monkeypatch):
    assert db.APPROVED_PROJECT_REF=="ozupgknqaqgvprliewxe"
    monkeypatch.setattr(db,"DATABASE_URL","postgresql://postgres.ozupgknqaqgvprliewxe:secret@pooler.example/postgres")
    assert db.using_postgres() is True
    assert db.approved_database_target() is True
    monkeypatch.setattr(db,"DATABASE_URL","postgresql://postgres.wrongproject:secret@pooler.example/postgres")
    assert db.approved_database_target() is False

def test_production_runtime_has_no_silent_sqlite_fallback_contract():
    source=(ROOT/"app/db.py").read_text()
    assert "if using_postgres():" in source
    assert "assert_approved_database_target()" in source
    assert "DATABASE_URL_TARGET_REJECTED" in source
    assert "return PostgresConnectionCompat(DATABASE_URL)" in source

def test_web_api_security_and_traffic_lock_contract():
    source=(ROOT/"app/main.py").read_text()
    assert "https://m3-nvocc-web-latest.onrender.com" in source
    assert "M3_PRODUCTION_TRAFFIC" in source
    assert "PRODUCTION_TRAFFIC_LOCKED" in source
    assert "Content-Security-Policy" in source
    assert "X-Content-Type-Options" in source
    assert "X-Frame-Options" in source
    assert "Referrer-Policy" in source
    assert "Strict-Transport-Security" in source
    assert "X-Request-Id" in source and "X-Correlation-Id" in source
    nginx=(ROOT/"web/nginx.conf").read_text()
    assert "Content-Security-Policy" in nginx
    assert "Strict-Transport-Security" in nginx
    assert "https://m3-nvocc-api-latest-1.onrender.com" in nginx

def test_provider_and_real_money_hard_stops_remain_inherited():
    m=json.loads((ROOT/"CLX050_ACCEPTANCE_MANIFEST.json").read_text())
    assert m["controls"]["liveProviders"] is False
    assert m["controls"]["realMoney"] is False
    assert m["controls"]["productionTraffic"] is False

def test_clx051_security_gate_inventory_is_exact_and_non_business_scope():
    assert len(SERVER_ONLY_RLS_TABLES)==9
    assert all(x.startswith(("provider_","operations_","management_")) for x in SERVER_ONLY_RLS_TABLES)

def test_rollback_target_is_immutable_clx050_deploy_evidence():
    m=json.loads((ROOT/"CLX050_ACCEPTANCE_MANIFEST.json").read_text())
    assert m["evidence"]["webDeploy"]=="LIVE"
    assert m["evidence"]["apiDeploy"]=="LIVE"
    assert m["evidence"]["webImageDigest"].startswith("sha256:")
    assert m["evidence"]["apiImageDigest"].startswith("sha256:")

def test_login_logout_session_revocation(isolated_db):
    auth=login(Login(username="admin",password="Admin123!",mfa_code="123456"))
    token=auth["session_token"]
    c=db.connect()
    try:
        assert session(c,token)["status"]=="ACTIVE"
    finally:
        c.close()
    assert logout(x_m3_session=token)["status"]=="LOGGED_OUT"
    c=db.connect()
    try:
        with pytest.raises(HTTPException) as exc:
            session(c,token)
        assert exc.value.status_code==401
    finally:
        c.close()

@pytest.fixture()
def isolated_db(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"clx051.db")
    monkeypatch.setattr(db,"DATABASE_URL","")
    seed_run(True);admin_seed_run();masterdata_seed_run()
    return db.DB_PATH
