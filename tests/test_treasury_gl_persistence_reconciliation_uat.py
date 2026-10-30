"""Executable disposable-DB Treasury -> GL posting reconciliation.

Proves actual voucher/link/line rows and transaction rollback, without
touching Render, Neon or real user records. External period/FX policy is
stubbed so this test focuses specifically on atomic ledger persistence.
"""
import json
import pytest
from app import db,treasury
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"treasury_reconciliation.sqlite")
    seed_run(True);admin_seed_run();masterdata_seed_run()
    monkeypatch.setattr(treasury,"assert_period_postable",lambda *a,**kw:None)
    monkeypatch.setattr(treasury,"fx_rate",lambda *a,**kw:1.0)
    monkeypatch.setattr(treasury,"treasury_account_move",lambda *a,**kw:None)
    monkeypatch.setattr(treasury,"next_voucher",lambda c,kind:"UAT-REC-LEDGER-001")
    with db.connect() as c:
        cur=c.execute("""INSERT INTO treasury_records(
          module,external_ref,job_id,party_type,party_name,currency,amount,
          status,version,maker_id,source_type,source_ref,payload_json,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
          "advance-receipts","UAT-REC-LEDGER-001",None,None,"Synthetic Customer",
          "USD",125.50,"Approved",1,"uat-maker",None,None,
          json.dumps({"Amount":"125.50","Currency":"USD","Date":"2026-09-23"}),
          treasury.now(),treasury.now()))
        rid=cur.lastrowid
        c.commit()
    return rid

def _ledger_rows(conn,rid):
    return conn.execute("""SELECT v.id,v.total_debit,v.total_credit,v.currency,
       v.source_ref,l.link_type FROM treasury_gl_links l
       JOIN gl_vouchers v ON v.id=l.voucher_id
       WHERE l.treasury_record_id=?""",(rid,)).fetchall()

def test_treasury_post_persists_one_balanced_voucher_two_lines_and_source_link(env):
    with db.connect() as c:
        db.tx(c)
        record=treasury.getrec(c,"advance-receipts",env)
        voucher_id,vno=treasury.gl_post(c,record,"advance-receipts","uat-checker",False)
        c.execute("COMMIT")
    with db.connect() as c:
        links=_ledger_rows(c,env)
        assert len(links)==1
        assert links[0]["id"]==voucher_id
        assert links[0]["source_ref"]=="UAT-REC-LEDGER-001"
        assert links[0]["link_type"]=="POSTING"
        assert links[0]["currency"]=="USD"
        assert links[0]["total_debit"]==links[0]["total_credit"]==125.50
        lines=c.execute("SELECT debit,credit FROM gl_voucher_lines WHERE voucher_id=? ORDER BY line_no",(voucher_id,)).fetchall()
        assert len(lines)==2
        assert round(sum(float(x["debit"]) for x in lines),2)==125.50
        assert round(sum(float(x["credit"]) for x in lines),2)==125.50
        assert all(not (x["debit"] and x["credit"]) for x in lines)

def test_treasury_post_rollback_leaves_no_voucher_or_link(env):
    with db.connect() as c:
        db.tx(c)
        record=treasury.getrec(c,"advance-receipts",env)
        treasury.gl_post(c,record,"advance-receipts","uat-checker",False)
        c.execute("ROLLBACK")
    with db.connect() as c:
        assert len(_ledger_rows(c,env))==0
        assert c.execute("SELECT COUNT(*) FROM gl_vouchers WHERE source_type='TREASURY' AND source_ref='UAT-REC-LEDGER-001'").fetchone()[0]==0
