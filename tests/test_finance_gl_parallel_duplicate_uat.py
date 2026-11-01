"""Concurrent duplicate GL invoice/payment/receipt creation, isolated SQLite only.

Two simultaneous requests for the same module + external reference must not
create multiple GL records. Tests use fresh seeded disposable database.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.main import app

@pytest.fixture
def isolated_finance_db(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"finance_parallel.db")
    monkeypatch.setenv("M3_BULK_PERMISSION_GOVERNANCE_ENABLED","false")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC","OFF")
    monkeypatch.setenv("M3_UAT_TOKEN","isolated-finance-parallel-test")
    seed_run(True);admin_seed_run();masterdata_seed_run()

@pytest.mark.parametrize("module,fields",[
    ("invoice",{"Invoice No.":"UAT-CONCURRENT-INVOICE","Currency":"USD","Invoice Amount":"100","Status":"Draft"}),
    ("payment",{"Voucher No.":"UAT-CONCURRENT-PAYMENT","Currency":"USD","Payment Amount":"100","Status":"Draft"}),
    ("receipt",{"Voucher No.":"UAT-CONCURRENT-RECEIPT","Currency":"USD","Receipt Amount":"100","Status":"Draft"}),
])
def test_parallel_distinct_keys_cannot_duplicate_gl_record(isolated_finance_db,module,fields):
    ref=f"UAT-PARALLEL-GL-{module.upper()}"
    barrier=Barrier(2)
    def submit(n):
        headers={"X-M3-UAT-Token":"isolated-finance-parallel-test","X-Role":"ADMIN","Idempotency-Key":f"parallel-{module}-{n}"}
        with TestClient(app,raise_server_exceptions=False) as client:
            barrier.wait(timeout=15)
            r=client.post(f"/api/v1/gl/{module}",json={"external_ref":ref,"fields":fields},headers=headers)
            return r.status_code,r.text
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(submit,n) for n in (1,2)]
        replies=[f.result(timeout=45) for f in futures]
    assert sorted(code for code,_ in replies)==[201,409],(module,replies)
    with db.connect() as conn:
        count=conn.execute("SELECT COUNT(*) FROM gl_records WHERE module=? AND external_ref=?",(module,ref)).fetchone()[0]
    assert count==1,(module,count)
