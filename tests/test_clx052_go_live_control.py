import json
from pathlib import Path

from app.screen_catalog import build_catalog

ROOT=Path(__file__).resolve().parents[1]

def parent():
    return json.loads((ROOT/"CLX051_ACCEPTANCE_MANIFEST.json").read_text())

def test_clx051_frozen_production_ready_parent():
    m=parent()
    assert m["status"]=="ACCEPTED"
    assert m["freeze"] is True
    assert m["production_ready"] is True
    assert m["scope"]["screenBaseline"]==196
    assert build_catalog()["screen_count"]==196
    assert m["controls"]=={"productionTraffic":False,"liveProviders":False,"realMoney":False}

def test_governed_runtime_targets_are_exact_clx051_targets():
    m=parent()
    assert m["evidence"]["restoredCandidate"]["webDigest"]=="sha256:069beaca63368729f10a5ab5be57824963b40ff25331a8a4f8574cf9a59aa00b"
    assert m["evidence"]["restoredCandidate"]["apiDigest"]=="sha256:bbc746f1317f5eec88addfb040acb94f2d7e33760c57dcd965cd3b013d050c31"
    rb=(ROOT/"CLX052_CUTOVER_RUNBOOK.md").read_text()
    assert "srv-darbgkvlk1mc738rohng" in rb
    assert "srv-dar9fh17lnhs73ahlh60" in rb
    assert "legacy-named m3-nvocc-api-prod and m3-nvocc-web-prod services are NOT cutover targets" in rb

def test_cutover_requires_separate_explicit_activation():
    rb=(ROOT/"CLX052_CUTOVER_RUNBOOK.md").read_text()
    assert "Separate explicit live-activation command" in rb
    assert "PRODUCTION_TRAFFIC=OFF" in rb
    assert "LIVE_PROVIDERS=OFF" in rb
    assert "REAL_MONEY=OFF" in rb

def test_watch_window_and_rollback_triggers_are_defined():
    rb=(ROOT/"CLX052_CUTOVER_RUNBOOK.md").read_text()
    assert "first 60 minutes" in rb
    for marker in ["Database health fails","Unauthorized cross-office/customer/agent data visibility",
                   "Maker-checker, four-eyes or SoD bypass","Repeated critical 5xx errors"]:
        assert marker in rb

def test_no_new_business_scope():
    m=parent()
    assert m["scope"]["newBusinessModules"] is False
