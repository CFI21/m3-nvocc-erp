import json
from pathlib import Path
from app.screen_catalog import build_catalog

ROOT = Path(__file__).resolve().parents[1]

def manifest():
    return json.loads((ROOT / "CLX053_ACCEPTANCE_MANIFEST.json").read_text())

def test_clx053_manifest_acceptance_and_freeze():
    m = manifest()
    assert m["phase"] == "CLX-053"
    assert m["status"] == "ACCEPTED"
    assert m["freeze"] is True
    assert m["scope"]["screenBaseline"] == 196
    assert build_catalog()["screen_count"] == 196

def test_clx053_controls_remain_off():
    m = manifest()
    assert m["controls"]["productionTraffic"] is False
    assert m["controls"]["liveProviders"] is False
    assert m["controls"]["realMoney"] is False
    assert m["controls"]["separateExplicitActivationRequired"] is True

def test_clx053_runtime_and_database_lock():
    m = manifest()
    assert m["runtime"]["webDigest"] == "sha256:069beaca63368729f10a5ab5be57824963b40ff25331a8a4f8574cf9a59aa00b"
    assert m["runtime"]["apiDigest"] == "sha256:bbc746f1317f5eec88addfb040acb94f2d7e33760c57dcd965cd3b013d050c31"
    assert m["database"]["projectRef"] == "ozupgknqaqgvprliewxe"
    assert m["database"]["status"] == "ACTIVE_HEALTHY"
    assert m["database"]["jobs50001to50005"] == ["50001","50002","50003","50004","50005"]

def test_clx053_regression_evidence_is_green():
    m = manifest()
    for k, v in m["regression"].items():
        if k.endswith("RunId"):
            assert str(v).isdigit()
        else:
            assert v == "PASS"
    assert m["rollback"]["controlledRollbackRestoreDrill"] == "PASS"
    assert m["noAnclineAncChanges"] is True
