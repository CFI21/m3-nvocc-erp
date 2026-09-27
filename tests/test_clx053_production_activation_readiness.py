import json
from pathlib import Path

from app.screen_catalog import build_catalog

ROOT = Path(__file__).resolve().parents[1]


def parent():
    return json.loads((ROOT / "CLX052_ACCEPTANCE_MANIFEST.json").read_text())


def test_clx052_is_frozen_accepted_parent():
    m = parent()
    assert m["phase"] == "CLX-052"
    assert m["status"] == "ACCEPTED"
    assert m["freeze"] is True
    assert m["go_live_ready"] is True
    assert m["production_ready"] is True
    assert m["scope"]["screenBaseline"] == 196
    assert build_catalog()["screen_count"] == 196


def test_runtime_lock_matches_clx052_candidate():
    m = parent()
    rt = m["governedRuntime"]
    assert rt["webServiceId"] == "srv-darbgkvlk1mc738rohng"
    assert rt["apiServiceId"] == "srv-dar9fh17lnhs73ahlh60"
    assert rt["webDigest"] == "sha256:069beaca63368729f10a5ab5be57824963b40ff25331a8a4f8574cf9a59aa00b"
    assert rt["apiDigest"] == "sha256:bbc746f1317f5eec88addfb040acb94f2d7e33760c57dcd965cd3b013d050c31"


def test_activation_remains_off_during_clx053_validation():
    m = parent()
    assert m["controls"] == {
        "productionTraffic": False,
        "liveProviders": False,
        "realMoney": False,
    }
    assert m["finalActivationGate"] == "SEPARATE_EXPLICIT_USER_COMMAND_REQUIRED"


def test_governance_and_contracts_preserved():
    s = parent()["scope"]
    assert s["menuOrder"] == "PRESERVED"
    assert s["businessLogic"] == "PRESERVED"
    assert s["dataModelBusinessChange"] is False
    assert s["apiContractBreak"] is False
    assert s["roleDataScope"] == "PRESERVED"
    assert s["makerChecker"] == "PRESERVED"
    assert s["fourEyes"] == "PRESERVED"
    assert s["sod"] == "PRESERVED"
    assert s["audit"] == "PRESERVED"
    assert s["newBusinessModules"] is False


def test_database_and_rollback_evidence_present():
    m = parent()
    assert m["database"]["provider"] == "Supabase"
    assert m["database"]["projectRef"] == "ozupgknqaqgvprliewxe"
    assert m["database"]["jobs50001to50005"] == ["50001", "50002", "50003", "50004", "50005"]
    assert m["evidence"]["controlledRollbackRestoreDrill"] == "PASS"
    assert m["evidence"]["rollbackTarget"]["status"] == "PASS"
    assert m["evidence"]["restoredCandidate"]["status"] == "PASS"
