"""Treasury governed-session routing contract.

This test intentionally documents the legacy no-session compatibility path.
It is NOT an authenticated UAT pass and must not be interpreted as one.
"""
from app import clx034_smart_approval_fast_track as governance
from app import treasury

def test_sessionless_treasury_approval_remains_explicit_legacy_path():
    result=governance.enforce_treasury_action("advance-receipts",1,"approve",None,1)
    assert result=={"legacy_compatibility":True,"phase":"CLX-034"}

def test_governed_treasury_action_uses_session_and_execution_policy(monkeypatch):
    calls={}
    class FakeConnection:
        def execute(self,sql,params):
            assert "treasury_records" in sql
            return self
        def fetchone(self):
            return {"maker_id":"maker-u","amount":125.5,"currency":"USD"}
        def close(self):calls["closed"]=True
    monkeypatch.setattr(governance,"connect",lambda:FakeConnection())
    monkeypatch.setattr(governance,"session",lambda c,token:{"user_ref":"checker-u","office_code":"UAT"})
    import app.clx033_finance_transaction_governance as finance
    monkeypatch.setattr(finance,"_country_for_office",lambda c,office:"NL")
    monkeypatch.setattr(finance,"_treasury_tx_type",lambda module:"RECEIPT")
    def check(*args,**kwargs):
        calls["args"]=args
        return {"authorized_by_policy":True}
    monkeypatch.setattr(governance,"enforce_execution",check)
    result=governance.enforce_treasury_action("advance-receipts",8,"approve","synthetic-session",2)
    assert result["authorized_by_policy"]
    assert calls["closed"]
    assert calls["args"][0:4]==("TREASURY","advance-receipts",8,"approve")
    assert calls["args"][4]=="synthetic-session"
    assert calls["args"][5]==2

def test_treasury_action_handler_calls_governance_before_mutation():
    from pathlib import Path
    s=(Path(__file__).resolve().parents[1]/"app"/"treasury.py").read_text()
    action=s[s.index("def action(module:"):]
    assert action.index("enforce_treasury_action(module,rid,action,x_m3_session,b.version)") < action.index("c=connect();tx(c)")
