import json
from pathlib import Path
from app.screen_catalog import build_catalog

ROOT = Path(__file__).resolve().parents[1]

def load_manifest():
    return json.loads((ROOT / "CLX054_ACCEPTANCE_MANIFEST.json").read_text())

def test_clx054_final_acceptance_after_plus60():
    m=load_manifest()
    assert m["phase"]=="CLX-054"
    assert m["status"]=="ACCEPTED"
    assert m["freeze"] is True
    assert m["watchWindow"]["plus60"]=="PASS"
    assert m["finalization"]["finalPlus60Verification"]=="PASS"

def test_clx054_preserves_frozen_scope():
    m=load_manifest()
    assert m["scope"]["screenBaseline"]==196
    assert m["scope"]["screenCountChange"]==0
    assert build_catalog()["screen_count"]==196
    assert m["scope"]["dataModelBusinessChange"] is False
    assert m["scope"]["apiContractBreak"] is False

def test_clx054_cutover_safety_state():
    m=load_manifest()
    assert m["runtime"]["productionTraffic"]=="ON"
    assert m["runtime"]["liveProviders"]=="OFF"
    assert m["runtime"]["realMoney"]=="OFF"
    assert m["controls"]["enabledLiveProviderCount"]==0
    assert m["controls"]["criticalRollbackTriggerDetected"] is False

def test_clx054_runtime_database_and_finance_evidence():
    m=load_manifest()
    assert m["runtime"]["webDigest"]=="sha256:069beaca63368729f10a5ab5be57824963b40ff25331a8a4f8574cf9a59aa00b"
    assert m["runtime"]["apiDigest"]=="sha256:bbc746f1317f5eec88addfb040acb94f2d7e33760c57dcd965cd3b013d050c31"
    assert m["database"]["projectRef"]=="ozupgknqaqgvprliewxe"
    assert m["database"]["status"]=="ACTIVE_HEALTHY"
    assert m["database"]["jobs50001to50005"]==["50001","50002","50003","50004","50005"]
    assert m["database"]["finance"]["balanced"] is True
    assert m["database"]["finance"]["totalDebit"]==m["database"]["finance"]["totalCredit"]
