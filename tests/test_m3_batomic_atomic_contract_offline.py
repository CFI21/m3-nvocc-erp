"""Secret-free offline contract tests; no HTTP or database operations."""
import copy
import importlib.util
from pathlib import Path
import pytest

path=Path(__file__).resolve().parents[1]/"scripts/m3_batomic_atomic_contract_offline.py"
spec=importlib.util.spec_from_file_location("atomic_contract",path)
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

def test_valid_two_row_fixture():
    a=gate.make_fixture("a"*16)
    assert gate.validate_fixture(a)
    assert a["body"]["domain"]=="customer"
    assert a["headers"]["Idempotency-Key"].startswith("m3-uat-b-atomic")
    assert a["headers"]["X-M3-Session"]=="<RUNTIME_ONLY>"
    assert a["submission_enabled"] is False

@pytest.mark.parametrize("mutation",[
    lambda a:a["headers"].update({"Idempotency-Key":"short"}),
    lambda a:a["headers"].update({"X-M3-Session":"real-token"}),
    lambda a:a["body"].update({"domain":"unsupported"}),
    lambda a:a["body"]["rows"][1].update({"record_key":a["body"]["rows"][0]["record_key"].lower()}),
    lambda a:a["body"]["rows"][0].pop("reason"),
    lambda a:a["body"]["rows"][0].update({"operation":"DEACTIVATE"}),
    lambda a:a.update({"submission_enabled":True}),
    lambda a:a["body"].update({"idempotency_key":"body-injection"}),
])
def test_bad_contract_rejected(mutation):
    a=copy.deepcopy(gate.make_fixture("a"*16))
    mutation(a)
    with pytest.raises(ValueError): gate.validate_fixture(a)

def test_unique_keys_between_generated_runs():
    a,b=gate.make_fixture(),gate.make_fixture()
    assert a["headers"]["Idempotency-Key"]!=b["headers"]["Idempotency-Key"]
    assert a["body"]["rows"][0]["record_key"]!=b["body"]["rows"][0]["record_key"]

def test_invalid_nonce_refused():
    with pytest.raises(ValueError):gate.make_fixture("nothex")
