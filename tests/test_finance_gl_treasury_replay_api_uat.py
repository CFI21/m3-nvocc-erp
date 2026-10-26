"""Executable finance retry regression with a disposable SQLite database.

No real UAT, Neon, or production data is accessed.
"""
import pytest
from fastapi.testclient import TestClient
from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.main import app

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"finance_replay.db")
    monkeypatch.setenv("M3_BULK_PERMISSION_GOVERNANCE_ENABLED","false")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC","OFF")
    monkeypatch.setenv("M3_UAT_TOKEN","isolated-finance-idempotency-test")
    seed_run(True);admin_seed_run();masterdata_seed_run()
    with TestClient(app,raise_server_exceptions=False,headers={
        "X-M3-UAT-Token":"isolated-finance-idempotency-test","X-Role":"ADMIN"
    }) as c:
        yield c

@pytest.mark.parametrize("module,fields",[
    ("invoice",{"Invoice No.":"UT-INV-001","Customer / Supplier":"UAT Customer","Currency":"USD","Invoice Amount":"100","Status":"Draft"}),
    ("bills",{"Bill No.":"UT-BILL-001","Vendor":"UAT Vendor","Currency":"USD","Invoice Amount":"100","Status":"Draft"}),
    ("payment",{"Voucher No.":"UT-PAY-001","Party Account":"UAT Vendor","Currency":"USD","Payment Amount":"100","Status":"Draft"}),
    ("receipt",{"Voucher No.":"UT-REC-001","Party Account":"UAT Customer","Currency":"USD","Receipt Amount":"100","Status":"Draft"})
])
def test_gl_document_exact_replay_changed_payload_and_external_ref(client,module,fields):
    url=f"/api/v1/gl/{module}"
    req={"external_ref":f"UAT-GL-{module.upper()}-REPLAY","fields":fields}
    hdr={"Idempotency-Key":f"uat-gl-{module}-replay"}
    first=client.post(url,json=req,headers=hdr)
    assert first.status_code==201,(module,first.text)
    second=client.post(url,json=req,headers=hdr)
    assert second.status_code==201,(module,second.text)
    assert second.json()["id"]==first.json()["id"]
    changed=client.post(url,json={**req,"external_ref":req["external_ref"]+"-CHANGED"},headers=hdr)
    assert changed.status_code==409,(module,changed.text)
    different_key=client.post(url,json=req,headers={"Idempotency-Key":hdr["Idempotency-Key"]+"-new"})
    assert different_key.status_code==409,(module,different_key.text)
    with db.connect() as conn:
        n=conn.execute("SELECT COUNT(*) FROM gl_records WHERE module=? AND external_ref=?",(module,req["external_ref"])).fetchone()[0]
    assert n==1

def test_treasury_idempotency_rejects_changed_payment_amount(client):
    url="/api/v1/treasury/advance-payments"
    req={"external_ref":"UAT-TREASURY-PAY-001","fields":{"Amount":"100","Currency":"USD","Status":"Draft"}}
    hdr={"Idempotency-Key":"uat-treasury-replay-amount"}
    first=client.post(url,json=req,headers=hdr)
    assert first.status_code==201,(first.status_code,first.text)
    again=client.post(url,json=req,headers=hdr)
    assert again.status_code in (200,201),again.text
    assert again.json()["id"]==first.json()["id"]
    modified=client.post(url,json={**req,"fields":{**req["fields"],"Amount":"200"}},headers=hdr)
    assert modified.status_code==409,modified.text
    with db.connect() as conn:
        n=conn.execute("SELECT COUNT(*) FROM treasury_records WHERE module=? AND external_ref=?",("advance-payments",req["external_ref"])).fetchone()[0]
    assert n==1
