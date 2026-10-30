"""Regression contracts for UAT form duplicate-click and retry protection."""
from pathlib import Path

HTML=(Path(__file__).resolve().parents[1]/"web"/"index.html").read_text()

def test_agent_and_finance_forms_single_flight_and_retry_keys():
    assert "const m3SaveState={agent:{busy:false,fingerprint:null,key:null},finance:{busy:false,fingerprint:null,key:null}}" in HTML
    for scope,fn in (("agent","Agent"),("finance","Finance")):
        start=HTML.index(f"async function save{fn}Form(mode){{")
        end=HTML.find("\nasync function ",start+1)
        chunk=HTML[start:end if end!=-1 else None]
        assert f"if(m3SaveState.{scope}.busy)return;" in chunk
        assert f"m3SaveState.{scope}.busy=true;" in chunk
        assert f"finally{{m3SaveState.{scope}.busy=false}}" in chunk
        assert f"m3SaveKey('{scope}',{{path,body}})" in chunk
        assert f"m3SaveState.{scope}.fingerprint=null;m3SaveState.{scope}.key=null;" in chunk

def test_no_timestamp_only_submission_keys_remain():
    assert "headers['Idempotency-Key']='CLX042-'+schema.module+'-'+jr+'-'+Date.now()" not in HTML
    assert "headers['Idempotency-Key']='CLX043-'+kind+'-'+state.current.key+'-'+Date.now()" not in HTML
    assert "if(slot.fingerprint!==fingerprint)" in HTML
