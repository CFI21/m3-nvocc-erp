"""Disposable SQLite maker/checker -> release -> ledger integration test.

Policy gates are replaced with explicitly named synthetic authorization stubs;
this tests financial persistence and maker/checker invariants, NOT IAM itself.
"""
import json
import pytest
from fastapi import HTTPException
from app import db, treasury
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run

@pytest.fixture
def synthetic_transaction(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"treasury_approval_release.db")
    seed_run(True);admin_seed_run();masterdata_seed_run()
    monkeypatch.setattr(treasury,"enforce_item8_sensitive_action",lambda *args,**kw:None)
    monkeypatch.setattr(treasury,"enforce_treasury_action",lambda *args,**kw:None)
    monkeypatch.setattr(treasury,"assert_period_postable",lambda *args,**kw:None)
    monkeypatch.setattr(treasury,"fx_rate",lambda *args,**kw:1.0)
    monkeypatch.setattr(treasury,"treasury_account_move",lambda *args,**kw:None)
    with db.connect() as c:
        cur=c.execute("""INSERT INTO treasury_records(
            module,external_ref,job_id,party_type,party_name,currency,amount,
            status,version,maker_id,source_type,source_ref,payload_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("advance-receipts","UAT-AUTH-RELEASE-001",None,None,"Synthetic Customer",
             "USD",125.5,"Draft",1,"maker-one",None,None,
             json.dumps({"Amount":"125.50","Currency":"USD","Date":"2026-09-23"}),
             treasury.now(),treasury.now()))
        rid=cur.lastrowid;c.commit()
    return rid

def test_checker_separation_release_creates_one_balanced_posting(synthetic_transaction):
    rid=synthetic_transaction
    with pytest.raises(HTTPException) as ex:
        treasury.action("advance-receipts",rid,"approve",treasury.ActionBody(version=1),
                        x_role="ADMIN",x_actor_id="maker-one",x_m3_session=None)
    assert ex.value.status_code==422
    approved=treasury.action("advance-receipts",rid,"approve",treasury.ActionBody(version=1),
                             x_role="ADMIN",x_actor_id="checker-two",x_m3_session=None)
    assert approved["record"]["version"]==2
    released=treasury.action("advance-receipts",rid,"release",treasury.ActionBody(version=2),
                             x_role="ADMIN",x_actor_id="checker-two",x_m3_session=None)
    assert released["record"]["status"]=="Released"
    with pytest.raises(HTTPException) as stale:
        treasury.action("advance-receipts",rid,"release",treasury.ActionBody(version=2),
                        x_role="ADMIN",x_actor_id="checker-two",x_m3_session=None)
    assert stale.value.status_code==409
    with db.connect() as c:
        links=c.execute("SELECT voucher_id FROM treasury_gl_links WHERE treasury_record_id=? AND link_type='POSTING'",(rid,)).fetchall()
        assert len(links)==1
        voucher=c.execute("SELECT total_debit,total_credit,source_ref FROM gl_vouchers WHERE id=?",(links[0]["voucher_id"],)).fetchone()
        assert voucher["total_debit"]==voucher["total_credit"]==125.5
        assert voucher["source_ref"]=="UAT-AUTH-RELEASE-001"
        lines=c.execute("SELECT debit,credit FROM gl_voucher_lines WHERE voucher_id=?",(links[0]["voucher_id"],)).fetchall()
        assert len(lines)==2
        assert round(sum(x["debit"] for x in lines),2)==round(sum(x["credit"] for x in lines),2)==125.5
